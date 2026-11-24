"""Qwen-Audio HTTP SSE -> raw mono PCM16 at 24 kHz. No audio URL fetching or retries."""
import asyncio
import base64
import binascii
import json
import re
from contextlib import asynccontextmanager
from contextlib import suppress
import httpx
from pydantic import BaseModel, ConfigDict, Field
from .config import settings
from .adapters import DomainError


class SpeechRequest(BaseModel):
    model_config = ConfigDict(extra='ignore')
    input: str = Field(min_length=1, max_length=1200)
    model: str | None = None
    voice: str | None = None
    response_format: str = 'pcm'
    sample_rate: int = 24000
    speed: float = 1.0


class QwenTTS:
    def __init__(self, config=settings, transport=None, reserve=None, client=None):
        self.config, self.transport, self.reserve = config, transport, reserve
        self.active = {}
        self.client = client

    @asynccontextmanager
    async def connection(self):
        if self.client is not None:
            yield self.client
        else:
            async with httpx.AsyncClient(transport=self.transport, timeout=httpx.Timeout(8, connect=3), follow_redirects=False) as client:
                yield client

    def register(self, sid, cancel):
        self.active.setdefault(sid, set()).add(cancel)

    def unregister(self, sid, cancel):
        events = self.active.get(sid)
        if events is not None:
            events.discard(cancel)
            if not events:self.active.pop(sid, None)

    def cancel_session(self, sid):
        for event in tuple(self.active.get(sid, ())):event.set()

    def endpoint(self):
        c = self.config
        if c.dashscope_region not in ('cn-beijing', 'ap-southeast-1') or not re.fullmatch(r'ws-[a-zA-Z0-9-]+', c.dashscope_workspace_id):
            raise DomainError('TTS_NOT_CONFIGURED', '请配置百炼地域与WorkspaceId', 503)
        url = f'https://{c.dashscope_workspace_id}.{c.dashscope_region}.maas.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer'
        if c.dashscope_tts_http_url and c.dashscope_tts_http_url != url:
            raise DomainError('TTS_NOT_CONFIGURED', '百炼地址必须与地域和业务空间一致', 503)
        return url

    def require(self):
        c = self.config
        if c.tts_provider != 'qwen' or not c.cloud_calls_enabled or not c.free_quota_confirmed or not c.dashscope_api_key:
            raise DomainError('TTS_NOT_CONFIGURED', '请填写百炼Key并确认赠送额度与云调用开关', 503)
        self.endpoint()
        if c.dashscope_tts_model != 'qwen-audio-3.1-tts-flash' or not re.fullmatch(r'[a-zA-Z0-9_.-]{1,160}', c.dashscope_tts_voice):
            raise DomainError('TTS_NOT_CONFIGURED', '请使用已验证的Qwen模型与音色配置', 503)

    def validate(self, body):
        self.require()
        if not body.input.strip() or (body.model is not None and body.model != self.config.dashscope_tts_model) or (body.voice is not None and body.voice != self.config.dashscope_tts_voice):
            raise DomainError('TTS_PARAMETERS_REJECTED', '文本、模型或音色不符合服务端配置', 422)
        if body.response_format != 'pcm' or body.sample_rate != 24000 or body.speed != 1.0:
            raise DomainError('TTS_PARAMETERS_REJECTED', '当前只支持24kHz PCM、默认语速', 422)

    async def stream(self, body, cancel):
        self.validate(body)
        if cancel.is_set():
            return
        if self.reserve:
            self.reserve('tts', self.config.max_daily_tts_calls)
        received = False

        async def audio():
            nonlocal received
            total, tail = 0, b''
            payload = {'model': self.config.dashscope_tts_model, 'input': {
                'text': body.input, 'voice': self.config.dashscope_tts_voice,
                'format': 'pcm', 'sample_rate': 24000, 'rate': 1.0, 'enable_ssml': False}}
            async with self.connection() as client:
                async with client.stream('POST', self.endpoint(), json=payload, headers={
                    'Authorization': 'Bearer '+self.config.dashscope_api_key,
                    'X-DashScope-SSE': 'enable'}) as response:
                    if response.status_code != 200:
                        code = {401:'TTS_AUTH_FAILED', 403:'TTS_PERMISSION_DENIED', 402:'TTS_QUOTA_EXHAUSTED', 429:'TTS_RATE_LIMITED'}.get(response.status_code, 'TTS_UPSTREAM_ERROR')
                        raise DomainError(code, '百炼语音服务不可用，请检查账号或额度', 503)
                    if not response.headers.get('content-type', '').startswith('text/event-stream'):
                        raise DomainError('TTS_BAD_STREAM', '百炼未返回SSE音频流', 503)
                    data = []
                    async for line in response.aiter_lines():
                        if line.startswith('data:'):
                            data.append(line[5:].lstrip())
                            if sum(map(len, data)) > 1024*1024:
                                raise DomainError('TTS_BAD_STREAM', '音频数据块过大', 503)
                            continue
                        if line or not data:
                            continue
                        raw, data = '\n'.join(data), []
                        if raw == '[DONE]':
                            if received and not tail:
                                return
                            raise DomainError('TTS_EMPTY', '音频输出为空或不完整', 503)
                        try:
                            obj = json.loads(raw)
                            if obj.get('code') not in (None, '', 0, '0', '20000000'):
                                raise DomainError('TTS_UPSTREAM_ERROR', '百炼合成失败，请检查账号或额度', 503)
                            output = obj['output']
                            encoded = output.get('audio', {}).get('data', '')
                            decoded = base64.b64decode(encoded, validate=True) if encoded else b''
                        except (ValueError, TypeError, KeyError, AttributeError, binascii.Error):
                            raise DomainError('TTS_BAD_STREAM', '音频流格式异常', 503)
                        total += len(decoded)
                        if total > 10*1024*1024:
                            raise DomainError('TTS_TOO_LARGE', '音频超出本轮限制', 503)
                        chunk = tail + decoded
                        tail = chunk[-1:] if len(chunk) % 2 else b''
                        chunk = chunk[:len(chunk)-len(tail)]
                        if chunk:
                            received = True
                            yield chunk
                        if output.get('finish_reason') == 'stop':
                            if not received or tail:
                                raise DomainError('TTS_EMPTY', '音频输出为空或不完整', 503)
                            return
                    raise DomainError('TTS_TRUNCATED', '音频流提前结束', 503)

        iterator = audio().__aiter__()
        cancelled = asyncio.create_task(cancel.wait())
        pending = None
        deadline = asyncio.get_running_loop().time()+self.config.tts_first_audio_timeout
        try:
            async with asyncio.timeout(self.config.tts_total_timeout):
                while not cancel.is_set():
                    pending = asyncio.create_task(anext(iterator))
                    timeout = None if received else max(.001, deadline-asyncio.get_running_loop().time())
                    done, _ = await asyncio.wait([pending, cancelled], timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
                    if not done:
                        raise TimeoutError()
                    if cancelled in done:
                        return
                    try:
                        yield pending.result()
                    except StopAsyncIteration:
                        return
        except (TimeoutError, httpx.TimeoutException):
            raise DomainError('TTS_TIMEOUT', '语音合成响应超时', 503)
        except httpx.HTTPError:
            raise DomainError('TTS_NETWORK_ERROR', '语音合成连接失败', 503)
        finally:
            if pending and not pending.done():
                pending.cancel()
                with suppress(asyncio.CancelledError):
                    await pending
            cancelled.cancel()
            with suppress(asyncio.CancelledError):
                await cancelled
            await iterator.aclose()
