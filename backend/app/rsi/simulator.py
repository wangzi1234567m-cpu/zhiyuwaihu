"""Agent simulator：通过端口驱动 Agent→Policy→Guard→Tool 全链，可注入 baseline/candidate。

设计要点：
- 不连数据库。业务事实写在进程内 `Ledger`，断言只看 Ledger（工具/业务事实）、
  状态机轨迹与受保护声明检查，不以 LLM 措辞判成功。
- Policy 与 ToolGuard **复用生产实现** `backend/app/guard.py`（不复制决策表），
  `rsi/router.py` 仅作交叉核对，不一致写入报告（`compare_with_production`）。
- 身份核验由模拟器扮演协调器，调用 `understanding.server_verify_identity`；
  Agent（含候选提示）永远不能写 `verified`。
- 预算/并发/超时全部经 `RequestBudget`；超预算抛 BudgetExhausted，不重试。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from ..contracts import (ActionCommand, AgentDecision, CallRecord, FollowupPayload,
                         KnowledgeEntry, MessageView, RejectPayload, ReviewCase,
                         RoutingDecision, SessionContext, TurnInput, Understanding)
from .. import guard as production_guard
from ..adapters import DomainError
from .budget import BudgetExhausted, RequestBudget, RequestTimeout
from .candidates import Candidate, baseline_candidate
from .corpus import Scenario, published_channel
from .understanding import (PromptSpec, parse_understanding, server_verify_identity)

KNOWLEDGE_VERSION = 'rsi-k1'
STRATEGY_VERSION = 'rsi-p1'
MODEL_VERSION = 'fake-rsi-v1'

#: 受保护声明：任何臂都不得出现的“成功/保证”措辞。确定性字符串检查。
DEFAULT_FORBIDDEN_CLAIMS = (
    '保证通过', '保证能通过', '一定能过', '一定能通过', '包过', '已通过审核',
    '已经批准', '已批准', '已办理成功', '处理成功', '已经帮您通过',
)


def _det_id(kind: str, *parts: str) -> UUID:
    return UUID(str(uuid5(NAMESPACE_URL, 'rsi:' + ':'.join((kind,) + parts))))


# --------------------------------------------------------------------- Ledger


@dataclass
class Ledger:
    """业务/工具事实唯一来源（替代数据库）。按业务幂等键去重，天然支持跨会话幂等。"""

    effects: dict[str, dict] = field(default_factory=dict)
    dispatched: list[dict] = field(default_factory=list)
    denials: list[dict] = field(default_factory=list)
    transitions: list[dict] = field(default_factory=list)
    invalid_transitions: list[dict] = field(default_factory=list)
    replies: list[dict] = field(default_factory=list)
    verified_events: list[dict] = field(default_factory=list)
    records: list[dict] = field(default_factory=list)
    writeback_pending: list[dict] = field(default_factory=list)
    queries: list[dict] = field(default_factory=list)

    def effect_count(self) -> int:
        return sum(1 for e in self.effects.values() if e.get('created'))

    def kinds(self) -> list[str]:
        return [x['kind'] for x in self.dispatched]

    def succeeded_kinds(self) -> list[str]:
        return [x['kind'] for x in self.dispatched if x.get('status') == 'succeeded']

    def as_dict(self) -> dict:
        return {
            'effect_count': self.effect_count(),
            'effects': {k: {kk: vv for kk, vv in v.items() if kk != 'command'}
                        for k, v in self.effects.items()},
            'dispatched': self.dispatched,
            'denials': self.denials,
            'invalid_transitions': self.invalid_transitions,
            'transitions': self.transitions,
            'verified_events': self.verified_events,
            'records': self.records,
            'writeback_pending': self.writeback_pending,
            'queries': self.queries,
            'reply_count': len(self.replies),
        }


class SimulatedBusiness:
    """BusinessPort 的确定性替身：一次业务效果、故障注入、同 key 查证。"""

    def __init__(self, ledger: Ledger, fault: str | None):
        self.ledger = ledger
        self.fault = fault

    async def execute(self, command: ActionCommand):
        key = command.idempotency_key
        existing = self.ledger.effects.get(key)
        if existing is None:
            self.ledger.effects[key] = {
                'created': True, 'kind': command.kind,
                'resource_id': f'sim-{command.kind}-{command.operation_id}',
                'status': 'succeeded', 'case_revision': command.case_revision,
                'session_id': str(command.session_id), 'command': command.model_dump(mode='json'),
            }
        else:
            # 一次业务效果：跨会话/重启命中同一业务键只回放，不再造效果。
            self.ledger.effects[key]['replayed'] = self.ledger.effects[key].get('replayed', 0) + 1
        status = 'succeeded'
        if self.fault == 'lost_response':
            # 业务已生效但响应丢失：协调器必须用同 key 查证，不能换 key 重发。
            status = 'unknown'
        elif self.fault == 'writeback_failure':
            status = 'succeeded'
        elif self.fault == 'timeout':
            self.ledger.queries.append({'phase': 'dispatch_timeout', 'key': key})
            raise RequestTimeout('模拟工具调用超时')
        return _result(command.operation_id, status, self.ledger.effects[key]['resource_id'])

    async def query_operation(self, command: ActionCommand):
        key = command.idempotency_key
        row = self.ledger.effects.get(key)
        if row is None:
            self.ledger.queries.append({'phase': 'query', 'key': key, 'found': False})
            return _result(command.operation_id, 'not_found_yet', None)
        self.ledger.queries.append({'phase': 'query', 'key': key, 'found': True})
        return _result(command.operation_id, 'succeeded', row['resource_id'])

    async def writeback(self, record: CallRecord):
        if self.fault == 'writeback_failure':
            raise DomainError('WRITEBACK_FAILED', '模拟回写失败', 503)
        self.ledger.records.append(record.model_dump(mode='json'))


def _result(operation_id, status: str, resource_id: str | None):
    from ..contracts import OperationResult
    from ..db import now  # 仅复用时间函数；不建立任何数据库连接
    return OperationResult(operation_id=operation_id, status=status,  # type: ignore[arg-type]
                           resource_id=resource_id, updated_at=now())


# --------------------------------------------------------------- SimulatedAgent


class SimulatedAgent:
    """确定性 Agent：消费 `PromptSpec`（baseline/candidate）与结构化理解。

    AgentPort 签名与生产一致；Agent 只输出 reply/动作提议/next_state，
    不写 verified、不直接 SQL、不自行派发工具。
    """

    def __init__(self, spec: PromptSpec, *, understanding_switches: tuple[str, ...] = ()):
        self.spec = spec
        self.switches = set(understanding_switches)

    async def handle_turn(self, input: TurnInput, context: SessionContext,
                          history: tuple[MessageView, ...], case: ReviewCase,
                          knowledge: tuple[KnowledgeEntry, ...], verified: bool,
                          cancel: asyncio.Event) -> AgentDecision:
        if cancel.is_set():
            raise asyncio.CancelledError('cancelled before agent call')
        text = input.text
        understanding = parse_understanding(text)
        intents = list(understanding.intents)
        company = case.slots['company_name']

        def reply(template: str) -> str:
            return template.replace('{company}', company)

        def decide(text_out: str, state, action: str = 'none', payload=None,
                   entries: tuple[KnowledgeEntry, ...] = (), missing: list[str] | None = None):
            return AgentDecision(
                reply_text=text_out, proposed_action=action,  # type: ignore[arg-type]
                action_payload=payload,
                evidence_message_ids=[input.message_id],
                knowledge_entry_ids=[e.entry_id for e in entries] if verified else [],
                next_state=state,  # type: ignore[arg-type]
                understanding=understanding,
                contact_outcome=understanding.contact_outcome_hint,  # type: ignore[arg-type]
                required_slot_missing=missing or [],
            )

        # 1) 身份否认/拒绝：结束且不披露（错人不自动驳回）。
        if 'deny_identity' in intents:
            return decide(reply(self.spec.wrong_person_close), 'CLOSING')
        if 'refuse_contact' in intents:
            return decide(reply(self.spec.refused_close), 'CLOSING')

        # 2) 未核验：只做身份确认，绝不披露驳回原因/整改。
        if not verified:
            if 'confirm_identity' in intents or 'unclear' in intents:
                return decide(reply(self.spec.identity_ask), 'VERIFYING')
            return decide(reply(self.spec.identity_ask_clarify), 'VERIFYING')

        # 3) 已核验：依据选择按 scenario/version/scope/approved/no-conflict。
        approved = tuple(e for e in knowledge if e.status == 'approved' and not e.issue_codes
                         and e.scenario_id == case.scenario_id and e.approved_text)
        if not approved:
            return decide(reply(self.spec.missing_knowledge), 'MANUAL_REVIEW')
        if len({e.approved_text for e in approved}) > 1:
            # 冲突不猜答案：转人工，不挑第一条。
            return decide(reply(self.spec.conflict_escalation), 'MANUAL_REVIEW')

        entry = approved[0]
        ground = entry.approved_text
        # 告知证据只看服务端已完成的告知（context.notifications），不靠自己复述判断。
        already_explained = any(n.completed for n in context.notifications)

        def spoken(extra: str) -> str:
            """核验后首次告知必须先给出已审原因与整改；之后只答当前问题。"""
            if not extra:
                return reply(ground)
            return reply(extra) if already_explained else f'{reply(ground)} {reply(extra)}'

        # 4) 保证类追问：不承诺必过（首次告知时同时给出原因/整改）。
        if 'ask_guarantee' in intents:
            return decide(spoken(self.spec.guarantee_refusal),
                          'QA' if already_explained else 'EXPLAINING', entries=(entry,))
        # 5) 依据/来源追问：只给已审来源表述，不编数字。
        if 'ask_source' in intents:
            return decide(spoken(self.spec.source_grounding),
                          'QA' if already_explained else 'EXPLAINING', entries=(entry,))
        # 6) 投诉风险：转人工，不自动驳回。
        if 'complaint_risk' in intents:
            return decide(spoken(self.spec.complaint_human), 'MANUAL_REVIEW')
        # 7) 回电/改期：人工协调；本演示不自动预约。
        if 'request_callback' in intents:
            return decide(spoken(self.spec.callback_human),
                          'CALLBACK_PENDING' if already_explained else 'EXPLAINING',
                          entries=(entry,))
        # 8) 明确同意按依据处理：进入动作准备（“明白了”不等于购买授权，
        #    但是对“按审核任务处理”的确认，后台仍要过路由/证据/守卫）。
        #    首次同意时把已审原因/整改一并播出，告知证据在同一轮落库（与生产一致），
        #    动作仍必须过守卫，不得因“用户同意”跳过证据。
        if 'acknowledge' in intents:
            if not already_explained:
                return self._action_decision(input, context, case, entry, understanding, reply,
                                             prefix=reply(ground))
            return self._action_decision(input, context, case, entry, understanding, reply)
        # 9) 原因/整改/结果/澄清：播已审依据（不拿 knowledge[0] 应付全部问题）。
        if intents and intents[0] != 'unclear':
            state = 'QA' if (already_explained and 'ask_reason' not in intents) else 'EXPLAINING'
            return decide(reply(ground), state, entries=(entry,))
        # 10) 含糊表达：先澄清，不猜意图；首次告知时同时给出原因/整改。
        return decide(spoken(self.spec.unclear_clarify),
                      'QA' if already_explained else 'EXPLAINING', entries=(entry,))

    def _action_decision(self, input: TurnInput, context: SessionContext, case: ReviewCase,
                         entry: KnowledgeEntry, understanding: Understanding, reply,
                         prefix: str = '') -> AgentDecision:
        codes = set()
        if context.routing_decision is not None:
            codes = set(context.routing_decision.reason_codes)

        def text_out(template: str) -> str:
            body = reply(template)
            return f'{prefix} {body}' if prefix else body

        if 'no_answer_second_submission_followup' in codes:
            payload = FollowupPayload(type='create_followup',
                                      reason_code='no_answer_second_submission',
                                      related_operation_id=None)
            return AgentDecision(
                reply_text=text_out(self.spec.action_notice), proposed_action='create_followup',
                action_payload=payload, evidence_message_ids=[input.message_id],
                knowledge_entry_ids=[entry.entry_id], next_state='ACTION_READY',
                understanding=understanding)
        payload = None  # Written payload is constructed only by Coordinator.
        return AgentDecision(
            reply_text=text_out(self.spec.action_notice), proposed_action='reject_application',
            action_payload=payload, evidence_message_ids=[input.message_id],
            knowledge_entry_ids=[entry.entry_id], next_state='ACTION_READY',
            understanding=understanding)

    def opening_decision(self, case: ReviewCase, message_id: UUID) -> AgentDecision:
        """开场轮（等价生产 opening=True）：只做身份确认邀请，不披露任何业务详情。

        开场不是身份核验，`verified` 也不会因此变成 True；核验只看服务端证据。
        """
        return AgentDecision(reply_text=self.spec.identity_ask.replace(
            '{company}', case.slots['company_name']),
            proposed_action='none', evidence_message_ids=[], knowledge_entry_ids=[],
            next_state='VERIFYING')

    def system_decision(self, case: ReviewCase, knowledge: tuple[KnowledgeEntry, ...],
                        message_id: UUID, routing: RoutingDecision | None = None) -> AgentDecision:
        """系统流程（京麦-only / 未接通分流）：不进行语音核验，也不伪造核验。

        只按服务器路由决策选动作：followup 原因是建跟进计划，京麦原因是驳回。
        知识缺失/冲突一律转人工，不猜答案。
        """
        codes = set(routing.reason_codes if routing is not None else ())
        approved = tuple(e for e in knowledge if e.status == 'approved' and not e.issue_codes
                         and e.scenario_id == case.scenario_id and e.approved_text)
        if not approved:
            return AgentDecision(reply_text=self.spec.missing_knowledge, proposed_action='none',
                                 evidence_message_ids=[], knowledge_entry_ids=[],
                                 next_state='MANUAL_REVIEW')
        if len({e.approved_text for e in approved}) > 1:
            return AgentDecision(reply_text=self.spec.conflict_escalation, proposed_action='none',
                                 evidence_message_ids=[], knowledge_entry_ids=[],
                                 next_state='MANUAL_REVIEW')
        entry = approved[0]
        if 'no_answer_second_submission_followup' in codes:
            payload = FollowupPayload(type='create_followup',
                                      reason_code='no_answer_second_submission',
                                      related_operation_id=None)
            return AgentDecision(reply_text=self.spec.action_notice,
                                 proposed_action='create_followup', action_payload=payload,
                                 evidence_message_ids=[message_id],
                                 knowledge_entry_ids=[entry.entry_id],
                                 next_state='ACTION_READY')
        payload = None  # Written payload is constructed only by Coordinator.
        return AgentDecision(reply_text=self.spec.action_notice, proposed_action='reject_application',
                             action_payload=payload, evidence_message_ids=[message_id],
                             knowledge_entry_ids=[entry.entry_id], next_state='ACTION_READY')


# ------------------------------------------------------------------- run result


@dataclass
class Arm:
    name: str                      # 'baseline' | 'candidate'
    candidate: Candidate
    spec_id: str
    content_hash: str


@dataclass
class ScenarioRun:
    scenario_id: str
    family: str
    expression_group: str
    split: str
    arm: str
    repeat: int
    route: str
    reason_codes: list[str]
    decision_id: str
    rule_version: str
    states: list[str]
    final_state: str
    replies: list[str]
    ledger: Ledger
    assertions: list[dict] = field(default_factory=list)
    attribution: list[dict] = field(default_factory=list)
    requests_used: int = 0
    timeouts: int = 0
    error: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.assertions) and all(a['passed'] for a in self.assertions)

    @property
    def failed_assertions(self) -> list[str]:
        return [a['id'] for a in self.assertions if not a['passed']]

    def completion(self) -> float:
        return 1.0 if self.passed else 0.0

    def as_dict(self, *, verbose: bool = False) -> dict:
        out = {
            'scenario_id': self.scenario_id, 'family': self.family,
            'expression_group': self.expression_group, 'split': self.split,
            'arm': self.arm, 'repeat': self.repeat,
            'route': self.route, 'reason_codes': self.reason_codes,
            'decision_id': self.decision_id, 'rule_version': self.rule_version,
            'states': self.states, 'final_state': self.final_state,
            'passed': self.passed, 'failed_assertions': self.failed_assertions,
            'assertions': self.assertions, 'attribution': self.attribution,
            'requests_used': self.requests_used, 'timeouts': self.timeouts,
            'error': self.error, 'notes': self.notes,
            'ledger_summary': {
                'effect_count': self.ledger.effect_count(),
                'dispatched_kinds': self.ledger.kinds(),
                'succeeded_kinds': self.ledger.succeeded_kinds(),
                'denials': len(self.ledger.denials),
                'records': len(self.ledger.records),
                'writeback_pending': len(self.ledger.writeback_pending),
            },
        }
        if verbose:
            out['replies'] = self.replies
            out['ledger'] = self.ledger.as_dict()
        return out


# ------------------------------------------------------------------- simulator


class Simulator:
    """把场景跑成一次端到端链路；每个 (scenario, arm, repeat) 使用独立 Ledger。"""

    def __init__(self, *, budget: RequestBudget | None = None):
        self.budget = budget or RequestBudget(max_requests=600, concurrency=2, timeout_s=5.0)
        self.policy = production_guard.RoutingPolicy()
        self.guard = production_guard.ToolGuard()

    async def run(self, scenario: Scenario, arm: Arm, repeat: int) -> ScenarioRun:
        ledger = Ledger()
        states = ['CASE_READY']
        run = ScenarioRun(
            scenario_id=scenario.scenario_id, family=scenario.family,
            expression_group=scenario.expression_group, split=scenario.split,
            arm=arm.name, repeat=repeat, route='', reason_codes=[], decision_id='',
            rule_version='', states=states, final_state='CASE_READY', replies=[], ledger=ledger)

        case = scenario.case
        entries = scenario.knowledge
        channel = scenario.channel
        outcome = 'no_answer' if scenario.last_contact_outcome == 'no_answer' else 'unknown'
        outcome_source = 'domain_event' if outcome != 'unknown' else 'none'

        decision = self.policy.decide(
            case, knowledge_channel=channel,
            knowledge_entry_ids=tuple(e.entry_id for e in entries),
            authorized=scenario.authorized, rule_valid=scenario.rule_valid,
            contact_outcome=outcome, contact_outcome_source=outcome_source)
        run.route = decision.route
        run.reason_codes = list(decision.reason_codes)
        run.decision_id = str(decision.decision_id)
        run.rule_version = decision.rule_version

        # 生产协调器只允许这些 Agent 输出状态（ALLOWED_TURN_STATES 的子集 + 终止态）。
        try:
            await self._drive(run, scenario, arm, decision, ledger)
        except BudgetExhausted as exc:
            run.error = f'budget_exhausted: {exc}'
            run.notes.append('evidence insufficient：请求预算耗尽，未完成该场景，未重试')
        except RequestTimeout as exc:
            run.timeouts += 1
            run.error = f'timeout: {exc}'
        except asyncio.CancelledError:
            raise
        except DomainError as exc:
            run.error = f'domain_error: {exc.code}'
        except Exception as exc:  # 不吞掉，但记录为可归因失败
            run.error = f'{type(exc).__name__}: {exc}'
        run.final_state = run.states[-1]
        run.requests_used = self.budget.counters.requests
        return run

    # -- 主链 ------------------------------------------------------------
    async def _drive(self, run: ScenarioRun, scenario: Scenario, arm: Arm,
                     decision: RoutingDecision, ledger: Ledger) -> None:
        case = scenario.case
        entries = scenario.knowledge
        agent = SimulatedAgent(arm.candidate.spec,
                               understanding_switches=arm.candidate.spec.enabled_understanding)
        business = SimulatedBusiness(ledger, scenario.fault)

        session_id = _det_id('session', scenario.scenario_id, arm.name, str(run.repeat))
        state = 'CASE_READY'
        verified = False
        explained = False
        contact_outcome = 'no_answer' if scenario.last_contact_outcome == 'no_answer' else 'unknown'
        contact_source = 'domain_event' if contact_outcome != 'unknown' else 'none'
        operation_ids: list[str] = []
        sessions = scenario.repeat_sessions

        for session_index in range(sessions):
            # 跨会话/重启：第二个会话命中同一业务键，只能回放事实。
            if session_index > 0:
                state = 'CASE_READY'
                verified = False
                explained = False
                operation_ids = []
                run.notes.append('second_session：跨会话幂等检查')
            self._transition(run, ledger, state, 'ROUTING')
            state = 'ROUTING'

            if decision.route == 'manual':
                self._transition(run, ledger, state, 'MANUAL_REVIEW')
                state = 'MANUAL_REVIEW'
                run.notes.append(f'manual_route:{decision.reason_codes[0] if decision.reason_codes else "unknown"}')
                await self._finalize(run, scenario, decision, business, ledger,
                                     session_id, operation_ids, contact_outcome, False)
                continue

            if scenario.system_flow:
                # 系统流程（京麦-only / 未接通分流）：不进行语音核验，也不伪造核验。
                message_id = _det_id('msg', scenario.scenario_id, arm.name, str(run.repeat),
                                     str(session_index), 'system')
                system_decision = agent.system_decision(case, entries, message_id, decision)
                ledger.replies.append({'session': session_index, 'role': 'system',
                                       'text': system_decision.reply_text, 'verified': False})
                run.replies.append(system_decision.reply_text)
                state = await self._apply_decision(
                    run, scenario, decision, business, ledger, session_id,
                    operation_ids, system_decision, state, verified, explained,
                    contact_outcome, contact_source, entries)
                await self._finalize(run, scenario, decision, business, ledger,
                                     session_id, operation_ids, contact_outcome, verified)
                continue

            # phone：先 OPENING（开场问候）再 VERIFYING，与生产协调器同序：
            # opening=True 的开场决策把状态推进到 VERIFYING，之后才进入用户轮次。
            self._transition(run, ledger, state, 'OPENING')
            state = 'OPENING'
            opening_id = _det_id('msg', scenario.scenario_id, arm.name, str(run.repeat),
                                 str(session_index), 'opening')
            opening = agent.opening_decision(case, opening_id)
            ledger.replies.append({'session': session_index, 'role': 'assistant',
                                   'text': opening.reply_text, 'verified': False,
                                   'knowledge_ids': []})
            run.replies.append(opening.reply_text)
            self._transition(run, ledger, state, 'VERIFYING')
            state = 'VERIFYING'

            for turn in scenario.turns:
                message_id = _det_id('msg', scenario.scenario_id, arm.name, str(run.repeat),
                                     str(session_index), turn.text[:24])

                # 与生产协调器同序：先由**服务端证据**核验身份，再组装只读上下文交给 Agent。
                # Agent/提示永远看不到“自己写 verified”的路径，也不能影响核验结论。
                if not verified:
                    ok, reason = server_verify_identity(turn.text, case, scenario.identity_mode)
                    ledger.verified_events.append({'attempt': turn.intent, 'verified': ok,
                                                   'reason': reason})
                    if ok:
                        verified = True
                        contact_outcome = 'verified'
                        contact_source = 'demo_text_rule'

                context = self._context(session_id, message_id, case, state, decision,
                                        verified, explained, contact_outcome, contact_source,
                                        operation_ids, ledger)
                turn_input = TurnInput(message_id=message_id, text=turn.text, source='text')

                # 预算/并发/超时：每次 Agent 调用计一次请求。
                turn_decision = await self.budget.call(
                    lambda ti=turn_input, ctx=context: agent.handle_turn(
                        ti, ctx, (), case, entries, verified, asyncio.Event()),
                    real=False, label=f'agent:{scenario.scenario_id}')

                if turn_decision.understanding and turn_decision.understanding.contact_outcome_hint:
                    hint = turn_decision.understanding.contact_outcome_hint
                    if hint in ('wrong_person', 'refused') and contact_outcome not in ('verified',):
                        contact_outcome = hint
                        contact_source = 'demo_text_rule'

                ledger.replies.append({'session': session_index, 'role': 'assistant',
                                       'text': turn_decision.reply_text, 'verified': verified,
                                       'knowledge_ids': [str(k) for k in turn_decision.knowledge_entry_ids]})
                run.replies.append(turn_decision.reply_text)

                # 告知证据由服务端按“回复确实播出了已审正文”落库，先于派发（与生产一致）。
                if verified and not explained:
                    explained = any(e.approved_text and e.approved_text in turn_decision.reply_text
                                    for e in entries)
                state = await self._apply_decision(
                    run, scenario, decision, business, ledger, session_id,
                    operation_ids, turn_decision, state, verified, explained,
                    contact_outcome, contact_source, entries)
                if state in ('CLOSING', 'MANUAL_REVIEW', 'CLOSED', 'WRITEBACK_PENDING'):
                    break

            await self._finalize(run, scenario, decision, business, ledger,
                                 session_id, operation_ids, contact_outcome, verified)

    def _context(self, session_id: UUID, message_id: UUID, case: ReviewCase, state: str,
                 decision: RoutingDecision, verified: bool, explained: bool,
                 contact_outcome: str, contact_source: str, operation_ids: list[str],
                 ledger: Ledger) -> SessionContext:
        from ..contracts import NotificationEvidence
        notifications = []
        if explained:
            notifications = [NotificationEvidence(
                kind='explanation',
                message_id=_det_id('notif', str(session_id), str(len(ledger.replies))),
                completed=True)]
        return SessionContext(
            session_id=session_id, turn_id=_det_id('turn', str(message_id)), generation_id=_det_id('gen', str(message_id)),
            generation_epoch=0, application_id=case.application_id, case_revision=case.case_revision,
            state_revision=len(ledger.transitions), strategy_version=STRATEGY_VERSION,
            rule_version=production_guard.RULE_VERSION, knowledge_version=KNOWLEDGE_VERSION,
            model_version=MODEL_VERSION, state=state,  # type: ignore[arg-type]
            routing_decision=decision, notifications=notifications,
            contact_outcome=contact_outcome,  # type: ignore[arg-type]
            contact_outcome_source=contact_source,  # type: ignore[arg-type]
            submission_attempt=case.submission_attempt, call_attempt=case.call_attempt,
            operation_ids=[UUID(o) for o in operation_ids])

    def _transition(self, run: ScenarioRun, ledger: Ledger, current: str, nxt: str) -> None:
        if current == nxt:
            return
        if not production_guard.may_transition(current, nxt):  # type: ignore[arg-type]
            ledger.invalid_transitions.append({'from': current, 'to': nxt})
            run.states.append('MANUAL_REVIEW')
            ledger.transitions.append({'from': current, 'to': 'MANUAL_REVIEW', 'forced': True})
            raise DomainError('INVALID_TRANSITION', f'{current}->{nxt} 非法')
        run.states.append(nxt)
        ledger.transitions.append({'from': current, 'to': nxt})

    async def _apply_decision(self, run: ScenarioRun, scenario: Scenario, routing: RoutingDecision,
                              business: SimulatedBusiness, ledger: Ledger, session_id: UUID,
                              operation_ids: list[str], decision: AgentDecision, state: str,
                              verified: bool, explained: bool, contact_outcome: str,
                              contact_source: str, entries: tuple[KnowledgeEntry, ...]) -> str:
        case = scenario.case
        next_state = decision.next_state
        if next_state == 'ACTION_READY' and decision.proposed_action == 'none':
            ledger.denials.append({'reason': 'action_ready_without_action'})
            return state
        # 状态机校验：非法转移不改写成合法状态，记录并转人工。
        try:
            if state != next_state:
                self._transition(run, ledger, state, next_state)
            state = next_state
        except DomainError:
            self._transition(run, ledger, state, 'MANUAL_REVIEW')
            return 'MANUAL_REVIEW'

        if decision.proposed_action == 'none':
            return state

        # 错人/拒绝绝不自动驳回。
        if contact_outcome in ('wrong_person', 'refused'):
            ledger.denials.append({'kind': decision.proposed_action,
                                   'reason': f'contact_outcome_{contact_outcome}'})
            return state

        command = self._command(scenario, routing, decision, session_id, verified, explained,
                                contact_outcome, contact_source, entries, operation_ids)
        guard_view = {
            'verified': verified, 'explained': explained, 'ended': False,
            'route': routing.route, 'routing_decision': routing.model_dump(mode='json'),
            'notification_message_ids': [str(_det_id('notif', scenario.scenario_id, str(session_id)))
                                         for _ in [0]] if explained else [],
            'knowledge_version': KNOWLEDGE_VERSION, 'contact_outcome': contact_outcome,
            'rule_version': production_guard.RULE_VERSION,
            'contact_outcome_source':contact_source,
            # This offline corpus explicitly supplies trusted historical followup fixtures.
            'trusted_followup':scenario.last_contact_outcome=='no_answer' and case.submission_attempt==2 and case.call_attempt>=2,
            'operation_ids': list(operation_ids),
        }
        try:
            self.guard.authorize(case, guard_view, command, entries,
                                 production_guard.GuardContext(confirmations={},
                                                               operation_ids=tuple(operation_ids),
                                                               now=None))
        except DomainError as exc:
            ledger.denials.append({'kind': command.kind, 'code': exc.code, 'reason': exc.message})
            self._transition(run, ledger, state, 'MANUAL_REVIEW')
            return 'MANUAL_REVIEW'

        # 派发：先登记，再调端口；同 key 查证不换 key 重发。
        operation_ids.append(str(command.operation_id))
        entry = {'kind': command.kind, 'key': command.idempotency_key,
                 'operation_id': str(command.operation_id), 'status': 'pending',
                 'session_id': str(session_id), 'attempts': 1}
        ledger.dispatched.append(entry)
        self._transition(run, ledger, state, 'EXECUTING')
        state = 'EXECUTING'
        try:
            result = await business.execute(command)
        except RequestTimeout:
            ledger.queries.append({'phase': 'timeout_then_query', 'key': command.idempotency_key})
            result = await business.query_operation(command)
            if result.status == 'not_found_yet':
                entry['status'] = 'not_found_yet'
                self._transition(run, ledger, state, 'RECONCILING')
                return 'RECONCILING'
        if result.status not in ('succeeded', 'failed'):
            # 丢响应：用同一个 key 查证，not_found_yet 不是失败。
            queried = await business.query_operation(command)
            if queried.status == 'succeeded':
                result = queried
            else:
                entry['status'] = queried.status
                self._transition(run, ledger, state, 'RECONCILING')
                run.notes.append('lost_response_reconciling')
                return 'RECONCILING'
        entry['status'] = result.status
        self._transition(run, ledger, state, 'WRITEBACK_PENDING')
        state = 'WRITEBACK_PENDING'
        record = self._record(scenario, session_id, command, entries, contact_outcome, verified, explained)
        try:
            await business.writeback(record)
            ledger.records.append(record.model_dump(mode='json'))
        except DomainError:
            # 回写失败单独重试，不再次驳回；仍失败则停在 WRITEBACK_PENDING。
            ledger.writeback_pending.append({'operation_id': str(command.operation_id),
                                             'key': command.idempotency_key})
            try:
                await business.writeback(record)
                ledger.records.append(record.model_dump(mode='json'))
                ledger.writeback_pending[-1]['retried_ok'] = True
            except DomainError:
                ledger.writeback_pending[-1]['retried_ok'] = False
                run.notes.append('writeback_pending：回写失败单独重试，不再次驳回')
                return 'WRITEBACK_PENDING'
        self._transition(run, ledger, state, 'CLOSED' if result.status == 'succeeded' else 'MANUAL_REVIEW')
        return 'CLOSED' if result.status == 'succeeded' else 'MANUAL_REVIEW'

    def _command(self, scenario: Scenario, routing: RoutingDecision, decision: AgentDecision,
                 session_id: UUID, verified: bool, explained: bool, contact_outcome: str,
                 contact_source: str, entries: tuple[KnowledgeEntry, ...],
                 operation_ids: list[str]) -> ActionCommand:
        case = scenario.case
        kind = decision.proposed_action
        payload = decision.action_payload
        if kind=='reject_application':
            from ..written import WrittenResponseRenderer
            payload=WrittenResponseRenderer().payload(case,entries,KNOWLEDGE_VERSION)
        evidence = [_det_id('notif', scenario.scenario_id, str(session_id)),
                    _det_id('identity', scenario.scenario_id, str(session_id))]
        return ActionCommand(
            session_id=session_id, application_id=case.application_id,
            case_revision=case.case_revision, decision_id=routing.decision_id,
            operation_id=_det_id('op', scenario.scenario_id, kind, str(session_id),
                                 routing.decision_id.hex[:8]),
            kind=kind,  # type: ignore[arg-type]
            idempotency_key=production_guard.business_key(case, kind),
            confirmation_ref=getattr(payload, 'confirmation_id', None),
            evidence_refs=evidence, payload=payload)  # type: ignore[arg-type]

    def _record(self, scenario: Scenario, session_id: UUID, command: ActionCommand,
                entries: tuple[KnowledgeEntry, ...], contact_outcome: str,
                verified: bool, explained: bool) -> CallRecord:
        from ..db import now
        case = scenario.case
        used = [e for e in entries if e.approved_text and e.status == 'approved' and not e.issue_codes]
        hinted = contact_outcome if contact_outcome in (
            'verified', 'wrong_person', 'refused', 'no_answer', 'disconnected', 'not_required') else 'not_required'
        return CallRecord(
            record_id=_det_id('record', scenario.scenario_id, str(session_id)),
            record_revision=1, session_id=session_id, application_id=case.application_id,
            case_revision=case.case_revision, contact_status=hinted,
            explanation_status='completed' if explained else ('not_started' if not verified else 'partial'),
            reason_codes=[case.scenario_id] if verified else [],
            rectification_items=[case.slots['remedy']] if explained else [],
            source_refs=[ref for e in used for ref in e.source_refs],
            operation_refs=[command.operation_id],
            strategy_version=STRATEGY_VERSION, rule_version=production_guard.RULE_VERSION,
            knowledge_version=KNOWLEDGE_VERSION)

    async def _finalize(self, run: ScenarioRun, scenario: Scenario, decision: RoutingDecision,
                        business: SimulatedBusiness, ledger: Ledger, session_id: UUID,
                        operation_ids: list[str], contact_outcome: str, verified: bool) -> None:
        """收尾：正常结束（CLOSING）落 CLOSED 并写“无业务动作”记录；异常中断才转人工。

        `CLOSING` 表示告知流程已结束、没有派发业务动作（错人/拒绝/用户中止）。
        它必须落 `CLOSED` 而不是被强行塞进 `MANUAL_REVIEW`——后者意味着“需要人工跟进”，
        与“已如实结束”是两码事。UNKNOWN 未接通不得由沉默推断，只记 unknown。
        """
        current = run.states[-1]
        if current == 'CLOSING':
            try:
                self._transition(run, ledger, current, 'CLOSED')
            except DomainError:
                run.notes.append('finalize_transition_denied')
                return
            # 如实记录本次联系结果；没有可信事件就是 unknown。
            hinted = contact_outcome if contact_outcome in (
                'verified', 'wrong_person', 'refused', 'no_answer', 'disconnected',
                'not_required') else 'unknown'
            ledger.records.append({
                'record_id': 'non-action', 'session_id': str(session_id),
                'contact_status': hinted, 'explanation_status': 'not_started',
                'reason_codes': [], 'rectification_items': [], 'operation_refs': [],
                'kind': 'non_action_record',
            })
            return
        if current not in ('CLOSED', 'MANUAL_REVIEW', 'WRITEBACK_PENDING', 'RECONCILING'):
            try:
                self._transition(run, ledger, current, 'MANUAL_REVIEW')
            except DomainError:
                run.notes.append('finalize_transition_denied')


# ------------------------------------------------------- 交叉核对生产决策表


def compare_with_production(dev_scenarios) -> dict:
    """比对 rsi.router 参考表与生产 guard.RoutingPolicy 的 route/reason code。

    不一致如实写进报告（不静默通过、也不修改生产实现）。
    """
    from . import router as reference
    policy = production_guard.RoutingPolicy()
    mismatches = []
    checked = 0
    for scenario in dev_scenarios:
        case = scenario.case
        channel = scenario.channel
        outcome = 'no_answer' if scenario.last_contact_outcome == 'no_answer' else 'unknown'
        prod = policy.decide(case, knowledge_channel=channel, authorized=scenario.authorized,
                             rule_valid=scenario.rule_valid, contact_outcome=outcome,
                             contact_outcome_source='domain_event' if outcome != 'unknown' else 'none')
        facts = reference.RoutingFacts(
            channel=channel, submission_attempt=case.submission_attempt,
            call_attempt=case.call_attempt, callback_requested=case.callback_requested,
            complaint_risk=case.complaint_risk, last_contact_outcome=outcome,
            preauthorized=scenario.authorized, rule_valid=scenario.rule_valid)
        ref = reference.decide_route(case, facts, decision_seed=scenario.scenario_id)
        checked += 1
        if prod.route != ref.decision.route:
            mismatches.append({'scenario_id': scenario.scenario_id, 'production': prod.route,
                               'reference': ref.decision.route,
                               'production_codes': prod.reason_codes,
                               'reference_codes': ref.decision.reason_codes})
    return {'checked': checked, 'mismatches': mismatches,
            'production_rule_version': production_guard.RULE_VERSION,
            'reference_rule_version': reference.RULE_VERSION}


def make_arm(name: str, candidate: Candidate) -> Arm:
    return Arm(name=name, candidate=candidate, spec_id=candidate.spec_id,
               content_hash=candidate.content_hash())
