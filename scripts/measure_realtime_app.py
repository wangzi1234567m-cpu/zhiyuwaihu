"""Loopback-only REAL-provider measurement host; production modules stay unchanged.

Requires an explicitly authorized run, a fresh dedicated _test PostgreSQL database,
and local secrets loaded from the existing workspace .env without publishing them.
"""
import asyncio
import json
import os
import re
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = Path(os.getenv('LATENCY_WORKSPACE', str(ROOT.parents[1] if ROOT.parent.name == 'tmp' else ROOT)))
sys.path.insert(0, str(ROOT / 'backend'))
from dotenv import load_dotenv
load_dotenv(WORKSPACE / '.env', override=False)

RUN = os.environ.get('LATENCY_RUN_ID', '')
TOKEN = os.environ.get('LATENCY_CONTROL_TOKEN', '')
if not re.fullmatch(r'v04-latency-[a-z0-9-]{1,48}', RUN) or len(TOKEN) < 24:
    raise RuntimeError('Explicit latency run ID and private control token required')
if not os.environ.get('DATABASE_URL', '').endswith('_test'):
    raise RuntimeError('A dedicated _test database is required')

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse
from sqlalchemy import select, func
from app.config import settings
from app.main import create_app
from app.cloud_budget import reserve_call
from app.db import Session, Effect, RequestReceipt, Conversation, Message
from app.realtime.provider import NativeInput, ProviderFailure
from app.realtime.speech import StreamingSpeech
from app.realtime.session import RealtimeSession
from app.realtime import manager as manager_module

TRACES = {}
DIAGNOSTICS = {}
LIMITS = {'native_realtime_session': 15, 'native_realtime_turn': 45, 'tts': 120}
BUDGET_ID = RUN
if os.getenv('LATENCY_OPTIMIZATION_AUTHORIZED') == '20261125-20-62-100':
    # User-approved additional budget, shared by ALL variants and final evaluation.
    LIMITS = {'native_realtime_session':20,'native_realtime_turn':62,'tts':100}
    BUDGET_ID = 'v04-latency-opt-20261125'


def ms():
    return time.perf_counter_ns() / 1_000_000

def trace(sid, kind, **fields):
    items = TRACES.setdefault(str(sid), [])
    if len(items) < 20000:
        items.append({'at_ms': ms(), 'kind': kind, **fields})

def reserve(kind, ignored_limit):
    # Separate bounded attempt ledger; never alter/reset the ordinary daily ledger.
    reserve_call(BUDGET_ID + ':' + kind, LIMITS[kind])

def active_sid():
    values = list(app.state.realtime.sessions.values())
    if len(values) != 1:
        raise RuntimeError('Exactly one measured realtime session required')
    return str(values[0].context.session_id)


class MeasuredInput(NativeInput):
    def __init__(self):
        super().__init__(settings, reserve)
        self.sid = None

    async def open(self, snapshot):
        self.sid = str(snapshot.session_id)
        trace(self.sid, 'provider_open_start')
        await super().open(snapshot)
        trace(self.sid, 'provider_open_ready')

    async def receive(self):
        # Same decode/error semantics as NativeInput.receive; retain safe metadata.
        value = json.loads(await self.ws.recv())
        kind = value.get('type')
        response = value.get('response') or {}
        fields = {k: value[k] for k in ('item_id', 'response_id', 'audio_start_ms', 'audio_end_ms') if k in value}
        if response.get('id'):
            fields['response_id'] = response['id']
        if response.get('usage'):
            fields['usage'] = response['usage']
        if kind == 'session.updated':
            session = value.get('session') or {}
            fields['effective'] = {k: session.get(k) for k in ('modalities', 'audio', 'turn_detection')}
        if kind == 'error':
            code = str((value.get('error') or {}).get('code', 'UNKNOWN'))
            fields['code'] = code if re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', code) else 'UNKNOWN'
        if kind not in ('response.text.delta',):
            trace(self.sid, 'provider_event', event=kind, **fields)
        elif not getattr(self, 'first_delta', False):
            self.first_delta = True
            trace(self.sid, 'provider_first_text', **fields)
        if kind == 'input_audio_buffer.speech_started':
            self.first_delta = False
        if kind == 'error':
            raise ProviderFailure('NATIVE_PROVIDER_ERROR')
        return value

    async def send_audio(self, pcm):
        from array import array
        self.sent_frames = getattr(self, 'sent_frames', 0) + 1
        if self.sent_frames % 50 == 0:
            values = array('h', pcm)
            trace(self.sid, 'input_audio_heartbeat', sent_frames=self.sent_frames,
                  rms=(sum(v*v for v in values)/len(values))**.5/32768)
        await super().send_audio(pcm)

    async def events(self):
        async for event in super().events():
            if event['type'] == 'final':
                trace(self.sid, 'input_final', provider_input_id=event['provider_input_id'],
                      text=event['text'], candidate=event['candidate'].model_dump(mode='json'))
            yield event


