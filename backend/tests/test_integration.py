from datetime import timedelta
from app.db import Confirmation, now
from app.contracts import ActionCommand, CallbackConfirmation
"""Run only against a dedicated PostgreSQL test DB. No SQLite substitutes."""
import asyncio
import os
from uuid import uuid4
import pytest
import httpx
from sqlalchemy import text,select,func
from app.main import create_app
from app.adapters import Services
from app.db import Session,Effect,Operation,Conversation,Message,EventRow,engine
from app.seed import seed,fixture_id

pytestmark=[pytest.mark.integration,pytest.mark.skipif(os.getenv('RUN_DB_TESTS')!='1',reason='RUN_DB_TESTS=1 requires a dedicated migrated PostgreSQL test DB')]

@pytest.fixture
async def setup():
    if not (engine.url.database or '').endswith('_test'):
        pytest.fail('Destructive integration reset requires a database name ending in _test')
    with Session.begin() as db:
        db.execute(text('TRUNCATE request_receipts, confirmations, call_records, business_effects, operations, events, messages, sessions, knowledge, applications CASCADE'))
    seed()
    services=Services()
    app=create_app(services)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'X-Requested-With':'zhiyu'}) as client:
            assert (await client.post('/api/v1/dev/login',json={'username':'demo','password':'demo-local-only'})).status_code==200
            yield client,app,services

async def wait_idle(client,sid):
    for _ in range(150):
        response=await client.get(f'/api/v1/sessions/{sid}')
        assert response.status_code==200,response.text
        data=response.json()
        if not data['busy']: return data
        await asyncio.sleep(.03)
    pytest.fail('generation did not complete')

async def create(client):
    response=await client.post('/api/v1/sessions',json={'application_id':fixture_id(1),'mode':'text'},headers={'Idempotency-Key':str(uuid4())})
    assert response.status_code==201,response.text
    return response.json()['session_id']

async def say(client,sid,value,key=None):
    response=await client.post(f'/api/v1/sessions/{sid}/turns',json={'text':value,'client_event_id':key or str(uuid4())})
    assert response.status_code==202,response.text
    return await wait_idle(client,sid)

async def opened(client):
    sid=await create(client)
    response=await client.post(f'/api/v1/sessions/{sid}/start',json={'expected_revision':0},headers={'Idempotency-Key':str(uuid4())})
    assert response.status_code==202,response.text
    await wait_idle(client,sid)
    return sid

async def ready_to_act(client):
    sid=await opened(client)
    result=await say(client,sid,'我是演示商家1')
    assert result['verified'] and result['reason']
    result=await say(client,sid,'补齐后一定必过吗')
    assert '不能保证' in result['messages'][-1]['text']
    return sid

async def wait_closed(client,sid):
    for _ in range(150):
        value=(await client.get(f'/api/v1/sessions/{sid}')).json()
        if value['state']=='CLOSED': return value
        await asyncio.sleep(.05)
    pytest.fail('operation/writeback not completed')

async def test_three_turns_persistence_and_idempotency(setup):
    client,app,services=setup
    sid=await ready_to_act(client)
    key=str(uuid4())
    await say(client,sid,'明白了',key)
    value=await wait_closed(client,sid)
    assert value['record'] and value['operations'][0]['status']=='succeeded'
    duplicate=await client.post(f'/api/v1/sessions/{sid}/turns',json={'text':'明白了','client_event_id':key})
    assert duplicate.status_code==202
    conflict=await client.post(f'/api/v1/sessions/{sid}/turns',json={'text':'改变参数','client_event_id':key})
    assert conflict.status_code==409
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Effect))==1
        seqs=list(db.scalars(select(EventRow.seq).where(EventRow.session_id==sid).order_by(EventRow.seq)))
        assert seqs==list(range(1,len(seqs)+1))

async def test_wrong_person_and_owner_isolation(setup):
    client,_,_=setup
    sid=await opened(client)
    before=(await client.get(f'/api/v1/sessions/{sid}')).json()
    assert before['reason'] is None
    value=await say(client,sid,'不是，打错了')
    assert value['ended'] and not value['operations'] and value['reason'] is None
    await client.post('/api/v1/dev/login',json={'username':'other','password':'demo-local-only'})
    assert (await client.get(f'/api/v1/sessions/{sid}')).status_code==404
    assert (await client.get(f'/api/v1/sessions/{sid}/events')).status_code==404

