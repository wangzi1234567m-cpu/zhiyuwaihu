"""业务规则层：路由决策表、工具守卫、状态机校验、模型配置（仅环境变量）。

本模块只包含纯函数与无 I/O 的决策表，方便反复测试；它不写数据库、不调用网络。
所有事实来自 ReviewCase（审核结论由业务系统提供，Agent 不能鉴真或批准）、
服务器保存的会话证据（核验、告知、操作）与已审核知识。

设计取舍（见 docs/agent/CONTRACT-CHANGES.md）：
- RoutingDecision.route 仍是 phone|jingmai|manual；followup 是**动作**，不是新 route。
- State 沿用 0.3.0 原枚举，不新增平行状态。
- submission_attempt（业务提交次数）与 call_attempt（本次提交的联系次数）分开计数。
- 联系结果只能来自可信领域事件；`unknown` 绝不能被推断成 no_answer。
- 普通"预约回电"是动作（create_callback），强制"驳回并回电告知"才是电话优先级例外。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from uuid import UUID, uuid4

from .adapters import DomainError
from .contracts import (ActionCommand, CallbackPayload, FollowupPayload, KnowledgeEntry,
                        RejectPayload, ReviewCase, RoutingDecision, RoutingEvidence,
                        State, WritebackPayload)

RULE_VERSION = 'route-r2'

#: 电话场景需要已核对接听方并完成告知；京麦-only 不需假造语音核验。
PHONE_MIN_EVIDENCE = ['identity_verification', 'explanation_completed']
JINGMAI_MIN_EVIDENCE = ['preauthorized_task', 'case_revision', 'routing_decision']

#: 联系结果枚举；unknown 表示没有可信事件。
TRUSTED_OUTCOMES = ('verified', 'wrong_person', 'refused', 'no_answer',
                    'disconnected', 'not_required', 'unknown')
#: 这些结果不是"未接通"，不能拿去套未接通关单规则。
NOT_NO_ANSWER = ('refused', 'wrong_person', 'disconnected', 'verified')

CASE_READY: State = 'CASE_READY'
TERMINAL: tuple[State, ...] = ('CLOSED', 'MANUAL_REVIEW')

#: 显式状态转移表。不在表中的转移一律拒绝并记录，不允许 Agent 自行跳步。
TRANSITIONS: dict[State, tuple[State, ...]] = {
    'CASE_READY': ('ROUTING', 'OPENING', 'MANUAL_REVIEW', 'CLOSING'),
    'ROUTING': ('OPENING', 'ACTION_READY', 'MANUAL_REVIEW', 'CLOSING'),
    'OPENING': ('VERIFYING', 'MANUAL_REVIEW', 'CLOSING'),
    'VERIFYING': ('VERIFYING', 'EXPLAINING', 'QA', 'MANUAL_REVIEW', 'CLOSING'),
    'EXPLAINING': ('EXPLAINING', 'QA', 'CALLBACK_PENDING', 'ACTION_READY', 'MANUAL_REVIEW', 'CLOSING'),
    'QA': ('QA', 'EXPLAINING', 'VERIFYING', 'CALLBACK_PENDING', 'ACTION_READY', 'MANUAL_REVIEW', 'CLOSING'),
    'CALLBACK_PENDING': ('CALLBACK_PENDING', 'QA', 'CLOSING', 'ACTION_READY', 'MANUAL_REVIEW'),
    'CLOSING': ('ACTION_READY', 'MANUAL_REVIEW', 'CLOSED'),
    'ACTION_READY': ('EXECUTING', 'MANUAL_REVIEW', 'CLOSING'),
    'EXECUTING': ('RECONCILING', 'WRITEBACK_PENDING', 'MANUAL_REVIEW', 'CLOSING'),
    'RECONCILING': ('RECONCILING', 'WRITEBACK_PENDING', 'MANUAL_REVIEW', 'CLOSING'),
    'WRITEBACK_PENDING': ('WRITEBACK_PENDING', 'CLOSED', 'MANUAL_REVIEW', 'CLOSING'),
    'CLOSED': ('CLOSED',),
    'MANUAL_REVIEW': ('MANUAL_REVIEW',),
}

#: 允许 Agent 提议的目标状态集合（只是候选，最终仍走 TRANSITIONS 校验）。
AGENT_PROPOSABLE_STATES: tuple[State, ...] = (
    'ROUTING', 'OPENING', 'VERIFYING', 'EXPLAINING', 'QA', 'CALLBACK_PENDING',
    'CLOSING', 'ACTION_READY', 'MANUAL_REVIEW')

#: 允许继续对话的会话状态（业务处理中/终态不接受新轮次）。
TURN_ALLOWED_STATES: tuple[State, ...] = ('OPENING', 'VERIFYING', 'EXPLAINING', 'QA', 'CALLBACK_PENDING')

ALLOWED_TRANSITIONS_DOC = 'docs/agent/STATE-TRANSITIONS.md'

#: 回写（writeback）独立重试上限。业务效果已经发生（operation=succeeded），
#: 失败的只是结果回写；必须有界重试后如实转人工，不能无限重试也不能谎报 CLOSED。
WRITEBACK_MAX_ATTEMPTS = 3
#: 回写重试退避上限（秒）。传输层 attempts 仍按原 min(10, attempts) 退避。
WRITEBACK_BACKOFF_SECONDS = 2


def may_transition(current: State, nxt: State) -> bool:
    return nxt in TRANSITIONS.get(current, ())


def validate_transition(current: State, nxt: State) -> None:
    """非法转移抛 INVALID_TRANSITION；异常对象带 from/to 供调用方落证据。"""
    if not may_transition(current, nxt):
        error = DomainError('INVALID_TRANSITION', f'不允许从{current}转移到{nxt}', 409)
        error.from_state, error.to_state = current, nxt
        raise error


# ---------------------------------------------------------------- 路由决策表

#: 分支名（稳定标识，供报告与守卫核对）。每个分支对应一个可复现输入组合。
BRANCHES = {
    'manual_preauth': '缺少预授权审核任务 -> 人工',
    'manual_rule': '规则版本/资料冲突 -> 人工',
    'manual_conclusion': '审核结论不是驳回 -> 人工',
    'manual_exception_unreachable': '例外场景持续未接通 -> 人工',
    'phone_exception': '要求驳回并回电告知或投诉/曝光风险 -> 电话优先',
    'jingmai_third_attempt': '无例外且第三次及以上错误提交 -> 京麦',
    'jingmai_channel': '已发布渠道为京麦 -> 京麦（不做语音核验）',
    'jingmai_first_no_answer': '电话场景首次错误提交且可信未接通 -> 京麦',
    'followup_second_submission': '电话场景第二次错误提交、本次首次未通 -> 再次联系（followup 动作）',
    'jingmai_after_followup': '第二次错误提交且跟进后仍未通 -> 京麦',
    'phone_explain': '电话渠道核对身份并告知 -> 电话',
}


def is_exception(case: ReviewCase) -> bool:
    """强制回电例外与投诉/曝光风险；普通"想约个回电"不是例外。"""
    return bool(case.callback_requested or case.complaint_risk)


def route_branch(case: ReviewCase, *, knowledge_channel: str | None = None,
                 authorized: bool = True, rule_valid: bool = True,
                 contact_outcome: str = 'unknown') -> str:
    """纯分支函数：返回 BRANCHES 的键。优先级见模块文档，冲突交人工。"""
    if not authorized:
        return 'manual_preauth'
    if not rule_valid:
        return 'manual_rule'
    if case.review_conclusion != 'rejected':
        return 'manual_conclusion'
    if is_exception(case):
        if contact_outcome in ('no_answer', 'disconnected') and case.call_attempt >= 2:
            return 'manual_exception_unreachable'
        return 'phone_exception'
    if case.submission_attempt >= 3:
        return 'jingmai_third_attempt'
    if knowledge_channel == 'jingmai':
        return 'jingmai_channel'
    if contact_outcome == 'no_answer':
        if case.submission_attempt <= 1:
            return 'jingmai_first_no_answer'
        if case.call_attempt <= 1:
            return 'followup_second_submission'
        return 'jingmai_after_followup'
    return 'phone_explain'


#: 每个分支允许的 route 与最低证据（守门与报告用同一张表）。
BRANCH_RULES: dict[str, tuple[str, tuple[str, ...]]] = {
    'manual_preauth': ('manual', ('preauthorized_task',)),
    'manual_rule': ('manual', ('effective_rule_version',)),
    'manual_conclusion': ('manual', ('rejected_conclusion',)),
    'manual_exception_unreachable': ('manual', ('manual_contact_disposition',)),
    'phone_exception': ('phone', tuple(PHONE_MIN_EVIDENCE) + ('exception_reason',)),
    'jingmai_third_attempt': ('jingmai', tuple(JINGMAI_MIN_EVIDENCE)),
    'jingmai_channel': ('jingmai', tuple(JINGMAI_MIN_EVIDENCE)),
    'jingmai_first_no_answer': ('jingmai', tuple(JINGMAI_MIN_EVIDENCE) + ('contact_outcome_event',)),
    'followup_second_submission': ('phone', tuple(PHONE_MIN_EVIDENCE) + ('followup_plan',)),
    'jingmai_after_followup': ('jingmai', tuple(JINGMAI_MIN_EVIDENCE) + ('contact_outcome_event',)),
    'phone_explain': ('phone', tuple(PHONE_MIN_EVIDENCE)),
}

#: 京麦分支在路由决策里留下的证据码（守卫据此拒绝无依据的京麦驳回）。
#: 注意：这里必须是 **证据码**（reason_codes 里的值），不是 BRANCHES 的分支键。
JINGMAI_BRANCH_CODES = ('submission_attempt_third_or_more', 'channel_jingmai_only',
                        'no_answer_first_submission', 'no_answer_after_followup')


class RoutingPolicy:
    """关单/联系渠道决策表（纯函数）。分支实现见 route_branch/BRANCH_RULES。"""

    version = RULE_VERSION

    def decide_route(self, case: ReviewCase, **kwargs) -> RoutingDecision:
        return self.decide(case, **kwargs)

    def decide(self, case: ReviewCase, *, knowledge_channel: str | None = None,
               knowledge_entry_ids: tuple[UUID, ...] = (),
               authorized: bool = True, rule_valid: bool = True,
               contact_outcome: str = 'unknown',
               contact_outcome_source: str = 'none') -> RoutingDecision:
        branch = route_branch(case, knowledge_channel=knowledge_channel, authorized=authorized,
                              rule_valid=rule_valid, contact_outcome=contact_outcome)
        route, required = BRANCH_RULES[branch]
        evidence: list[RoutingEvidence] = []
        codes: list[str] = []
        entry_ids = tuple(knowledge_entry_ids)

        def add(code: str, detail: str, source: str = 'rule', ref: str | None = None,
                entry_refs: tuple[UUID, ...] = ()) -> None:
            codes.append(code)
            evidence.append(RoutingEvidence(code=code, detail=detail, source=source, ref=ref,
                                            knowledge_entry_ids=list(entry_refs)))

        # 0 预授权 / 规则有效性：不进业务分支，直接人工。
        if branch == 'manual_preauth':
            add('preauth_missing', '缺少预授权审核任务，禁止外呼与派发', 'authorization')
            return self._build(case, route, required, codes, evidence)
        if branch == 'manual_rule':
            add('rule_invalid', '规则版本无效或存在资料冲突，需人工核实', 'rule')
            return self._build(case, route, required, codes, evidence)
        if branch == 'manual_conclusion':
            add('conclusion_not_rejection', f'审核结论为{case.review_conclusion}，不属于驳回告知', 'case')
            return self._build(case, route, required, codes, evidence)

        add('preauth_ok', '预授权任务与规则版本有效', 'authorization', ref=self.version)
        add('case_revision_bound', f'绑定审核版本{case.case_revision}', 'case')
        # 次数来源随决策留档：submission_attempt 与 call_attempt 是两组独立事实，
        # 任何一侧缺失都不能用另一侧或沉默补齐。
        add('attempts_bound',
            f'submission_attempt={case.submission_attempt},call_attempt={case.call_attempt}',
            'attempts')

        if branch in ('phone_exception', 'manual_exception_unreachable'):
            reason = 'complaint_risk' if case.complaint_risk else 'callback_request_requested'
            add('exception_phone_priority',
                f'{reason}：要求驳回并回电告知或投诉/曝光风险，先电话告知', 'case', ref=reason)
            if branch == 'manual_exception_unreachable':
                add('exception_unreachable_manual',
                    '例外场景持续未接通，按规则转人工，不套普通未接通关单', 'contact_history',
                    ref=f'call_attempt={case.call_attempt}')
                return self._build(case, route, required, codes, evidence)
            if contact_outcome in NOT_NO_ANSWER:
                add('contact_outcome_not_no_answer',
                    f'联系结果为{contact_outcome}，不得当作未接通', 'contact_history',
                    ref=contact_outcome_source)
            return self._build(case, route, required, codes, evidence)

        if branch == 'jingmai_third_attempt':
            add('submission_attempt_third_or_more',
                f'第{case.submission_attempt}次错误提交，达到京麦驳回条件', 'attempts',
                ref=f'submission_attempt={case.submission_attempt}')
            return self._build(case, route, required, codes, evidence)

        if branch == 'jingmai_channel':
            add('channel_jingmai_only', '已发布渠道为京麦，无需外呼，不做语音核验', 'knowledge',
                entry_refs=entry_ids)
            return self._build(case, route, required, codes, evidence)

        # 以下都按电话渠道处理，渠道来自已审核知识条目，不由模型猜。
        if knowledge_channel == 'phone':
            add('channel_phone', '已发布渠道为电话，先尝试告知', 'knowledge', entry_refs=entry_ids)
        else:
            add('channel_unspecified', '未取得已发布渠道，按电话先尝试，缺依据时人工', 'knowledge')

        if contact_outcome == 'no_answer':
            add('contact_outcome_no_answer', '可信领域事件：本次联系未接通', 'contact_history',
                ref=contact_outcome_source)
            if branch == 'jingmai_first_no_answer':
                add('no_answer_first_submission', '首次错误提交且真实未接通，可转京麦驳回', 'attempts')
            elif branch == 'followup_second_submission':
                add('no_answer_second_submission_followup',
                    '第二次错误提交、本次首次未通：创建跟进计划再次联系（跟进是动作，不是渠道）',
                    'attempts', ref=f'call_attempt={case.call_attempt}')
            else:
                add('no_answer_after_followup',
                    f'第二次错误提交且已联系{case.call_attempt}次仍未通，转京麦驳回', 'attempts')
            return self._build(case, route, required, codes, evidence)

        if contact_outcome in NOT_NO_ANSWER:
            add('contact_outcome_not_no_answer',
                f'联系结果为{contact_outcome}，不是未接通，不套未接通关单', 'contact_history',
                ref=contact_outcome_source)
        elif contact_outcome == 'unknown':
            add('contact_outcome_unknown',
                '没有可信联系结果事件，不得由次数或沉默推断未接通', 'contact_history')

        add('phone_explain', '按电话渠道进行身份核对与告知', 'rule')
        return self._build(case, route, required, codes, evidence)

    @staticmethod
    def _build(case: ReviewCase, route: str, required: tuple[str, ...],
               codes: list[str], evidence: list[RoutingEvidence]) -> RoutingDecision:
        return RoutingDecision(
            decision_id=uuid4(), application_id=case.application_id,
            case_revision=case.case_revision, route=route, reason_codes=codes,
            rule_version=RULE_VERSION, required_evidence=list(required),
            evidence=evidence, submission_attempt=case.submission_attempt,
            call_attempt=case.call_attempt)


# ---------------------------------------------------------------- 幂等键

def business_key(case: ReviewCase, kind: str) -> str:
    """业务效果幂等键：申请单 + 场景 + 动作 + 审核版本 + 业务轮次。

    不含 session_id：换会话、重启进程都必须命中同一个业务效果。
    reject_application 只绑定申请单、审核版本和动作，不随提交/联系次数变化；
    其他动作仍由 submission_attempt 与 call_attempt 组成业务轮次。
    """
    parts = [
        str(case.application_id),
        case.scenario_id,
        kind,
        f'r{case.case_revision}',
    ]
    if kind != 'reject_application':
        parts.extend([
            f's{max(case.submission_attempt, 0)}',
            f'c{max(case.call_attempt, 0)}',
        ])
    return ':'.join(parts)


# ---------------------------------------------------------------- 工具守卫

@dataclass
class GuardContext:
    """协调器提供的服务端事实；客户端不能提交这些字段。"""
    confirmations: dict[str, dict] | None = None
    operation_ids: tuple[str, ...] = ()
    now: object = None


class ToolGuard:
    """派发前的守卫表。分支：phone 告知后驳回 / 京麦驳回 / followup 再次联系 / 人工。

    只接受服务器构造的 ActionCommand；客户端布尔值无效。
    守卫表与路由决策表共用 BRANCH_RULES，避免两处规则漂移。
    """

    def authorize(self, case: ReviewCase, session_data: dict, command: ActionCommand,
                  entries, context: GuardContext | None = None) -> None:
        self._common(case, session_data, command)
        if command.kind == 'reject_application':
            self._reject(case, session_data, command, entries)
        elif command.kind == 'create_callback':
            self._callback(session_data, command, context)
        elif command.kind == 'create_followup':
            self._followup(case, session_data, command, context)
        elif command.kind == 'writeback':
            self._writeback(session_data, command)
        else:  # pragma: no cover - 枚举已在契约层限制
            raise DomainError('ACTION_FORBIDDEN', '未知动作类型', 403)

    def _common(self, case: ReviewCase, data: dict, command: ActionCommand) -> None:
        if command.case_revision != case.case_revision:
            raise DomainError('STALE_REVISION', '审核版本已变化，拒绝执行', 409)
        if str(command.application_id) != str(case.application_id):
            raise DomainError('ACTION_FORBIDDEN', '动作与申请单不一致', 403)
        decision = data.get('routing_decision') or {}
        if not decision:
            raise DomainError('ACTION_FORBIDDEN', '缺少路由决策绑定，拒绝执行', 403)
        if str(decision.get('application_id')) != str(case.application_id):
            raise DomainError('ACTION_FORBIDDEN', '路由决策与申请单不一致', 403)
        if decision.get('case_revision') != case.case_revision:
            raise DomainError('STALE_REVISION', '路由决策版本与当前审核版本不一致', 409)
        if str(decision.get('decision_id')) != str(command.decision_id):
            raise DomainError('ACTION_FORBIDDEN', '动作与路由决策不一致', 403)
        if decision.get('rule_version') != RULE_VERSION and command.kind != 'writeback':
            raise DomainError('ACTION_FORBIDDEN', '规则版本已失效，需重新路由', 403)
        session_revision = data.get('case_revision')
        if session_revision is None:
            session_revision = decision.get('case_revision')
        if session_revision != case.case_revision:
            raise DomainError('STALE_REVISION', '会话证据版本已过期，拒绝执行', 409)
        if data.get('ended') and command.kind != 'writeback':
            raise DomainError('SESSION_ENDED', '会话已结束，不再派发新动作', 409)
        if case.review_conclusion != 'rejected':
            raise DomainError('ACTION_FORBIDDEN', '审核结论不是驳回，不能执行驳回动作', 403)
        route = decision.get('route') or data.get('route')
        if command.kind != 'writeback' and route not in ('phone', 'jingmai'):
            # manual 路由（缺授权/规则冲突/例外持续未通）一律不派发，交人工。
            raise DomainError('ACTION_FORBIDDEN', '当前路由为人工核实，禁止派发业务动作', 403)
        if data.get('rule_version') not in (None, RULE_VERSION) and command.kind != 'writeback':
            raise DomainError('ACTION_FORBIDDEN', '规则版本已失效，需重新路由', 403)

    # -- 驳回 --------------------------------------------------------
    def _reject(self, case: ReviewCase, data: dict, command: ActionCommand, entries) -> None:
        decision = data.get('routing_decision') or {}
        route = decision.get('route') or data.get('route')
        codes = set(decision.get('reason_codes') or [])
        outcome = data.get('contact_outcome', 'unknown')

        if route == 'phone':
            # 电话路径必须有已核对接听方与已告知证据。
            if not data.get('verified') or not data.get('explained'):
                raise DomainError('ACTION_FORBIDDEN', '未满足模拟驳回条件', 403)
            if outcome in ('wrong_person', 'refused'):
                raise DomainError('ACTION_FORBIDDEN', '错人或已拒绝联系，不得自动驳回', 403)
            if not data.get('notification_message_ids'):
                raise DomainError('INVALID_EVIDENCE', '缺少已发出的告知证据', 403)
        else:
            # 京麦路径：不要求语音核验，也不允许伪造核验。
            if not decision:
                raise DomainError('INVALID_EVIDENCE', '缺少服务器路由决策，不能派发', 403)
            if not codes & set(JINGMAI_BRANCH_CODES):
                raise DomainError('ACTION_FORBIDDEN', '缺少允许走京麦的规则依据', 403)
            if 'channel_jingmai_only' in codes and outcome == 'verified':
                raise DomainError('INVALID_EVIDENCE', '京麦-only 任务不得伪造语音核验', 403)
            if data.get('verified') and not data.get('explained'):
                raise DomainError('INVALID_EVIDENCE', '已核验但未完成告知，不能按京麦结论驳回', 403)

        allowed_entries = {e.entry_id: e for e in entries
                           if e.status == 'approved' and not e.issue_codes
                           and e.scenario_id == case.scenario_id
                           and e.knowledge_version == data.get('knowledge_version', 'demo-k1')}
        if not isinstance(command.payload, RejectPayload):
            raise DomainError('INVALID_FACTS', '驳回动作的payload类型不匹配', 403)
        if not allowed_entries:
            raise DomainError('INVALID_EVIDENCE', '缺少已审核依据', 403)
        if any(i not in allowed_entries for i in command.payload.knowledge_entry_ids):
            raise DomainError('INVALID_EVIDENCE', '缺少已审核依据', 403)
        if command.payload.written_text not in [allowed_entries[i].approved_text for i in command.payload.knowledge_entry_ids]:
            raise DomainError('INVALID_FACTS', '书面意见与已审依据不一致', 403)
        if command.payload.reason_codes != [case.scenario_id]:
            raise DomainError('INVALID_FACTS', '驳回原因与申请单不一致', 403)

    # -- 预约回电 ----------------------------------------------------
    def _callback(self, data: dict, command: ActionCommand, context: GuardContext | None) -> None:
        from datetime import datetime, timezone

        def _parse_aware(value: object) -> datetime:
            if isinstance(value, datetime):
                dt = value
            elif isinstance(value, str):
                dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
            else:
                raise ValueError('not a datetime')
            if dt.tzinfo is None or dt.utcoffset() is None:
                raise ValueError('naive datetime')
            return dt

        if not isinstance(command.payload, CallbackPayload):
            raise DomainError('INVALID_FACTS', '预约动作的payload类型不匹配', 403)
        ref = command.confirmation_ref
        if ref is None or ref != command.payload.confirmation_id:
            raise DomainError('INVALID_EVIDENCE', '预约需要服务器确认记录', 403)
        confirmations = (context.confirmations if context else None) or {}
        row = confirmations.get(str(ref))
        if row is None:
            raise DomainError('INVALID_EVIDENCE', '确认记录不存在或不属于该会话', 403)
        title = '预约需要商户最终确认的有效时间窗'
        if row.get('revoked_at'):
            raise DomainError('CONFIRMATION_REVOKED', f'{title}（该确认已因改期失效）', 409)
        if str(row.get('confirmation_id')) != str(ref):
            raise DomainError('INVALID_EVIDENCE', '确认记录与命令不匹配', 403)
        cmd_app = getattr(command, 'application_id', None)
        cmd_rev = getattr(command, 'case_revision', None)
        if cmd_app is None or cmd_rev is None:
            raise DomainError('INVALID_EVIDENCE', '预约命令缺少应用或修订信息', 403)
        if str(row.get('application_id')) != str(cmd_app):
            raise DomainError('INVALID_EVIDENCE', '确认记录与命令不匹配', 403)
        try:
            row_rev = int(row.get('case_revision'))
            cmd_rev_int = int(cmd_rev)
        except (TypeError, ValueError):
            raise DomainError('INVALID_EVIDENCE', '确认记录缺少有效修订号', 403)
        if row_rev != cmd_rev_int:
            raise DomainError('INVALID_EVIDENCE', '确认记录与命令不匹配', 403)
        try:
            expires_at = _parse_aware(row.get('expires_at'))
        except (TypeError, ValueError):
            raise DomainError('INVALID_EVIDENCE', '缺少有效的确认过期时间', 403)
        if context and context.now is not None:
            try:
                now = _parse_aware(context.now)
            except (TypeError, ValueError):
                raise DomainError('INVALID_FACTS', '守卫时间无效', 403)
        else:
            now = datetime.now(timezone.utc)
        if row.get('expired') or now >= expires_at:
            raise DomainError('CONFIRMATION_EXPIRED', f'{title}（已过期）', 409)
        if row.get('timezone') != 'Asia/Shanghai':
            raise DomainError('INVALID_FACTS', '预约时区必须是 Asia/Shanghai', 403)
        try:
            window_start = _parse_aware(row.get('window_start'))
            window_end = _parse_aware(row.get('window_end'))
        except (TypeError, ValueError):
            raise DomainError('INVALID_EVIDENCE', '缺少明确回电时间窗', 403)
        if window_end <= window_start:
            raise DomainError('INVALID_EVIDENCE', '回电时间窗无效', 403)
        if not data.get('verified'):
            raise DomainError('ACTION_FORBIDDEN', '未核对身份不能创建预约', 403)

    # -- 跟进（再次联系）---------------------------------------------
    def _followup(self, case: ReviewCase, data: dict, command: ActionCommand,
                  context: GuardContext | None) -> None:
        if not isinstance(command.payload, FollowupPayload):
            raise DomainError('INVALID_FACTS', '跟进动作的payload类型不匹配', 403)
        if data.get('contact_outcome', 'unknown') != 'no_answer':
            raise DomainError('INVALID_EVIDENCE', '只有可信未接通才能创建跟进计划', 403)
        if case.submission_attempt != 2 or case.call_attempt > 1:
            raise DomainError('ACTION_FORBIDDEN',
                              '跟进计划只用于第二次错误提交的首次未接通', 403)
        if is_exception(case):
            raise DomainError('ACTION_FORBIDDEN', '例外场景不自动建跟进，转人工核实', 403)
        decision = data.get('routing_decision') or {}
        if 'no_answer_second_submission_followup' not in set(decision.get('reason_codes') or []):
            raise DomainError('INVALID_EVIDENCE', '缺少服务器路由决策', 403)
        related = command.payload.related_operation_id
        known = set()
        if context:
            known = {str(i) for i in context.operation_ids}
        if related is not None and str(related) not in known:
            raise DomainError('INVALID_EVIDENCE', '关联操作不属于该会话', 403)

    # -- 回写 --------------------------------------------------------
    def _writeback(self, data: dict, command: ActionCommand) -> None:
        if not isinstance(command.payload, WritebackPayload):
            raise DomainError('INVALID_FACTS', '回写动作的payload类型不匹配', 403)
        if not data.get('operation_ids'):
            raise DomainError('INVALID_EVIDENCE', '没有已登记的操作用于回写', 403)


# ---------------------------------------------------------------- 模型配置

#: 已授权 GLM 组（工作台配置）的**公开连接参数**，来源 .project-brief/GLM-CONNECTION.json。
#: 注意：历史脚本里出现的 `glm-4.6` / `open.bigmodel.cn` 是**错误默认值**，不属于本工作台
#: 已授权的 glm 供应商配置，不得作为默认值使用（那会指向另一个未授权服务）。
#: 密钥只从继承的环境变量 GLM_API_KEY 读取，这里没有、也不能有任何密钥值。
GLM_MODEL_ID = 'ybg-youlaber-ds4f'
GLM_BASE_URL = 'https://gateway.baigongai.cn/v1'
#: 兼容别名：明确标注为"历史错误默认"，仅供迁移脚本识别，不参与任何调用。
LEGACY_WRONG_DEFAULTS = {'model_id': 'glm-4.6',
                         'base_url': 'https://open.bigmodel.cn/api/paas/v4'}


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    model_id: str
    base_url: str
    api_key_present: bool
    source: str

    def public_view(self) -> dict:
        """可写入报告/日志的字段：永远不含密钥本身。"""
        return {'provider': self.provider, 'model_id': self.model_id,
                'base_url': self.base_url, 'api_key_present': self.api_key_present,
                'source': self.source}


def load_model_config(env: dict[str, str] | None = None) -> ModelConfig:
    """真实模型配置只从环境变量读取；缺失时报清晰错误，绝不静默回退假模型。

    model id / baseURL 锁定为工作台 glm 组配置，环境变量只能覆盖地址但不能带密钥。
    永不返回或打印密钥本身，只报告是否存在。
    """
    env = dict(os.environ if env is None else env)
    present = bool(env.get('GLM_API_KEY'))
    model_id = env.get('GLM_MODEL_ID') or GLM_MODEL_ID
    base_url = env.get('GLM_BASE_URL') or GLM_BASE_URL
    return ModelConfig(provider='glm', model_id=model_id, base_url=base_url,
                       api_key_present=present,
                       source='env' if present else 'env_missing_key')


def require_model_config(env: dict[str, str] | None = None) -> ModelConfig:
    config = load_model_config(env)
    if not config.api_key_present:
        raise DomainError(
            'MODEL_NOT_CONFIGURED',
            '未继承 GLM_API_KEY；真实模型调用被拒绝，请由宿主注入后重试（假模型仅用于可重复测试）',
            503)
    return config