class MeasuredSpeech(StreamingSpeech):
    def __init__(self):
        super().__init__(settings, reserve)
        self.sid = active_sid()

    async def stream(self, text, cancel):
        waiting = text == '正在核实，请稍等。'
        identity = str(__import__('uuid').uuid4())
        trace(self.sid, 'synthesis_start', synthesis_id=identity, waiting=waiting)
        first = True
        total = 0
        try:
            async for chunk in super().stream(text, cancel):
                if first:
                    first = False
                    trace(self.sid, 'synthesis_first_pcm', synthesis_id=identity, waiting=waiting)
                total += len(chunk)
                yield chunk
        except Exception as error:
            trace(self.sid, 'synthesis_error', synthesis_id=identity,
                  error_type=type(error).__name__, code=getattr(error, 'code', None))
            raise
        finally:
            trace(self.sid, 'synthesis_end', synthesis_id=identity, waiting=waiting,
                  pcm_bytes=total, cancelled=cancel.is_set())


class MeasuredSession(RealtimeSession):
    def diagnostic_snapshot(self):
        return {'session':self.probe.snapshot(),
            'provider':self.provider.probe.snapshot() if self.provider else None,
            'tts':self.speech.tts.probe.snapshot() if self.speech else None,
            'output':self.media.output.probe.snapshot() if self.media else None}

    async def close(self):
        try:
            await super().close()
        finally:
            DIAGNOSTICS[str(self.context.session_id)]=self.diagnostic_snapshot()

    def metric(self, name, duration_ms=None, **fields):
        super().metric(name, duration_ms, **fields)
        trace(self.context.session_id, 'session_metric', name=name, duration_ms=duration_ms,
              generation_id=str(self.generation) if self.generation else None, **{
                  k:v for k,v in fields.items() if k != 'generation_id'})

    def notify(self, kind, **data):
        trace(self.context.session_id, 'notice', type=kind, **data)
        return super().notify(kind, **data)

    async def control(self, command):
        if command.type == 'playback_ready':
            trace(self.context.session_id, 'browser_playback_ready',
                  playback_instance_id=str(command.playback_instance_id))
        return await super().control(command)

    async def handle_event(self, event):
        if event.payload.type == 'reply.segment_ready':
            trace(self.context.session_id, 'reviewed_segment_received',
                generation_id=str(event.generation_id), text=event.payload.text,
                message_id=str(event.payload.message_id), segment_id=str(event.payload.segment_id))
        await super().handle_event(event)


manager_module.RealtimeSession = MeasuredSession
app = create_app(realtime_input_factory=MeasuredInput, realtime_speech_factory=MeasuredSpeech)
port = app.state.realtime.business
original_submit = port.submit_turn

async def measured_submit(context, value):
    trace(context.session_id, 'business_submit_start', request_id=str(context.request_id))
    try:
        accepted = await original_submit(context, value)
        trace(context.session_id, 'business_accepted', generation_id=str(accepted.generation_id),
              request_id=str(context.request_id))
        return accepted
    except Exception as error:
        trace(context.session_id, 'business_rejected', code=getattr(getattr(error, 'error', None), 'code', type(error).__name__))
        raise
port.submit_turn = measured_submit

