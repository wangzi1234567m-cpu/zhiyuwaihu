"""失败归因：把失败断言分配到 理解 / 路由 / 守卫 / 知识 / 状态 / 工具 / 表达 层。

规则（docs/design/08-evaluation-rsi.md §RSI 归因）：
- 知识缺失/冲突 -> knowledge（交翟处理，不进 Prompt 优化范围）
- 路由/证据不符 -> routing（规则/门槛问题，必人工）
- 守卫拒绝/未授权动作 -> guard（权限问题，交魏/王修代码）
- 状态机非法 -> state
- 工具效果/幂等/回写 -> tool
- 未核验泄露/意图漏识别 -> understanding
- 禁语/必含措辞 -> expression（唯一进 Prompt 候选的层）

“合理拒绝/正确人工升级”单列为 correct_handling，不算告知成功也不算失败优化项。

本轮修复（t4 保留集识别缺陷）新增 `harness` 层：
    如果失败场景的标注意图**生产理解层本来就能识别**，但 RSI 自己的副本识别不出来，
    那么这条失败是**评测脚手架缺陷**，不是候选/业务缺陷。归因必须写成 `harness`
    并标记 `actionable_by_prompt=False`，否则会把"脚手架过期"错记成"模型没泛化"，
    进而逼着优化器去改提示词——那正是伪造提升的来源。
"""
from __future__ import annotations

from .simulator import ScenarioRun

#: 断言 id -> 层（细粒度覆盖 assertions 的默认 layer）。
ASSERTION_LAYER = {
    'route_matches_expected': 'routing',
    'routing_evidence_present': 'routing',
    'jingmai_requires_routing_decision': 'routing',
    'terminal_state_allowed': 'state',
    'no_invalid_transition': 'state',
    'business_effect_once': 'tool',
    'business_effect_absent': 'tool',
    'business_effect_kind': 'tool',
    'tool_not_blocked_by_guard': 'guard',
    'no_leak_before_verify': 'understanding',
    'no_forbidden_claims': 'expression',
    'required_terms_present': 'knowledge',
    'no_auto_reject_on_wrong_person_or_refusal': 'guard',
    'no_effect_without_approved_knowledge': 'knowledge',
    'escalates_instead_of_guessing': 'knowledge',
    'no_fake_voice_verification': 'guard',
    'writeback_attempted': 'tool',
    'writeback_pending_not_rerejected': 'tool',
    'lost_response_same_key_query': 'tool',
    'lost_response_no_duplicate_effect': 'tool',
    'timeout_bounded_dispatch': 'tool',
    'cross_session_single_effect': 'tool',
    'no_runtime_error': 'tool',
}

#: 这些层不由 Prompt 候选修复（必须交人/交代码）。
#: 修复：`understanding` 必须在这里。理解层认不出意图时，改提示词只会让表达更好看、
#: 业务动作照样不发生——把它排除在外，正是"用改提示词掩盖理解缺口"的来源。
NON_PROMPT_LAYERS = frozenset({'understanding', 'knowledge', 'routing', 'guard', 'state',
                               'tool', 'harness'})

#: 症状层 -> 只在"期望动作却全程无派发"时才允许下沉为 understanding。
#: 这是**派生**结论（依据 ledger 事实），不是把任何失败都推给理解层。
NO_ACTION_SYMPTOM_ASSERTIONS = frozenset({
    'terminal_state_allowed', 'business_effect_once', 'business_effect_kind',
    'writeback_attempted', 'cross_session_single_effect',
})


