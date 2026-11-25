"""Per-session pooled deterministic TTS. Approved segment text is never rewritten."""
import asyncio
import httpx
from ..tts import QwenTTS, SpeechRequest


class StreamingSpeech:
    def __init__(self, config, reserve):
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(8, connect=3), follow_redirects=False)
        # Budget transaction runs in a thread before each synthesis request.
        self.reserve, self.config = reserve, config
        self.tts = QwenTTS(config, client=self.client)

    async def stream(self, text, cancel):
        # Synthesis chunks retain the SAME business segment ID and exact text order.
        for offset in range(0, len(text), 1200):
            if cancel.is_set():
                return
            body = SpeechRequest(input=text[offset:offset+1200])
            self.tts.validate(body)
            await asyncio.to_thread(self.reserve, 'tts', self.config.max_daily_tts_calls)
            async for pcm in self.tts.stream(body, cancel):
                yield pcm

    async def close(self):
        await self.client.aclose()


def speech_profile(config):
    if config.realtime_speech_backend=='qwen_http':
        return {'backend':'qwen_http','model':config.dashscope_tts_model,'voice':config.dashscope_tts_voice,'transport':'http_sse'}
    from .ws_speech import PROFILES
    if config.realtime_speech_backend not in PROFILES:
        from ..adapters import DomainError
        raise DomainError('TTS_NOT_CONFIGURED','未知语音合成配置',503)
    model,voice=PROFILES[config.realtime_speech_backend]
    return {'backend':config.realtime_speech_backend,'model':model,'voice':voice,'transport':'websocket'}


def create_speech(config,reserve):
    speech_profile(config)
    if config.realtime_speech_backend=='qwen_http':return StreamingSpeech(config,reserve)
    from .ws_speech import WebSocketSpeech
    return WebSocketSpeech(config,reserve)


def require_speech(config):
    speech_profile(config)
    if config.realtime_speech_backend=='qwen_http':QwenTTS(config).require()
    else:
        from .ws_speech import WebSocketSpeech
        WebSocketSpeech(config,lambda *args:None).require()