original_decide = app.state.services.agent.decide
def measured_decide(**kwargs):
    sid = kwargs['context'].session_id
    trace(sid, 'business_decision_start')
    result = original_decide(**kwargs)
    trace(sid, 'business_decision_end', next_state=result.next_state)
    return result
app.state.services.agent.decide = measured_decide

# Diagnostic wrappers only: record query/render boundaries without changing results.
original_lookup=app.state.services.knowledge.lookup
def measured_lookup(*args,**kwargs):
    sid=next(iter(app.state.realtime.sessions),None)
    if sid:trace(sid,'knowledge_lookup_start')
    result=original_lookup(*args,**kwargs)
    if sid:trace(sid,'knowledge_lookup_end',entry_count=len(result))
    return result
app.state.services.knowledge.lookup=measured_lookup

from app import coordinator as coordinator_module
original_render=coordinator_module.available_entries
def measured_render(*args,**kwargs):
    sid=next(iter(app.state.realtime.sessions),None)
    if sid:trace(sid,'template_render_start')
    result=original_render(*args,**kwargs)
    if sid:trace(sid,'template_render_end',entry_count=len(result))
    return result
coordinator_module.available_entries=measured_render

from app.adapters import FakeModel
original_fake_stream=FakeModel.stream
async def observed_fake_stream(self,*args,**kwargs):
    sid=next(iter(app.state.realtime.sessions),None)
    if sid:trace(sid,'fake_model_stream_invoked')
    async for chunk in original_fake_stream(self,*args,**kwargs):yield chunk
FakeModel.stream=observed_fake_stream

async def private(request):
    import secrets
    if not secrets.compare_digest(request.headers.get('x-latency-token', ''), TOKEN):
        raise HTTPException(403)

@app.get('/')
def index():
    return HTMLResponse('<!doctype html><meta charset="utf-8"><title>Bounded real-provider latency evaluation</title>')

@app.get('/realtime-meter.js')
def worklet():
    return FileResponse(ROOT/'frontend/public/realtime-meter.js', media_type='text/javascript')

@app.get('/eval/clock')
async def clock(request: Request):
    await private(request)
    return {'server_ms': ms()}

@app.get('/eval/config')
async def config(request: Request):
    await private(request)
    return {'run_id': RUN, 'limits': LIMITS, 'model': 'qwen3.8-omni-flash-realtime',
        'region': settings.dashscope_region, 'tts_model': settings.dashscope_tts_model,
        'voice': settings.dashscope_tts_voice, 'voice_writes': settings.voice_writes_enabled,
        'vad_silence_ms':settings.realtime_vad_silence_ms, 'vad_threshold':settings.realtime_vad_threshold,
        'diagnostics':settings.realtime_diagnostics_enabled, 'budget_id':BUDGET_ID,
        'server_clock':'perf_counter_ns',
        'knowledge_version':os.getenv('ZHIYU_KNOWLEDGE_VERSION','demo-k1')}

@app.get('/eval/state/{sid}')
async def state(sid: str, request: Request):
    await private(request)
    session = app.state.realtime.sessions.get(sid)
    with Session() as db:
        row = db.get(Conversation, sid)
        if row is None:
            raise HTTPException(404)
        messages = [{'id': m.id, **m.data['view']} for m in db.scalars(
            select(Message).where(Message.session_id == sid).order_by(Message.position))]
        effects = db.scalar(select(func.count()).select_from(Effect))
        receipts = {r.key.rsplit(':', 1)[-1]: r.data for r in db.scalars(
            select(RequestReceipt).where(RequestReceipt.key.contains(BUDGET_ID)))}
    return {'diagnostics':session.diagnostic_snapshot() if session else DIAGNOSTICS.get(sid),
        'traces': TRACES.get(sid, []), 'session_active': bool(session and not session.closed),
        'metrics': list(session.metrics) if session else [], 'messages': messages,
        'state': row.state, 'verified': row.data.get('verified', False), 'effects': effects,
        'busy': row.data.get('busy', False), 'budget': receipts}


@app.post('/eval/stop')
async def stop_measurement(request:Request):
    await private(request)
    server=getattr(app.state,'measurement_server',None)
    if server is None:raise HTTPException(409)
    server.should_exit=True
    return {'stopping':True}
