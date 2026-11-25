"""Isolated native audio experiment. No stream() method, RTC output, or playback proof."""
import asyncio,base64,json
from uuid import uuid4
from contextlib import suppress
from websockets.asyncio.client import connect
from ..db import Application
from ..realtime_business.events import RealtimeEventRow
from ..realtime_contract.dto import RealtimeBusinessEvent,ReplySegmentReady
from .provider import NativeInput
from .diagnostics import RealtimeProbe


class ShadowDenied(Exception):pass


def approved_shadow_text(facade,context,event_id):
    with facade.repo.read() as db:
        row=facade.session(db,context)
        event_row=db.get(RealtimeEventRow,str(event_id))
        if not row.data.get('verified') or row.data.get('ended') or event_row is None:raise ShadowDenied('SHADOW_UNVERIFIED')
        event=RealtimeBusinessEvent.model_validate(event_row.data)
        application=db.get(Application,row.application_id)
        if (event.session_id!=context.session_id or str(event.generation_id)!=row.data.get('generation_id')
            or event.knowledge_version!=row.data['knowledge_version'] or application.data['case_revision']!=row.data['case_revision']
            or not isinstance(event.payload,ReplySegmentReady)):
            raise ShadowDenied('SHADOW_STALE_OR_UNREVIEWED')
        return event.payload.text,event.payload.text_sha256,str(event.generation_id)


class ShadowVoice:
    def __init__(self,config,reserve,facade,connector=connect):
        self.config,self.reserve,self.facade,self.connector=config,reserve,facade,connector
        self.ws=None;self.turns=0;self.lock=asyncio.Lock();self.audio_started=asyncio.Event();self.probe=RealtimeProbe(True)

    def require(self):
        if not self.config.realtime_shadow_voice_enabled or self.config.environment not in ('development','test') or self.config.voice_writes_enabled:
            raise ShadowDenied('SHADOW_DISABLED')
        NativeInput(self.config,self.reserve).require()

    async def _send(self,kind,**fields):
        await self.ws.send(json.dumps({'type':kind,'event_id':str(uuid4()),**fields},ensure_ascii=False))

    async def _receive(self,cancel,timeout=8):
        pending=asyncio.create_task(self.ws.recv());stopper=asyncio.create_task(cancel.wait())
        try:
            done,_=await asyncio.wait([pending,stopper],timeout=timeout,return_when=asyncio.FIRST_COMPLETED)
            if stopper in done:raise asyncio.CancelledError()
            if not done:raise TimeoutError()
            value=json.loads(pending.result())
            if value.get('type')=='error':raise ShadowDenied('SHADOW_PROVIDER_ERROR')
            return value
        finally:
            for task in (pending,stopper):
                if not task.done():task.cancel()
            await asyncio.gather(pending,stopper,return_exceptions=True)

    async def _open(self,cancel):
        if self.ws is not None:return
        await asyncio.to_thread(self.reserve,'native_realtime_session',self.config.max_daily_native_sessions)
        for _ in range(self.config.max_daily_native_turns):
            await asyncio.to_thread(self.reserve,'native_realtime_turn',self.config.max_daily_native_turns)
        self.ws=await self.connector(f'wss://{self.config.dashscope_workspace_id}.{self.config.dashscope_region}.maas.aliyuncs.com/api-ws/v1/realtime?model=qwen3.8-omni-flash-realtime',
            additional_headers={'Authorization':'Bearer '+self.config.dashscope_api_key},open_timeout=3,max_size=1024*1024)
        while (await self._receive(cancel))['type']!='session.created':pass
        await self._send('session.update',session={'modalities':['text','audio'],'voice':'Tina','turn_detection':None,
            'instructions':'仅朗读后台批准的正文，不改写、不补充、不执行任何业务。',
            'audio':{'input':{'format':{'type':'pcm','sample_rate':16000}},'output':{'format':{'type':'pcm','sample_rate':24000}}}})
        while (await self._receive(cancel))['type']!='session.updated':pass

    async def generate(self,context,event_id,cancel):
        self.require()
        text,digest,generation=approved_shadow_text(self.facade,context,event_id)
        async with self.lock:
            if cancel.is_set():return None
            self.audio_started.clear()
            try:
                await self._open(cancel)
                if self.turns>=self.config.max_daily_native_turns:raise ShadowDenied('SHADOW_TURN_LIMIT')
                self.turns+=1
                await self._send('conversation.item.create',item={'type':'message','role':'user','content':[{'type':'input_text','text':text}]})
                await self._send('response.create')
                rid=None;pcm=bytearray();transcript=''
                async with asyncio.timeout(30):
                    while True:
                        event=await self._receive(cancel);kind=event.get('type')
                        if kind=='response.created':
                            if rid is not None:raise ShadowDenied('SHADOW_RESPONSE_OVERLAP')
                            rid=event['response']['id']
                        elif kind in ('response.audio.delta','response.audio_transcript.delta','response.audio_transcript.done'):
                            if rid is None or event.get('response_id')!=rid:raise ShadowDenied('SHADOW_RESPONSE_UNBOUND')
                            if kind=='response.audio.delta':
                                pcm.extend(base64.b64decode(event.get('delta',''),validate=True))
                                if len(pcm)>10*1024*1024:raise ShadowDenied('SHADOW_AUDIO_LIMIT')
                                if pcm:self.audio_started.set()
                            elif kind.endswith('.done'):transcript=event.get('transcript','')
                            else:transcript+=event.get('delta','')
                        elif kind=='response.done':
                            if event['response']['id']!=rid or event['response'].get('status')!='completed':raise ShadowDenied('SHADOW_RESPONSE_INCOMPLETE')
                            break
                if not pcm or len(pcm)%2:raise ShadowDenied('SHADOW_INVALID_PCM')
                if approved_shadow_text(self.facade,context,event_id)!=(text,digest,generation):raise ShadowDenied('SHADOW_STALE')
                return {'candidate_pcm':bytes(pcm),'approved_sha256':digest,'generation_id':generation,'response_id':rid,
                    'model_transcript':transcript,'text_matches':transcript==text,'audio_verified':False,'merchant_playback':False}
            except asyncio.CancelledError:
                if self.ws:
                    with suppress(Exception):await asyncio.wait_for(self._send('response.cancel'),.3)
                await self.close()
                if cancel.is_set():return None
                raise
            except BaseException:
                await self.close();raise

    async def close(self):
        if self.ws:
            ws,self.ws=self.ws,None
            with suppress(Exception):
                async with asyncio.timeout(1):await ws.close()
