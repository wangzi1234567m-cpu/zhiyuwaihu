"""Test-only boundary fixtures. NOT the teammate's reusable business Mock."""
import asyncio
import hashlib
import time
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.config import settings
from app.contracts import AcceptedTurn, Interrupted
from app.realtime.manager import RealtimeManager
from app.realtime.session import RealtimeSession
from app.realtime.wire import RealtimeControl
from app.realtime_contract.dto import (BusinessSnapshot, RealtimeBusinessEvent, ReplySegmentReady,
    PlaybackReportResult, UnderstandingCandidate)


class BoundaryPort:
    """Manually driven contract boundary, no business logic or reusable scenarios."""
    def __init__(self):
        self.sid = uuid4()
        self.accepted = AcceptedTurn(turn_id=uuid4(), generation_id=uuid4())
        self.queue = asyncio.Queue()
        self.accept_gate = asyncio.Event()
        self.accept_gate.set()
        self.entered = asyncio.Event()
        self.reports, self.interrupts, self.inputs = [], [], []
        self.seq = 0

    async def get_snapshot(self, context):
        return BusinessSnapshot(session_id=self.sid, state='QA', state_revision=1,
            case_revision=1, knowledge_version='k1', last_event_seq=0, oldest_available_seq=1)

    async def submit_turn(self, context, user_input):
        self.inputs.append((context, user_input))
        self.entered.set()
        await self.accept_gate.wait()
        return self.accepted

    async def subscribe_events(self, context, subscription):
        while True:
            yield await self.queue.get()

    async def interrupt_generation(self, context, request):
        self.interrupts.append(request)
        return Interrupted(applied=True)

    async def report_playback(self, context, report):
        self.reports.append(report)
        return PlaybackReportResult(report_id=report.report_id, status='observation_recorded', reason_code='OBSERVATION_ONLY')

    def event(self, payload=None, generation=None):
        self.seq += 1
        if payload is None:
            text = '补齐材料后仍需审核，不能保证通过。'
            payload = ReplySegmentReady(message_id=uuid4(), segment_id=uuid4(), segment_index=0,
                text=text, text_sha256=hashlib.sha256(text.encode()).hexdigest())
        return RealtimeBusinessEvent(event_id=uuid4(), session_id=self.sid,
            turn_id=self.accepted.turn_id, generation_id=generation or self.accepted.generation_id,
            operation_id=getattr(getattr(payload, 'result', None), 'operation_id', None),
            seq=self.seq, state_revision=1, occurred_at=datetime.now(timezone.utc), knowledge_version='k1', payload=payload)


class InputBoundary:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.sent = 0
        self.closed = False

    async def open(self, snapshot): pass
    async def send_audio(self, pcm): self.sent += 1
    async def cancel(self): pass
    async def close(self): self.closed = True
    async def events(self):
        while True: yield await self.queue.get()


class SpeechBoundary:
    def __init__(self):
        self.calls = []
        self.finish = asyncio.Event()
        self.finish.set()
        self.closed = False

    async def stream(self, text, cancel):
        self.calls.append(text)
        yield b'\x01\x02' * 480
        await self.finish.wait()
        if not cancel.is_set(): yield b'\x01\x02' * 480

    async def close(self): self.closed = True


class OutputBoundary:
    def __init__(self): self.writes = []; self.epoch = 0
    def clear(self, epoch): self.epoch = epoch
    async def write(self, pcm, epoch):
        if epoch == self.epoch: self.writes.append((epoch, pcm))
    async def finish(self, epoch): pass


class MediaBoundary:
    def __init__(self): self.output = OutputBoundary(); self.closed = False
    async def close(self): self.closed = True


async def running():
    port, provider, speech = BoundaryPort(), InputBoundary(), SpeechBoundary()
    manager = RealtimeManager(replace(settings, realtime_enabled=True, voice_writes_enabled=False),
        port, lambda: provider, lambda: speech, lambda *a: None)
    session = RealtimeSession(manager, 'demo', port.sid)
    session.media = MediaBoundary()
    manager.sessions[str(port.sid)] = session
    session.connected.set(); session.browser_ready.set()
    await session.start()
    return session, port, provider, speech


