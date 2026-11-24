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
