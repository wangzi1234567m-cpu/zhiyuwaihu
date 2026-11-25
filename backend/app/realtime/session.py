"""Bounded realtime tasks. The only business dependency is RealtimeBusinessPort."""
import asyncio
import time
from collections import deque
from contextlib import suppress, aclosing
from uuid import uuid4

from .diagnostics import RealtimeProbe, now_ns

from ..realtime_contract.dto import (
    BusinessTurnInput, EventSubscription, GenerationInterrupt, PlaybackReport,
    ReplySegmentReady, TrustedSessionContext, TrustedTurnContext,
)


class RealtimeSession:
    def __init__(self, manager, owner, sid):
        self.manager, self.port = manager, manager.business
        self.context = TrustedSessionContext(owner=owner, session_id=sid, connection_binding=uuid4())
        self.created = self.last_heartbeat = time.monotonic()
        self.connected, self.browser_ready = asyncio.Event(), asyncio.Event()
        self.closed = self.input_ready = self.attached = False
        self.tasks = set()
        self.probe = RealtimeProbe(manager.config.realtime_diagnostics_enabled)
        self.last_input_at = self.last_send_at = None
        self.input_sequence = 0
        self.audio = asyncio.Queue(25)  # 25 x 20ms = 500ms input maximum.
        self.events = asyncio.Queue(128)
        self.notices = asyncio.Queue(128)
        self.segments = asyncio.Queue(2)
        self.epoch = self.input_epoch = 0
        self.generation = None
        self.invalid_generations = set()
        self.cursor = 0
        self.pending = []
        self.submit_inflight = False
        self.seen_inputs = set()
        self.seen_segments = {}
        self.segment_index = {}
        self.control_ids = deque(maxlen=256)
        self.playback_id = None
        self.playback_gate = asyncio.Event()
        self.speech_task = None
        self.submit_task = None
        self.cancel_audio = asyncio.Event()
        self.provider = self.speech = self.media = None
        self.snapshot = None
        self.turn_id = self.request_id = None
        self.metrics = deque(maxlen=512)
        self.wait_task = None
        self.playing_wait = False
        self.active_segment = None

    def spawn(self, coro):
        if len(self.tasks) >= 32:
            coro.close()
            raise RuntimeError('realtime task limit')
        async def guarded():
            try:
                return await coro
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # Exception messages can contain provider credentials. Publish stable codes only.
                await self.fail('NATIVE_INPUT_ASSOCIATION_UNCERTAIN' if str(error)=='NATIVE_INPUT_ASSOCIATION_UNCERTAIN' else getattr(getattr(error, 'error', None), 'code', 'REALTIME_TASK_FAILED'))
        task = asyncio.create_task(guarded())
        self.tasks.add(task)
        def finished(completed):
            self.tasks.discard(completed)
            # A task cancelled before guarded() starts still owns an unstarted coroutine.
            coro.close()
        task.add_done_callback(finished)
        return task

    def metric(self, name, duration_ms=None, **fields):
        self.metrics.append({'name': name, 'duration_ms': duration_ms,
                             'clock_domain': 'server.monotonic', 'scheme_version':'v04-realtime',
                             'session_id':str(self.context.session_id),
                             'generation_id':str(self.generation) if self.generation else None,
                             'turn_id':str(self.turn_id) if self.turn_id else None,
                             'request_id':str(self.request_id) if self.request_id else None, **fields})

    def notify(self, kind, **data):
        if not self.closed:
            self.notices.put_nowait({'type': kind, **data})

    def receive_audio(self, pcm):
        if not self.input_ready or self.closed:
            return
        if len(pcm) != 640:
            self.spawn(self.fail('WEBRTC_BAD_INPUT_FRAME'))
            return
        try:
            self.audio.put_nowait((pcm, now_ns() if self.probe.enabled else 0))
        except asyncio.QueueFull:
            self.input_ready = False
            self.spawn(self.fail('WEBRTC_INPUT_BACKPRESSURE'))

    async def start(self):
        async with asyncio.timeout(max(.001, 20 - (time.monotonic()-self.created))):
            await self.connected.wait()
            await self.browser_ready.wait()
        self.snapshot = await self.port.get_snapshot(self.context)
        if self.snapshot.session_id != self.context.session_id:
            raise ValueError('snapshot session mismatch')
        self.cursor = self.snapshot.last_event_seq
        self.generation = self.snapshot.generation_id
        # Never replay pre-connection speech, even when restoring current state.
        if self.generation:
            self.invalid_generations.add(self.generation)
        self.provider = self.manager.input_factory()
        self.speech = self.manager.speech_factory()
        await self.provider.open(self.snapshot)
        self.spawn(self.read_business())
        self.spawn(self.reduce_events())
        self.spawn(self.read_provider())
        self.spawn(self.send_audio())
        self.spawn(self.play_segments())
        start_opening = getattr(self.port, 'start_opening', None)
        if start_opening is not None and self.snapshot.state == 'CASE_READY':
            self.submit_inflight = True
            accepted = await start_opening(self.context)
            if accepted is not None:
                await self.events.put(('accepted', accepted, self.input_epoch))
            else:
                self.submit_inflight = False
        self.input_ready = True
        self.notify('ready', connection_binding=str(self.context.connection_binding))

    async def watchdog(self):
        while not self.closed:
            before = time.monotonic()
            await asyncio.sleep(1)
            lag = time.monotonic()-before-1
            if lag > .1:
                self.metric('event_loop_lag', lag*1000)
            age = time.monotonic() - self.created
            if age > 180 or time.monotonic()-self.last_heartbeat > 30:
                await self.fail('REALTIME_EXPIRED')
            elif not self.input_ready and age > 20:
                await self.fail('REALTIME_CONNECT_TIMEOUT')

    async def send_audio(self):
        while not self.closed:
            pcm, queued_at = await self.audio.get()
            started = now_ns() if self.probe.enabled else 0
            self.input_sequence += 1
            try:
                await self.provider.send_audio(pcm)
            finally:
                if self.probe.enabled:
                    from array import array
                    values=array('h',pcm)
                    self.probe.emit('input_frame', sequence=self.input_sequence,
                        rms=(sum(v*v for v in values)/len(values))**.5/32768,
                        queued_at_ns=queued_at, queue_wait_ns=started-queued_at,
                        received_gap_ns=queued_at-self.last_input_at if self.last_input_at else None,
                        send_gap_ns=started-self.last_send_at if self.last_send_at else None,
                        send_total_ns=now_ns()-started, queue_depth=self.audio.qsize(),
                        frame_duration_ms=20)
                    self.last_input_at, self.last_send_at = queued_at, started
                self.audio.task_done()

    async def read_provider(self):
        async for event in self.provider.events():
            if self.closed:
                return
            if event['type'] == 'speech_started':
                self.input_epoch = event['epoch']
                self.stop_output()
                self.spawn(self.interrupt_business())
                self.spawn(self.provider.cancel())
            elif event['type'] == 'speech_stopped':
                self.metric('provider_speech_stopped')
            elif event['type'] == 'final' and event['epoch'] == self.input_epoch:
                key = event['provider_input_id']
                if key in self.seen_inputs:
                    continue
                if len(self.seen_inputs) >= 64:
                    raise ValueError('input limit')
                self.seen_inputs.add(key)
                # One acceptance coordinator at a time; superseded input is rejected,
                # never re-bound to a newer user utterance.
                if self.submit_task and not self.submit_task.done():
                    raise ValueError('overlapping acceptances')
                self.submit_inflight = True
                self.submit_task = self.spawn(self.submit(event))

    async def submit(self, event):
        accepted = None
        try:
            snapshot = await self.port.get_snapshot(self.context)
            if event['epoch'] != self.input_epoch or self.closed:
                return
            context = TrustedTurnContext(**self.context.model_dump(), request_id=uuid4(),
                provider_input_id=event['provider_input_id'], expected_state_revision=snapshot.state_revision,
                expected_case_revision=snapshot.case_revision, knowledge_version=snapshot.knowledge_version)
            user_input = BusinessTurnInput(input_id=uuid4(), text=event['text'],
                                          understanding_candidate=event['candidate'])
            self.request_id = context.request_id
            started = time.monotonic()
            accepted = await self.port.submit_turn(context, user_input)
            self.metric('turn_accept', (time.monotonic()-started)*1000)
            await self.events.put(('accepted', accepted, event['epoch']))
        finally:
            if accepted is None:
                self.submit_inflight = False

    async def read_business(self):
        async for event in self.port.subscribe_events(self.context, EventSubscription(after_seq=self.cursor)):
            # Receive loop is independent from reducer, TTS, and playback.
            self.events.put_nowait(('business', event))

    async def reduce_events(self):
        while not self.closed:
            entry = await self.events.get()
            if entry[0] == 'accepted':
                self.submit_inflight = False
                accepted, input_epoch = entry[1:]
                if input_epoch != self.input_epoch:
                    self.invalid_generations.add(accepted.generation_id)
                    self.spawn(self.interrupt_business(accepted.generation_id))
                    self.pending.clear()
                    continue
                self.generation = accepted.generation_id
                self.turn_id = accepted.turn_id
                self.notify('turn.accepted', generation_id=str(self.generation))
                for event in self.pending:
                    await self.handle_event(event)
                self.pending.clear()
                if not self.seen_segments:
                    self.wait_task = self.spawn(self.wait_feedback(self.epoch))
            else:
                event = entry[1]
                if event.session_id != self.context.session_id:
                    raise ValueError('cross-session event')
                if event.seq <= self.cursor:
                    continue
                if event.seq != self.cursor + 1:
                    raise ValueError('event cursor gap')
                self.cursor = event.seq
                if event.generation_id != self.generation and self.submit_inflight:
                    if len(self.pending) >= 128:
                        raise ValueError('early event overflow')
                    self.pending.append(event)
                else:
                    await self.handle_event(event)

    async def handle_event(self, event):
        payload = event.payload
        kind = payload.type
        if kind in ('operation.updated', 'writeback.completed'):
            # State refresh only, including late operation results. Never speak unchecked text.
            self.notify('business.updated', seq=event.seq, event_type=kind)
            return
        if kind == 'turn.started' and event.generation_id != self.generation:
            snapshot = await self.port.get_snapshot(self.context)
            if snapshot.generation_id == event.generation_id:
                self.generation = event.generation_id
        if event.generation_id != self.generation or event.generation_id in self.invalid_generations:
            return
        if isinstance(payload, ReplySegmentReady):
            previous = self.seen_segments.get(payload.segment_id)
            if previous:
                if previous != payload.text_sha256:
                    raise ValueError('segment content conflict')
                return
            if len(self.seen_segments) >= 128:
                raise ValueError('segment count limit')
            index = self.segment_index.get(payload.message_id, 0)
            if payload.segment_index != index:
                raise ValueError('segment order conflict')
            self.segment_index[payload.message_id] = index + 1
            self.seen_segments[payload.segment_id] = payload.text_sha256
            if self.wait_task:
                self.wait_task.cancel()
            if self.playing_wait:
                self.playing_wait = False
                self.epoch += 1
                self.cancel_audio.set()
                if self.speech_task:
                    self.speech_task.cancel()
                self.media.output.clear(self.epoch)
                self.notify('playback.stop', epoch=self.epoch)
            self.metric('reviewed_segment_ready', generation_id=str(event.generation_id))
            self.segments.put_nowait((event.generation_id, payload, self.epoch))
        elif kind == 'turn.interrupted':
            self.stop_output()
        elif kind == 'turn.failed' or (kind == 'generation.finished' and payload.status == 'failed'):
            self.stop_output()
            self.notify('error', code='BUSINESS_TURN_FAILED')

    async def wait_feedback(self, epoch):
        await asyncio.sleep(.6)
        if epoch == self.epoch and not self.seen_segments:
            self.notify('waiting', text='正在核实，请稍等。', answer=False)
            self.segments.put_nowait((self.generation, None, epoch))

    async def play_segments(self):
        permitted_epoch = None
        while not self.closed:
            generation, segment, epoch = await self.segments.get()
            if epoch != self.epoch or generation in self.invalid_generations:
                continue
            if segment is None and self.seen_segments:
                continue
            self.playing_wait = segment is None
            self.cancel_audio = asyncio.Event()
            self.playback_id = uuid4()
            self.active_segment = (generation, segment, self.playback_id) if segment else None
            self.playback_gate.clear()
            self.notify('playback.permit', generation_id=str(generation),
                segment_id=str(segment.segment_id) if segment else None, playback_instance_id=str(self.playback_id),
                epoch=epoch, answer=segment is not None, requires_ready=(permitted_epoch != epoch))
            self.speech_task = self.spawn(self.render(generation, segment, epoch, permitted_epoch != epoch))
            try:
                await asyncio.shield(self.speech_task)
            except asyncio.CancelledError:
                if self.closed or not self.speech_task.cancelled():
                    raise
            finally:
                self.playing_wait = False
                self.active_segment = None
            if epoch == self.epoch:
                permitted_epoch = epoch

    async def render(self, generation, segment, epoch, needs_ready):
        first = True
        playback_id = self.playback_id
        started = time.monotonic()
        chunks=self.speech.stream(segment.text if segment else '正在核实，请稍等。',self.cancel_audio)
        async with aclosing(chunks):
            async for pcm in chunks:
                if epoch != self.epoch or self.closed or self.cancel_audio.is_set():
                    return
                if first:
                    self.metric('tts_first_pcm', (time.monotonic()-started)*1000)
                    if needs_ready:
                        async with asyncio.timeout(3):
                            await self.playback_gate.wait()
                    first = False
                await self.media.output.write(pcm, epoch)
        if epoch != self.epoch or self.cancel_audio.is_set():
            return
        if first:
            raise ValueError('TTS returned no PCM')
        await self.media.output.finish(epoch)
        if epoch != self.epoch or self.closed or self.cancel_audio.is_set():
            return
        if segment is None:
            return
        # Frames consumed by the media sender are not proof of physical/client playback.
        result = await self.port.report_playback(self.context, PlaybackReport(report_id=uuid4(),
            generation_id=generation, message_id=segment.message_id, segment_id=segment.segment_id,
            playback_instance_id=playback_id, text_sha256=segment.text_sha256,
            observation='completed', clock_domain='server.monotonic'))
        if result.status == 'evidence_verified':
            raise ValueError('server send observation cannot grant playback proof')
        self.notify('playback.drained', segment_id=str(segment.segment_id), evidence=False)

    def stop_output(self):
        if self.active_segment:
            self.spawn(self.report_interrupted(self.active_segment))
            self.active_segment = None
        old = self.generation
        if old:
            self.invalid_generations.add(old)
        if len(self.invalid_generations) > 128:
            raise ValueError('generation limit')
        self.epoch += 1
        self.cancel_audio.set()
        if self.speech_task and not self.speech_task.done():
            self.speech_task.cancel()
        if self.wait_task:
            self.wait_task.cancel()
        self.media.output.clear(self.epoch)
        while not self.segments.empty():
            self.segments.get_nowait()
        self.seen_segments.clear()
        self.segment_index.clear()
        self.playback_id = None
        self.notify('playback.stop', epoch=self.epoch)

    async def report_interrupted(self, active):
        generation, segment, playback_id = active
        await self.port.report_playback(self.context, PlaybackReport(report_id=uuid4(),
            generation_id=generation, message_id=segment.message_id, segment_id=segment.segment_id,
            playback_instance_id=playback_id, text_sha256=segment.text_sha256,
            observation='interrupted', clock_domain='server.monotonic'))

    async def interrupt_business(self, generation=None):
        generation = generation or self.generation
        if generation:
            await self.port.interrupt_generation(self.context,
                GenerationInterrupt(request_id=uuid4(), generation_id=generation))

    async def control(self, command):
        self.last_heartbeat = time.monotonic()
        if command.request_id in self.control_ids:
            return
        self.control_ids.append(command.request_id)
        if command.type == 'ready':
            self.browser_ready.set()
        elif command.type == 'playback_ready' and command.playback_instance_id == self.playback_id:
            self.playback_gate.set()
        elif command.type == 'interrupt':
            if command.playback_instance_id is not None and command.playback_instance_id != self.playback_id:
                return  # Late acoustic interruption for an old playback must not cancel new input.
            self.input_epoch = -1  # Suppress unfinished input until a new provider speech_started.
            self.stop_output()
            self.spawn(self.interrupt_business())
            if self.provider:
                self.spawn(self.provider.cancel())
        elif command.type == 'observation':
            # Browser clock domain stays explicit; never subtract from server monotonic.
            self.metrics.append({'name': command.name, 'duration_ms': command.duration_ms,
                                 'clock_domain': 'browser.performance', 'evidence': False})
        elif command.type == 'end':
            await self.close()

    async def fail(self, code):
        if self.closed:
            return
        with suppress(asyncio.QueueFull):
            self.notify('error', code=code)
        self.metric('failure', code=code)
        await self.close()

    async def close(self):
        if self.closed:
            return
        self.closed, self.input_ready = True, False
        active = self.active_segment
        self.active_segment = None
        self.cancel_audio.set()
        self.manager.sessions.pop(str(self.context.session_id), None)
        tasks = [t for t in self.tasks if t is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        with suppress(Exception):
            async with asyncio.timeout(2):
                if active:
                    await self.report_interrupted(active)
                await self.interrupt_business()
        for resource in (self.media, self.provider, self.speech):
            if resource:
                with suppress(Exception):
                    async with asyncio.timeout(3):
                        await resource.close()
        self.manager.completed.append(list(self.metrics))
