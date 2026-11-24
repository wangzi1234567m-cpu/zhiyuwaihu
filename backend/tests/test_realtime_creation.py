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
