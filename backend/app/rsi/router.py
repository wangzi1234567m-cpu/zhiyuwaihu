"""Policy 决策表的 RSI 参考实现（纯函数，返回完整 RoutingDecision）。

为什么在 RSI 包里还有一份决策表：评测必须能对“路由”层单独归因，且不能依赖协调器
或数据库可用。此实现是 docs/design/04-state-machine.md §关单规则 与团队会议路由优先级的
直接编码，与生产 `Policy.decide_route` 的一致性由 `interop.compare_with_production`
在运行期交叉核对（不一致会写入报告，不静默通过）。

优先级（按序命中即停）：
1. 预授权/规则版本无效 → manual
2. 回电/投诉例外：持续未通（call_attempt>=2 且最近未接通）→ manual；否则 phone
3. 同场景第 3 次及以上错误提交 → jingmai
4. 已发布渠道为 jingmai → jingmai
5. 渠道为 phone 且最近一次未接通：
   - 首次提交 → jingmai
   - 第二次提交且尚未外呼 → phone + 建跟进事件（followup 是动作，不是 route 值）
   - 第二次提交且已外呼仍未通 → jingmai
6. 其余 → phone
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import NAMESPACE_URL, uuid5

from ..contracts import ReviewCase, RoutingDecision

RULE_VERSION = 'rsi-routing-v1'
PRIORITY_ORDER = (
    'PREAUTH_INVALID',
    'EXCEPTION_PERSISTENT_NO_ANSWER',
    'EXCEPTION_CALLBACK_REQUESTED',
    'EXCEPTION_COMPLAINT_RISK',
    'THIRD_SUBMISSION_JINGMAI',
    'PUBLISHED_CHANNEL_JINGMAI',
    'PHONE_FIRST_NO_ANSWER_JINGMAI',
    'PHONE_SECOND_NO_ANSWER_FOLLOWUP',
    'PHONE_SECOND_RETRY_EXHAUSTED_JINGMAI',
    'PUBLISHED_CHANNEL_PHONE',
)


@dataclass(frozen=True)
class RoutingFacts:
    """路由只消费“已发布业务事实”，绝不由对话文本推断。"""

    channel: str
    submission_attempt: int
    call_attempt: int
    callback_requested: bool
    complaint_risk: bool
    last_contact_outcome: str
    preauthorized: bool = True
    rule_valid: bool = True


@dataclass(frozen=True)
class RoutingOutcome:
    decision: RoutingDecision
    followup_required: bool
    system_only: bool


def decide_route(case: ReviewCase, facts: RoutingFacts, *, decision_seed: str = '') -> RoutingOutcome:
    def build(route: str, code: str, evidence: list[str], *, followup: bool = False, system_only: bool = False) -> RoutingOutcome:
        decision = RoutingDecision(
            decision_id=str(uuid5(NAMESPACE_URL, f'rsi:route:{decision_seed}:{case.application_id}:{case.case_revision}:{code}')),
            application_id=case.application_id,
            case_revision=case.case_revision,
            route=route,  # type: ignore[arg-type]
            reason_codes=[code],
            rule_version=RULE_VERSION,
            required_evidence=evidence,
        )
        return RoutingOutcome(decision=decision, followup_required=followup, system_only=system_only)

    if not facts.preauthorized or not facts.rule_valid:
        return build('manual', 'PREAUTH_INVALID', ['preauthorization', 'rule_version'])

    unreached = facts.last_contact_outcome == 'no_answer'

    if facts.callback_requested or facts.complaint_risk:
        if unreached and facts.call_attempt >= 2:
            return build('manual', 'EXCEPTION_PERSISTENT_NO_ANSWER', ['callback_request', 'contact_attempts'])
        code = 'EXCEPTION_CALLBACK_REQUESTED' if facts.callback_requested else 'EXCEPTION_COMPLAINT_RISK'
        return build('phone', code, ['callback_request_or_complaint', 'review_conclusion'])

    if facts.submission_attempt >= 3:
        return build('jingmai', 'THIRD_SUBMISSION_JINGMAI', ['submission_attempt'], system_only=True)

    if facts.channel == 'jingmai':
        return build('jingmai', 'PUBLISHED_CHANNEL_JINGMAI', ['published_channel'], system_only=True)

    if unreached:
        if facts.submission_attempt <= 1:
            return build('jingmai', 'PHONE_FIRST_NO_ANSWER_JINGMAI', ['contact_attempts', 'published_channel'], system_only=True)
        if facts.call_attempt == 0:
            return build('phone', 'PHONE_SECOND_NO_ANSWER_FOLLOWUP', ['submission_attempt', 'contact_attempts'], followup=True, system_only=True)
        return build('jingmai', 'PHONE_SECOND_RETRY_EXHAUSTED_JINGMAI', ['contact_attempts'], system_only=True)

    return build('phone', 'PUBLISHED_CHANNEL_PHONE', ['published_channel', 'review_conclusion'])
