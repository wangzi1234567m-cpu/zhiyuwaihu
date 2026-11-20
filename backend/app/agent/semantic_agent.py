"""生产 Agent 实现：结构化理解 -> 依据选择 -> 决策提议（不派发、不写库）。

边界（与 docs/agent/CONTRACT-CHANGES.md、docs/agent/TOOL-GUARDS.md 一致）：
- 输入只有 `AgentPort.handle_turn(input, context, history, case, knowledge, verified, cancel)`。
- `verified` 由协调器用**服务端证据**给出，Agent 只读；Agent 不能把它改成 True，
  也不能在未核验时披露驳回原因/整改。
- Agent 只输出 `AgentDecision`（回复文本、动作提议、next_state、understanding）；
  动作的真实派发由协调器在 `ToolGuard` 之后统一执行。
- 不连数据库、不读第二套权威历史、不调用 HTTP 工具。
- 依据选择：scenario_id + knowledge_version + scope + status=approved + 无 issue_codes；
  多条正文冲突 → 转人工，绝不挑第一条应付。
- 缺必需槽位不播空模板（`required_slot_missing` 交协调器改问缺项）。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ..contracts import (AgentDecision, KnowledgeEntry, ReviewCase, SessionContext,
                         TurnInput, Understanding)
from ..semantic import RuleUnderstanding, UnderstandingPort, parse_understanding


# ------------------------------------------------------------------ 依据选择


def select_grounding(case: ReviewCase, knowledge, *,
                     knowledge_version: str | None = None,
                     scope=('synthetic',),
                     route: str | None = None) -> tuple[list[KnowledgeEntry], bool]:
    """按 scenario/version/scope/approved/no-conflict 选依据。

    返回 (可用条目, 是否冲突)。冲突时不猜答案，调用方必须转人工。
    `route` 只用于**偏好**：渠道与发布渠道一致的条目优先，但不会因为渠道不同就
    挑一条不相关的依据，也不会掩盖正文冲突。
    """
    approved = [
        entry for entry in knowledge
        if entry.status == 'approved' and not entry.issue_codes and entry.approved_text
        and entry.scenario_id == case.scenario_id
        and (knowledge_version is None or entry.knowledge_version == knowledge_version)
        and set(scope).issubset(set(entry.scope))
    ]
    if route in ('phone', 'jingmai') and approved:
        # 渠道匹配是"排序偏好"而非过滤：否则京麦-only 场景会拿不到依据。
        approved = sorted(approved, key=lambda e: 0 if e.channel == route else 1)
    texts = {entry.approved_text for entry in approved}
    return approved, len(texts) > 1


def missing_required_slots(case: ReviewCase, entry: KnowledgeEntry,
                           understanding: Understanding | None) -> list[str]:
    """已审依据声明必需但本轮仍未取得的槽位；缺项不播空模板。"""
    slots = dict(case.slots)
    if understanding is not None:
        slots.update({k: v for k, v in understanding.slots.items() if v})
    missing = [name for name in entry.required_slots if not str(slots.get(name) or '').strip()]
    # company_name 由审核任务提供，属于可信事实，不算"用户没给"。
    return [name for name in missing if name != 'company_name']


# ------------------------------------------------------------------ 表达模板


@dataclass(frozen=True)
class ReplyTemplates:
    """表达模板：只描述措辞，不含任何审核结论/权限/数字。

    所有业务事实（原因、整改、依据）都必须来自已审核知识条目正文，模板只做包装。
    """

    identity_ask: str = '这里是模拟商户服务AI。请确认您是否是{company}的联系人。'
    identity_clarify: str = '抱歉没听清。为保护商家信息，请确认您是否是{company}的联系人，我再按已审核依据说明。'
    guarantee_refusal: str = '补齐材料后仍需审核，不能保证通过，也不能承诺时限；是否通过以审核结论为准。'
    source_grounding: str = '以上结论来自已审核依据，具体数字和时限未核实前我们不作答复。'
    missing_knowledge: str = '当前缺少已审核依据，需要人工核实，暂不办理。'
    conflict_escalation: str = '当前知识依据存在冲突，已转人工核实，暂不给出具体标准。'
    callback_human: str = '回电或改期需要人工确认具体时间窗，本演示不自动预约，也不代表真人已接通。'
    complaint_human: str = '已记录投诉风险，转人工处理，本次不自动办理驳回。'
    unclear_clarify: str = '我没有完全理解，请确认您是想了解驳回原因，还是整改要补的材料？'
    ungrounded_escalation: str = '我没能确认您的具体诉求，为避免误判，已转人工核实，本次不办理任何业务。'
    wrong_person_close: str = '已记录本次为非本人接听，不会披露任何申请详情，后续由人工处理。'
    refused_close: str = '已记录您的要求，停止本次告知，后续不再自动联系。'
    action_notice: str = '已记录您的反馈，正在提交模拟处理；请以页面业务结果为准。'
    understanding_unavailable: str = '理解服务暂时不可用，已转人工核实，稍后由人工联系您。'


DEFAULT_TEMPLATES = ReplyTemplates()


# ------------------------------------------------------------------ Agent


class SemanticAgent:
    """结构化理解驱动的 Agent（生产实现）。

    理解端口可注入：默认 `RuleUnderstanding()`（确定性，测试可重复）；
    真实模型模式由 `Services` 注入 `GlmUnderstanding`（见 `app.semantic`）。
    """

    def __init__(self, understanding: UnderstandingPort | None = None, *,
                 templates: ReplyTemplates | None = None,
                 knowledge_version: str | None = None,
                 scope=('synthetic',)) -> None:
        self.understanding = understanding or RuleUnderstanding()
        self.templates = templates or DEFAULT_TEMPLATES
        self.knowledge_version = knowledge_version
        self.scope = tuple(scope)
        #: 可观测计数（供探针/证据使用，不含任何密钥）。
        self.calls = 0
        self.understanding_failures = 0

    # -- 公开端口 ----------------------------------------------------
    async def handle_turn(self, input: TurnInput, context: SessionContext,
                          history: tuple, case: ReviewCase,
                          knowledge: tuple[KnowledgeEntry, ...], verified: bool,
                          cancel: asyncio.Event) -> AgentDecision:
        if cancel.is_set():
            raise asyncio.CancelledError('cancelled before agent call')
        self.calls += 1
        text = input.text
        message_ids = tuple(m.message_id for m in history) + (input.message_id,)
        try:
            understanding = await self.understanding.understand(
                text=text, case=case, context=context, knowledge=knowledge,
                message_ids=[str(m) for m in message_ids])
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 理解失败必须如实上报，不猜意图
            self.understanding_failures += 1
            return AgentDecision(
                reply_text=self.templates.understanding_unavailable,
                proposed_action='none', evidence_message_ids=[input.message_id],
                knowledge_entry_ids=[], next_state='MANUAL_REVIEW',
                understanding=parse_understanding(text),
                required_slot_missing=[])
            _ = exc
        return self.decide(input=input, context=context, case=case,
                           knowledge=knowledge, verified=verified,
                           understanding=understanding)

    # -- 纯决策（可单独测试，无异步/无 IO）---------------------------
    def decide(self, *, input: TurnInput, context: SessionContext, case: ReviewCase,
               knowledge, verified: bool, understanding: Understanding) -> AgentDecision:
        intents = list(understanding.intents)
        templates = self.templates
        company = case.slots.get('company_name', '')

        def reply(template: str) -> str:
            return template.replace('{company}', company)

        def decision(text_out: str, state: str, *, action: str = 'none', payload=None,
                     entries: tuple[KnowledgeEntry, ...] = (),
                     missing: list[str] | None = None,
                     outcome: str | None = None) -> AgentDecision:
            return AgentDecision(
                reply_text=text_out, proposed_action=action,  # type: ignore[arg-type]
                action_payload=payload,
                evidence_message_ids=[input.message_id],
                knowledge_entry_ids=[e.entry_id for e in entries] if verified else [],
                next_state=state,  # type: ignore[arg-type]
                understanding=understanding,
                contact_outcome=outcome,  # type: ignore[arg-type]
                required_slot_missing=missing or [])

        # 1) 错人 / 拒绝联系：最先处理，绝不披露任何申请详情，也绝不自动驳回。
        if 'deny_identity' in intents:
            return decision(reply(templates.wrong_person_close), 'CLOSING',
                            outcome='wrong_person')
        if 'refuse_contact' in intents:
            return decision(reply(templates.refused_close), 'CLOSING', outcome='refused')

        # 2) 未核验：只能确认身份，不能确认"您就是本人"以外的任何业务事实。
        if not verified:
            if 'confirm_identity' in intents and understanding.needs_clarification is False:
                return decision(reply(templates.identity_ask), 'VERIFYING')
            return decision(reply(templates.identity_clarify), 'VERIFYING')

        # 3) 已核验：先选依据（scenario/version/scope/approved/no-conflict）。
        #    知识版本以**会话绑定的版本**为准（来自数据库），Agent 不能自行放宽。
        version = self.knowledge_version or context.knowledge_version
        route = context.routing_decision.route if context.routing_decision else None
        entries, conflict = select_grounding(case, knowledge,
                                             knowledge_version=version,
                                             scope=self.scope,
                                             route=route)
        if not entries:
            return decision(reply(templates.missing_knowledge), 'MANUAL_REVIEW')
        if conflict:
            # 依据互相矛盾：不续写、不挑第一条，转人工。
            return decision(reply(templates.conflict_escalation), 'MANUAL_REVIEW')
        entry = entries[0]
        ground = entry.approved_text or ''

        already_explained = any(n.completed for n in context.notifications)
        wants_reason = any(i in intents for i in ('ask_reason', 'ask_remedy', 'ask_outcome'))
        sections: list[str] = []
        if not already_explained or wants_reason:
            sections.append(reply(ground))
        if 'ask_guarantee' in intents:
            sections.append(reply(templates.guarantee_refusal))
        if 'ask_source' in intents:
            sections.append(reply(templates.source_grounding))

        missing = missing_required_slots(case, entry, understanding)

        def spoken(extra: str = '') -> str:
            parts = list(sections)
            if extra:
                parts.append(extra)
            if not parts:
                parts.append(reply(ground))
            return ' '.join(parts)

        next_base = 'QA' if already_explained else 'EXPLAINING'

        # 4) 投诉/曝光风险：转人工，不自动驳回。
        if 'complaint_risk' in intents:
            return decision(spoken(reply(templates.complaint_human)), 'MANUAL_REVIEW',
                            missing=missing)
        # 5) 回电/改期/预约：人工确认时间窗；不自动预约，不称真人已接通。
        if 'request_callback' in intents:
            return decision(spoken(reply(templates.callback_human)), 'CALLBACK_PENDING',
                            missing=missing)
        # 6) 保证类追问：不承诺必过。
        if 'ask_guarantee' in intents:
            return decision(spoken(), next_base, entries=(entry,), missing=missing)
        # 7) 明确表示理解/按依据处理：进入动作准备。
        #    “明白了”只是理解，不是购买授权；后台仍按审核任务+路由+证据处理。
        #    但**多意图**时不能凭一句"明白了"就跳过用户还没得到回答的追问：
        #    "明白了，那到底哪里出的问题?" 仍属未答完，必须先如实说明再谈办理。
        open_questions = [i for i in intents
                          if i in ('ask_reason', 'ask_remedy', 'ask_guarantee', 'ask_source',
                                   'ask_outcome', 'unclear')]
        if 'acknowledge' in intents and not missing and not open_questions:
            return self._action_decision(input=input, context=context, case=case,
                                         entry=entry, understanding=understanding,
                                         reply=reply, prefix=' '.join(sections))
        # 8) 原因/整改/结果/来源：只播已审依据（不拿 knowledge[0] 应付全部问题）。
        if any(i in intents for i in ('ask_reason', 'ask_remedy', 'ask_outcome', 'ask_source')):
            return decision(spoken(), next_base, entries=(entry,), missing=missing)
        # 8.5) 理解层给了高风险意图（否认身份/拒绝联系/投诉）但**原文不支持**，
        #      且降级后没有任何可证诉求：这是"模型自述当依据"，必须转人工，
        #      不能把它当成正常理解继续走到派发。
        if understanding.clarification_reason == 'grounding_insufficient':
            return decision(reply(templates.ungrounded_escalation), 'MANUAL_REVIEW',
                            missing=missing)
        # 9) 澄清：不猜意图；首次告知时同时给出已审原因/整改。
        if understanding.needs_clarification or 'unclear' in intents:
            return decision(spoken(reply(templates.unclear_clarify)), next_base,
                            entries=(entry,), missing=missing)
        # 10) 其他（含多意图中只剩已覆盖项）：播已审依据，保持会话。
        return decision(spoken(), next_base, entries=(entry,), missing=missing)

    # -- 动作提议（仍由协调器守卫后派发）-----------------------------
    def _action_decision(self, *, input: TurnInput, context: SessionContext,
                         case: ReviewCase, entry: KnowledgeEntry,
                         understanding: Understanding, reply, prefix: str) -> AgentDecision:
        from ..contracts import FollowupPayload, RejectPayload

        def text_out(template: str) -> str:
            body = reply(template)
            return f'{prefix} {body}'.strip() if prefix else body

        codes = set(context.routing_decision.reason_codes) if context.routing_decision else set()
        if 'no_answer_second_submission_followup' in codes:
            # 跟进是动作不是渠道；真实派发前仍要守卫校验"可信未接通"。
            payload = FollowupPayload(type='create_followup',
                                      reason_code='no_answer_second_submission',
                                      related_operation_id=None)
            return AgentDecision(reply_text=text_out(self.templates.action_notice),
                                 proposed_action='create_followup', action_payload=payload,
                                 evidence_message_ids=[input.message_id],
                                 knowledge_entry_ids=[entry.entry_id],
                                 next_state='ACTION_READY', understanding=understanding)
        payload = RejectPayload(type='reject_application', reason_codes=[case.scenario_id],
                                written_text=entry.approved_text or '',
                                knowledge_entry_ids=[entry.entry_id])
        return AgentDecision(reply_text=text_out(self.templates.action_notice),
                             proposed_action='reject_application', action_payload=payload,
                             evidence_message_ids=[input.message_id],
                             knowledge_entry_ids=[entry.entry_id],
                             next_state='ACTION_READY', understanding=understanding)

    # -- 可观测（不含密钥）-------------------------------------------
    def public_view(self) -> dict:
        base = {'agent': type(self).__name__, 'calls': self.calls,
                'understanding_failures': self.understanding_failures,
                'understanding': getattr(self.understanding, 'name', 'unknown')}
        port = getattr(self.understanding, 'public_view', None)
        if callable(port):
            base['model'] = port()
        return base
