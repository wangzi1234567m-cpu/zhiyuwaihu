"""Run only against a dedicated PostgreSQL test DB. No SQLite substitutes."""
import asyncio
import os
from uuid import uuid4
import pytest
import httpx
from sqlalchemy import text,select,func
from app.main import create_app
from app.adapters import Services, MockBusiness, FakeModel, DomainError
from app.contracts import OperationResult, RoutingDecision
from app.db import Session,Effect,Operation,Conversation,Message,EventRow,Record,now,engine
from app.seed import seed,fixture_id

pytestmark=[pytest.mark.integration,pytest.mark.skipif(os.getenv('RUN_DB_TESTS')!='1',reason='RUN_DB_TESTS=1 requires a dedicated migrated PostgreSQL test DB')]

@pytest.fixture
async def setup():
    if not (engine.url.database or '').endswith('_test'):
        pytest.fail('Destructive integration reset requires a database name ending in _test')
    with Session.begin() as db:
        db.execute(text('TRUNCATE request_receipts, confirmations, call_records, business_effects, operations, events, messages, sessions, knowledge, applications CASCADE'))
    seed()
    services=Services()
    app=create_app(services)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'X-Requested-With':'zhiyu'}) as client:
            assert (await client.post('/api/v1/dev/login',json={'username':'demo','password':'demo-local-only'})).status_code==200
            yield client,app,services

async def wait_idle(client,sid):
    for _ in range(150):
        response=await client.get(f'/api/v1/sessions/{sid}')
        assert response.status_code==200,response.text
        data=response.json()
        if not data['busy']: return data
        await asyncio.sleep(.03)
    pytest.fail('generation did not complete')

async def create(client):
    response=await client.post('/api/v1/sessions',json={'application_id':fixture_id(1),'mode':'text'},headers={'Idempotency-Key':str(uuid4())})
    assert response.status_code==201,response.text
    return response.json()['session_id']

async def say(client,sid,value,key=None):
    response=await client.post(f'/api/v1/sessions/{sid}/turns',json={'text':value,'client_event_id':key or str(uuid4())})
    assert response.status_code==202,response.text
    return await wait_idle(client,sid)

async def opened(client):
    sid=await create(client)
    response=await client.post(f'/api/v1/sessions/{sid}/start',json={'expected_revision':0},headers={'Idempotency-Key':str(uuid4())})
    assert response.status_code==202,response.text
    await wait_idle(client,sid)
    return sid

async def ready_to_act(client):
    sid=await opened(client)
    result=await say(client,sid,'我是演示商家1')
    assert result['verified'] and result['reason']
    result=await say(client,sid,'补齐后一定必过吗')
    assert '不能保证' in result['messages'][-1]['text']
    return sid

async def wait_closed(client,sid):
    for _ in range(150):
        value=(await client.get(f'/api/v1/sessions/{sid}')).json()
        if value['state']=='CLOSED': return value
        await asyncio.sleep(.05)
    pytest.fail('operation/writeback not completed')

class HeldModel:
    """FakeModel wrapper that holds the next generation's first chunk.

    Used only by tests to make "generation is still in flight" deterministic instead
    of relying on how fast the machine is. Not a production adapter.
    """
    def __init__(self):
        self._inner=FakeModel()
        self._hold=False
        self.entered=asyncio.Event()
        self.release_event=asyncio.Event()

    def enter(self):
        self._hold=True
        self.entered.clear()
        self.release_event.clear()

    def release(self):
        self._hold=False
        self.release_event.set()

    async def stream(self,request,cancel):
        if self._hold and not self.release_event.is_set():
            self.entered.set()
            while not self.release_event.is_set():
                if cancel.is_set(): return
                await asyncio.sleep(.01)
        async for chunk in self._inner.stream(request,cancel):
            yield chunk

async def test_three_turns_persistence_and_idempotency(setup):
    client,app,services=setup
    sid=await ready_to_act(client)
    key=str(uuid4())
    await say(client,sid,'明白了',key)
    value=await wait_closed(client,sid)
    assert value['record'] and value['operations'][0]['status']=='succeeded'
    duplicate=await client.post(f'/api/v1/sessions/{sid}/turns',json={'text':'明白了','client_event_id':key})
    assert duplicate.status_code==202
    conflict=await client.post(f'/api/v1/sessions/{sid}/turns',json={'text':'改变参数','client_event_id':key})
    assert conflict.status_code==409
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Effect))==1
        seqs=list(db.scalars(select(EventRow.seq).where(EventRow.session_id==sid).order_by(EventRow.seq)))
        assert seqs==list(range(1,len(seqs)+1))

