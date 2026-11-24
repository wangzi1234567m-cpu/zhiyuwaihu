"""Agora REST lifecycle and authenticated, replayable Chat Completions bridge."""
import asyncio
import hashlib
import hmac
import json
import re
import time
from datetime import timedelta
from uuid import UUID
from urllib.parse import urlparse, quote
from contextlib import suppress
import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from .config import settings
from .contracts import *
from .db import Session, Conversation, Message, EventRow, RequestReceipt, now, new_id
from .coordinator import owned, emit, transition, receipt
from .adapters import DomainError
from .knowledge_templates import task_slot
from .cloud_budget import reserve_call
from .vendor.agora.RtcTokenBuilder2 import RtcTokenBuilder, Role_Publisher
from .vendor.agora.RtmTokenBuilder2 import RtmTokenBuilder

class ProviderMessage(BaseModel):
    model_config=ConfigDict(extra='ignore')
    role: str
    content: str | list[dict] | None = None
    turn_id: int | None = Field(default=None,ge=0)

class ProviderRequest(BaseModel):
    model_config=ConfigDict(extra='ignore')
    messages: list[ProviderMessage] = Field(min_length=1,max_length=200)
    stream: bool = True
    turn_id: int | None = Field(default=None,ge=0)

def current_input(body):
    if not body.stream: raise DomainError('STREAM_REQUIRED','只支持流式文本',422)
    users=[m for m in body.messages if m.role=='user']
    if not users: raise DomainError('USER_INPUT_REQUIRED','缺少本轮用户输入',422)
    user=users[-1]
    turn=user.turn_id if user.turn_id is not None else body.turn_id
    if turn is None: raise DomainError('PROVIDER_TURN_REQUIRED','请使用custom vendor传入声网turn_id',422)
    if body.turn_id is not None and user.turn_id is not None and body.turn_id!=user.turn_id:
        raise DomainError('PROVIDER_TURN_MISMATCH','轮次字段不一致',422)
    if isinstance(user.content,str): value=user.content
    elif isinstance(user.content,list):
        pieces=[p.get('text') for p in user.content if p.get('type')=='text']
        if not all(isinstance(p,str) for p in pieces):raise DomainError('BAD_INPUT','文本片段格式无效',422)
        value=''.join(pieces)
    else: value=''
    if not value.strip() or len(value)>8000: raise DomainError('BAD_INPUT','用户文本为空或过长',422)
    return turn,re.sub(r'(?<!\d)\d{11,18}(?!\d)','[已脱敏号码]',value)

