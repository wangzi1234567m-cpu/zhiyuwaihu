"""Regression probes for the Zhai delivery. Uses the normal main-based session worker."""
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.contracts import ActionCommand
from app.db import Application, Conversation, Effect, Knowledge, Operation, Record, Session
from app.seed import fixture_id, seed
from app.knowledge_templates import REQUIRED_SLOTS, render
from test_integration import setup, opened, say, wait_idle, wait_closed, pytestmark


async def pending(client, app):
    sid = await opened(client)
    await say(client, sid, '我是演示商家1')
    await app.state.coordinator.shutdown()
    await say(client, sid, '明白了')
    with Session() as db:
        row = db.scalar(select(Operation).where(Operation.session_id == sid))
        assert row.status == 'pending'
        return sid, ActionCommand.model_validate(row.data['command'])


@pytest.mark.parametrize('revision,conclusion,code', [(2, 'approved', 'STALE_REVISION'),
    (1, 'approved', 'ACTION_FORBIDDEN'), (1, 'pending', 'ACTION_FORBIDDEN')])
async def test_stale_queued_rejection_has_no_effect(setup, revision, conclusion, code):
    client, app, services = setup
    sid, command = await pending(client, app)
    with Session.begin() as db:
        case = db.get(Application, str(command.application_id))
        case.data = {**case.data, 'case_revision': revision, 'review_conclusion': conclusion}
    await app.state.coordinator.process_operation(str(command.operation_id))
    result = await services.business.query_operation(command)
    assert result.status == 'failed' and result.error.code == code
    assert (await services.business.execute(command)) == result
    assert (await client.get(f'/api/v1/sessions/{sid}')).json()['state'] == 'MANUAL_REVIEW'
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Effect)) == 0


@pytest.mark.parametrize('fault', ['query_timeout', 'query_failure'])
async def test_reconcile_recovers_original_success_after_revision_change(setup, fault):
    client, app, services = setup
    sid, command = await pending(client, app)
    oid = str(command.operation_id)
    worker = app.state.coordinator
    services.business.fault = 'lost_response'
    await worker.process_operation(oid)
    with Session.begin() as db:
        case = db.get(Application, str(command.application_id))
        case.data = {**case.data, 'case_revision': 2, 'review_conclusion': 'approved'}
    services.business.fault = fault
    await worker.process_operation(oid)
    view = (await client.get(f'/api/v1/sessions/{sid}')).json()
    assert view['state'] == 'RECONCILING' and view['operations'][0]['status'] == 'unknown'
    services.business.fault = 'none'
    await worker.process_operation(oid)
    result = await services.business.execute(command)
    assert result.status == 'succeeded' and result == await services.business.query_operation(command)
    with Session() as db:
        assert db.get(Operation, oid).key == command.idempotency_key
        assert db.scalar(select(func.count()).select_from(Effect)) == 1


@pytest.mark.parametrize('value', ['', '  ', '【brand_name】', 'XXX'])
async def test_missing_slots_go_manual_before_phone_disclosure(setup, value):
    client, _, _ = setup
    with Session.begin() as db:
        case = db.get(Application, fixture_id(1))
        case.data = {**case.data, 'slots': {**case.data['slots'], 'brand_name': value}}
    sid = await opened(client)
    view = await say(client, sid, '我是演示商家1')
    assert view['state'] == 'MANUAL_REVIEW' and not view['operations']
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Effect)) == 0


@pytest.mark.parametrize('brand,template,written,success', [
    ('', '【授权时间】请核对【brand_name】', '【授权时间】请核对【brand_name】', False),
    ('合成乙', '【授权时间】请核对【brand_name】', '【授权时间】请核对合成乙', True),
    ('合成乙', '【类目：正确类似群：】', '【类目：正确类似群：】', False),
])
async def test_written_command_checked_at_mock_boundary(setup, brand, template, written, success):
    client, _, services = setup
    seed(include_routing_fixtures=True)
    aid = fixture_id(4)
    response = await client.post('/api/v1/sessions', json={'application_id': aid, 'mode': 'text'},
                                 headers={'Idempotency-Key': str(uuid4())})
    sid = response.json()['session_id']
    oid = uuid4()
    with Session.begin() as db:
        case = db.get(Application, aid)
        slots = {**case.data['slots'], 'brand_name': brand}
        case.data = {**case.data, 'slots': slots}
        kb = db.get(Knowledge, fixture_id(204))
        kb.data = {**kb.data, 'approved_text': template,
                   'required_slots': ['brand_name']}
        # The team policy supplies a written routing decision at this port boundary.
        session = db.get(Conversation, sid)
        session.data = {**session.data, 'route': 'jingmai'}
        command = ActionCommand(session_id=sid, application_id=aid, case_revision=1,
            decision_id=uuid4(), operation_id=oid, kind='reject_application', idempotency_key=str(oid),
            evidence_refs=[], payload={'type': 'reject_application', 'reason_codes': ['demo.no_brand'],
                'knowledge_entry_ids': [kb.id], 'written_text': written})
        db.add(Operation(id=str(oid), session_id=sid, key=command.idempotency_key, status='pending',
                         data={'manual': True, 'command': command.model_dump(mode='json')}))
    result = await services.business.execute(command)
    assert result.status == ('succeeded' if success else 'failed')
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Effect)) == int(success)


