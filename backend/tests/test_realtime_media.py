"""Actual local aiortc media, no provider, business Mock, external ICE or database."""
import asyncio
import math
from types import SimpleNamespace

import pytest
aiortc = pytest.importorskip('aiortc')
from aiortc import RTCPeerConnection, RTCConfiguration
from app.realtime.media import StreamingAudio, WebRTCMedia


async def test_pcm_queue_is_bounded_and_old_epoch_frames_are_never_sent():
    output = StreamingAudio()
    await output.write(b'\x01\x02'*480*12, 0)
    assert output.frames.full()
    blocked = asyncio.create_task(output.write(b'\x03\x04'*480, 0))
    await asyncio.sleep(.01)
    assert not blocked.done()
    output.clear(1)
    await asyncio.wait_for(blocked, 1)
    frame = await output.recv()
    assert bytes(frame.planes[0]) == b'\0'*960
    await output.write(b'\x05\x06'*480, 1)
    frame = await output.recv()
    assert bytes(frame.planes[0]) == b'\x05\x06'*480
    output.stop()


async def test_actual_local_duplex_rtp_and_resampling():
    received, decoded = asyncio.Event(), asyncio.Event()
    failures = []
    class Session:
        connected = asyncio.Event()
        closed = False
        input_ready = True
        def receive_audio(self, pcm):
            assert len(pcm) == 640
            received.set()
        async def fail(self, code): failures.append(code)
    session = Session()
    media = WebRTCMedia(session, [])
    client = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    client.addTrack(aiortc.AudioStreamTrack())
    readers = []

    @client.on('track')
    def track(track):
        async def read():
            while True:
                frame = await track.recv()
                if any(bytes(frame.planes[0])):
                    decoded.set()
        readers.append(asyncio.create_task(read()))

    try:
        await client.setLocalDescription(await client.createOffer())
        async with asyncio.timeout(10):
            answer = await media.answer(client.localDescription.sdp)
            await client.setRemoteDescription(aiortc.RTCSessionDescription(sdp=answer, type='answer'))
            await session.connected.wait()
            await received.wait()
            # A deterministic tone verifies non-silent outbound decoded audio.
            import struct
            tone = b''.join(struct.pack('<h', int(8000*math.sin(i*2*math.pi*440/24000))) for i in range(480))
            for _ in range(8): await media.output.write(tone, 0)
            await decoded.wait()
        assert not failures
    finally:
        session.closed = True
        for reader in readers: reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        await media.close(); await client.close()
    assert media.pc.connectionState == 'closed' and not media.tasks


async def test_non_audio_sdp_is_rejected_before_peer_setup():
    session = SimpleNamespace(closed=False, connected=asyncio.Event())
    media = WebRTCMedia(session, [])
    try:
        with pytest.raises(ValueError): await media.answer('v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\n')
    finally: await media.close()