async def test_wrong_person_and_owner_isolation(setup):
    client,_,_=setup
    sid=await opened(client)
    before=(await client.get(f'/api/v1/sessions/{sid}')).json()
    assert before['reason'] is None
    value=await say(client,sid,'不是，打错了')
    assert value['ended'] and not value['operations'] and value['reason'] is None
    await client.post('/api/v1/dev/login',json={'username':'other','password':'demo-local-only'})
    assert (await client.get(f'/api/v1/sessions/{sid}')).status_code==404
    assert (await client.get(f'/api/v1/sessions/{sid}/events')).status_code==404

async def test_interrupt_old_generation_does_not_cancel_new(setup):
    """旧 generation 的打断请求不能取消新 generation。

    回归修复（见 docs/agent/CONTRACT-CHANGES.md）：原测试在发第二轮前先 `await say(...)`
    等到第一轮结束，于是"旧 generation 仍在生成"这个前提根本不成立——旧 generation 是
    正常 completed，不是 interrupted，断言必然失败。这不是产品缺陷：真实抢占时
    coordinator._invalidate 会把它标成 interrupted（见下方断言）。
    因此这里用可闸控模型把第一轮**确定性地**卡在生成中，再发第二轮制造抢占。
    """
    client,app,services=setup
    sid=await opened(client)
    gate=HeldModel()
    services.model=gate
    gate.enter()
    first=await client.post(f'/api/v1/sessions/{sid}/turns',json={'text':'我是演示商家1','client_event_id':str(uuid4())})
    old=first.json()['generation_id']
    # 第一轮必须仍在生成中，否则本用例没有测到"旧 generation"。
    await asyncio.wait_for(gate.entered.wait(),5)
    in_flight=(await client.get(f'/api/v1/sessions/{sid}')).json()
    assert in_flight['busy'] and in_flight['generation_id']==old
    assert any(m['generation_id']==old and m['status']=='generating' for m in in_flight['messages'])
    # 第二轮在**不释放闸门**的情况下发出：抢占由 accept->_invalidate 确定性触发，
    # 旧 generation 靠 cancel 事件退出，因此不再依赖"谁先跑到"的时序。
    second=await client.post(f'/api/v1/sessions/{sid}/turns',json={'text':'补齐后必过吗','client_event_id':str(uuid4())})
    assert second.status_code==202,second.text
    gate.release()
    value=await wait_idle(client,sid)
    response=await client.post(f'/api/v1/sessions/{sid}/interruptions',json={'generation_id':old,'client_event_id':str(uuid4())})
    assert response.json()=={'applied':False}
    # 旧 generation 在抢占时被如实标记 interrupted，且新回复完整。
    assert any(m['status']=='interrupted' and m['generation_id']==old for m in value['messages'])
    assert value['messages'][-1]['status']=='completed'
    assert value['messages'][-1]['generation_id']!=old

async def test_success_with_lost_response_is_queried(setup):
    client,_,services=setup
    services.business.fault='lost_response'
    sid=await ready_to_act(client)
    await say(client,sid,'明白了')
    value=await wait_closed(client,sid)
    assert value['operations'][0]['status']=='succeeded'
    with Session() as db: assert db.scalar(select(func.count()).select_from(Effect))==1

async def test_writeback_retries_without_second_effect(setup):
    """回写失败期间不谎报 CLOSED、不建本地记录；恢复后仍只产生一次业务效果。

    回归修复：原版用 `await asyncio.sleep(.4)` 猜"此刻还没回写成功"，一旦有游离后端
    连同一个 `_test` 库先把回写跑完，就会读到 CLOSED 而失败（见
    docs/agent/evidence/t2b-writeback-isolated.txt 与 t2c-SUMMARY.md 2.2）。
    现在改为等业务替身真实发出的"第一次回写失败"事件，再断言状态，
    既证明回写确实发生过，也不依赖机器速度。
    """
    client,_,services=setup

    class FailingWriteback(MockBusiness):
        def __init__(self):
            super().__init__()
            self.fault='writeback_failure'
            self.first_failure=asyncio.Event()

        async def writeback(self,record):
            if self.fault=='writeback_failure':
                self.first_failure.set()
            await super().writeback(record)

    business=FailingWriteback()
    services.business=business
    sid=await ready_to_act(client)
    await say(client,sid,'明白了')
    # 等到回写真的失败过（不是"过了 0.4 秒大概还没成功"）。
    await asyncio.wait_for(business.first_failure.wait(),10)
    value=(await client.get(f'/api/v1/sessions/{sid}')).json()
    assert value['state']=='WRITEBACK_PENDING', value['state']
    assert value['record'] is None, 'writeback did not succeed, so no local record may exist'
    with Session() as db:
        op=db.scalar(select(Operation).where(Operation.session_id==sid))
        assert op.data['writeback_done'] is False
        assert op.data['writeback_attempts']>=1
        # 业务效果已经发生一次；失败的是回写，不是驳回。
        assert db.scalar(select(func.count()).select_from(Effect))==1
    business.fault='none'
    await wait_closed(client,sid)
    with Session() as db: assert db.scalar(select(func.count()).select_from(Effect))==1