@pytest.mark.parametrize('company', [' ', '【company_name】', '{company_name}', 'XXX'])
async def test_opening_requires_a_real_company(setup, company):
    client, _, _ = setup
    with Session.begin() as db:
        case = db.get(Application, fixture_id(1))
        case.data = {**case.data, 'slots': {**case.data['slots'], 'company_name': company}}
    tasks = (await client.get('/api/v1/applications')).json()
    assert next(t for t in tasks if t['application_id'] == fixture_id(1))['company_hint'] == '待核实主体'
    sid = await opened(client)
    view = await wait_idle(client, sid)
    assert view['state'] == 'MANUAL_REVIEW' and view['ended'] and not view['verified']
    assert '已转人工' in view['messages'][-1]['text']
    assert not view['operations'] and not view['source_refs']


async def test_three_releases_render_task_values_and_writeback(setup, monkeypatch):
    from publish_reviewed_knowledge import publish, validate
    from prepare_knowledge_review import draft
    from convert_private_sources import convert
    client, _, services = setup
    release = 'test-only-authorizations'
    templates = ['{brand_name}补齐{missing_items}', '{brand_name}请重新提交有效授权',
                 '{brand_name}补齐{authorization_from}到{authorization_to}的证明']
    source = os.getenv('ZHAI_ACCEPTANCE_ZIP')
    if source:
        staging = json.dumps(convert([Path(source)]), ensure_ascii=False).encode()
        manifest = draft(staging, release, Path(os.environ['ZHAI_ACCEPTANCE_OPINION']).read_bytes())
        assert manifest['source_review']['sha256'] and manifest['reviewers'] == []
        manifest['reviewers'] = ['test-fixture-a', 'test-fixture-b']
        for entry in manifest['entries']:
            assert entry['status'] == 'draft' and entry['reviewer'] is None
            entry.update(status='approved', issue_codes=[], reviewer='test-fixture-a')
        _, entries = validate(staging, manifest)
    else:
        entries = []
        for scenario, template in zip(REQUIRED_SLOTS, templates, strict=True):
            entry = services.knowledge.lookup(scenario, '', ['synthetic'], 'demo-k1')[0]
            entries.append(entry.model_copy(update={'entry_id': uuid4(), 'knowledge_version': release,
                'required_slots': list(REQUIRED_SLOTS[scenario]), 'approved_text': template}))
    publish(release, entries)
    publish(release, entries)
    monkeypatch.setenv('ZHIYU_KNOWLEDGE_VERSION', release)
    for i, entry in enumerate(entries, 1):
        aid = fixture_id(i)
        case = services.cases.get_case('demo', aid)
        expected = render(entry, case).approved_text
        sid = (await client.post('/api/v1/sessions', json={'application_id': aid, 'mode': 'text'},
               headers={'Idempotency-Key': str(uuid4())})).json()['session_id']
        await client.post(f'/api/v1/sessions/{sid}/start', json={'expected_revision': 0},
                          headers={'Idempotency-Key': str(uuid4())})
        before = await wait_idle(client, sid)
        assert before['reason'] is None and not before['source_refs']
        assert case.slots['brand_name'] not in before['messages'][-1]['text']
        view = await say(client, sid, '我是'+case.slots['company_name'])
        assert expected in view['messages'][-1]['text']
        await say(client, sid, '明白了')
        view = await wait_closed(client, sid)
        assert view['record']['knowledge_version'] == release
        assert view['record']['source_refs'] == [ref.model_dump() for ref in entry.source_refs]
        with Session() as db:
            effect = db.scalar(select(Effect).where(Effect.operation_id == view['operations'][0]['operation_id']))
            assert effect.data['command']['payload']['written_text'] == expected
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Effect)) == 3
        assert db.scalar(select(func.count()).select_from(Record)) == 3


@pytest.mark.parametrize('number', [1, 2, 3])
async def test_every_authorization_slot_is_required_and_task_specific(setup, number):
    _, _, services = setup
    case = services.cases.get_case('demo', fixture_id(number))
    names = REQUIRED_SLOTS[case.scenario_id]
    entry = services.knowledge.lookup(case.scenario_id, '', ['synthetic'], 'demo-k1')[0].model_copy(
        update={'required_slots': list(names), 'approved_text': '【授权时间】' + '/'.join('{'+name+'}' for name in names)})
    for suffix in ['甲', '乙']:
        slots = {name: name+suffix for name in names}
        actual = render(entry, case.model_copy(update={'slots': slots}))
        assert actual.approved_text == '【授权时间】' + '/'.join(slots.values())
        for name in names:
            with pytest.raises(ValueError):
                render(entry, case.model_copy(update={'slots': {**slots, name:'  '}}))
