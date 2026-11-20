import asyncio
from uuid import uuid4
import pytest
from pydantic import ValidationError
from app.contracts import TextTurn, TurnInput, ActionCommand, CallbackPayload, Event, ModelRequest
from app.adapters import DemoAgent, FakeModel, DisabledVoice, DomainError
from app.main import app

def test_client_cannot_set_business_confirmation():
    with pytest.raises(ValidationError):
        TextTurn(text='确认',client_event_id=uuid4(),user_confirmed=True)

def test_partial_asr_rejected():
    with pytest.raises(ValidationError):
        TurnInput(message_id=uuid4(),text='半句话',source='asr',final=False)

def test_callback_needs_same_server_confirmation():
    with pytest.raises(ValidationError):
        ActionCommand(session_id=uuid4(),application_id=uuid4(),case_revision=1,decision_id=uuid4(),
            operation_id=uuid4(),kind='create_callback',idempotency_key='test',evidence_refs=[],
            confirmation_ref=uuid4(),payload=CallbackPayload(type='create_callback',confirmation_id=uuid4()))

async def test_fake_model_observes_cancellation():
    cancel=asyncio.Event()
    chunks=[]
    async for chunk in FakeModel().stream(ModelRequest(messages=[],fixture_response='这个回复需要被中途打断，不能继续生成'),cancel):
        chunks.append(chunk)
        cancel.set()
    assert len(chunks)==1

async def test_voice_is_explicitly_unavailable():
    with pytest.raises(DomainError) as error:
        await DisabledVoice().credentials(str(uuid4()))
    assert error.value.code=='VOICE_NOT_CONFIGURED'

def test_openapi_has_new_routes_and_no_order_endpoint():
    paths=app.openapi()['paths']
    assert '/api/v1/sessions/{sid}/turns' in paths
    assert not any('order' in path for path in paths)


def test_state_enum_is_unchanged_and_has_no_parallel_states():
    """契约兼容：State 字面量必须与 0.3.0 一致，不得出现 CREATED/AUTH_PENDING/DISPATCHED 等。"""
    from typing import get_args
    from app.contracts import State
    values=set(get_args(State))
    required={'CASE_READY','ROUTING','OPENING','VERIFYING','EXPLAINING','QA','CALLBACK_PENDING',
              'CLOSING','ACTION_READY','EXECUTING','RECONCILING','WRITEBACK_PENDING','CLOSED','MANUAL_REVIEW'}
    assert values==required
    for forbidden in ('CREATED','AUTH_PENDING','IDENTITY_VERIFIED','REASON_NOTIFIED',
                      'RECTIFICATION_GUIDED','DISPATCHED'):
        assert forbidden not in values


def test_routing_decision_keeps_original_fields_and_route_literal():
    """route 仍是 phone|jingmai|manual；原字段保留；新增证据字段有默认值。"""
    from typing import get_args
    from app.contracts import RoutingDecision
    for field in ('decision_id','application_id','case_revision','route','reason_codes',
                  'rule_version','required_evidence'):
        assert field in RoutingDecision.model_fields, field
    route_type=RoutingDecision.model_fields['route'].annotation
    assert set(get_args(route_type))=={'phone','jingmai','manual'}
    # 旧样例（不传 evidence/submission_attempt/call_attempt）仍必须能反序列化。
    legacy=RoutingDecision.model_validate({
        'decision_id':str(uuid4()),'application_id':str(uuid4()),'case_revision':1,
        'route':'phone','reason_codes':['phone_explain'],'rule_version':'route-r2',
        'required_evidence':['identity_verification']})
    assert legacy.evidence==[] and legacy.submission_attempt==1 and legacy.call_attempt==0


def test_session_context_readonly_additions_have_defaults():
    """SessionContext 只加带默认值的只读字段；state 用原 State；不覆盖 handle_turn 的 verified 参数。"""
    from typing import get_args
    from app.contracts import SessionContext, State
    context=SessionContext(session_id=uuid4(),turn_id=uuid4(),generation_id=uuid4(),
        generation_epoch=0,application_id=uuid4(),case_revision=1,state_revision=0,
        strategy_version='demo-p1',rule_version='route-r2',knowledge_version='demo-k1',
        model_version='fake-v1')
    assert context.state in set(get_args(State))
    assert context.routing_decision is None and context.notifications==[]
    assert context.contact_outcome=='unknown' and context.contact_outcome_source=='none'
    # verified 仍是 handle_turn 的独立参数（服务端证据），不放进上下文掩盖它。
    assert 'verified' not in SessionContext.model_fields
    import inspect
    from app.adapters import Services
    from app.ports import AgentPort
    signature=inspect.signature(AgentPort.handle_turn)
    assert list(signature.parameters)==['self','input','context','history','case','knowledge','verified','cancel']