def attribute(run: ScenarioRun, scenario=None, *, harness_defect: bool = False) -> list[dict]:
    """返回归因条目：{assertion, layer, actionable_by_prompt, reason}。

    `scenario` 用于把 `required_terms_present` 区分开：知识已审且齐全时，缺条款是
    **表达**问题（Prompt 可修）；知识缺失/冲突/隔离时才是**知识**问题（交翟，不可 Prompt 修）。

    `harness_defect=True` 表示本条 run 的失败已被判定为"评测脚手架自身缺陷"
    （RSI 识别规则落后于生产理解层），此时所有条目归入 `harness` 层且不可由 Prompt 修复。
    """
    knowledge_deficient = scenario is not None and scenario.knowledge_mode in (
        'missing', 'conflict', 'quarantined')
    # 期望业务动作但整轮无派发、也无守卫拒绝 = 症状在 state/tool，根因在理解/脚手架。
    no_action_root_cause = (
        scenario is not None
        and scenario.expected.tool_effect == 'one'
        and not run.ledger.dispatched
        and not run.ledger.denials
    )
    items: list[dict] = []
    for assertion in run.assertions:
        if assertion['passed']:
            continue
        layer = ASSERTION_LAYER.get(assertion['id'], assertion.get('layer', 'tool'))
        if assertion['id'] == 'required_terms_present' and not knowledge_deficient:
            layer = 'expression'
        if harness_defect:
            layer = 'harness'
        elif no_action_root_cause and assertion['id'] in NO_ACTION_SYMPTOM_ASSERTIONS:
            # 症状下沉：state/tool 是表象，真正没跑起来的是理解/决策。
            layer = 'understanding'
        elif no_action_root_cause and layer == 'expression':
            # 修复：整轮根本没走到"播出已审依据"这一步时，缺条款不是措辞问题。
            # 旧逻辑会把它标成 actionable_by_prompt=True，等于让优化器去改提示词掩盖
            # 理解缺口——那是伪造提升。这里如实下沉到 understanding。
            layer = 'understanding'
        items.append({
            'assertion': assertion['id'],
            'layer': layer,
            'actionable_by_prompt': layer == 'expression' and not harness_defect,
            'reason': _reason(assertion['id'], run),
            'detail': assertion.get('detail', ''),
            'symptom_layer': ASSERTION_LAYER.get(assertion['id'],
                                                 assertion.get('layer', 'tool')),
        })
    if run.error and run.error.startswith('budget_exhausted'):
        items.append({'assertion': 'budget', 'layer': 'tool',
                      'actionable_by_prompt': False,
                      'reason': 'evidence_insufficient：预算不足，未完成该场景（不重试）',
                      'detail': run.error})
    return items


def _reason(assertion_id: str, run: ScenarioRun) -> str:
    ledger = run.ledger
    if assertion_id == 'route_matches_expected':
        return f'路由不符：实际 {run.route}/{run.reason_codes}'
    if assertion_id == 'terminal_state_allowed':
        return f'终态不符：{run.final_state}，轨迹 {run.states}'
    if assertion_id in ('business_effect_once', 'business_effect_absent'):
        return f'业务效果计数异常：{ledger.effect_count()}，kinds={ledger.kinds()}'
    if assertion_id == 'tool_not_blocked_by_guard':
        return f'守卫拒绝了期望动作：{ledger.denials}'
    if assertion_id == 'no_leak_before_verify':
        return '核验前出现已审知识正文（可能泄露驳回详情）'
    if assertion_id == 'no_forbidden_claims':
        return '回复出现承诺通过/谎称成功的措辞'
    if assertion_id == 'required_terms_present':
        return '回复缺少应有业务条款（原因/整改或应有的人工/依据说明）'
    if assertion_id in ('no_effect_without_approved_knowledge', 'escalates_instead_of_guessing'):
        return '缺知识/冲突仍派发或未转人工'
    if assertion_id == 'no_fake_voice_verification':
        return '京麦路径出现伪造语音核验'
    if assertion_id in ('lost_response_same_key_query', 'lost_response_no_duplicate_effect'):
        return '丢响应查证/幂等异常'
    if assertion_id == 'no_runtime_error':
        return f'运行期异常：{run.error}'
    return '断言失败'


def summarize(attribution_items: list[dict], runs: list[ScenarioRun]) -> dict:
    """按层汇总归因分布，并单列“正确处置”场景。"""
    by_layer: dict[str, int] = {}
    for item in attribution_items:
        by_layer[item['layer']] = by_layer.get(item['layer'], 0) + 1
    correct_handling = sum(
        1 for r in runs
        if r.passed and r.final_state in ('MANUAL_REVIEW', 'CLOSING')
        and r.ledger.effect_count() == 0)
    return {
        'by_layer': dict(sorted(by_layer.items())),
        'failed_assertions': len(attribution_items),
        'prompt_actionable': sum(1 for i in attribution_items if i['actionable_by_prompt']),
        'by_assertion': dict(sorted(
            (k, sum(1 for i in attribution_items if i['assertion'] == k))
            for k in {i['assertion'] for i in attribution_items})),
        'correct_handling_scenarios': correct_handling,
        'total_runs': len(runs),
    }
