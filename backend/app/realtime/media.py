"""Optional aiortc runtime; imported only after feature/dependency checks."""
import asyncio
import time
from fractions import Fraction
from .diagnostics import RealtimeProbe, now_ns
from aiortc import AudioStreamTrack, RTCPeerConnection, RTCConfiguration, RTCIceServer, RTCSessionDescription
from aiortc.mediastreams import MediaStreamError
from av import AudioFrame, AudioResampler


class StreamingAudio(AudioStreamTrack):
    """20ms frames, at most 240ms queued; not an end-to-end latency target."""
    def __init__(self, diagnostics=False):
        super().__init__()
        self.probe = RealtimeProbe(diagnostics)
        self.frames = asyncio.Queue(maxsize=12)
        self.epoch = 0
        self.tail = b''
        self.pts = 0
        self.next_at = None

    def clear(self, epoch):
        self.epoch, self.tail = epoch, b''
        while not self.frames.empty():
            self.frames.get_nowait()
            self.frames.task_done()

    async def write(self, pcm, epoch):
        if len(pcm) % 2:
            raise ValueError('PCM must contain complete int16 samples')
        if epoch != self.epoch or self.readyState != 'live':
            return
        data, self.tail = self.tail + pcm, b''
        complete = len(data) - len(data) % 960
        for offset in range(0, complete, 960):
            if epoch != self.epoch:
                return
            queued_at = now_ns() if self.probe.enabled else 0
            await self.frames.put((epoch, data[offset:offset+960], queued_at))
            if self.probe.enabled:
                self.probe.emit('output_enqueue',queued_at_ns=queued_at,producer_wait_ns=now_ns()-queued_at,queue_depth=self.frames.qsize())
            if epoch != self.epoch:
                return
        if epoch == self.epoch:
            self.tail = data[complete:]

    async def finish(self, epoch):
        if epoch == self.epoch and self.tail:
            tail, self.tail = self.tail, b''
            await self.frames.put((epoch, tail.ljust(960, b'\0'), now_ns() if self.probe.enabled else 0))
        await self.frames.join()

    async def recv(self):
        if self.readyState != 'live':
            raise MediaStreamError
        now = time.perf_counter()
        if self.next_at is None or self.next_at < now - .1:
            self.next_at = now
        await asyncio.sleep(max(0, self.next_at - now))
        self.next_at += .02
        pcm = b'\0' * 960
        while not self.frames.empty():
            epoch, data, queued_at = self.frames.get_nowait()
            self.frames.task_done()
            if epoch == self.epoch:
                if self.probe.enabled:
                    self.probe.emit('output_frame', epoch=epoch, queued_at_ns=queued_at,queue_wait_including_producer_ns=now_ns()-queued_at,
                                    queue_depth=self.frames.qsize(), schedule_lag_ns=max(0,int((time.perf_counter()-self.next_at+.02)*1e9)))
                pcm = data
                break
        frame = AudioFrame(format='s16', layout='mono', samples=480)
        frame.planes[0].update(pcm)
        frame.sample_rate, frame.pts, frame.time_base = 24000, self.pts, Fraction(1, 24000)
        self.pts += 480
        return frame


class WebRTCMedia:
    def __init__(self, session, ice_servers):
        self.session = session
        self.pc = RTCPeerConnection(RTCConfiguration(iceServers=[RTCIceServer(**s.model_dump(exclude_none=True)) for s in ice_servers]))
        self.output = StreamingAudio(session.probe.enabled if hasattr(session, 'probe') else False)
        self.pc.addTrack(self.output)
        self.tasks = set()
        self.input_count = 0

        @self.pc.on('track')
        def received(track):
            self.input_count += 1
            self.spawn(session.fail('WEBRTC_MEDIA_INVALID') if track.kind != 'audio' or self.input_count > 1 else self.read_audio(track))

        @self.pc.on('datachannel')
        def reject(channel):
            channel.close()

        @self.pc.on('connectionstatechange')
        async def changed():
            if self.pc.connectionState == 'connected':
                session.connected.set()
            elif self.pc.connectionState in ('failed', 'closed') and not session.closed:
                self.spawn(session.fail('WEBRTC_MEDIA_DISCONNECTED'))

    def spawn(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def answer(self, sdp):
        if not sdp.startswith('v=0') or sdp.count('m=audio ') != 1 or 'm=video ' in sdp or 'm=application ' in sdp:
            raise ValueError('Only a single audio track is supported')
        await self.pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type='offer'))
        await self.pc.setLocalDescription(await self.pc.createAnswer())
        return self.pc.localDescription.sdp

    async def read_audio(self, track):
        resampler = AudioResampler(format='s16', layout='mono', rate=16000, frame_size=320)
        try:
            while not self.session.closed:
                frame = await track.recv()
                if self.session.input_ready:
                    for audio in resampler.resample(frame):
                        self.session.receive_audio(bytes(audio.planes[0])[:audio.samples*2])
        except asyncio.CancelledError:
            raise
        except Exception:
            if not self.session.closed:
                self.spawn(self.session.fail('WEBRTC_INPUT_ENDED'))

    async def close(self):
        self.output.clear(self.output.epoch + 1)
        self.output.stop()
        tasks = [t for t in self.tasks if t is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.pc.close()
