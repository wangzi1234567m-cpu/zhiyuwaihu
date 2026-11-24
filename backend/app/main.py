import asyncio
import logging
from contextlib import asynccontextmanager
from contextlib import suppress
from uuid import UUID, uuid4
from fastapi import FastAPI, Request, Depends, Header
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy import select, text
from .config import settings
from .contracts import *
from .db import Session, Application, Operation, EventRow, now
from . import mock_routing
from .knowledge_templates import task_slot
from .adapters import DomainError, Services
from .coordinator import Coordinator, owned, snapshot

log=logging.getLogger('zhiyu')

def create_app(services=None, *, tts=None, realtime_business=None,
               realtime_input_factory=None, realtime_speech_factory=None, realtime_media_factory=None):
    services=services or Services()
    from .tts import QwenTTS, SpeechRequest
    from .cloud_budget import reserve_call
    tts=tts or QwenTTS(reserve=reserve_call)
    services.voice.cancel_tts=tts.cancel_session
    coordinator=Coordinator(services)
    signer=URLSafeTimedSerializer(settings.cookie_secret)
    from .realtime.manager import RealtimeManager
    realtime=RealtimeManager(settings, realtime_business, realtime_input_factory,
                             realtime_speech_factory, realtime_media_factory)

    @asynccontextmanager
    async def lifespan(app):
        await coordinator.startup()
        await services.voice.startup(coordinator)
        try:yield
        finally:
            await realtime.close()
            await services.voice.shutdown()
            await coordinator.shutdown()

    app=FastAPI(title='智语外呼文本联调骨架',version='0.3.0',lifespan=lifespan)
    app.state.services,app.state.coordinator=services,coordinator
    app.state.realtime=realtime

    def error_response(request,code,message,status):
        error=ApiError(code=code,message=message,retryable=status>=500,request_id=request.state.request_id)
        return JSONResponse(error.model_dump(mode='json'),status_code=status)

    @app.middleware('http')
    async def boundary(request,call_next):
        request.state.request_id=str(uuid4())
        if request.method in ['POST','PUT','PATCH','DELETE'] and not request.url.path.startswith('/integrations/'):
            if request.headers.get('x-requested-with')!='zhiyu' or (request.headers.get('origin') and request.headers['origin']!=settings.public_origin):
                return error_response(request,'CSRF_REJECTED','请求来源不允许',403)
        response=await call_next(request)
        response.headers['X-Request-ID']=request.state.request_id
        response.headers['Cache-Control']='no-store, no-transform' if response.headers.get('content-type','').startswith('text/event-stream') else 'no-store'
        # Do not log bodies, cookies, queries, API keys or raw exception text.
        log.info('request method=%s status=%s request_id=%s',request.method,response.status_code,request.state.request_id)
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request,exc):
        return error_response(request,exc.code,exc.message,exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request,exc):
        return error_response(request,'VALIDATION_ERROR','请求参数不符合公共契约',422)

    @app.exception_handler(Exception)
    async def unexpected(request,exc):
        log.error('request_failed kind=%s request_id=%s',type(exc).__name__,request.state.request_id)
        return error_response(request,'INTERNAL_ERROR','服务暂时不可用',503)

    def owner(request: Request):
        try:
            value=signer.loads(request.cookies.get('zhiyu_session',''),max_age=8*3600)
            if value.get('owner') not in ['demo','other']: raise BadSignature('owner')
            return value['owner']
        except (BadSignature,SignatureExpired):
            raise DomainError('UNAUTHENTICATED','请先登录演示环境',401)

    def key(value: str | None):
        if not value or len(value)>100: raise DomainError('INVALID_IDEMPOTENCY_KEY','需要长度不超过100的Idempotency-Key',422)
        return value

    async def authorize_realtime(user, sid):
        # Entire short transaction stays in one worker thread, never blocks audio tasks.
        def check():
            with Session() as db:
                row=owned(db,str(sid),user)
                if row.data.get('voice_transport')!='native_webrtc' or row.data.get('ended'):
                    raise DomainError('WRONG_INPUT_MODE','不是活动实时会话',409)
                if not realtime.allowed_application(row.application_id):
                    raise DomainError('REALTIME_NOT_ALLOWED','申请单未在实时白名单中',403)
        await asyncio.to_thread(check)

    from .realtime.routes import mount_realtime
    mount_realtime(app,realtime,owner,authorize_realtime,settings.public_origin)

    @app.get('/health/live')
    def live(): return {'status':'ok'}

    @app.get('/health/ready')
    def ready():
        with Session() as db:
            db.execute(text('SELECT 1 FROM alembic_version LIMIT 1'))
        return {'status':'ready'}

    @app.post('/api/v1/dev/login',response_model=LoginResult)
    def login(body: LoginRequest):
        if settings.environment not in ['development','test']:
            raise DomainError('DEV_LOGIN_DISABLED','开发登录未开启',404)
        import secrets
        if not secrets.compare_digest(body.password,settings.demo_password):
            raise DomainError('BAD_CREDENTIALS','登录失败',401)
        response=JSONResponse({'owner':body.username})
        response.set_cookie('zhiyu_session',signer.dumps({'owner':body.username}),httponly=True,samesite='strict',
            secure=settings.public_origin.startswith('https://'),max_age=8*3600)
        return response

    @app.post('/api/v1/auth/login',response_model=LoginResult)
    def staging_login(body: LoginRequest):
        import secrets
        if settings.environment in ['development','test']:return login(body)
        if settings.environment!='staging' or not settings.public_origin.startswith('https://') or len(settings.staging_password)<16 or len(settings.cookie_secret)<32 or settings.cookie_secret.startswith('local-'):
            raise DomainError('STAGING_LOGIN_DISABLED','公网联调登录尚未安全配置',503)
        if body.username!='demo' or not secrets.compare_digest(body.password,settings.staging_password):
            raise DomainError('BAD_CREDENTIALS','登录失败',401)
        response=JSONResponse({'owner':'demo'})
        response.set_cookie('zhiyu_session',signer.dumps({'owner':'demo'}),httponly=True,samesite='strict',secure=True,max_age=3600)
        return response

    @app.get('/api/v1/capabilities',response_model=Capabilities)
    def capabilities(user=Depends(owner)):
        return Capabilities(realtime_webrtc=realtime.unavailable_reason() is None,
            realtime_unavailable_reason=realtime.unavailable_reason(),
            routing_preview=settings.environment in ('development', 'test'), voice=services.voice.available(),model=settings.model_provider,
            voice_unavailable_reason=services.voice.unavailable_reason())

    def demo_catalog(user=Depends(owner)):
        if settings.environment not in ('development', 'test'):
            raise DomainError('MOCK_DISABLED', '仅演示环境可用', 404)
        return mock_routing.catalog()

    @app.get('/api/v1/dev/routing', response_model=mock_routing.Catalog)
    def routing_catalog(data=Depends(demo_catalog)):
        return data

    @app.post('/api/v1/dev/routing', response_model=mock_routing.Outcome)
    def preview_routing(body: mock_routing.Conditions, data=Depends(demo_catalog)):
        scenario = next((s for s in data.scenarios if s.row == body.row), None)
        if scenario is None:
            raise DomainError('UNKNOWN_SCENARIO', '场景不存在', 422)
        return mock_routing.evaluate(services.policy, scenario, body)

    @app.get('/api/v1/applications',response_model=list[ApplicationView])
    def applications(user=Depends(owner)):
        with Session() as db:
            return [ApplicationView(application_id=a.id,label=f'合成申请单 {i+1}',company_hint=task_slot(a.data['slots'], 'company_name') or '待核实主体')
                for i,a in enumerate(db.scalars(select(Application).where(Application.owner==user).order_by(Application.id)))]

    @app.post('/api/v1/sessions',response_model=SessionView,status_code=201)
    def create(body: CreateSession,user=Depends(owner),idempotency_key: str | None=Header(default=None)):
        if body.voice_transport=='native_webrtc':
            realtime.require()
            if not realtime.allowed_application(body.application_id):
                raise DomainError('REALTIME_NOT_ALLOWED','申请单未在实时白名单中',403)
        return coordinator.create(user,body,key(idempotency_key),realtime_available=body.voice_transport=='native_webrtc')

    @app.get('/api/v1/sessions/{sid}',response_model=SessionView)
    def get_session(sid: UUID,user=Depends(owner)):
        with Session.begin() as db: return snapshot(db,owned(db,str(sid),user,True))

    @app.post('/api/v1/sessions/{sid}/start',response_model=AcceptedTurn,status_code=202)
    async def start(sid: UUID,body: StartSession,user=Depends(owner),idempotency_key: str | None=Header(default=None)):
        return await coordinator.accept(user,str(sid),'__opening__',key(idempotency_key),start_revision=body.expected_revision)

    @app.post('/api/v1/sessions/{sid}/turns',response_model=AcceptedTurn,status_code=202)
    async def turn(sid: UUID,body: TextTurn,user=Depends(owner)):
        # client_event_id is the durable idempotency key for this endpoint.
        import re
        text_value=re.sub(r'(?<!\d)\d{11,18}(?!\d)','[已脱敏号码]',body.text)
        return await coordinator.accept(user,str(sid),text_value,str(body.client_event_id))

    @app.post('/api/v1/sessions/{sid}/interruptions',response_model=Interrupted)
    async def interrupt(sid: UUID,body: InterruptRequest,user=Depends(owner)):
        return coordinator.interrupt(user,str(sid),body)

    @app.post('/api/v1/sessions/{sid}/end',response_model=SessionView)
    async def end(sid: UUID,body: EndRequest,user=Depends(owner)):
        result=coordinator.end(user,str(sid))
        if result.voice_transport=='native_webrtc':await realtime.close_session(sid)
        elif result.mode=='voice':await services.voice.stop(user,str(sid))
        return result

    @app.get('/api/v1/operations/{oid}',response_model=OperationResult)
    def operation(oid: UUID,user=Depends(owner)):
        with Session() as db:
            op=db.get(Operation,str(oid))
            if op is None: raise DomainError('NOT_FOUND','操作不存在',404)
            owned(db,op.session_id,user)
            return OperationResult.model_validate(op.data['result'])

    @app.get('/api/v1/sessions/{sid}/events',responses={200:{'content':{'text/event-stream':{'schema':{'type':'string'}}}}})
    async def events(sid: UUID,request: Request,after_seq: int=0,last_event_id: str | None=Header(default=None),user=Depends(owner)):
        try: cursor=int(last_event_id) if last_event_id else after_seq
        except ValueError: raise DomainError('BAD_CURSOR','游标无效',422)
        with Session() as db:
            row=owned(db,str(sid),user)
            if cursor<0 or cursor>row.seq: raise DomainError('BAD_CURSOR','请重新读取会话快照',409)
        async def stream():
            nonlocal cursor
            ticks=0
            while not await request.is_disconnected():
                with Session() as db:
                    rows=list(db.scalars(select(EventRow).where(EventRow.session_id==str(sid),EventRow.seq>cursor).order_by(EventRow.seq).limit(100)))
                    batch=[(r.seq,r.data) for r in rows]
                import json
                for seq,value in batch:
                    cursor=seq
                    yield f'id: {seq}\nevent: {value["payload"]["type"]}\ndata: {json.dumps(value,ensure_ascii=False)}\n\n'
                ticks+=1
                if ticks%75==0: yield ': heartbeat\n\n'
                await asyncio.sleep(.2)
        return StreamingResponse(stream(),media_type='text/event-stream',headers={'X-Accel-Buffering':'no'})

    @app.post('/api/v1/sessions/{sid}/credentials',response_model=MediaCredentials)
    async def credentials(sid: UUID,user=Depends(owner)):
        with Session() as db: owned(db,str(sid),user)
        return await services.voice.credentials(str(sid))

    @app.post('/api/v1/sessions/{sid}/voice/start',response_model=VoiceStartResult)
    async def voice_start(sid: UUID,body: StartSession,user=Depends(owner)):
        return await services.voice.start(user,str(sid),body.expected_revision)

    @app.post('/api/v1/sessions/{sid}/voice/interrupt',response_model=Interrupted)
    async def voice_interrupt(sid: UUID,body: VoiceInterruptRequest,user=Depends(owner)):
        return await services.voice.interrupt(user,str(sid),body)

    @app.post('/api/v1/sessions/{sid}/voice/heartbeat')
    def voice_heartbeat(sid: UUID,body: VoiceHeartbeat,user=Depends(owner)):
        services.voice.heartbeat(user,str(sid))
        return {'accepted':True}

    @app.get('/api/v1/sessions/{sid}/voice/status',response_model=VoiceStartResult)
    async def voice_status(sid: UUID,user=Depends(owner)):
        return await services.voice.status(user,str(sid))

    @app.post('/api/v1/sessions/{sid}/voice/telemetry',status_code=202)
    def voice_telemetry(sid: UUID,body: VoiceTelemetry,user=Depends(owner)):
        from .coordinator import receipt
        from .db import RequestReceipt
        with Session.begin() as db:
            row=owned(db,str(sid),user,True)
            if row.data.get('mode')!='voice':raise DomainError('WRONG_INPUT_MODE','不是语音会话')
            key=f'{sid}:telemetry:{body.client_event_id}'
            digest,old=receipt(db,key,body.model_dump(mode='json'))
            if old is None:
                db.add(RequestReceipt(key=key,payload_hash=digest,data={'accepted':True,'observation':body.model_dump(mode='json'),'received_at':now().isoformat()}))
                # Raw client observations never change identity, confirmation, playback facts or safety guards.
                row.data={**row.data,'last_client_observation':body.model_dump(mode='json')}
                if body.kind=='interrupted' and body.provider_turn_id is not None and body.provider_turn_id==row.data.get('last_provider_turn') and row.data.get('busy'):
                    coordinator._invalidate(db,row)
        return {'accepted':True}

    @app.post('/integrations/agora/sessions/{sid}/v1/chat/completions')
    async def agora_completions(sid: UUID,request: Request,authorization: str | None=Header(default=None)):
        from .voice import ProviderRequest
        if not services.voice.available():raise DomainError('VOICE_NOT_CONFIGURED',services.voice.unavailable_reason(),503)
        # Bound body size before parsing untrusted cloud requests.
        services.voice.authorize_provider(str(sid),authorization)
        data=bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data)>128*1024:raise DomainError('BODY_TOO_LARGE','语音请求过大',413)
        from pydantic import ValidationError
        try:body=ProviderRequest.model_validate_json(data)
        except ValidationError:raise DomainError('BAD_PROVIDER_REQUEST','声网请求格式不正确',422)
        stream=await services.voice.completion(str(sid),body,authorization)
        return StreamingResponse(stream,media_type='text/event-stream',headers={'X-Accel-Buffering':'no'})

    @app.post('/integrations/agora/sessions/{sid}/v1/audio/speech',response_class=StreamingResponse,
        openapi_extra={'requestBody':{'required':True,'content':{'application/json':{'schema':SpeechRequest.model_json_schema()}}},
            'responses':{'200':{'description':'24kHz mono PCM16 audio stream','content':{'audio/pcm':{'schema':{'type':'string','format':'binary'}}}}}})
    async def agora_speech(sid: UUID,request: Request,authorization: str | None=Header(default=None)):
        from .tts import SpeechRequest
        from pydantic import ValidationError
        services.voice.authorize_tts(str(sid),authorization)
        data=bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data)>16*1024:raise DomainError('BODY_TOO_LARGE','语音合成请求过大',413)
        try:body=SpeechRequest.model_validate_json(data)
        except ValidationError:raise DomainError('BAD_TTS_REQUEST','语音合成参数不正确',422)
        cancel=asyncio.Event()
        tts.register(str(sid),cancel)
        async def watch_disconnect():
            while not await request.is_disconnected():await asyncio.sleep(.05)
            cancel.set()
        watcher=asyncio.create_task(watch_disconnect())
        stream=tts.stream(body,cancel)
        # Fetch first audio before sending 200 so upstream auth/quota errors stay JSON errors.
        try:
            try:first=await anext(stream)
            except StopAsyncIteration:raise DomainError('TTS_EMPTY','音频生成取消或无输出',503)
        except BaseException:
            await stream.aclose()
            tts.unregister(str(sid),cancel)
            raise
        finally:
            watcher.cancel()
            with suppress(asyncio.CancelledError):await watcher
        async def content():
            try:
                yield first
                async for chunk in stream:yield chunk
            finally:
                cancel.set()
                await stream.aclose()
                tts.unregister(str(sid),cancel)
        return StreamingResponse(content(),media_type='audio/pcm',headers={
            'X-Accel-Buffering':'no','X-Audio-Sample-Rate':'24000','X-Audio-Channels':'1'})

    if settings.faults_enabled and settings.environment in ['development','test']:
        class FaultRequest(DTO):
            fault: Literal['none','write_failure','lost_response','persistent_unknown','writeback_failure','query_timeout','query_failure']
        @app.post('/api/v1/dev/faults')
        def set_fault(body: FaultRequest,user=Depends(owner)):
            if user!='demo': raise DomainError('FORBIDDEN','禁止故障注入',403)
            services.business.fault=body.fault
            return {'fault':body.fault}

    return app

app=create_app()