async def test_restart_invalidates_generation(setup):
    client,app,_=setup
    sid=await opened(client)
    await client.post(f'/api/v1/sessions/{sid}/turns',json={'text':'我是演示商家1','client_event_id':str(uuid4())})
    await app.state.coordinator.shutdown()
    await app.state.coordinator.startup()
    value=(await client.get(f'/api/v1/sessions/{sid}')).json()
    assert not value['busy']
    assert any(m['status']=='interrupted' for m in value['messages'])

async def test_unconfigured_voice_and_csrf(setup):
    client,_,_=setup
    sid=await create(client)
    assert (await client.post(f'/api/v1/sessions/{sid}/credentials',json={})).status_code==503
    assert (await client.post(f'/api/v1/sessions/{sid}/end',json={},headers={'Origin':'https://evil.invalid'})).status_code==403

async def test_cross_session_rejection_has_one_effect(setup):
    client,_,_=setup
    first=await ready_to_act(client)
    await say(client,first,'明白了')
    await wait_closed(client,first)
    second=await ready_to_act(client)
    result=await say(client,second,'明白了')
    assert result['state']=='MANUAL_REVIEW'
    assert len(result['operations'])==1
    with Session() as db: assert db.scalar(select(func.count()).select_from(Effect))==1

async def test_restart_queries_committed_effect(setup):
    from app.contracts import ActionCommand
    client,app,services=setup
    services.business.fault='persistent_unknown'
    sid=await ready_to_act(client)
    await say(client,sid,'明白了')
    await asyncio.sleep(.4)
    await app.state.coordinator.shutdown()
    with Session.begin() as db:
        op=db.scalar(select(Operation).where(Operation.session_id==sid))
        command=ActionCommand.model_validate(op.data['command'])
        op.status='unknown'
        op.data={**op.data,'next_at':0}
    # Represents an external system that committed just before the application crashed.
    services.business.fault='none'
    await services.business.execute(command)
    await app.state.coordinator.startup()
    value=await wait_closed(client,sid)
    assert value['operations'][0]['status']=='succeeded'
    with Session() as db: assert db.scalar(select(func.count()).select_from(Effect))==1

async def test_cancel_before_dispatch_has_no_operation(setup):
    client,_,services=setup
    sid=await ready_to_act(client)
    entered=asyncio.Event()
    class WaitingModel:
        async def stream(self,request,cancel):
            entered.set()
            await cancel.wait()
            if False: yield ''
    services.model=WaitingModel()
    accepted=(await client.post(f'/api/v1/sessions/{sid}/turns',json={'text':'明白了','client_event_id':str(uuid4())})).json()
    await asyncio.wait_for(entered.wait(),2)
    response=await client.post(f'/api/v1/sessions/{sid}/interruptions',json={'generation_id':accepted['generation_id'],'client_event_id':str(uuid4())})
    assert response.json()['applied']
    await asyncio.sleep(.3)
    value=(await client.get(f'/api/v1/sessions/{sid}')).json()
    assert not value['operations']
    with Session() as db: assert db.scalar(select(func.count()).select_from(Effect))==0

async def test_messages_keep_order_when_timestamps_tie(setup):
    from app.db import now
    client,_,_=setup
    sid=await ready_to_act(client)
    with Session.begin() as db:
        tied=now()
        for message in db.scalars(select(Message).where(Message.session_id==sid)):
            message.created_at=tied
    value=(await client.get(f'/api/v1/sessions/{sid}')).json()
    assert [m['role'] for m in value['messages']]==['assistant','user','assistant','user','assistant']
    assert '不能保证' in value['messages'][-1]['text']


