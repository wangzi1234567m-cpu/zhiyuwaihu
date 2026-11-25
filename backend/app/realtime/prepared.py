"""At most 120ms PCM prefetched per reviewed segment; parent owns cancellation."""
import asyncio
from contextlib import aclosing

class PreparedSpeech:
    def __init__(self,text,speech,spawn):
        self.cancel=asyncio.Event();self.queue=asyncio.Queue(6);self.error=None
        self.task=spawn(self.produce(text,speech))

    async def produce(self,text,speech):
        try:
            async with aclosing(speech.stream(text,self.cancel)) as chunks:
                async for pcm in chunks:
                    for start in range(0,len(pcm),960):
                        if self.cancel.is_set():return
                        await self.queue.put(pcm[start:start+960])
        except asyncio.CancelledError:raise
        except Exception as error:self.error=error
        finally:
            if not self.cancel.is_set():await self.queue.put(None)

    async def stream(self):
        while not self.cancel.is_set():
            pcm=await self.queue.get()
            if pcm is None:
                if self.error:raise self.error
                return
            yield pcm

    def stop(self):
        self.cancel.set();self.task.cancel()
