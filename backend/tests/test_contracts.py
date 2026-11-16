import asyncio
from uuid import uuid4
import pytest
from pydantic import ValidationError
from app.contracts import TextTurn, TurnInput, ActionCommand, CallbackPayload, Event, ModelRequest
from app.adapters import DemoAgent, FakeModel, DisabledVoice, DomainError
from app.main import app

def test_client_cannot_set_business_confirmation():
    with pytest.raises(ValidationError):
        TextTurn(text='确认',client_event_id=uuid4(),user_confirmed=True)

def test_partial_asr_rejected():
    with pytest.raises(ValidationError):
        TurnInput(message_id=uuid4(),text='半句话',source='asr',final=False)

def test_callback_needs_same_server_confirmation():
    with pytest.raises(ValidationError):
        ActionCommand(session_id=uuid4(),application_id=uuid4(),case_revision=1,decision_id=uuid4(),
            operation_id=uuid4(),kind='create_callback',idempotency_key='test',evidence_refs=[],
            confirmation_ref=uuid4(),payload=CallbackPayload(type='create_callback',confirmation_id=uuid4()))

async def test_fake_model_observes_cancellation():
    cancel=asyncio.Event()
    chunks=[]
    async for chunk in FakeModel().stream(ModelRequest(messages=[],fixture_response='这个回复需要被中途打断，不能继续生成'),cancel):
        chunks.append(chunk)
        cancel.set()
    assert len(chunks)==1

async def test_voice_is_explicitly_unavailable():
    with pytest.raises(DomainError) as error:
        await DisabledVoice().credentials(str(uuid4()))
    assert error.value.code=='VOICE_NOT_CONFIGURED'

def test_openapi_has_new_routes_and_no_order_endpoint():
    paths=app.openapi()['paths']
    assert '/api/v1/sessions/{sid}/turns' in paths
    assert not any('order' in path for path in paths)
