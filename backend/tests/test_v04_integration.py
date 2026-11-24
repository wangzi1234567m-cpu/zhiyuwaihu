"""Exercise A's actual orchestrator with B's shared fixtures and durable facade."""
import asyncio
from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from app.config import settings
from app.realtime.manager import RealtimeManager
from app.realtime.session import RealtimeSession
from app.realtime.wire import RealtimeControl
from app.realtime_business.fixtures import Scenario, ScenarioBusiness
from app.realtime_contract.dto import UnderstandingCandidate
from test_realtime_runtime import InputBoundary, SpeechBoundary, MediaBoundary


async def until(predicate, timeout=7):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(.005)


async def auto_ready(session):
    """A browser playback gate; observations deliberately confer no proof."""
    while not session.closed:
        event = await session.notices.get()
        if event['type'] == 'playback.permit':
            await session.control(RealtimeControl(type='playback_ready', request_id=uuid4(),
                playback_instance_id=event['playback_instance_id']))


async def attach(port, context):
    provider, speech = InputBoundary(), SpeechBoundary()
    manager = RealtimeManager(replace(settings, realtime_enabled=True, voice_writes_enabled=False),
        port, lambda: provider, lambda: speech, lambda *args: None)
    session = RealtimeSession(manager, context.owner, context.session_id)
    session.context = context
    session.media = MediaBoundary()
    manager.sessions[str(context.session_id)] = session
    session.connected.set()
    session.browser_ready.set()
    session.spawn(auto_ready(session))
    await session.start()
    return session, provider, speech


async def final(provider, text='补齐材料一定必过吗', epoch=1, intent='ask_guarantee'):
    await provider.queue.put({'type': 'speech_started', 'epoch': epoch})
    await provider.queue.put({'type': 'final', 'epoch': epoch, 'provider_input_id': f'item-{epoch}',
        'text': text, 'candidate': UnderstandingCandidate(intents=[intent])})


@pytest.mark.parametrize('scenario', ['normal', 'slow', 'duplicate', 'timeout', 'late', 'interrupt', 'disconnect'])
async def test_actual_realtime_orchestrator_with_shared_business_scenarios(scenario):
    port = ScenarioBusiness(Scenario(scenario, delay_seconds=.12, segment_gap=.04))
    session, provider, speech = await attach(port, port.context)
    try:
        await final(provider)
        await port.entered.wait()
        for _ in range(5):
            session.receive_audio(bytes(640))
            await asyncio.sleep(.005)
        assert provider.sent == 5
        if scenario in ('late', 'interrupt'):
            session.stop_output()
            await session.interrupt_business()
            await asyncio.gather(*list(port.tasks))
            await asyncio.sleep(.03)
            assert not speech.calls and not session.media.output.writes
        elif scenario == 'timeout':
            await until(lambda: bool(port.operations))
            await asyncio.sleep(.03)
            assert [o.status for o in port.operations.values()] == ['unknown']
            assert not speech.calls and port.effect_count == 0
        elif scenario == 'disconnect':
            # This fixture exposes completion then reconnect; it does not model
            # a process crash or a media disconnect DURING external dispatch.
            await until(lambda: port.effect_count == 1)
            await session.close()
            snap = await port.get_snapshot(port.context)
            assert port.effect_count == 1 and snap.operations[0].status == 'succeeded'
            recovered, _, recovered_speech = await attach(port, port.context)
            try:
                await asyncio.sleep(.05)
                assert recovered.cursor == snap.last_event_seq and not recovered_speech.calls
            finally:
                await recovered.close()
        else:
            await until(lambda: len(speech.calls) == 2)
            assert len(session.seen_segments) == 2
            assert len(port.providers) == 1
            assert any(m['name'] == 'tts_first_pcm' for m in session.metrics)
        assert not any(m['name'] == 'failure' for m in session.metrics)
    finally:
        await session.close()
        await port.close()


from test_integration import setup, pytestmark as db_marks


@pytest.mark.integration
@db_marks[1]
async def test_negotiation_registers_the_same_binding_in_media_and_business(setup):
    from app.contracts import CreateSession
    from app.realtime.wire import RealtimeOffer
    from app.realtime_contract.ports import ContractFailure
    from app.seed import fixture_id
    _, app, _ = setup
    manager = app.state.realtime
    manager.config = replace(settings, realtime_enabled=True, voice_writes_enabled=False)
    manager.custom_factories = True
    class Negotiation(MediaBoundary):
        def __init__(self, session, ice):
            super().__init__()
        async def answer(self, sdp):
            return 'v=0\r\n'
    manager.media_factory = Negotiation
    view = app.state.coordinator.create('demo', CreateSession(application_id=fixture_id(1),
        mode='voice', voice_transport='native_webrtc'), str(uuid4()), realtime_available=True)
    answer = await manager.offer('demo', view.session_id,
        RealtimeOffer(sdp='v=0', expected_state_revision=view.state_revision))
    session = manager.bound('demo', view.session_id, answer.connection_binding)
    try:
        snap = await manager.business.get_snapshot(session.context)
        assert snap.session_id == view.session_id and snap.state == 'CASE_READY'
        with pytest.raises(ContractFailure, match='BINDING_EXPIRED'):
            await manager.business.get_snapshot(session.context.model_copy(update={'connection_binding': uuid4()}))
    finally:
        await session.close()
    rebound = await manager.business.open_connection('demo', view.session_id)
    with pytest.raises(ContractFailure, match='BINDING_EXPIRED'):
        await manager.business.get_snapshot(session.context)
    assert (await manager.business.get_snapshot(rebound)).state == 'CASE_READY'


@pytest.mark.integration
@db_marks[1]
async def test_default_app_realtime_opening_three_turns_and_safe_write_refusal(setup):
    from sqlalchemy import select, func
    from app.contracts import CreateSession
    from app.db import Session, Conversation, Effect, Message
    from app.seed import fixture_id
    client, app, services = setup
    port = app.state.realtime.business
    view = app.state.coordinator.create('demo', CreateSession(application_id=fixture_id(1),
        mode='voice', voice_transport='native_webrtc'), str(uuid4()), realtime_available=True)
    context = await port.open_connection('demo', view.session_id)
    session, provider, speech = await attach(port, context)
    def state_is(state):
        with Session() as db:
            row = db.get(Conversation, str(view.session_id))
            return row.state == state and not row.data['busy']
    try:
        await until(lambda: state_is('VERIFYING'))
        await until(lambda: len(speech.calls) == 1)
        assert '身份' in speech.calls[0] or '联系人' in speech.calls[0]
        for epoch, text, intent, state in (
            (1, '我是演示商家1', 'confirm_identity', 'EXPLAINING'),
            (2, '补齐材料一定必过吗', 'ask_guarantee', 'EXPLAINING'),
            (3, '明白了', 'acknowledge', 'MANUAL_REVIEW'),
        ):
            await final(provider, text, epoch, intent)
            await until(lambda: len(speech.calls) >= epoch + 1)
            await until(lambda: state_is(state))
        assert any('不能保证' in text for text in speech.calls)
        with Session() as db:
            row = db.get(Conversation, str(view.session_id))
            assert row.data['verified'] and not row.data.get('voice_explanation_verified')
            assert db.scalar(select(func.count()).select_from(Effect)) == 0
            assert db.scalar(select(func.count()).select_from(Message).where(
                Message.session_id == str(view.session_id), Message.data['view']['role'].as_string() == 'user')) == 3
        assert not any(m['name'] == 'failure' for m in session.metrics)
    finally:
        await session.close()
