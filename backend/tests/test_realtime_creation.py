"""Dedicated DB only: transport metadata and isolation from legacy entrypoints."""
from uuid import uuid4
import hashlib
import json
from app.contracts import CreateSession
from app.db import Session, Conversation, RequestReceipt
from app.seed import fixture_id
from test_integration import setup, pytestmark


async def test_realtime_creation_preserves_business_state_and_rejects_legacy_input(setup):
    client, app, services = setup
    coordinator = app.state.coordinator
    # Exercise only the server-side creation boundary; media/business injection is separate.
    view = coordinator.create('demo', CreateSession(application_id=fixture_id(1), mode='voice',
        voice_transport='native_webrtc'), str(uuid4()), realtime_available=True)
    assert view.state == 'CASE_READY' and view.voice_transport == 'native_webrtc'
    with Session() as db:
        row = db.get(Conversation, str(view.session_id))
        assert row.data['model_version'] == 'qwen3.8-omni-flash-realtime'
        assert not row.data['verified'] and not row.data['notifications']
    response = await client.post(f'/api/v1/sessions/{view.session_id}/turns',
        json={'text':'是的','client_event_id':str(uuid4())})
    assert response.status_code == 409 and response.json()['code'] == 'WRONG_INPUT_MODE'
    response = await client.post(f'/api/v1/sessions/{view.session_id}/start',
        json={'expected_revision':view.state_revision}, headers={'Idempotency-Key':str(uuid4())})
    assert response.status_code == 409 and response.json()['code'] == 'WRONG_INPUT_MODE'


async def test_pre_v04_creation_receipt_can_still_be_replayed(setup):
    client, app, services = setup
    key = str(uuid4())
    payload = {'application_id':fixture_id(1), 'mode':'text'}
    first = await client.post('/api/v1/sessions', json=payload, headers={'Idempotency-Key':key})
    assert first.status_code == 201
    old_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with Session.begin() as db:
        db.get(RequestReceipt, f'demo:create:{key}').payload_hash = old_hash
    replay = await client.post('/api/v1/sessions', json=payload, headers={'Idempotency-Key':key})
    assert replay.status_code == 201 and replay.json()['session_id'] == first.json()['session_id']


async def test_end_before_native_opening_records_unknown_without_inventing_turn(setup):
    from sqlalchemy import select,func
    from app.db import Record,Effect,Operation
    from app.realtime_business.events import RealtimeEventRow
    client,app,services=setup
    view=app.state.coordinator.create('demo',CreateSession(application_id=fixture_id(1),mode='voice',voice_transport='native_webrtc'),str(uuid4()),realtime_available=True)
    response=await client.post(f'/api/v1/sessions/{view.session_id}/end',json={'reason':'user_ended'})
    assert response.status_code==200 and response.json()['ended'] and response.json()['state']=='CLOSING'
    assert (await client.post(f'/api/v1/sessions/{view.session_id}/end',json={'reason':'user_ended'})).status_code==200
    with Session() as db:
        row=db.get(Conversation,str(view.session_id))
        assert not row.data.get('turn_id') and not row.data.get('generation_id') and not row.data['verified']
        record=db.scalar(select(Record).where(Record.session_id==row.id))
        assert record.data['contact_status']=='unknown' and record.data['explanation_status']=='not_started'
        assert db.scalar(select(func.count()).select_from(RealtimeEventRow).where(RealtimeEventRow.session_id==row.id))==0
        assert db.scalar(select(func.count()).select_from(Effect))==0
        assert db.scalar(select(func.count()).select_from(Operation))==0