async def final(provider, epoch=1):
    await provider.queue.put({'type': 'speech_started', 'epoch': epoch})
    await provider.queue.put({'type': 'final', 'epoch': epoch, 'provider_input_id': f'input-{epoch}',
        'text': '补齐材料能保证通过吗', 'candidate': UnderstandingCandidate(intents=['ask_guarantee'])})


async def notice(session, kind):
    async with asyncio.timeout(2):
        while True:
            event = await session.notices.get()
            if event['type'] == kind: return event


async def eventually(predicate):
    async with asyncio.timeout(2):
        while not predicate(): await asyncio.sleep(.005)


async def ready_playback(session):
    event = await notice(session, 'playback.permit')
    await session.control(RealtimeControl(type='playback_ready', request_id=uuid4(),
        playback_instance_id=event['playback_instance_id']))
    return event


async def test_slow_acceptance_does_not_block_audio_events_or_interrupt():
    session, port, provider, speech = await running()
    try:
        port.accept_gate.clear()
        blocked_started = time.monotonic()
        await final(provider)
        await port.entered.wait()
        for _ in range(20):
            session.receive_audio(b'\0'*640)
            await asyncio.sleep(.005)
        assert provider.sent == 20
        await session.control(RealtimeControl(type='interrupt', request_id=uuid4()))
        assert not session.closed
        await provider.queue.put({'type': 'speech_stopped', 'epoch': 1})
        await eventually(lambda: any(m['name'] == 'provider_speech_stopped' for m in session.metrics))
        await asyncio.sleep(max(0, 3-(time.monotonic()-blocked_started)))
        # Accept may complete AFTER interruption; its generation is still invalidated.
        port.accept_gate.set()
        await eventually(lambda: bool(port.interrupts))
        assert not speech.calls
    finally: await session.close()


async def test_early_event_before_acceptance_is_not_lost_and_first_pcm_is_streamed():
    session, port, provider, speech = await running()
    try:
        port.accept_gate.clear(); speech.finish.clear()
        await final(provider); await port.entered.wait()
        event = port.event(); await port.queue.put(event)
        await eventually(lambda: len(session.pending) == 1)
        port.accept_gate.set()
        await ready_playback(session)
        await eventually(lambda: bool(session.media.output.writes))
        assert not speech.finish.is_set() and not port.reports
        speech.finish.set()
        await eventually(lambda: len(port.reports) == 1)
        assert port.reports[0].evidence_ref is None
        await port.queue.put(event)  # duplicate seq, no repeated synthesis
        await asyncio.sleep(.02)
        assert len(speech.calls) == 1
    finally: await session.close()


async def test_interrupt_during_synthesis_keeps_playback_worker_available_for_next_turn():
    session, port, provider, speech = await running()
    try:
        speech.finish.clear()
        await final(provider); await notice(session, 'turn.accepted')
        old = port.accepted.generation_id
        await port.queue.put(port.event()); await ready_playback(session)
        await eventually(lambda: bool(session.media.output.writes))
        await session.control(RealtimeControl(type='interrupt', request_id=uuid4()))
        count = len(session.media.output.writes)
        speech.finish.set()
        await port.queue.put(port.event(generation=old))
        await asyncio.sleep(.02)
        assert len(session.media.output.writes) == count
        port.accepted = AcceptedTurn(turn_id=uuid4(), generation_id=uuid4())
        await final(provider, 2); await notice(session, 'turn.accepted')
        await port.queue.put(port.event()); await ready_playback(session)
        await eventually(lambda: len(session.media.output.writes) > count)
        assert not session.closed
    finally: await session.close()


async def test_two_segments_can_play_before_second_is_published():
    session, port, provider, speech = await running()
    try:
        await final(provider); await notice(session, 'turn.accepted')
        first = port.event(); await port.queue.put(first); await ready_playback(session)
        await eventually(lambda: len(port.reports) == 1)
        second = first.payload.model_copy(update={'segment_id': uuid4(), 'segment_index': 1})
        await port.queue.put(port.event(payload=second))
        await eventually(lambda: len(port.reports) == 2)
        assert len(speech.calls) == 2
    finally: await session.close()