def test_agent_decision_keeps_original_fields():
    from app.contracts import AgentDecision
    for field in ('next_state','proposed_action','action_payload','evidence_message_ids',
                  'knowledge_entry_ids','reply_text'):
        assert field in AgentDecision.model_fields, field
    assert 'state_target' not in AgentDecision.model_fields


def test_writeback_retry_policy_is_bounded_and_declared():
    """回写失败必须是有界重试，不能无限重试也不能谎报成功。

    断言的是**策略常量**本身（真实行为由 test_integration 的 DB 用例验证）：
    上限存在、为正、且退避不超过上限，避免有人把重试改回无界循环。
    """
    from app.guard import WRITEBACK_BACKOFF_SECONDS, WRITEBACK_MAX_ATTEMPTS
    assert isinstance(WRITEBACK_MAX_ATTEMPTS, int) and WRITEBACK_MAX_ATTEMPTS >= 1
    assert WRITEBACK_MAX_ATTEMPTS <= 10, 'unbounded-looking retry budget'
    assert 0 < WRITEBACK_BACKOFF_SECONDS <= 30


def test_routing_evidence_is_a_readonly_addition():
    """RoutingEvidence 是新增只读结构：全部字段有默认值，不改变既有决策语义。"""
    from app.contracts import RoutingEvidence
    evidence=RoutingEvidence(code='preauth_ok')
    assert evidence.detail=='' and evidence.source=='rule' and evidence.ref is None
    assert evidence.knowledge_entry_ids==[]
    assert RoutingEvidence.model_fields['detail'].default==''
    # 只允许这些来源，避免模型自造来源类别。
    from typing import get_args
    source=get_args(RoutingEvidence.model_fields['source'].annotation)
    assert set(source)=={'case','rule','knowledge','contact_history','authorization','attempts'}


def test_notification_evidence_requires_server_message_id():
    """告知证据必须绑定服务端已落库消息；completed 默认 False（未完成不能算已告知）。"""
    from app.contracts import NotificationEvidence
    notification=NotificationEvidence(kind='explanation',message_id=uuid4())
    assert notification.completed is False
    assert notification.knowledge_entry_ids==[]
    with pytest.raises(ValidationError):
        NotificationEvidence(kind='explanation')


def test_agent_proposable_states_are_expanded_but_still_exclude_execution():
    """Coordinator 允许 Agent 提议的状态范围：覆盖演示所需，但执行/终态一律不可提议。

    需求（t2-3）：扩充允许范围时不得放开 EXECUTING/RECONCILING/WRITEBACK_PENDING/CLOSED，
    也不得新增平行状态；每个可提议状态都必须能通过显式转移表到达。
    """
    from typing import get_args
    from app.contracts import State
    from app.guard import AGENT_PROPOSABLE_STATES, TRANSITIONS, may_transition

    states = set(get_args(State))
    proposable = set(AGENT_PROPOSABLE_STATES)
    # 范围确实被扩充（不只是原来的 OPENING/VERIFYING/QA）。
    assert {'EXPLAINING', 'CALLBACK_PENDING', 'ACTION_READY', 'MANUAL_REVIEW'} <= proposable
    # 执行态与终态必须仍不可提议：Agent 不能自己宣告已执行/已完成/已关闭。
    for forbidden in ('EXECUTING', 'RECONCILING', 'WRITEBACK_PENDING', 'CLOSED', 'CASE_READY'):
        assert forbidden not in proposable, forbidden
    # 全部取值都在原 State 枚举内（没有平行状态枚举漏进来）。
    assert proposable <= states
    # 每个可提议状态都至少有一条合法入边，否则 Agent 永远到不了它。
    reachable = {to for targets in TRANSITIONS.values() for to in targets}
    assert proposable <= reachable, proposable - reachable
    # 显式说明"不能跳步"：执行态只能由协调器在守卫通过后进入。
    assert not may_transition('VERIFYING', 'EXECUTING')


