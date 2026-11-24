"""One native input receiver; text-only understanding, no model audio passthrough."""
import asyncio
import base64
import json
import re
from collections import deque
from uuid import uuid4
from websockets.asyncio.client import connect
from ..realtime_contract.dto import UnderstandingCandidate


class ProviderFailure(Exception):
    pass


class NativeInput:
    def __init__(self, config, reserve, connector=connect):
        self.config, self.reserve, self.connector = config, reserve, connector
        self.ws = None
        self.send_lock = asyncio.Lock()
        self.active = set()
        self.turns = 0

    def require(self):
        c = self.config
        if not c.cloud_calls_enabled or not c.free_quota_confirmed or not c.dashscope_api_key:
            raise ProviderFailure('NATIVE_NOT_CONFIGURED')
        if c.dashscope_region not in ('cn-beijing', 'ap-southeast-1') or not re.fullmatch(r'ws-[A-Za-z0-9-]+', c.dashscope_workspace_id):
            raise ProviderFailure('NATIVE_REGION_INVALID')

    async def send(self, kind, **payload):
        async with self.send_lock:
            async with asyncio.timeout(2):
                await self.ws.send(json.dumps({'type': kind, 'event_id': str(uuid4()), **payload}, ensure_ascii=False))

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
                self.ws = await self.connector(url, additional_headers={'Authorization': 'Bearer '+c.dashscope_api_key},
                    open_timeout=8, max_size=1024*1024, ping_interval=15)
                while (await self.receive()).get('type') != 'session.created':
                    pass
                await self.send('session.update', session={
                    'modalities': ['text'], 'tools': [], 'max_tokens': 512,
                    'instructions': ('你是音频输入理解器，不对用户回答。只输出JSON：transcript为本轮用户原话，'
                        'intents为意图列表，slots只取本轮原话片段，needs_clarification和negated为布尔。'
                        '不得补写用户授权、身份或业务成功。意图仅允许confirm_identity,deny_identity,ask_reason,'
                        'ask_remedy,ask_guarantee,ask_source,ask_outcome,refuse_contact,request_callback,'
                        'acknowledge,unclear,complaint_risk。未知或歧义用unclear。当前业务状态：'+snapshot.state),
                    'audio': {'input': {'format': {'type': 'pcm', 'sample_rate': 16000}}},
                    'turn_detection': {'type': 'server_vad', 'threshold': .5, 'silence_duration_ms': 400},
                })
                while (await self.receive()).get('type') != 'session.updated':
                    pass
        except BaseException:
            await self.close()
            raise

    async def send_audio(self, pcm):
        await self.send('input_audio_buffer.append', audio=base64.b64encode(pcm).decode())

    async def cancel(self):
        if self.active:
            await self.send('response.cancel')

    async def events(self):
        epoch = 0
        items, pending, responses = {}, deque(), {}
        while True:
            event = await self.receive()
            kind = event.get('type')
            if kind == 'input_audio_buffer.speech_started':
                self.turns += 1
                if self.turns > self.config.max_daily_native_turns:
                    raise ProviderFailure('NATIVE_TURN_LIMIT')
                epoch += 1
                item = event.get('item_id')
                if not item or len(items) >= 16:
                    raise ProviderFailure('NATIVE_INPUT_BINDING_INVALID')
                items[item] = epoch
                yield {'type': 'speech_started', 'epoch': epoch}
            elif kind == 'input_audio_buffer.speech_stopped':
                yield {'type': 'speech_stopped', 'epoch': epoch}
            elif kind == 'input_audio_buffer.committed':
                item = event.get('item_id')
                if item not in items or len(pending) >= 4:
                    raise ProviderFailure('NATIVE_INPUT_UNBOUND')
                pending.append((item, items.pop(item)))
            elif kind == 'response.created':
                if not pending or len(responses) >= 4:
                    raise ProviderFailure('NATIVE_RESPONSE_UNBOUND')
                rid = event['response']['id']
                item, turn_epoch = pending.popleft()
                responses[rid] = {'item': item, 'epoch': turn_epoch, 'text': ''}
                self.active.add(rid)
            elif kind in ('response.text.delta', 'response.text.done'):
                response = responses.get(event.get('response_id'))
                if response:
                    response['text'] = (response['text'] + event.get('delta', '') if kind.endswith('delta') else event.get('text', ''))
                    if len(response['text']) > 16000:
                        raise ProviderFailure('NATIVE_OUTPUT_LIMIT')
            elif kind == 'response.done':
                rid = event['response']['id']
                self.active.discard(rid)
                response = responses.pop(rid, None)
                if not response or response['epoch'] != epoch:
                    continue
                if event['response'].get('status') == 'cancelled':
                    continue
                if event['response'].get('status') != 'completed':
                    raise ProviderFailure('NATIVE_RESPONSE_INCOMPLETE')
                try:
                    raw = json.loads(response['text'])
                    text = raw.pop('transcript')
                    if not isinstance(text, str) or not text.strip() or len(text) > 8000:
                        raise ValueError()
                    candidate = UnderstandingCandidate.model_validate(raw)
                except (ValueError, TypeError, KeyError, AttributeError):
                    raise ProviderFailure('NATIVE_INVALID_UNDERSTANDING') from None
                yield {'type': 'final', 'epoch': epoch, 'provider_input_id': response['item'],
                       'text': text, 'candidate': candidate}

    async def close(self):
        if self.ws:
            ws, self.ws = self.ws, None
            await ws.close()