async def test_three_second_business_delay_and_wait_feedback_yield_to_two_answer_segments():
    session, port, provider, speech = await running()
    try:
        speech.finish.clear()
        started = time.monotonic()
        await final(provider); await notice(session, 'turn.accepted')
        waiting = await ready_playback(session)
        assert waiting['answer'] is False
        await eventually(lambda: bool(session.media.output.writes))
        # Input/event paths remain live while waiting speech is still being synthesized.
        session.receive_audio(b'\0'*640)
        await eventually(lambda: provider.sent == 1)
        await asyncio.sleep(max(0, 3-(time.monotonic()-started)))
        first = port.event()
        second = port.event(payload=first.payload.model_copy(update={'segment_id':uuid4(), 'segment_index':1}))
        await port.queue.put(first); await port.queue.put(second)
        answer = await ready_playback(session)
        assert answer['answer'] and answer['epoch'] > waiting['epoch']
        speech.finish.set()
        await eventually(lambda: len([r for r in port.reports if r.observation=='completed']) == 2)
        assert not session.closed and speech.calls[0] == '正在核实，请稍等。'
    finally: await session.close()


async def test_twenty_session_exits_leave_no_registered_tasks_or_resources():
    for _ in range(20):
        session, port, provider, speech = await running()
        await session.close(); await session.close()
        await asyncio.sleep(0)
        assert not session.manager.sessions and not session.tasks
        assert session.media.closed and provider.closed and speech.closed


async def test_input_backpressure_closes_instead_of_silently_losing_audio():
    session, *_ = await running()
    try:
        for _ in range(26): session.receive_audio(b'\0'*640)
        await eventually(lambda: session.closed)
        assert not session.input_ready
    finally: await session.close()


async def test_unchecked_provider_audio_cannot_enter_output():
    session, _, provider, speech = await running()
    try:
        await provider.queue.put({'type': 'response.audio.delta', 'audio': 'untrusted'})
        await asyncio.sleep(.02)
        assert not speech.calls and not session.media.output.writes
    finally: await session.close()


def test_absolute_latency_gate_rejects_2000ms_and_keeps_missing_samples():
    from report_realtime_latency import compare
    meta = dict(case_set_sha256='synthetic', network_profile='local', client='same',
                measurement_method='controlled_input_end_to_remote_pcm')
    def report(ms): return {'metadata': meta, 'samples': [{'status': 'success', 'effective_answer_ms': ms}]*30}
    assert not compare(report(2500), report(2000))['latency_gate_passed']
    assert compare(report(2500), report(1999))['latency_gate_passed']
    failed = report(1999); failed['samples'].append({'status': 'timeout'})
    result = compare(report(2500), failed)
    assert result['candidate']['failed_or_missing'] == 1
    assert not result['latency_gate_passed']


async def test_business_timeout_and_unknown_operation_never_generate_success_speech():
    from app.realtime_contract.dto import TurnFailed, ContractError
    from app.contracts import OperationUpdated, OperationResult
    session, port, provider, speech = await running()
    try:
        await final(provider); await notice(session, 'turn.accepted')
        result = OperationResult(operation_id=uuid4(), status='unknown', updated_at=datetime.now(timezone.utc))
        event = port.event(payload=OperationUpdated(type='operation.updated', result=result))
        await port.queue.put(event)
        await notice(session, 'business.updated')
        await port.queue.put(port.event(payload=TurnFailed(error=ContractError(
            code='BUSINESS_TIMEOUT', message='测试等待超时', retryable=False))))
        await notice(session, 'error')
        assert not speech.calls and not session.media.output.writes
    finally: await session.close()


async def test_late_playback_scoped_interrupt_preserves_new_input_epoch():
    session,port,provider,speech=await running()
    try:
        old=uuid4();session.playback_id=uuid4();session.input_epoch=7
        await session.control(RealtimeControl(type='interrupt',request_id=uuid4(),playback_instance_id=old))
        assert session.input_epoch==7 and session.playback_id is not None and not port.interrupts
    finally:await session.close()
