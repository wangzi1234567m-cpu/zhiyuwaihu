"""Whitelisted Qwen duplex TTS, exact reviewed text and bounded cancellation."""
import asyncio
import json
import re
import hashlib
from uuid import uuid4
from contextlib import suppress
from websockets.asyncio.client import connect
from ..adapters import DomainError
from .diagnostics import RealtimeProbe, now_ns

PROFILES={
    'qwen_ws':('qwen-audio-3.1-tts-flash','longanhuan_v3.1'),
}


class WebSocketSpeech:
    def __init__(self,config,reserve,connector=connect):
        self.config,self.reserve,self.connector=config,reserve,connector
        self.ws=None;self.closed=False;self.lock=asyncio.Lock();self.probe=RealtimeProbe(config.realtime_diagnostics_enabled)
        self.tts=self  # Diagnostic interface shared with the HTTP adapter; no new business DTO.
        self.model,self.voice=PROFILES.get(config.realtime_speech_backend,('',''))

    def require(self):
        c=self.config
        if c.realtime_speech_backend not in PROFILES or not c.cloud_calls_enabled or not c.free_quota_confirmed or not c.dashscope_api_key:
            raise DomainError('TTS_NOT_CONFIGURED','实时语音合成未配置',503)
        if c.dashscope_region not in ('cn-beijing','ap-southeast-1') or not re.fullmatch(r'ws-[A-Za-z0-9-]+',c.dashscope_workspace_id):
            raise DomainError('TTS_NOT_CONFIGURED','语音地域或业务空间不合法',503)

    async def _connect(self):
        if self.ws is None:
            self.probe.emit('tts_ws_connect_start')
            self.ws=await self.connector(f'wss://{self.config.dashscope_workspace_id}.{self.config.dashscope_region}.maas.aliyuncs.com/api-ws/v1/inference',
                additional_headers={'Authorization':'Bearer '+self.config.dashscope_api_key},open_timeout=3,max_size=1024*1024,ping_interval=15)
            self.probe.emit('tts_ws_connected')

    async def _send(self,action,task,payload):
        async with asyncio.timeout(2):
            await self.ws.send(json.dumps({'header':{'action':action,'task_id':task,'streaming':'duplex'},'payload':payload},ensure_ascii=False))

    async def _wait(self,awaitable,cancel,timeout):
        reader=asyncio.create_task(awaitable);stopper=asyncio.create_task(cancel.wait())
        try:
            done,_=await asyncio.wait([reader,stopper],timeout=max(.001,timeout),return_when=asyncio.FIRST_COMPLETED)
            if stopper in done:raise asyncio.CancelledError()
            if not done:raise TimeoutError()
            return reader.result()
        finally:
            for task in (reader,stopper):
                if not task.done():task.cancel()
            await asyncio.gather(reader,stopper,return_exceptions=True)

    async def _recv(self,cancel,timeout):
        return await self._wait(self.ws.recv(),cancel,timeout)

    def _event(self,raw,task):
        try:
            value=json.loads(raw);header=value['header']
            if header.get('task_id')!=task:raise ValueError()
            event=header['event']
        except (ValueError,TypeError,KeyError):
            raise DomainError('TTS_BAD_STREAM','语音任务响应无法关联',503) from None
        if event=='task-failed':
            code=str(header.get('error_code','UNKNOWN'))
            if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',code):code='UNKNOWN'
            self.probe.emit('tts_ws_failed',task_id=task,provider_code=code)
            public='TTS_RATE_LIMITED' if any(x in code.upper() for x in ('THROTTL','RATE_LIMIT')) else 'TTS_UPSTREAM_ERROR'
            raise DomainError(public,'语音供应商任务失败',503)
        usage=value.get('payload',{}).get('usage') or {}
        self.probe.emit('tts_ws_event',task_id=task,name=event,**{k:v for k,v in usage.items() if k in ('characters','input_tokens','output_tokens','total_tokens') and isinstance(v,(int,float))})
        return event

    async def _cancel_task(self,task):
        if self.ws is None:return
        try:
            async with asyncio.timeout(.6):
                await self._send('finish-task',task,{'input':{'directive':'cancel'}})
                while True:
                    raw=await self.ws.recv()
                    if not isinstance(raw,bytes) and self._event(raw,task)=='task-finished':break
        except (Exception,asyncio.CancelledError):
            pass
        finally:
            # Cancellation may race a normal terminal event and leave another ack.
            # Reconnect before any next task; never interpret old audio/acks as new.
            await self._drop()

    async def _drop(self):
        if self.ws is not None:
            ws,self.ws=self.ws,None
            with suppress(Exception):
                async with asyncio.timeout(1):await ws.close()

    async def stream(self,text,cancel):
        self.require()
        if not isinstance(text,str) or not text.strip() or len(text)>2000:
            raise DomainError('TTS_PARAMETERS_REJECTED','审核正文为空或超长',422)
        async with self.lock:
            if self.closed or cancel.is_set():return
            for offset in range(0,len(text),1200):
                if cancel.is_set():return
                await asyncio.to_thread(self.reserve,'tts',self.config.max_daily_tts_calls)
                task=str(uuid4());finished=False;submitted=False;first=True;tail=b'';size=0
                start=now_ns();deadline=asyncio.get_running_loop().time()+self.config.tts_first_audio_timeout
                reused=self.ws is not None
                self.probe.emit('tts_request_start',request_id=task,text_chars=len(text[offset:offset+1200]),text_sha256=hashlib.sha256(text[offset:offset+1200].encode()).hexdigest(),model=self.model,voice=self.voice,connection_reused_observed=reused)
                try:
                    async with asyncio.timeout(self.config.tts_total_timeout):
                        await self._wait(self._connect(),cancel,deadline-asyncio.get_running_loop().time())
                        if cancel.is_set():return
                        await self._send('run-task',task,{'task_group':'audio','task':'tts','function':'SpeechSynthesizer','model':self.model,
                            'parameters':{'text_type':'PlainText','voice':self.voice,'format':'pcm','sample_rate':24000,'rate':1.0,'enable_ssml':False},'input':{}})
                        submitted=True
                        while True:
                            raw=await self._recv(cancel,deadline-asyncio.get_running_loop().time())
                            if isinstance(raw,bytes) or self._event(raw,task)!='task-started':
                                raise DomainError('TTS_BAD_STREAM','语音任务未确认启动',503)
                            break
                        self.probe.emit('tts_response_headers',request_id=task,connection_reused_observed=reused,transport='websocket')
                        await self._send('continue-task',task,{'input':{'text':text[offset:offset+1200]}})
                        await self._send('finish-task',task,{'input':{}})
                        while True:
                            timeout=deadline-asyncio.get_running_loop().time() if first else self.config.tts_total_timeout
                            raw=await self._recv(cancel,timeout)
                            if isinstance(raw,bytes):
                                size+=len(raw)
                                if size>10*1024*1024:raise DomainError('TTS_TOO_LARGE','语音输出超过限制',503)
                                chunk=tail+raw;tail=chunk[-1:] if len(chunk)%2 else b'';chunk=chunk[:len(chunk)-len(tail)]
                                if chunk:
                                    if first:self.probe.emit('tts_first_pcm',request_id=task,bytes=len(chunk));first=False
                                    if cancel.is_set():return
                                    yield chunk
                            else:
                                event=self._event(raw,task)
                                if event=='task-finished':
                                    finished=True
                                    if first or tail:raise DomainError('TTS_EMPTY','语音输出为空或不完整',503)
                                    break
                except asyncio.CancelledError:
                    self.probe.emit('tts_cancelled',request_id=task)
                    if cancel.is_set():return
                    raise
                except TimeoutError:
                    self.probe.emit('tts_error',request_id=task,code='TTS_TIMEOUT',before_first_pcm=first)
                    raise DomainError('TTS_TIMEOUT','语音合成响应超时',503) from None
                except DomainError:
                    await self._drop();raise
                except Exception:
                    await self._drop()
                    raise DomainError('TTS_NETWORK_ERROR','语音合成连接失败',503) from None
                finally:
                    if submitted and not finished:await self._cancel_task(task)
                    self.probe.emit('tts_stream_end',request_id=task,received_pcm=not first,cancelled=cancel.is_set(),duration_ns=now_ns()-start)

    async def close(self):
        self.closed=True
        await self._drop()
