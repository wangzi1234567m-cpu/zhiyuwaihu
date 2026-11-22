"""受约束的 prompt 候选生成器。

硬约束（见 docs/design/08-evaluation-rsi.md §RSI）：
- 只允许改**表达措辞**与**理解提示开关**；
- 任何试图改审核结论、权限/授权、官方知识正文、路由门槛、工具白名单的候选，
  在**生成期**（`propose`）与**校验期**（`validate_candidate`）都被拒绝；
- 生成器只能读取 dev 场景对象，不接触 holdout（见 runner/`ReadLog`）。

`Candidate` 是不可变值对象，带 `content_hash`，供 gate 做版本证据与回滚。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace

from .understanding import (BASELINE_PROMPT, CANDIDATE_PROMPT, FORBIDDEN_UNDERSTANDING_KEYS,
                            PromptSpec)

#: 关键 bug 名（对 spec 字段的别名），用于把候选改动映射到断言/归因。
SPEC_FIELDS = tuple(CANDIDATE_PROMPT.__dataclass_fields__.keys())

#: 候选允许改的键（白名单）。不在表内的键一律拒绝。
EDITABLE_FIELDS = frozenset({
    'identity_ask', 'identity_ask_clarify', 'guarantee_refusal', 'source_grounding',
    'conflict_escalation', 'missing_knowledge', 'callback_human', 'complaint_human',
    'unclear_clarify', 'wrong_person_close', 'refused_close', 'action_notice',
    'enabled_understanding',
})

#: 一旦出现在候选 diff 里就必须拒绝的键（越权面）。
FORBIDDEN_FIELDS = frozenset({
    'review_conclusion', 'conclusion', 'approved', 'approve', 'verified', 'verify',
    'route', 'routes', 'rule_version', 'required_evidence', 'threshold', 'thresholds',
    'gate', 'gate_v2', 'allowlist', 'tool_allowlist', 'tools', 'authorization',
    'authorized', 'preauthorized', 'knowledge_text', 'knowledge_entry_ids',
    'knowledge_version', 'approved_text', 'success', 'succeeded', 'idempotency_key',
    'case_revision', 'submission_attempt', 'call_attempt', 'contact_outcome',
    'slots', 'scenario_id', 'speech_threshold', 'max_requests',
})

#: 允许打开的理解开关（其余值拒绝）。
ALLOWED_UNDERSTANDING_SWITCHES = frozenset({
    'negation', 'multi_intent', 'clarification', 'guarantee_refusal',
    'source_grounding', 'conflict_escalation', 'callback_human', 'complaint_human',
    'wrong_person_handling', 'refused_handling',
})

#: 文本类字段的长度上限，防止把 API key/原始资料灌进提示。
MAX_FIELD_CHARS = 400

#: 明显是密钥/个人信息形态的内容直接拒绝（不打印命中的值本身）。
_SECRET_SHAPES = ('sk-', 'Bearer ', 'GLM_API_KEY', 'api_key', 'password', 'AKID',
                  '-----BEGIN', '13800138000')


class IllegalCandidateError(RuntimeError):
    """候选试图触碰受保护面（审核结论/权限/知识/门槛/白名单）。"""

    def __init__(self, message: str, *, field: str | None = None):
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class ExpressionRule:
    """一条表达改动：把 spec 的某个字段替换成新措辞。"""

    field: str
    text: str

    def __post_init__(self) -> None:
        if self.field not in EDITABLE_FIELDS:
            raise IllegalCandidateError(f'不允许修改字段 {self.field}', field=self.field)
        if not isinstance(self.text, str) or not self.text.strip():
            raise IllegalCandidateError(f'字段 {self.field} 的候选措辞为空', field=self.field)
        if len(self.text) > MAX_FIELD_CHARS:
            raise IllegalCandidateError(f'字段 {self.field} 措辞超过 {MAX_FIELD_CHARS} 字符', field=self.field)
        lowered = self.text.lower()
        for shape in _SECRET_SHAPES:
            if shape.lower() in lowered:
                raise IllegalCandidateError(
                    f'字段 {self.field} 含疑似密钥/隐私形态内容（{shape!r}），拒绝入候选', field=self.field)


@dataclass(frozen=True)
class Candidate:
    spec: PromptSpec
    rules: tuple[ExpressionRule, ...]
    notes: str = ''

    @property
    def spec_id(self) -> str:
        return self.spec.spec_id

    def content_hash(self) -> str:
        payload = {
            'spec_id': self.spec.spec_id,
            'fields': {f: getattr(self.spec, f) for f in SPEC_FIELDS},
            'rules': [{'field': r.field, 'text': r.text} for r in self.rules],
        }
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode('utf-8')
        return hashlib.sha256(blob).hexdigest()

    def rule_fields(self) -> tuple[str, ...]:
        return tuple(r.field for r in self.rules)


def baseline_candidate() -> Candidate:
    return Candidate(spec=BASELINE_PROMPT, rules=(), notes='baseline：生产默认表达 + 无额外理解开关')


def reference_candidate() -> Candidate:
    """内置参考候选（chat 表达 + 理解开关），用于展示 gate 判定链路。"""
    rules = tuple(
        ExpressionRule(field=f, text=getattr(CANDIDATE_PROMPT, f))
        for f in ('identity_ask', 'identity_ask_clarify', 'guarantee_refusal',
                  'source_grounding', 'conflict_escalation', 'missing_knowledge',
                  'callback_human', 'complaint_human', 'unclear_clarify',
                  'wrong_person_close', 'refused_close')
    )
    return Candidate(spec=CANDIDATE_PROMPT, rules=rules,
                     notes='candidate-v1：表达更清楚 + 打开否定/多意图/澄清/依据说明开关')


def validate_candidate(candidate: Candidate) -> Candidate:
    """候选校验器（生成期/入库前/运行前都会调用）。返回原对象，非法则抛异常。

    baseline 本身是合法可运行的臂：`spec_id == baseline` 且**没有**任何表达改动规则时放行；
    一旦带改动规则却仍复用 baseline 的 spec_id，就拒绝（无法保留版本证据）。

    防绕过：`PromptSpec` 是 frozen dataclass，但 `object.__setattr__` 能给它塞进
    **表外属性**（例如 `spec.route = 'jingmai'`）。因此这里额外做两件事：
    1) spec 的实例属性集合必须**恰好**等于 `SPEC_FIELDS`，多一个都拒绝；
    2) 任何属性名命中 `FORBIDDEN_FIELDS` / `FORBIDDEN_UNDERSTANDING_KEYS` 一律拒绝。
    """
    if not isinstance(candidate, Candidate):
        raise IllegalCandidateError('候选必须是 Candidate 实例')
    extra = sorted(set(vars(candidate.spec)) - set(SPEC_FIELDS))
    if extra:
        # 只报字段名，不回显取值，避免把注入内容写进日志。
        raise IllegalCandidateError(f'候选 spec 含表外属性 {extra}', field=extra[0])
    smuggled = sorted((FORBIDDEN_FIELDS | FORBIDDEN_UNDERSTANDING_KEYS) & set(vars(candidate.spec)))
    if smuggled:
        raise IllegalCandidateError(f'候选 spec 含受保护字段 {smuggled}', field=smuggled[0])
    is_baseline_spec = candidate.spec.spec_id == BASELINE_PROMPT.spec_id
    if is_baseline_spec and candidate.rules:
        raise IllegalCandidateError('baseline 不允许携带表达改动规则')
    for rule in candidate.rules:
        if rule.field not in EDITABLE_FIELDS:
            raise IllegalCandidateError(f'候选试图修改受保护字段 {rule.field}', field=rule.field)
    switches = tuple(candidate.spec.enabled_understanding)
    unknown = [s for s in switches if s not in ALLOWED_UNDERSTANDING_SWITCHES]
    if unknown:
        raise IllegalCandidateError(f'候选打开未知理解开关 {unknown}', field='enabled_understanding')
    for field in SPEC_FIELDS:
        value = getattr(candidate.spec, field)
        if field == 'enabled_understanding':
            continue
        if not isinstance(value, str):
            raise IllegalCandidateError(f'候选字段 {field} 类型非法', field=field)
        if len(value) > MAX_FIELD_CHARS:
            raise IllegalCandidateError(f'候选字段 {field} 过长', field=field)
        for shape in _SECRET_SHAPES:
            if shape.lower() in value.lower():
                raise IllegalCandidateError(f'候选字段 {field} 含疑似密钥内容', field=field)
    if is_baseline_spec and candidate.notes and candidate.notes.startswith('candidate'):
        raise IllegalCandidateError('候选必须使用新的 spec_id 以保留版本证据')
    return candidate


def apply_rules(rules: tuple[ExpressionRule, ...], base: PromptSpec = BASELINE_PROMPT,
                spec_id: str = 'prompt-candidate-custom') -> Candidate:
    """把一组表达改动应用到 base 上；任何越权改动在这里就抛错。"""
    updated = replace(base, spec_id=spec_id)
    for rule in rules:
        updated = replace(updated, **{rule.field: rule.text})
    candidate = Candidate(spec=updated, rules=rules)
    return validate_candidate(candidate)


def propose(dev_scenarios, *, spec_id: str = 'prompt-candidate-proposed') -> Candidate:
    """从 **dev** 证据生成候选：只看 dev 场景的失败家族，改表达措辞。

    安全边界：函数签名只接受场景序列；调用方必须只传 dev（runner 断言
    `READ_LOG.splits() == {'dev'}`，否则抛 HoldoutLeakError）。这里再检查一次：
    任何 `split != 'dev'` 的场景都会让生成器拒绝工作。
    """
    scenarios = list(dev_scenarios)
    if not scenarios:
        raise IllegalCandidateError('缺少 dev 证据，不能生成候选')
    leaked = sorted({s.split for s in scenarios if s.split != 'dev'})
    if leaked:
        raise IllegalCandidateError(f'生成器只允许 dev 证据，收到分组 {leaked}')

    families = {s.family for s in scenarios}
    rules: list[ExpressionRule] = [
        ExpressionRule('identity_ask',
                       '您好，这里是模拟商户服务AI。为保护商家信息，请先确认您是否是{company}的联系人？'),
        ExpressionRule('guarantee_refusal',
                       '补齐材料后仍需审核，不能保证通过，也不能承诺时限；是否通过以审核结论为准。'),
        ExpressionRule('unclear_clarify',
                       '我没有完全理解，请确认您是想了解驳回原因，还是整改要补的材料？'),
        ExpressionRule('wrong_person_close',
                       '已记录本次为非本人接听，不会披露任何申请详情，后续由人工处理。'),
    ]
    if 'knowledge_conflict' in families:
        rules.append(ExpressionRule('conflict_escalation',
                                    '当前知识依据存在冲突，已转人工核实，暂不给出具体标准。'))
    if 'callback_exception' in families:
        rules.append(ExpressionRule('callback_human',
                                    '回电或改期需要人工协调确认具体时间窗，本演示不自动预约，也不代表真人已接通。'))
    if 'complaint_risk' in families:
        rules.append(ExpressionRule('complaint_human',
                                    '已记录投诉风险，转人工处理，本次不自动办理驳回。'))

    spec = replace(CANDIDATE_PROMPT, spec_id=spec_id)
    for rule in rules:
        spec = replace(spec, **{rule.field: rule.text})
    candidate = Candidate(spec=spec, rules=tuple(rules),
                          notes=f'由 dev 家族 {sorted(families)} 自动提出，只改表达与理解开关')
    return validate_candidate(candidate)


def diff_fields(candidate: Candidate, base: PromptSpec = BASELINE_PROMPT) -> list[dict]:
    """结构化 diff，用于版本证据与审查。"""
    out: list[dict] = []
    for field in SPEC_FIELDS:
        before, after = getattr(base, field), getattr(candidate.spec, field)
        if before != after:
            out.append({
                'field': field,
                'before': list(before) if isinstance(before, tuple) else before,
                'after': list(after) if isinstance(after, tuple) else after,
            })
    return out


def reject_guard_payload_example() -> dict:
    """给测试/审查用的非法候选样例（不参与正常运行）。"""
    return {'review_conclusion': 'approved', 'route': 'jingmai', 'verified': True}