class AgoraVoiceGateway:
    def __init__(self,config=settings,transport=None):
        self.config=config
        self.transport=transport
        self.coordinator=None
        self.worker=None
        self.locks={}
        self.subscribers={}
        self.cancel_tts=None

    def unavailable_reason(self):
        c=self.config
        if not c.voice_enabled: return '语音未配置'
        if not c.cloud_calls_enabled or not c.free_quota_confirmed: return '尚未确认赠送额度与云调用开关'
        if not all([c.agora_app_id,c.agora_certificate,c.agora_customer_id,c.agora_customer_secret,c.integration_secret]):return '缺少声网服务端凭证'
        if len(c.integration_secret)<32:return '入口签名密钥至少32字符'
        if c.agora_region not in ['cn','global']:return '声网区域必须为cn或global'
        url=urlparse(c.integration_public_url)
        if url.scheme!='https' or not url.hostname or url.hostname in ['localhost','127.0.0.1']:return '等待固定公网HTTPS地址'
        try:
            if c.tts_provider == 'qwen':
                from .tts import QwenTTS
                try: QwenTTS(c).require()
                except DomainError as error: return error.message
            elif c.tts_provider == 'manual':
                if not json.loads(c.agora_tts_config).get('vendor'):return '等待可用中文TTS配置'
            else: return 'TTS_PROVIDER必须为qwen或manual'
            json.loads(c.agora_asr_config)
        except (ValueError,AttributeError):return 'ASR或TTS配置不是有效JSON对象'
        if not re.fullmatch('[a-fA-F0-9]{32}',c.agora_app_id) or not re.fullmatch('[a-fA-F0-9]{32}',c.agora_certificate):return '声网App ID或证书格式不正确'
        return None

    def available(self):return self.unavailable_reason() is None

    def require(self):
        reason=self.unavailable_reason()
        if reason: raise DomainError('VOICE_NOT_CONFIGURED',reason,503)

    def binding_token(self,sid,binding):
        return hmac.new(self.config.integration_secret.encode(),f'{sid}:{binding}'.encode(),hashlib.sha256).hexdigest()

    def tts_token(self,sid,binding):
        return hmac.new(self.config.integration_secret.encode(),f'tts:{sid}:{binding}'.encode(),hashlib.sha256).hexdigest()

    def tts_config(self,sid,voice):
        c=self.config
        if c.tts_provider!='qwen': return json.loads(c.agora_tts_config)
        return {'vendor':'generic_http',
            'url':c.integration_public_url.rstrip('/')+f'/integrations/agora/sessions/{sid}/v1/audio/speech',
            'headers':{'Authorization':'Bearer '+self.tts_token(sid,voice['binding'])},
            'params':{'model':c.dashscope_tts_model,'voice':c.dashscope_tts_voice,'speed':1.0,
                'sample_rate':24000,'response_format':'pcm','enable_request_id':False}}

    def lock(self,sid):return self.locks.setdefault(sid,asyncio.Lock())

    async def request(self,method,path,payload=None):
        c=self.config
        prefix='/cn' if c.agora_region=='cn' else ''
        url=f'https://api.agora.io{prefix}/api/conversational-ai-agent/v2/projects/{c.agora_app_id}/{path}'
        try:
            async with httpx.AsyncClient(transport=self.transport,timeout=10) as client:
                r=await client.request(method,url,json=payload,auth=(c.agora_customer_id,c.agora_customer_secret))
                if r.status_code==404 and path.endswith('/leave'):return {}
                if r.status_code not in [200,201]:
                    raise DomainError('VOICE_UPSTREAM_ERROR',f'声网服务返回状态{r.status_code}，请核对权限或额度',503)
                return r.json()
        except httpx.TimeoutException:raise DomainError('VOICE_RESULT_UNKNOWN','声网请求超时，结果待查证',503)
        except (httpx.HTTPError,ValueError):raise DomainError('VOICE_NETWORK_ERROR','声网连接或响应异常',503)

    async def credentials(self,sid):
        self.require()
        c=self.config
        with Session.begin() as db:
            row=db.scalar(select(Conversation).where(Conversation.id==sid).with_for_update())
            if row.data.get('mode')!='voice' or row.data.get('voice_transport','agora')!='agora' or row.data.get('ended'): raise DomainError('VOICE_SESSION_INVALID','不是可用声网语音会话')
            db.execute(text("SELECT pg_advisory_xact_lock(hashtext('single-voice-session'))"))
            others=list(db.scalars(select(Conversation).where(Conversation.id!=sid)))
            if any(r.data.get('voice',{}).get('status') in ['prepared','starting','running','cleanup_pending'] for r in others):
                raise DomainError('VOICE_CONCURRENCY_LIMIT','联调阶段只允许一个语音会话',429)
            voice=row.data.get('voice')
            if not voice:
                voice={'binding':new_id(),'channel':'zy_'+UUID(sid).hex,'uid':101,'agent_uid':102,
                    'status':'prepared','created_at':now().timestamp(),'heartbeat_at':now().timestamp(),
                    'expires_at':now().timestamp()+180,'name':'zy_'+UUID(sid).hex}
                row.data={**row.data,'voice':voice}
            if voice['expires_at']<=now().timestamp():raise DomainError('VOICE_EXPIRED','语音会话已过期，请新建会话')
            expiry=int(voice['expires_at']-now().timestamp())+30
            return MediaCredentials(app_id=c.agora_app_id,channel=voice['channel'],rtc_uid=voice['uid'],agent_uid=voice['agent_uid'],
                rtc_token=RtcTokenBuilder.build_token_with_uid(c.agora_app_id,c.agora_certificate,voice['channel'],voice['uid'],Role_Publisher,expiry,expiry),
                rtm_token=RtmTokenBuilder.build_token(c.agora_app_id,c.agora_certificate,str(voice['uid']),expiry),expires_at=now()+timedelta(seconds=expiry))

    async def start(self,owner,sid,revision):
        self.require()
        async with self.lock(sid):
            with Session.begin() as db:
                row=owned(db,sid,owner,True)
                if row.data.get('voice_transport','agora')!='agora':
                    raise DomainError('WRONG_INPUT_MODE','实时会话不能启动旧声网链路',409)
                voice=dict(row.data.get('voice',{}))
                if voice.get('status')=='running':return VoiceStartResult(status='running',agent_id=voice['agent_id'])
                if row.revision!=revision or row.state!='CASE_READY' or voice.get('status')!='prepared' or row.data.get('ended'):
                    raise DomainError('VOICE_START_CONFLICT','语音启动状态不一致')
                case = self.coordinator.services.cases.get_case(owner, row.application_id)
                company = task_slot(case.slots, 'company_name')
                if company:
                    row.data={**row.data,'voice':{**voice,'status':'starting'}}
                    transition(db,row,'OPENING')
                else:
                    row.data={**row.data,'voice':{**voice,'status':'stopped'},'ended':True}
                    transition(db,row,'MANUAL_REVIEW')
                    self.coordinator.save_non_action_record(db,row,case,'disconnected')
                    emit(db,row,SessionEnded(type='session.ended',reason='manual_review'))
            if not company:
                raise DomainError('INVALID_IDENTITY_SUBJECT','审核任务缺少有效核验主体，已转人工。')
            try: reserve_call('voice',self.config.max_daily_voice_calls)
            except DomainError:
                with Session.begin() as db:
                    row=owned(db,sid,owner,True);row.data={**row.data,'voice':{**voice,'status':'stopped'}}
                    row.data={**row.data,'ended':True};transition(db,row,'MANUAL_REVIEW')
                raise
            greeting=f'您好，这里是模拟商户服务AI演示，请问您是{company}的联系人吗？'
            expiry=210
            properties={'channel':voice['channel'],'token':RtcTokenBuilder.build_token_with_rtm(self.config.agora_app_id,self.config.agora_certificate,voice['channel'],str(voice['agent_uid']),Role_Publisher,expiry,expiry),
                'agent_rtc_uid':str(voice['agent_uid']),'remote_rtc_uids':[str(voice['uid'])],
                'enable_string_uid':False,'idle_timeout':30,
                'advanced_features':{'enable_rtm':True},'parameters':{'data_channel':'rtm','enable_metrics':True,'enable_error_message':True},
                'asr':json.loads(self.config.agora_asr_config),'tts':self.tts_config(sid,voice),
                'llm':{'vendor':'custom','url':self.config.integration_public_url.rstrip('/')+f'/integrations/agora/sessions/{sid}/v1/chat/completions',
                    'api_key':self.binding_token(sid,voice['binding']),'system_messages':[{'role':'system','content':'模拟商户服务演示。'}],
                    'greeting_message':greeting,'greeting_configs':{'mode':'single_first','interruptable':True},
                    'failure_message':'服务暂时不可用，请稍后重试。','max_history':10,'params':{'model':'zhiyu-session'}}}
            try:
                result=await self.request('POST','join',{'name':voice['name'],'properties':properties})
                agent_id=result['agent_id']
            except (DomainError,KeyError):
                with Session.begin() as db:
                    row=owned(db,sid,owner,True);row.data={**row.data,'voice':{**voice,'status':'cleanup_pending'}}
                raise DomainError('VOICE_START_UNKNOWN','语音启动未确认；后台将查证清理，勿重复启动',503)
            with Session.begin() as db:
                row=owned(db,sid,owner,True)
                voice.update(status='running',agent_id=agent_id)
                row.data={**row.data,'voice':voice}
                # Greeting is cloud-generated TTS, playback evidence remains unknown.
                turn,generation=new_id(),new_id()
                row.data={**row.data,'message_count':row.data.get('message_count',0)+1}
                db.add(Message(id=new_id(),session_id=sid,position=row.data['message_count'],data={'view':{'turn_id':turn,'generation_id':generation,'role':'assistant','text':greeting,'status':'completed','playback_status':'unknown'}}))
                transition(db,row,'VERIFYING')
            return VoiceStartResult(status='running',agent_id=agent_id)

    def authorize_provider(self,sid,authorization):
        self.require()
        with Session() as db:
            row=db.get(Conversation,sid)
            voice=row.data.get('voice',{}) if row else {}
            expected='Bearer '+self.binding_token(sid,voice.get('binding',''))
            if not authorization or not hmac.compare_digest(authorization,expected):raise DomainError('VOICE_UNAUTHENTICATED','入口鉴权失败',401)
            if not row or row.data.get('ended') or voice.get('status')!='running' or voice.get('expires_at',0)<=now().timestamp():
                raise DomainError('VOICE_SESSION_EXPIRED','语音会话已结束',409)
            return row.owner,voice

    def authorize_tts(self,sid,authorization):
        self.require()
        with Session() as db:
            row=db.get(Conversation,sid)
            voice=row.data.get('voice',{}) if row else {}
            expected='Bearer '+self.tts_token(sid,voice.get('binding',''))
            if not authorization or not hmac.compare_digest(authorization,expected):
                raise DomainError('VOICE_UNAUTHENTICATED','入口鉴权失败',401)
            # Cloud greeting synthesis can arrive while join is still in flight.
            if not row or row.data.get('mode')!='voice' or row.data.get('ended') or voice.get('status') not in ['starting','running'] or voice.get('expires_at',0)<=now().timestamp():
                raise DomainError('VOICE_SESSION_EXPIRED','语音会话已结束',409)
            return row.owner,voice

    async def completion(self,sid,body,authorization):
        owner,voice=self.authorize_provider(sid,authorization)
        provider_turn,value=current_input(body)
        accepted=await self.coordinator.accept(owner,sid,value,f'agora:{voice["binding"]}:{provider_turn}',source='asr',provider_turn=provider_turn)
        gid=str(accepted.generation_id)
        with Session() as db:
            message=db.scalar(select(Message).where(Message.session_id==sid,Message.data['view']['generation_id'].astext==gid).order_by(Message.position))
            created=int(message.created_at.timestamp())
        key=(sid,gid)
        self.subscribers[key]=self.subscribers.get(key,0)+1
        async def stream():
            cursor=0
            finished=False
            def packet(delta,finish=None):
                return 'data: '+json.dumps({'id':'chatcmpl-'+gid,'object':'chat.completion.chunk','created':created,'model':'zhiyu-session',
                    'choices':[{'index':0,'delta':delta,'finish_reason':finish}]},ensure_ascii=False)+'\n\n'
            try:
                yield packet({'role':'assistant'})
                async with asyncio.timeout(15):
                    while True:
                        with Session() as db:
                            row=owned(db,sid,owner)
                            if row.data.get('generation_id')!=gid: return
                            events=[r.data for r in db.scalars(select(EventRow).where(EventRow.session_id==sid,EventRow.seq>cursor).order_by(EventRow.seq))]
                        for event in events:
                            cursor=event['seq']
                            if event.get('generation_id')!=gid:continue
                            payload=event['payload']
                            if payload['type']=='text.delta':yield packet({'content':payload['text']})
                            elif payload['type']=='generation.finished':
                                finished=True
                                if payload['status']=='completed':
                                    yield packet({},'stop');yield 'data: [DONE]\n\n'
                                else:
                                    yield 'data: '+json.dumps({'error':{'code':'GENERATION_'+payload['status'].upper(),'message':'本轮生成未完成'}})+'\n\n'
                                return
                        await asyncio.sleep(.03)
            finally:
                self.subscribers[key]-=1
                if not self.subscribers[key]:
                    self.subscribers.pop(key,None)
                    if not finished:
                        self.coordinator.interrupt(owner,sid,InterruptRequest(generation_id=gid,client_event_id=new_id()))
        return stream()

    async def interrupt(self,owner,sid,request):
        # Cancel locally before network I/O, even if provider interrupt fails.
        async with self.lock(sid):
            with Session.begin() as db:
                row=owned(db,sid,owner,True);voice=row.data.get('voice',{})
                key=f'{sid}:voice-interrupt:{request.client_event_id}'
                digest,previous=receipt(db,key,request.model_dump(mode='json'))
                if previous:return Interrupted(**previous)
                last=row.data.get('last_provider_turn')
                if request.provider_turn_id is not None and request.provider_turn_id!=last:return Interrupted(applied=False)
                if voice.get('status')!='running':return Interrupted(applied=False)
                if row.data.get('busy'):self.coordinator._invalidate(db,row)
                db.add(RequestReceipt(key=key,payload_hash=digest,data={'applied':True}))
            if self.cancel_tts:self.cancel_tts(sid)
            await self.request('POST',f'agents/{voice["agent_id"]}/interrupt',{})
            return Interrupted(applied=True)

    def heartbeat(self,owner,sid):
        with Session.begin() as db:
            row=owned(db,sid,owner,True);voice=row.data.get('voice',{})
            if voice.get('status')=='running':row.data={**row.data,'voice':{**voice,'heartbeat_at':now().timestamp()}}

    async def stop(self,owner,sid):
        async with self.lock(sid):
            with Session.begin() as db:
                row=owned(db,sid,owner,True);voice=dict(row.data.get('voice',{}))
                if not voice or voice.get('status')=='stopped':return VoiceStartResult(status='stopped')
                row.data={**row.data,'voice':{**voice,'status':'cleanup_pending'}}
            if self.cancel_tts:self.cancel_tts(sid)
            try:
                agent_id=voice.get('agent_id')
                if not agent_id and voice.get('status') in ['starting','cleanup_pending']:
                    result=await self.request('GET','agents?channel='+quote(voice['channel'],safe='')+'&limit=20')
                    # Official list response uses data.list; accept agents for older versions.
                    candidates=result.get('data',{}).get('list',[]) or result.get('agents',[])
                    # The unique server-owned channel is the list filter; list entries
                    # need not include name or channel fields.
                    for candidate in candidates:
                        if candidate.get('agent_id'):await self.request('POST',f'agents/{candidate["agent_id"]}/leave',{})
                    agent_id=candidates[0].get('agent_id') if candidates else None
                    if not agent_id:
                        # Ambiguous creation must remain pending for manual/cloud verification.
                        return VoiceStartResult(status='cleanup_pending')
                elif agent_id:await self.request('POST',f'agents/{agent_id}/leave',{})
            except DomainError:return VoiceStartResult(status='cleanup_pending',agent_id=voice.get('agent_id'))
            with Session.begin() as db:
                row=owned(db,sid,owner,True);row.data={**row.data,'voice':{**voice,'status':'stopped'}}
            return VoiceStartResult(status='stopped',agent_id=voice.get('agent_id'))

    async def status(self,owner,sid):
        with Session() as db:voice=owned(db,sid,owner).data.get('voice',{})
        return VoiceStartResult(status=voice.get('status','stopped') if voice.get('status')!='prepared' else 'stopped',agent_id=voice.get('agent_id'))

    async def startup(self,coordinator):
        self.coordinator=coordinator
        # Previous process's agents must not be silently reused after a restart.
        if self.available():
            with Session() as db:
                previous=[(r.owner,r.id) for r in db.scalars(select(Conversation)) if r.data.get('voice',{}).get('status') in ['running','starting','cleanup_pending','prepared']]
            for owner,sid in previous:
                coordinator.end(owner,sid)
                await self.stop(owner,sid)
        self.worker=asyncio.create_task(self.reap())

    async def reap(self):
        while True:
            await asyncio.sleep(5)
            await self.reap_once()

    async def reap_once(self):
        if not self.available():return
        with Session() as db:
            targets=[(r.owner,r.id) for r in db.scalars(select(Conversation)) if (v:=r.data.get('voice',{})) and
                v.get('status')!='stopped' and (r.data.get('ended') or v.get('expires_at',0)<now().timestamp() or
                v.get('heartbeat_at',0)<now().timestamp()-45 or v.get('status')=='cleanup_pending')]
        for owner,sid in targets:
            self.coordinator.end(owner,sid)
            await self.stop(owner,sid)

    async def shutdown(self):
        if self.worker:
            self.worker.cancel()
            with suppress(asyncio.CancelledError):await self.worker
        if self.available():
            with Session() as db:targets=[(r.owner,r.id) for r in db.scalars(select(Conversation)) if r.data.get('voice',{}).get('status') in ['running','starting','cleanup_pending','prepared']]
            for owner,sid in targets:await self.stop(owner,sid)