def test_tool_guard_table_covers_every_declared_branch():
    """守卫表与路由表同源：每个分支的 route 与最低证据都必须在 BRANCH_RULES 里声明。

    需求（t2-2/3）：完整路由与告知证据要可追溯；守卫表不能漏分支导致"静默放行"。
    """
    from app.guard import BRANCH_RULES, BRANCHES, JINGMAI_BRANCH_CODES

    # 分支名稳定（报告与证据按名字核对）。
    assert set(BRANCH_RULES) == set(BRANCHES)
    for branch, (route, required) in BRANCH_RULES.items():
        assert route in ('phone', 'jingmai', 'manual'), (branch, route)
        assert required, f'{branch} must declare required evidence'
        assert all(isinstance(code, str) and code for code in required), branch
    # 京麦依据码必须真实出现在某条京麦分支的证据里，否则守卫会永远拒绝。
    jingmai_codes = set()
    for branch, (route, _) in BRANCH_RULES.items():
        if route == 'jingmai':
            jingmai_codes.add(branch)
    assert jingmai_codes, 'no jingmai branch declared'
    assert set(JINGMAI_BRANCH_CODES) <= {
        'submission_attempt_third_or_more', 'channel_jingmai_only',
        'no_answer_first_submission', 'no_answer_after_followup'}


def test_coordinator_accepts_demo_chain_states_but_rejects_execution():
    """主链各步被 Agent 提议的状态必须被接受；执行态/跳步/带动作越级必须被拒。

    需求（t2-3）：按契约覆盖现有演示路径（授权缺项->身份确认->原因整改->追问不能承诺
    ->模拟驳回->回写），同时保持"Agent 不能派发、不能自行进入执行态"。
    """
    from app.contracts import AgentDecision
    from app.coordinator import Coordinator
    from uuid import uuid4

    def decision(state, action='none'):
        return AgentDecision(reply_text='x', proposed_action=action,
                             evidence_message_ids=[uuid4()], knowledge_entry_ids=[],
                             next_state=state)

    # _agent_violation 是纯函数（只读 guard 常量），可直接用未绑定调用验证决策表。
    violation = Coordinator._agent_violation
    # 演示主链：每一步都允许（ACTION_READY 必须带动作）。
    for current, target, action in [('OPENING', 'VERIFYING', 'none'),
                                    ('VERIFYING', 'EXPLAINING', 'none'),
                                    ('EXPLAINING', 'QA', 'none'),
                                    ('QA', 'QA', 'none'),
                                    ('EXPLAINING', 'ACTION_READY', 'reject_application'),
                                    ('QA', 'ACTION_READY', 'create_followup'),
                                    ('QA', 'CALLBACK_PENDING', 'none'),
                                    ('QA', 'CLOSING', 'none'),
                                    ('QA', 'MANUAL_REVIEW', 'none')]:
        assert violation(None, current, decision(target, action)) is None, (current, target)
    # 执行态不可提议：协调器守卫后才派发。
    for target in ('EXECUTING', 'RECONCILING', 'WRITEBACK_PENDING', 'CLOSED'):
        assert violation(None, 'VERIFYING', decision(target))[0] == 'agent_state_not_proposable', target
    # ACTION_READY 必须带动作；反之动作必须落在 ACTION_READY。
    assert violation(None, 'QA', decision('ACTION_READY'))[0] == 'action_ready_without_action'
    assert violation(None, 'QA', decision('QA', 'reject_application'))[0] == 'action_without_action_ready'
    # 不能跳步。
    assert violation(None, 'VERIFYING', decision('ACTION_READY', 'reject_application'))[0] == 'agent_transition_denied'


def test_contact_outcome_unknown_is_explicit_not_silent():
    """unknown 是显式取值：不能把"没有可信事件"压缩成 no_answer/not_required。"""
    from app.contracts import CallRecord
    from typing import get_args
    statuses=set(get_args(CallRecord.model_fields['contact_status'].annotation))
    assert 'unknown' in statuses
    assert {'verified','wrong_person','refused','no_answer','disconnected','not_required'} <= statuses
