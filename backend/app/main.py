import asyncio
import logging
from contextlib import asynccontextmanager
from uuid import UUID, uuid4
from fastapi import FastAPI, Request, Depends, Header
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy import select, text
from .config import settings
from . import mock_routing
from .contracts import *
from .db import Session, Application, Operation, EventRow, now
from .adapters import DomainError, Services
from .coordinator import Coordinator, owned, snapshot

log=logging.getLogger('zhiyu')

def create_app(services=None):
    services=services or Services()
    coordinator=Coordinator(services)
    signer=URLSafeTimedSerializer(settings.cookie_secret)

    @asynccontextmanager
    async def lifespan(app):
        await coordinator.startup()
        yield
        await coordinator.shutdown()

    app=FastAPI(title='智语外呼文本联调骨架',version='0.3.0',lifespan=lifespan)
    app.state.services,app.state.coordinator=services,coordinator

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

    @app.get('/api/v1/capabilities',response_model=Capabilities)
    def capabilities(user=Depends(owner)): return Capabilities(model=services.model_mode)

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
        return mock_routing.evaluate(scenario.channel, body.submission, body.missed, body.callback, body.risk)

    @app.get('/api/v1/applications',response_model=list[ApplicationView])
    def applications(user=Depends(owner)):
        with Session() as db:
            return [ApplicationView(application_id=a.id,label=f'合成申请单 {i+1}',company_hint=a.data['slots']['company_name'])
                for i,a in enumerate(db.scalars(select(Application).where(Application.owner==user).order_by(Application.id)))]

    @app.post('/api/v1/sessions',response_model=SessionView,status_code=201)
    def create(body: CreateSession,user=Depends(owner),idempotency_key: str | None=Header(default=None)):
        return coordinator.create(user,body,key(idempotency_key))

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
        return coordinator.end(user,str(sid))

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

    @app.post('/api/v1/sessions/{sid}/credentials')
    async def credentials(sid: UUID,user=Depends(owner)):
        with Session() as db: owned(db,str(sid),user)
        return await services.voice.credentials(str(sid))

    @app.post('/integrations/agora/sessions/{sid}/v1/chat/completions')
    async def disabled_agora(sid: UUID):
        raise DomainError('VOICE_NOT_CONFIGURED','声网适配尚未配置',503)

    if settings.faults_enabled and settings.environment in ['development','test']:
        class FaultRequest(DTO):
            fault: Literal['none','write_failure','lost_response','persistent_unknown','writeback_failure']
        @app.post('/api/v1/dev/faults')
        def set_fault(body: FaultRequest,user=Depends(owner)):
            if user!='demo': raise DomainError('FORBIDDEN','禁止故障注入',403)
            services.business.fault=body.fault
            return {'fault':body.fault}

    return app

app=create_app()