async def test_interrupt_old_generation_does_not_cancel_new(setup):
    client,_,_=setup
    sid=await opened(client)
    first=await client.post(f'/api/v1/sessions/{sid}/turns',json={'text':'我是演示商家1','client_event_id':str(uuid4())})
    old=first.json()['generation_id']
    await say(client,sid,'补齐后必过吗')
    response=await client.post(f'/api/v1/sessions/{sid}/interruptions',json={'generation_id':old,'client_event_id':str(uuid4())})
    assert response.json()=={'applied':False}
    value=(await client.get(f'/api/v1/sessions/{sid}')).json()
    assert any(m['status']=='interrupted' for m in value['messages'])
    assert value['messages'][-1]['status']=='completed'

async def test_success_with_lost_response_is_queried(setup):
    client,_,services=setup
    services.business.fault='lost_response'
    sid=await ready_to_act(client)
    await say(client,sid,'明白了')
    value=await wait_closed(client,sid)
    assert value['operations'][0]['status']=='succeeded'
    with Session() as db: assert db.scalar(select(func.count()).select_from(Effect))==1

async def test_writeback_retries_without_second_effect(setup):
    client,_,services=setup
    services.business.fault='writeback_failure'
    sid=await ready_to_act(client)
    await say(client,sid,'明白了')
    await asyncio.sleep(.4)
    value=(await client.get(f'/api/v1/sessions/{sid}')).json()
    assert value['state']=='WRITEBACK_PENDING' and value['record'] is None
    services.business.fault='none'
    await wait_closed(client,sid)
    with Session() as db: assert db.scalar(select(func.count()).select_from(Effect))==1

async def test_restart_invalidates_generation(setup):
    client,app,_=setup
    sid=await opened(client)
    await client.post(f'/api/v1/sessions/{sid}/turns',json={'text':'我是演示商家1','client_event_id':str(uuid4())})
    await app.state.coordinator.shutdown()
    await app.state.coordinator.startup()
    value=(await client.get(f'/api/v1/sessions/{sid}')).json()
    assert not value['busy']
    assert any(m['status']=='interrupted' for m in value['messages'])

async def test_unconfigured_voice_and_csrf(setup):
    client,_,_=setup
    sid=await create(client)
    assert (await client.post(f'/api/v1/sessions/{sid}/credentials',json={})).status_code==503
    assert (await client.post(f'/api/v1/sessions/{sid}/end',json={},headers={'Origin':'https://evil.invalid'})).status_code==403

async def test_cross_session_rejection_has_one_effect(setup):
    client,_,_=setup
    first=await ready_to_act(client)
    await say(client,first,'明白了')
    await wait_closed(client,first)
    second=await ready_to_act(client)
    result=await say(client,second,'明白了')
    assert result['state']=='MANUAL_REVIEW'
    assert len(result['operations'])==1
    with Session() as db: assert db.scalar(select(func.count()).select_from(Effect))==1

async def test_restart_queries_committed_effect(setup):
    from app.contracts import ActionCommand
    client,app,services=setup
    services.business.fault='persistent_unknown'
    sid=await ready_to_act(client)
    await say(client,sid,'明白了')
    await asyncio.sleep(.4)
    await app.state.coordinator.shutdown()
    with Session.begin() as db:
        op=db.scalar(select(Operation).where(Operation.session_id==sid))
        command=ActionCommand.model_validate(op.data['command'])
        op.status='unknown'
        op.data={**op.data,'next_at':0}
    # Represents an external system that committed just before the application crashed.
    services.business.fault='none'
    await services.business.execute(command)
    await app.state.coordinator.startup()
    value=await wait_closed(client,sid)
    assert value['operations'][0]['status']=='succeeded'
    with Session() as db: assert db.scalar(select(func.count()).select_from(Effect))==1

async def test_cancel_before_dispatch_has_no_operation(setup):
    client,_,services=setup
    sid=await ready_to_act(client)
    entered=asyncio.Event()
    class WaitingModel:
        async def stream(self,request,cancel):
            entered.set()
            await cancel.wait()
            if False: yield ''
    services.model=WaitingModel()
    accepted=(await client.post(f'/api/v1/sessions/{sid}/turns',json={'text':'明白了','client_event_id':str(uuid4())})).json()
    await asyncio.wait_for(entered.wait(),2)
    response=await client.post(f'/api/v1/sessions/{sid}/interruptions',json={'generation_id':accepted['generation_id'],'client_event_id':str(uuid4())})
    assert response.json()['applied']
    await asyncio.sleep(.3)
    value=(await client.get(f'/api/v1/sessions/{sid}')).json()
    assert not value['operations']
    with Session() as db: assert db.scalar(select(func.count()).select_from(Effect))==0