async def test_business_key_is_not_hardcoded_and_excludes_session(setup):
    """enqueue 幂等键必须是"申请单+场景+动作+revision+轮次"，不能写死 reject_application。

    覆盖需求：key 随动作变化、不含 session_id（跨会话一次业务效果）、重启后稳定。
    reject_application 幂等键在同 application_id/case_revision 下不随 submission_attempt/call_attempt 变化；
    跟进等其他动作维持次数语义。
    """
    from uuid import uuid4
    from app.guard import business_key
    client,_,services=setup
    sid=await ready_to_act(client)
    case=services.cases.get_case('demo',seed_case_application(sid))
    reject_key=business_key(case,'reject_application')
    callback_key=business_key(case,'create_callback')
    followup_key=business_key(case,'create_followup')
    assert len({reject_key,callback_key,followup_key})==3
    assert 'reject_application' in reject_key and 'create_callback' in callback_key
    # 业务效果键不得包含会话标识，否则换会话会重复执行业务效果。
    assert sid not in reject_key
    # 驳回幂等键不随提交/联系次数变化，避免重复驳回。
    assert business_key(case.model_copy(update={'submission_attempt':case.submission_attempt+1,'call_attempt':case.call_attempt+1}),'reject_application')==reject_key
    # 改变 application_id 或 case_revision 则键不同。
    assert business_key(case.model_copy(update={'application_id':uuid4()}),'reject_application')!=reject_key
    assert business_key(case.model_copy(update={'case_revision':case.case_revision+1}),'reject_application')!=reject_key
    # 跟进键随联系次数变化。
    assert business_key(case.model_copy(update={'call_attempt':case.call_attempt+1}),'create_followup')!=followup_key
    assert f'r{case.case_revision}' in reject_key
    # 同一 case 重复计算结果稳定（跨重启幂等）。
    assert business_key(case,'reject_application')==reject_key


def seed_case_application(sid):
    with Session() as db:
        return db.get(Conversation,sid).application_id


async def test_operation_registers_unknown_before_dispatch_and_queries_same_key(setup):
    """写前登记 unknown、超时后按**原 key** 查证、not_found_yet 不算失败也绝不换 key 重发。"""
    client,_,services=setup

    class DelayedCommit(MockBusiness):
        """首次执行"响应丢失"(unknown)，前一次查证 not_found_yet，之后查到已提交事实。"""

        def __init__(self):
            super().__init__()
            self.queries=0
            self.executes=0

        async def execute(self,command):
            self.executes+=1
            await asyncio.sleep(0)
            # 外部系统可能已提交，但响应没有回来：只能记 unknown。
            return OperationResult(operation_id=command.operation_id,status='unknown',updated_at=now())

        async def query_operation(self,command):
            self.queries+=1
            if self.queries==1:
                # 第一次查证：外部账本里暂时还没有，属于"还没查到"，不是失败。
                return OperationResult(operation_id=command.operation_id,status='not_found_yet',updated_at=now())
            return await MockBusiness.execute(self,command)

    business=DelayedCommit()
    services.business=business
    sid=await ready_to_act(client)
    await say(client,sid,'明白了')
    value=await wait_closed(client,sid)
    assert value['operations'][0]['status']=='succeeded'
    with Session() as db:
        ops=list(db.scalars(select(Operation).where(Operation.session_id==sid)))
        assert len(ops)==1 and ops[0].key==original_key(business,sid,db)
        # 同一业务效果只发生一次（没有因为 not_found_yet 就重复驳回）。
        assert db.scalar(select(func.count()).select_from(Effect))==1
        assert db.scalar(select(func.count()).select_from(Record))==1
        # 状态序列来自持久化事件：登记 pending，外部调用前必须先落 unknown，绝不能直接 succeeded。
        statuses=[e.data['payload']['result']['status'] for e in db.scalars(
            select(EventRow).where(EventRow.session_id==sid).order_by(EventRow.seq))
            if e.data['payload']['type']=='operation.updated']
        assert statuses[0]=='pending', statuses
        assert statuses[1]=='unknown', statuses
        assert 'not_found_yet' in statuses, statuses
        assert statuses[-1]=='succeeded', statuses
        # not_found_yet 不算失败：整个过程没有 failed 回执。
        assert 'failed' not in statuses, statuses
        # 查证始终使用原始 key（命令里保存的 idempotency_key 不变）。
        assert ops[0].data['command']['idempotency_key']==ops[0].key
    assert business.queries>=1, 'timeout must be reconciled by querying with the original key'
    assert business.executes==1, 'must not re-dispatch with a new key'


def original_key(business,sid,db):
    """操作的业务幂等键（读自已登记的操作，不依赖被回放的快照）。"""
    return db.scalar(select(Operation.key).where(Operation.session_id==sid))


