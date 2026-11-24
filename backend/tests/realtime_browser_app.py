"""Loopback-only browser harness. Test boundaries, NOT the team's business Mock.

No database, provider, budget modification, production route or business operation.
"""
import asyncio
import math
import struct
from dataclasses import replace
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse
from pathlib import Path
from app.config import settings
from app.realtime.manager import RealtimeManager
from app.realtime.routes import mount_realtime
from app.realtime.wire import RealtimeOffer
from app.contracts import AcceptedTurn
from test_realtime_runtime import BoundaryPort, InputBoundary, final

port, input_boundary = BoundaryPort(), InputBoundary()
request_counter = 0


class LoopbackSpeech:
    async def stream(self, text, cancel):
        for block in range(100):
            if cancel.is_set(): return
            yield b''.join(struct.pack('<h', int(9000*math.sin((i+block*480)*2*math.pi*440/24000))) for i in range(480))
    async def close(self): pass


original_snapshot = port.get_snapshot
async def snapshot(context):
    return (await original_snapshot(context)).model_copy(update={'last_event_seq': port.seq})
port.get_snapshot = snapshot
config = replace(settings, realtime_enabled=True, voice_writes_enabled=False, realtime_stun_urls='[]', realtime_turn_urls='')
manager = RealtimeManager(config, port, lambda: input_boundary, LoopbackSpeech)
app = FastAPI()


def owner(request: Request): return 'demo'
async def authorize(user, sid):
    if sid != port.sid: raise ValueError('test session mismatch')
mount_realtime(app, manager, owner, authorize, 'http://127.0.0.1:18764')


@app.get('/')
def index(): return HTMLResponse('<!doctype html><html><body>Local WebRTC adapter test</body></html>')


@app.get('/realtime-meter.js')
def worklet():
    return FileResponse(Path(__file__).resolve().parents[2]/'frontend/public/realtime-meter.js', media_type='text/javascript')


@app.get('/test/state')
def state():
    return {'sid': str(port.sid), 'sessions': len(manager.sessions), 'input_frames': input_boundary.sent,
            'reports': len(port.reports), 'evidence_refs': [str(r.evidence_ref) if r.evidence_ref else None for r in port.reports]}


@app.post('/test/reply')
async def reply():
    global request_counter
    request_counter += 1
    # Explicitly drive a unit-test boundary; it does not understand or execute business.
    port.accepted = AcceptedTurn(turn_id=__import__('uuid').uuid4(), generation_id=__import__('uuid').uuid4())
    port.entered.clear()
    await final(input_boundary, request_counter)
    await asyncio.wait_for(port.entered.wait(), 2)
    await port.queue.put(port.event())
    return {'ok': True}