async def test_messages_keep_order_when_timestamps_tie(setup):
    from app.db import now
    client,_,_=setup
    sid=await ready_to_act(client)
    with Session.begin() as db:
        tied=now()
        for message in db.scalars(select(Message).where(Message.session_id==sid)):
            message.created_at=tied
    value=(await client.get(f'/api/v1/sessions/{sid}')).json()
    assert [m['role'] for m in value['messages']]==['assistant','user','assistant','user','assistant']
    assert '不能保证' in value['messages'][-1]['text']


async def test_callback_and_followup_mock_ports(setup):
    client, _, services = setup
    sid = await opened(client)
    verified = await say(client, sid, '我是演示商家1')
    evidence_id = next(message['message_id'] for message in verified['messages'] if message['role'] == 'user')
    confirmation_id = uuid4()
    application_id = fixture_id(1)
    timestamp = now()
    confirmation = CallbackConfirmation(
        confirmation_id=confirmation_id, application_id=application_id, case_revision=1,
        evidence_message_id=evidence_id, window_start=timestamp + timedelta(hours=1),
        window_end=timestamp + timedelta(hours=2), expires_at=timestamp + timedelta(minutes=10))
    with Session.begin() as db:
        db.add(Confirmation(id=str(confirmation_id), session_id=sid, data=confirmation.model_dump(mode='json')))

    def register(kind, payload, confirmation_ref=None, evidence_refs=()):
        operation_id = uuid4()
        command = ActionCommand(session_id=sid, application_id=application_id, case_revision=1,
            decision_id=uuid4(), operation_id=operation_id, kind=kind, idempotency_key=f'test:{operation_id}',
            confirmation_ref=confirmation_ref, evidence_refs=list(evidence_refs), payload=payload)
        with Session.begin() as db:
            db.add(Operation(id=str(operation_id), session_id=sid, key=command.idempotency_key,
                status='pending', data={'command': command.model_dump(mode='json'), 'manual': True}))
        return command

    payload = {'type': 'create_callback', 'confirmation_id': confirmation_id}
    unconfirmed = register('create_callback', payload, confirmation_id)
    failed = await services.business.execute(unconfirmed)
    assert failed.status == 'failed'
    assert failed.error.code == 'CALLBACK_CONFIRMATION_INVALID'
    assert await services.business.query_operation(unconfirmed) == failed

    callback = register('create_callback', payload, confirmation_id, [evidence_id])
    services.business.fault = 'lost_response'
    assert (await services.business.execute(callback)).status == 'unknown'
    actual = await services.business.query_operation(callback)
    assert actual.status == 'succeeded'
    assert actual.resource_id == f'mock-callback-{callback.operation_id}'
    services.business.fault = 'none'
    assert await services.business.execute(callback) == actual
    with Session() as db:
        assert db.get(Effect, callback.idempotency_key).data['details']['timezone'] == 'Asia/Shanghai'

    followup = register('create_followup', {'type': 'create_followup', 'reason_code': 'synthetic_followup',
                                           'related_operation_id': callback.operation_id})
    result = await services.business.execute(followup)
    assert result.resource_id == f'mock-followup-{followup.operation_id}'
    assert await services.business.query_operation(followup) == result

async def test_release_publish_and_session_version_binding(setup,monkeypatch):
    from pathlib import Path
    import importlib.util
    script = Path(__file__).resolve().parents[2] / 'scripts/publish_reviewed_knowledge.py'
    spec = importlib.util.spec_from_file_location('reviewed_knowledge', script)
    publisher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publisher)
    client, _, services = setup
    old_sid = await create(client)
    entry = services.knowledge.lookup('demo.authorization_missing','',['synthetic'],'demo-k1')[0]
    release = 'integration-synthetic-v1'
    entry = entry.model_copy(update={'entry_id':uuid4(),'knowledge_version':release})
    publisher.publish(release,[entry])
    publisher.publish(release,[entry])
    with pytest.raises(ValueError,match='immutable'):
        publisher.publish(release,[entry.model_copy(update={'approved_text':'different text'})])
    monkeypatch.setenv('ZHIYU_KNOWLEDGE_VERSION',release)
    sid = await create(client)
    with Session() as db:
        assert db.get(Conversation,old_sid).data['knowledge_version']=='demo-k1'
        assert db.get(Conversation,sid).data['knowledge_version']==release
