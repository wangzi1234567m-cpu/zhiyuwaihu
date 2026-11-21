"""Codex独立审查回归；不修改生产实现，只在专用review测试库验证发现。
预期正确行为写成断言；失败用于复现缺陷，不替实现放宽条件。
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select, text

MISSION = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MISSION / 'backend' / 'tests'))
from test_semantic import make_case, make_context, make_entry
from test_integration import opened, say, wait_closed
from app.adapters import DomainError, Services, MockBusiness
from app.contracts import ActionCommand, CallbackConfirmation, CallbackPayload, RejectPayload
from app.db import Application, Conversation, Effect, Operation, Session, engine
from app.guard import GuardContext, ModelConfig, RoutingPolicy, ToolGuard, business_key
from app.main import create_app
from app.seed import fixture_id, seed
from app.semantic import GlmUnderstanding, UnderstandingBudget


def reject_command(case, entry, decision_id):
    return ActionCommand(session_id=uuid4(), application_id=case.application_id,
                         case_revision=case.case_revision, decision_id=decision_id,
                         operation_id=uuid4(), kind='reject_application',
                         idempotency_key=business_key(case, 'reject_application'),
                         evidence_refs=[], payload=RejectPayload(
                             type='reject_application', reason_codes=[case.scenario_id],
                             written_text=entry.approved_text,
                             knowledge_entry_ids=[entry.entry_id]))


def guard_data(case):
    decision = RoutingPolicy().decide(case, knowledge_channel='phone')
    return {'verified':True, 'explained':True, 'ended':False, 'route':'phone',
            'knowledge_version':'demo-k1', 'rule_version':decision.rule_version,
            'case_revision':case.case_revision,
            'notification_message_ids':[str(uuid4())],
            'routing_decision':decision.model_dump(mode='json')}


def test_reject_key_stable_when_only_contact_attempt_changes():
    case = make_case(call_attempt=1)
    changed = case.model_copy(update={'call_attempt':2})
    assert business_key(case, 'reject_application') == business_key(changed, 'reject_application'), \
        'Same application revision can get a second reject key merely by increasing call_attempt'


def test_callback_canonical_expiry_is_enforced():
    case = make_case()
    data = guard_data(case)
    ref, sid = uuid4(), uuid4()
    current = datetime.now(timezone.utc)
    confirmation = CallbackConfirmation(
        confirmation_id=ref, application_id=case.application_id, case_revision=case.case_revision,
        evidence_message_id=uuid4(), window_start=current+timedelta(days=1),
        window_end=current+timedelta(days=1,hours=1), expires_at=current-timedelta(minutes=1))
    command = ActionCommand(session_id=sid, application_id=case.application_id,
        case_revision=1, decision_id=UUID(data['routing_decision']['decision_id']), operation_id=uuid4(),
        kind='create_callback', idempotency_key='review-callback',
        confirmation_ref=ref, evidence_refs=[],
        payload=CallbackPayload(type='create_callback', confirmation_id=ref))
    with pytest.raises(DomainError, match='过期'):
        ToolGuard().authorize(case, data, command, (),
                              GuardContext(confirmations={str(ref):confirmation.model_dump(mode='json')}, now=current))


def test_guard_rejects_command_bound_to_another_decision():
    case=make_case()
    entry=make_entry(case)
    data=guard_data(case)
    command=reject_command(case, entry, uuid4())
    assert str(command.decision_id) != data['routing_decision']['decision_id']
    with pytest.raises(DomainError):
        ToolGuard().authorize(case, data, command, (entry,))


def test_guard_rejects_old_session_evidence_for_new_case_revision():
    case=make_case()
    data=guard_data(case)
    fresh=case.model_copy(update={'case_revision':2})
    entry=make_entry(fresh)
    command=reject_command(fresh, entry, UUID(data['routing_decision']['decision_id']))
    with pytest.raises(DomainError):
        ToolGuard().authorize(fresh, data, command, (entry,))


async def model_result(payload, user_text):
    case=make_case()
    def handle(request):
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(payload)}}]})
    port=GlmUnderstanding(config=ModelConfig('glm','synthetic','https://unused.invalid/v1',True,'test'),
                          api_key='synthetic-test-value',
                          budget=UnderstandingBudget(max_requests=1,max_retries=0),
                          transport=httpx.MockTransport(handle))
    return await port.understand(text=user_text,case=case,context=make_context(case))


@pytest.mark.asyncio
async def test_glm_retains_slot_present_in_user_text():
    result=await model_result({'intents':['request_callback'],'slots':{'callback_window':'明天下午'}}, '请明天下午回电')
    assert result.slots.get('callback_window') == '明天下午', 'GLM adapter discarded an evidenced slot because it did not pass text into validation'


@pytest.mark.asyncio
async def test_glm_rejects_ungrounded_wrong_person_claim():
    result=await model_result({'intents':['deny_identity']}, '请说明一下整改方法')
    assert 'deny_identity' not in result.intents, 'GLM path accepted unsupported high-impact intent'


@pytest.fixture
def dedicated_db():
    if os.getenv('RUN_DB_TESTS') != '1':
        pytest.skip('RUN_DB_TESTS=1 requires a dedicated migrated PostgreSQL test DB')
    if not (engine.url.database or '').endswith('_test'):
        pytest.fail('Destructive integration reset requires a database name ending in _test')
    with Session.begin() as db:
        db.execute(text('TRUNCATE request_receipts, confirmations, call_records, business_effects, operations, events, messages, sessions, knowledge, applications CASCADE'))
    seed()


@pytest.mark.asyncio
async def test_real_db_reject_not_duplicated_after_contact_counter_update(dedicated_db):
    app=create_app(Services())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'X-Requested-With':'zhiyu'}) as client:
            assert (await client.post('/api/v1/dev/login',json={'username':'demo','password':'demo-local-only'})).status_code == 200
            for counter in (0,1):
                if counter:
                    with Session.begin() as db:
                        row=db.get(Application,fixture_id(1))
                        row.data={**row.data,'call_attempt':counter}
                sid=await opened(client)
                await say(client,sid,'我是演示商家1')
                value=await say(client,sid,'明白了')
                if value['state']=='MANUAL_REVIEW':
                    # 已有业务的跨会话重放按原契约转人工，但必须链接原成功操作。
                    assert len(value['operations'])==1 and value['operations'][0]['status']=='succeeded'
                else:
                    await wait_closed(client,sid)
            with Session() as db:
                count=db.scalar(select(func.count()).select_from(Effect))
            assert count == 1, f'Same application and revision produced {count} persisted business effects after only call_attempt changed'


@pytest.mark.asyncio
async def test_real_db_case_revision_change_invalidates_old_phone_evidence(dedicated_db):
    app=create_app(Services())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'X-Requested-With':'zhiyu'}) as client:
            await client.post('/api/v1/dev/login',json={'username':'demo','password':'demo-local-only'})
            sid=await opened(client)
            await say(client,sid,'我是演示商家1')
            with Session.begin() as db:
                row=db.get(Application,fixture_id(1))
                row.data={**row.data,'case_revision':row.data['case_revision']+1}
            await say(client,sid,'明白了')
            await asyncio.sleep(.8)
            with Session() as db:
                effects=list(db.scalars(select(Effect)))
                state=db.get(Conversation,sid).data
                detail=[{'operation_case_revision':e.data['command']['case_revision'],
                         'session_case_revision':state['case_revision']} for e in effects]
            assert not effects, f'Old phone evidence authorized a new review revision: {detail}'


@pytest.mark.asyncio
async def test_end_after_dispatch_still_finishes_writeback(dedicated_db):
    started, release=asyncio.Event(),asyncio.Event()
    class PausedBusiness(MockBusiness):
        async def execute(self, command):
            result=await super().execute(command)
            started.set()
            await release.wait()
            return result
    app=create_app(Services(business=PausedBusiness()))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'X-Requested-With':'zhiyu'}) as client:
            await client.post('/api/v1/dev/login',json={'username':'demo','password':'demo-local-only'})
            sid=await opened(client)
            await say(client,sid,'我是演示商家1')
            await say(client,sid,'明白了')
            try:
                await asyncio.wait_for(started.wait(),3)
                ended=await client.post(f'/api/v1/sessions/{sid}/end',json={})
                assert ended.status_code == 200
            finally:
                release.set()
            await asyncio.sleep(1.5)
            value=(await client.get(f'/api/v1/sessions/{sid}')).json()
            with Session() as db:
                count=db.scalar(select(func.count()).select_from(Effect))
            assert count == 1
            assert value['record'] is not None, f'Dispatched effect survived, but writeback stalled at state={value["state"]} operations={value["operations"]}'


@pytest.mark.asyncio
@pytest.mark.parametrize('legacy_format',['attempt_suffix','baseline_0_3'])
async def test_legacy_rejection_key_is_recognized_after_upgrade(dedicated_db,legacy_format):
    """修键格式后仍需识别升级前的真实业务，不可对已有成功申请再驳回。"""
    app=create_app(Services())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'X-Requested-With':'zhiyu'}) as client:
            await client.post('/api/v1/dev/login',json={'username':'demo','password':'demo-local-only'})
            sid=await opened(client)
            await say(client,sid,'我是演示商家1')
            await say(client,sid,'明白了')
            await wait_closed(client,sid)
            with Session.begin() as db:
                case=db.get(Application,fixture_id(1)).data
                legacy=f'{fixture_id(1)}:{case["scenario_id"]}:reject_application:r{case["case_revision"]}:s{case["submission_attempt"]}:c{case["call_attempt"]}'
                if legacy_format=='baseline_0_3':
                    legacy=f'{fixture_id(1)}:{case["case_revision"]}:reject_application'
                op=db.scalar(select(Operation))
                effect=db.scalar(select(Effect))
                op.key=legacy
                op.data={**op.data,'business_key':legacy,
                         'command':{**op.data['command'],'idempotency_key':legacy}}
                effect.key=legacy
                effect.data={**effect.data,'command':{**effect.data['command'],'idempotency_key':legacy}}
            second=await opened(client)
            await say(client,second,'我是演示商家1')
            await say(client,second,'明白了')
            await asyncio.sleep(.7)
            with Session() as db:
                count=db.scalar(select(func.count()).select_from(Effect))
            assert count == 1, f'Changing key format caused {count} business effects despite a persisted legacy rejection'