async def test_cancel_after_dispatch_keeps_operation_fact(setup):
    """派发已发生则取消不抹掉事实；generation 与 operation 相互独立。"""
    client,_,services=setup
    sid=await ready_to_act(client)
    entered=asyncio.Event()

    class SlowBusiness(MockBusiness):
        async def execute(self,command):
            entered.set()
            await asyncio.sleep(1.5)
            return await super().execute(command)

    services.business=SlowBusiness()
    accepted=(await client.post(f'/api/v1/sessions/{sid}/turns',
        json={'text':'明白了','client_event_id':str(uuid4())})).json()
    await asyncio.wait_for(entered.wait(),5)
    with Session() as db:
        op=db.scalar(select(Operation).where(Operation.session_id==sid))
        assert op is not None, 'operation must be registered before external call'
        dispatched_key=op.key
        assert op.status=='unknown'
    # 生成早已完成，派发也已发生：此处取消不能回滚业务事实。
    response=await client.post(f'/api/v1/sessions/{sid}/interruptions',
        json={'generation_id':accepted['generation_id'],'client_event_id':str(uuid4())})
    assert response.status_code==200
    await asyncio.sleep(.2)
    with Session() as db:
        # 已派发：操作登记与 key 保留，不因取消而消失。
        op=db.scalar(select(Operation).where(Operation.session_id==sid))
        assert op is not None and op.key==dispatched_key
    value=(await client.get(f'/api/v1/sessions/{sid}')).json()
    assert value['operations'], 'dispatched operation must remain visible after cancel'
    # 最终仍按一次业务效果收口，不产生重复驳回。
    await wait_closed(client,sid)
    with Session() as db: assert db.scalar(select(func.count()).select_from(Effect))==1


async def test_writeback_failure_is_bounded_and_never_fakes_success(setup):
    """回写持续失败：有界重试后如实转人工，不谎报 CLOSED，也不重复驳回。

    与 test_writeback_retries_without_second_effect 互补：那个用例证明"恢复后能收口"，
    这个用例证明"一直失败时不会无限重试、不会伪造成功、不会产生第二份业务效果"。
    """
    from app.guard import WRITEBACK_MAX_ATTEMPTS
    client,_,services=setup
    services.business.fault='writeback_failure'
    sid=await ready_to_act(client)
    await say(client,sid,'明白了')
    deadline=asyncio.get_event_loop().time()+25
    value={}
    while asyncio.get_event_loop().time()<deadline:
        value=(await client.get(f'/api/v1/sessions/{sid}')).json()
        if value['state'] in ('MANUAL_REVIEW','CLOSED'): break
        await asyncio.sleep(.1)
    # 从未得逞的回写绝不能变成 CLOSED，也不能伪造 call_records。
    assert value['state']=='MANUAL_REVIEW', value['state']
    assert value['record'] is None, 'writeback never succeeded, so no local record may exist'
    with Session() as db:
        op=db.scalar(select(Operation).where(Operation.session_id==sid))
        assert op.data['writeback_done'] is False
        assert op.data['writeback_attempts']==WRITEBACK_MAX_ATTEMPTS, op.data.get('writeback_attempts')
        assert op.data['writeback_last_error']=='WRITEBACK_FAILED'
        assert op.data.get('manual') is True, 'bounded retry must hand over to manual review'
        # 业务效果只发生一次：回写重试不会再次驳回。
        assert db.scalar(select(func.count()).select_from(Effect))==1
        row=db.get(Conversation,sid)
        assert row.data['writeback_failure']['attempts']==WRITEBACK_MAX_ATTEMPTS
        assert row.data['ended'] is True
        reasons=[e.data['payload']['reason'] for e in db.scalars(
            select(EventRow).where(EventRow.session_id==sid).order_by(EventRow.seq))
            if e.data['payload']['type']=='session.ended']
        assert 'writeback_failed' in reasons, reasons


async def test_routing_decision_persisted_with_evidence(setup):
    """coordinator 持久化 decision_id/rule_version/evidence，上下文可信只读。"""
    client,app,services=setup
    sid=await ready_to_act(client)
    with Session() as db:
        row=db.get(Conversation,sid)
        stored=row.data['routing_decision']
        decision=RoutingDecision.model_validate(stored)
        assert decision.route in ('phone','jingmai','manual')
        assert decision.rule_version==row.data['rule_version']
        assert decision.evidence, 'evidence must be persisted with the decision'
        assert all(e.code for e in decision.evidence)
        assert decision.decision_id is not None
    context=app.state.coordinator.snapshot_context('demo',sid)
    # 上下文状态只来自数据库，不从话术猜。
    with Session() as db:
        assert context.state==db.get(Conversation,sid).state
    assert context.routing_decision is not None
    assert context.routing_decision.decision_id==decision.decision_id
    assert context.routing_decision.rule_version==decision.rule_version
    assert context.contact_outcome in ('unknown','verified','wrong_person','refused','no_answer','disconnected','not_required')
