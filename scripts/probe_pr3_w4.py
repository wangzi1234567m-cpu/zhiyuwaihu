"""W4 跨模块验收探针：预期未修的 Mock 将返回非零；不是豁免或成功用例。

仅显式 RUN_DB_TESTS=1 + *_test PostgreSQL，重置指定测试库的合成数据。
先核验魏侧守卫拒绝陈旧命令，再检查翟侧 execute 原子拒绝及历史成功查证。
不修改 MockBusiness；不得对生产数据库执行。
"""
import asyncio,json,os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'backend/tests')]
import httpx
from sqlalchemy import select,text
from app.adapters import Services,DomainError
from app.contracts import ActionCommand
from app.db import Application,Conversation,Effect,Operation,Session,engine
from app.guard import ToolGuard
from app.main import create_app
from app.seed import seed,fixture_id
from test_integration import opened,say


async def probe():
    assert os.getenv('RUN_DB_TESTS')=='1' and (engine.url.database or '').endswith('_test')
    assert os.getenv('MODEL_PROVIDER')=='fake' and os.getenv('CLOUD_CALLS_ENABLED')=='false'
    with Session.begin() as db:
        db.execute(text('TRUNCATE request_receipts,confirmations,call_records,business_effects,operations,events,messages,sessions,knowledge,applications CASCADE'))
    seed();services=Services();app=create_app(services)
    async with app.router.lifespan_context(app):
        app.state.coordinator.operation_worker.cancel()
        await asyncio.gather(app.state.coordinator.operation_worker,return_exceptions=True)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'X-Requested-With':'zhiyu'}) as client:
            assert (await client.post('/api/v1/dev/login',json={'username':'demo','password':'demo-local-only'})).status_code==200
            sid=await opened(client)
            await say(client,sid,'我是演示商家1');await say(client,sid,'明白了')
            with Session() as db:
                op=db.scalar(select(Operation).where(Operation.session_id==sid))
                command=ActionCommand.model_validate(op.data['command'])
                session_data=dict(db.get(Conversation,sid).data)
            with Session.begin() as db:
                application=db.get(Application,fixture_id(1))
                application.data={**application.data,'case_revision':2}
            fresh=services.cases.get_case('demo',command.application_id)
            guard_denied=False
            try:ToolGuard().authorize(fresh,session_data,command,())
            except DomainError as exc:guard_denied=exc.code=='STALE_REVISION'
            execute_denied=False;error_code=None
            try:await services.business.execute(command)
            except DomainError as exc:execute_denied=True;error_code=exc.code
            with Session() as db:stale_effect=db.get(Effect,command.idempotency_key) is not None
            # 独立重置同一专用测试库：先真正成功，再升版，必须仍能查到历史事实。
            with Session.begin() as db:
                db.execute(text('TRUNCATE request_receipts,confirmations,call_records,business_effects,operations,events,messages,sessions,knowledge,applications CASCADE'))
            seed();sid=await opened(client)
            await say(client,sid,'我是演示商家1');await say(client,sid,'明白了')
            with Session() as db:
                op=db.scalar(select(Operation).where(Operation.session_id==sid))
                historical=ActionCommand.model_validate(op.data['command'])
            success=await services.business.execute(historical)
            with Session.begin() as db:
                application=db.get(Application,fixture_id(1))
                application.data={**application.data,'case_revision':2}
            queried=await services.business.query_operation(historical)
    historical_ok=success.status=='succeeded' and queried.status=='succeeded' and queried.resource_id==success.resource_id
    result={'scope':'synthetic_test_only','cloud_calls':0,'wei_guard_denied_stale':guard_denied,
        'zhai_execute_denied_stale':execute_denied,'execute_error_code':error_code,
        'stale_effect_created':stale_effect,'historical_success_query_preserved':historical_ok}
    result['passed']=guard_denied and execute_denied and not stale_effect and historical_ok
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return result

if __name__=='__main__':
    raise SystemExit(0 if asyncio.run(probe())['passed'] else 1)
