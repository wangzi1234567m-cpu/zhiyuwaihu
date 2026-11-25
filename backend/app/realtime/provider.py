"""One native input receiver; text-only understanding, no model audio passthrough."""
import asyncio
import base64
import json
import re
from collections import deque
from uuid import uuid4
from websockets.asyncio.client import connect
from ..realtime_contract.dto import UnderstandingCandidate
from .diagnostics import RealtimeProbe, now_ns


class ProviderFailure(Exception):
    pass


class NativeInput:
    def __init__(self, config, reserve, connector=connect):
        self.config, self.reserve, self.connector = config, reserve, connector
        self.probe = RealtimeProbe(config.realtime_diagnostics_enabled)
        self.ws = None
        self.send_lock = asyncio.Lock()
        self.active = set()
        self.turns = 0
        self.inputs, self.responses = {}, {}
        self.completed_responses = set()
        self.cancel_requested = set()

    def require(self):
        c = self.config
        if c.realtime_vad_mode not in ('server_vad','semantic_vad_ec'):
            raise ProviderFailure('NATIVE_VAD_CONFIG_INVALID')
        if c.realtime_vad_silence_ms not in (250, 300, 400) or not 0 < c.realtime_vad_threshold < 1:
            raise ProviderFailure('NATIVE_VAD_CONFIG_INVALID')
        if not c.cloud_calls_enabled or not c.free_quota_confirmed or not c.dashscope_api_key:
            raise ProviderFailure('NATIVE_NOT_CONFIGURED')
        if c.dashscope_region not in ('cn-beijing', 'ap-southeast-1') or not re.fullmatch(r'ws-[A-Za-z0-9-]+', c.dashscope_workspace_id):
            raise ProviderFailure('NATIVE_REGION_INVALID')

    async def send(self, kind, **payload):
        waiting = now_ns() if self.probe.enabled else 0
        async with self.send_lock:
            started = now_ns() if self.probe.enabled else 0
            try:
                async with asyncio.timeout(2):
                    await self.ws.send(json.dumps({'type': kind, 'event_id': str(uuid4()), **payload}, ensure_ascii=False))
            finally:
                if self.probe.enabled:
                    self.probe.emit('websocket_send', kind=kind, lock_wait_ns=started-waiting,
                                    send_ns=now_ns()-started)


    async def receive(self):
        value = json.loads(await self.ws.recv())
        if value.get('type') == 'error':
            raise ProviderFailure('NATIVE_PROVIDER_ERROR')
        return value

    async def open(self, snapshot):
        self.require()
        c = self.config
        await asyncio.to_thread(self.reserve, 'native_realtime_session', c.max_daily_native_sessions)
        # Conservatively reserve the whole bounded call BEFORE sending cloud audio.
        # No synchronous DB transaction is performed in the model receive loop.
        if not 1 <= c.max_daily_native_turns <= 64:
            raise ProviderFailure('NATIVE_TURN_LIMIT_INVALID')
        def reserve_turns():
            for _ in range(c.max_daily_native_turns):
                self.reserve('native_realtime_turn', c.max_daily_native_turns)
        await asyncio.to_thread(reserve_turns)
        url = f'wss://{c.dashscope_workspace_id}.{c.dashscope_region}.maas.aliyuncs.com/api-ws/v1/realtime?model=qwen3.8-omni-flash-realtime'
        try:
            async with asyncio.timeout(8):
                self.probe.emit('native_ws_connect_start')
                self.ws = await self.connector(url, additional_headers={'Authorization': 'Bearer '+c.dashscope_api_key},
                    open_timeout=8, max_size=1024*1024, ping_interval=15)
                self.probe.emit('native_ws_connected')
                while (await self.receive()).get('type') != 'session.created':
                    pass
                self.probe.emit('native_session_created')
                await self.send('session.update', session={
                    'modalities': ['text'], 'tools': [], 'max_tokens': 512,
                    'instructions': ('你是音频输入理解器，不对用户回答。只输出JSON：transcript为本轮用户原话，'
                        '顶层仅包含transcript、intents、slots、needs_clarification、negated。'
                        'intents为意图列表，needs_clarification和negated为布尔。'
                        'slots仅允许callback_time、callback_window、timezone，且仅预约回电时从本轮原话提取。'
                        '其他意图的slots必须为{}；不得输出identity_claim、company_name等额外槽位。'
                        '不得补写用户授权、身份或业务成功。意图仅允许confirm_identity,deny_identity,ask_reason,'
                        'ask_remedy,ask_guarantee,ask_source,ask_outcome,refuse_contact,request_callback,'
                        'acknowledge,unclear,complaint_risk。未知或歧义用unclear。当前业务状态：'+snapshot.state),
                    'audio': {'input': {'format': {'type': 'pcm', 'sample_rate': 16000}}},
                    'turn_detection': {'type': c.realtime_vad_mode, 'threshold': c.realtime_vad_threshold, 'silence_duration_ms': c.realtime_vad_silence_ms},
                })
                while True:
                    updated=await self.receive()
                    if updated.get('type')=='session.updated':break
                if c.realtime_vad_mode=='semantic_vad_ec':
                    actual=(updated.get('session') or {}).get('turn_detection') or {}
                    if actual.get('type')!=c.realtime_vad_mode or actual.get('silence_duration_ms')!=c.realtime_vad_silence_ms:
                        raise ProviderFailure('NATIVE_VAD_CONFIG_REJECTED')
                self.probe.emit('native_session_updated')
        except BaseException as error:
            self.probe.emit('native_open_failed',error_type=type(error).__name__)
            await self.close()
            raise

    async def send_audio(self, pcm):
        await self.send('input_audio_buffer.append', audio=base64.b64encode(pcm).decode())

    async def cancel(self):
        targets=self.active-self.cancel_requested
        if targets:
            self.cancel_requested.update(targets)
            await self.send('response.cancel')

    async def events(self):
        # Acoustic starts interrupt playback. Business input identity follows item_id,
        # not the number/order of acoustic events. Never guess among committed inputs.
        epoch=0
        pending=set()
        seen_ids,seen_order=set(),deque()
        while True:
            event=await self.receive()
            eid=event.get('event_id')
            if eid:
                if eid in seen_ids:continue
                if len(seen_order)>=1024:seen_ids.discard(seen_order.popleft())
                seen_order.append(eid);seen_ids.add(eid)
            kind=event.get('type')
            if kind=='input_audio_buffer.speech_started':
                item=event.get('item_id')
                if not isinstance(item,str) or not item:raise ProviderFailure('NATIVE_INPUT_BINDING_INVALID')
                existing=self.inputs.get(item)
                if existing and existing['phase'] in ('done','cancelled'):continue
                continuation=existing is not None
                if not existing:
                    if len(self.inputs)>=64:raise ProviderFailure('NATIVE_INPUT_BINDING_INVALID')
                    self.turns+=1
                    if self.turns>self.config.max_daily_native_turns:raise ProviderFailure('NATIVE_TURN_LIMIT')
                    epoch+=1
                    existing={'epoch':epoch,'phase':'capturing','response_id':None}
                    self.inputs[item]=existing
                yield {'type':'speech_started','epoch':existing['epoch'],'continuation':continuation}
            elif kind=='input_audio_buffer.speech_stopped':
                item=event.get('item_id')
                if item not in self.inputs:raise ProviderFailure('NATIVE_INPUT_UNBOUND')
                yield {'type':'speech_stopped','epoch':self.inputs[item]['epoch']}
            elif kind=='input_audio_buffer.committed':
                item=event.get('item_id')
                if item not in self.inputs:raise ProviderFailure('NATIVE_INPUT_UNBOUND')
                if self.inputs[item]['phase']!='capturing':continue
                self.inputs[item]['phase']='committed';pending.add(item)
                if len(pending)>4:raise ProviderFailure('NATIVE_INPUT_UNBOUND')
            elif kind=='response.created':
                rid=event.get('response',{}).get('id')
                if not isinstance(rid,str) or not rid:raise ProviderFailure('NATIVE_RESPONSE_UNBOUND')
                if rid in self.responses or rid in self.completed_responses:continue
                if len(pending)!=1 or self.responses:
                    raise ProviderFailure('NATIVE_INPUT_ASSOCIATION_UNCERTAIN')
                item=next(iter(pending));pending.remove(item)
                record=self.inputs[item];record.update(phase='bound',response_id=rid)
                self.responses[rid]={'item':item,'epoch':record['epoch'],'text':''}
                self.active.add(rid)
            elif kind in ('response.text.delta','response.text.done'):
                rid=event.get('response_id');response=self.responses.get(rid)
                if response is None:
                    if rid in self.completed_responses:continue
                    raise ProviderFailure('NATIVE_RESPONSE_UNBOUND')
                response['text']=(response['text']+event.get('delta','') if kind.endswith('delta') else event.get('text',''))
                if len(response['text'])>16000:raise ProviderFailure('NATIVE_OUTPUT_LIMIT')
            elif kind=='response.done':
                rid=event.get('response',{}).get('id')
                if rid in self.completed_responses:continue
                response=self.responses.pop(rid,None)
                if response is None:raise ProviderFailure('NATIVE_RESPONSE_UNBOUND')
                self.active.discard(rid);self.completed_responses.add(rid)
                record=self.inputs[response['item']]
                cancelled=event['response'].get('status')=='cancelled' or rid in self.cancel_requested
                record['phase']='cancelled' if cancelled else 'done'
                if cancelled or response['epoch']!=epoch:continue
                if event['response'].get('status')!='completed':raise ProviderFailure('NATIVE_RESPONSE_INCOMPLETE')
                try:
                    raw=json.loads(response['text']);text=raw.pop('transcript')
                    if not isinstance(text,str) or not text.strip() or len(text)>8000:raise ValueError()
                    candidate=UnderstandingCandidate.model_validate(raw)
                except (ValueError,TypeError,KeyError,AttributeError):
                    raise ProviderFailure('NATIVE_INVALID_UNDERSTANDING') from None
                yield {'type':'final','epoch':response['epoch'],'provider_input_id':response['item'],
                       'text':text,'candidate':candidate}

    async def close(self):
        if self.ws:
            ws, self.ws = self.ws, None
            await ws.close()
