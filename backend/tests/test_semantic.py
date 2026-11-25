"""T2 语义表达与结构化理解测试（无数据库、无网络，可用 pytest 直接跑）。

覆盖（按任务要求）：否定 / 多意图 / 澄清 / 拒绝 / 错人 / 未通 / 缺知识 / 未知 / 预约改期，
外加：模型不得自造 verified 或成功、越权字段/未知字段/伪造证据一律拒绝、
依据按 scenario/version/scope/approved/no-conflict 选择、缺必需槽位不播空模板、
FakeModel（fixture_response）路径完整保留。

这些用例**不硬编码评测答案**：断言的是一致性/安全边界（例如"未核验不得出现原因正文"、
"不承诺通过"、"冲突转人工"），而不是某一句模板字符串的全文匹配；
表达差异通过公共模板常量引用，避免文案微调就误报。
"""
from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from app.agent import SemanticAgent, select_grounding
from app.agent.semantic_agent import DEFAULT_TEMPLATES, missing_required_slots
from app.adapters import FakeModel
from app.contracts import (AgentDecision, KnowledgeEntry, ModelRequest, NotificationEvidence,
                           ReviewCase, RoutingDecision, RoutingEvidence, SessionContext,
                           SourceRef, TurnInput)
from app.semantic import (FORBIDDEN_UNDERSTANDING_KEYS, RuleUnderstanding, build_understanding,
                          detect_intents, parse_understanding, validate_understanding)


# ------------------------------------------------------------------ helpers


def make_case(**overrides) -> ReviewCase:
    base = dict(application_id=uuid4(), merchant_id=uuid4(), case_revision=1,
                scenario_id='demo.authorization_missing',
                slots={'company_name': '演示商家1', 'reason': '模拟授权书缺项',
                       'remedy': '请在模拟业务入口补充完整授权书。'},
                review_conclusion='rejected', submission_attempt=1, call_attempt=0,
                callback_requested=False, complaint_risk=False)
    base.update(overrides)
    return ReviewCase(**base)


def make_entry(case: ReviewCase, *, text=None, version='demo-k1', scope=('synthetic',),
               status='approved', issues=(), channel='phone',
               required_slots=('company_name',)) -> KnowledgeEntry:
    return KnowledgeEntry(entry_id=uuid4(), knowledge_version=version, kind='scenario',
                          scenario_id=case.scenario_id, scope=list(scope), channel=channel,
                          required_slots=list(required_slots),
                          approved_text=text if text is not None else
                          f'{case.slots["reason"]}。{case.slots["remedy"]}',
                          source_refs=[SourceRef(file_sha256='0' * 64, sheet='synthetic',
                                                 row=1, columns=['text'])],
                          status=status, issue_codes=list(issues))


def make_routing(case: ReviewCase, *, route='phone', codes=('phone_explain',)) -> RoutingDecision:
    return RoutingDecision(decision_id=uuid4(), application_id=case.application_id,
                           case_revision=case.case_revision, route=route,
                           reason_codes=list(codes), rule_version='route-r2',
                           required_evidence=[], evidence=[RoutingEvidence(code=codes[0])],
                           submission_attempt=case.submission_attempt,
                           call_attempt=case.call_attempt)


def make_context(case: ReviewCase, **overrides) -> SessionContext:
    base = dict(session_id=uuid4(), turn_id=uuid4(), generation_id=uuid4(),
                generation_epoch=0, application_id=case.application_id, case_revision=1,
                state_revision=0, strategy_version='demo-p1', rule_version='route-r2',
                knowledge_version='demo-k1', model_version='fake-v1', state='VERIFYING')
    base.update(overrides)
    return SessionContext(**base)


def turn(agent, text, case, entries, context, verified):
    return agent.handle_turn(TurnInput(message_id=uuid4(), text=text, source='text'),
                             context, (), case, entries, verified, asyncio.Event())


# ------------------------------------------------------------------ 意图理解


INTENT_CASES = [
    ('不是，打错了', {'deny_identity'}),
    ('我不是这家公司的', {'deny_identity'}),
    ('不用再打了，谢谢', {'refuse_contact'}),
    ('补齐后一定能通过吗', {'ask_guarantee'}),
    ('这条依据是哪份文件', {'ask_source'}),
    ('能帮我约个明天下午回电吗', {'request_callback'}),
    ('明白了', {'acknowledge'}),
    ('呃……那个啥', {'unclear'}),
    ('我要投诉并且曝光你们', {'complaint_risk'}),
    ('为什么驳回，另外要补什么材料', {'ask_reason', 'ask_remedy'}),
    ('审核结果多久能出来，还要重新提交吗', {'ask_outcome'}),
]

#: 表达**家族**泛化用例：这些句子不来自任何评测语料，必须是同一意图家族的新说法。
#: 目的：证明识别按表达家族泛化，而不是记住某几个具体句子。
FAMILY_CASES = [
    ('清楚了，那就按这个办', 'acknowledge'),
    ('了解，我照这个走', 'acknowledge'),
    ('收到，就按流程处理吧', 'acknowledge'),
    ('好的，那就这么办', 'acknowledge'),
    ('行，那就按这个流程走吧', 'acknowledge'),
    ('是哪一个资质出的问题', 'ask_reason'),
    ('麻烦说明一下原因', 'ask_reason'),
    ('到底哪一项不符', 'ask_reason'),
    ('麻烦讲一下是哪里出的问题', 'ask_reason'),
    ('我还需要提交哪些资料', 'ask_remedy'),
    ('要准备什么东西', 'ask_remedy'),
    ('需要我上传些什么呢', 'ask_remedy'),
    ('你们能不能电话联系我', 'request_callback'),
    ('可以直接打电话跟我讲吗', 'request_callback'),
    ('驳回的事能不能直接电话跟我说', 'request_callback'),
    ('你打错了吧', 'deny_identity'),
    ('这号码不是我的', 'deny_identity'),
    ('以后别再打过来了', 'refuse_contact'),
    ('不必再联系', 'refuse_contact'),
    ('我会去12315举报', 'complaint_risk'),
]


@pytest.mark.parametrize('text,expected', INTENT_CASES)
def test_intent_rules_cover_expression_families(text, expected):
    assert set(detect_intents(text)) == expected


@pytest.mark.parametrize('text,intent', FAMILY_CASES)
def test_expression_families_generalize_beyond_fixture_sentences(text, intent):
    """同一表达家族的新说法必须被识别（不是背句子，也不是只认评测原文）。"""
    assert intent in detect_intents(text)


@pytest.mark.parametrize('text,expected', [
    ('嗯……那个啥', {'unclear'}),
    ('不需要，别再联系了', {'refuse_contact'}),
])
def test_vague_and_refusal_are_not_misread_as_consent(text, expected):
    """负例：含糊/拒绝不得被放宽成"认同处理"，否则会误触发业务动作。"""
    assert set(detect_intents(text)) == expected


def test_denial_wins_over_polite_self_assertion():
    # “我不是…”“我是…”同时出现时必须按否认处理，不能当成自然身份表达。
    assert detect_intents('我不是这家公司的') == ['deny_identity']
    assert parse_understanding('我不是这家公司的').contact_outcome_hint == 'wrong_person'


def test_multi_intent_keeps_every_requested_subject():
    understanding = parse_understanding('为什么驳回，另外要补什么材料')
    assert understanding.multi_intent is True
    assert {'ask_reason', 'ask_remedy'}.issubset(set(understanding.intents))


def test_unclear_requests_clarification_instead_of_guessing():
    understanding = parse_understanding('呃……那个啥')
    assert understanding.needs_clarification is True
    assert understanding.intents == ['unclear']


# --------------------------------------------- 服务端校验（模型不得越权）


@pytest.mark.parametrize('payload', [
    {'intents': ['acknowledge'], 'verified': True},
    {'intents': ['acknowledge'], 'route': 'jingmai'},
    {'intents': ['acknowledge'], 'approved': True},
    {'intents': ['acknowledge'], 'success': 'succeeded'},
    {'intents': ['acknowledge'], 'knowledge_entry_ids': [str(uuid4())]},
    {'intents': ['acknowledge'], 'next_state': 'ACTION_READY'},
    {'intents': ['acknowledge'], 'unexpected_field': 1},
    {'intents': ['approve_application']},
])
def test_server_rejects_smuggled_or_unknown_understanding(payload):
    with pytest.raises(ValueError):
        validate_understanding(payload)


def test_every_forbidden_key_is_actually_rejected():
    for key in sorted(FORBIDDEN_UNDERSTANDING_KEYS):
        with pytest.raises(ValueError):
            validate_understanding({'intents': ['acknowledge'], key: 'x'})


def test_model_cannot_claim_verified_or_no_answer_outcome():
    for claim in ('verified', 'no_answer', 'disconnected', 'not_required'):
        result = validate_understanding({'intents': ['acknowledge'],
                                         'contact_outcome_hint': claim,
                                         'source': 'glm'}, text='明白了')
        assert result.contact_outcome_hint is None


def test_model_cannot_reference_messages_it_never_saw():
    with pytest.raises(ValueError):
        validate_understanding({'intents': ['ask_reason'],
                                'evidence_message_ids': [str(uuid4())]},
                               message_ids=[str(uuid4())])


def test_server_recomputes_negation_and_multi_intent():
    result = validate_understanding({'intents': ['ask_reason', 'ask_remedy'],
                                     'negated': False, 'multi_intent': False,
                                     'source': 'glm'}, text='不需要解释了')
    assert result.negated is True          # 服务端按原文重算，不采信模型自述
    assert result.multi_intent is True


# ------------------------------------------- 服务端校验：槽位证据与越权降级


def test_slots_must_have_evidence_in_user_text():
    """模型填的槽位是"理解"，不是事实：原文没提到的值必须丢弃。"""
    result = validate_understanding(
        {'intents': ['ask_remedy'], 'source': 'glm',
         'slots': {'contact_name': '蓝海贸易', 'brand_name': '凭空品牌'}},
        text='我是蓝海贸易的，要补什么材料')
    assert result.slots.get('contact_name') == '蓝海贸易'
    assert 'brand_name' not in result.slots


@pytest.mark.parametrize('authority_slot', ['company_name', 'reason', 'remedy',
                                            'review_conclusion', 'knowledge_version'])
def test_model_cannot_overwrite_server_authority_slots(authority_slot):
    """审核结论/主体/原因/整改/版本属于审核任务与已审知识，模型不能覆盖。"""
    result = validate_understanding(
        {'intents': ['ask_reason'], 'source': 'glm',
         'slots': {authority_slot: '蓝海贸易'}}, text='我是蓝海贸易的')
    assert authority_slot not in result.slots


@pytest.mark.parametrize('claimed_text', ['明白了', '好，那就按这个办'])
def test_model_cannot_end_session_on_high_impact_intent_without_support(claimed_text):
    """否认身份/拒绝联系/投诉必须有原文支撑，否则降级——模型不能凭一句结束会话。"""
    result = validate_understanding(
        {'intents': ['deny_identity', 'refuse_contact', 'complaint_risk'],
         'contact_outcome_hint': 'wrong_person', 'source': 'glm'}, text=claimed_text)
    assert 'deny_identity' not in result.intents
    assert 'refuse_contact' not in result.intents
    assert 'complaint_risk' not in result.intents
    assert result.contact_outcome_hint is None


def test_high_impact_intent_is_kept_when_text_proves_it():
    result = validate_understanding({'intents': ['deny_identity'], 'source': 'glm'},
                                    text='不是，打错了')
    assert result.intents == ['deny_identity']
    assert result.contact_outcome_hint == 'wrong_person'


def test_model_cannot_return_free_text_as_reply():
    """Understanding 没有任何正文/回复槽位：模型输出无法直接变成 Agent 回复。"""
    from app.contracts import Understanding
    forbidden = {'reply_text', 'reply', 'text', 'answer', 'spoken_text', 'message'}
    assert not (forbidden & set(Understanding.model_fields))


# ------------------------------------------------------------------ Agent 主链


async def test_unverified_never_discloses_reason_or_remedy():
    case, entry = make_case(), None
    entry = make_entry(case)
    context = make_context(case, routing_decision=make_routing(case))
    decision = await turn(SemanticAgent(RuleUnderstanding()), '我是演示商家1',
                          case, (entry,), context, False)
    assert decision.next_state == 'VERIFYING'
    assert entry.approved_text not in decision.reply_text
    assert decision.knowledge_entry_ids == []


async def test_wrong_person_closes_without_auto_rejection():
    case = make_case()
    entry = make_entry(case)
    context = make_context(case, routing_decision=make_routing(case))
    decision = await turn(SemanticAgent(), '不是，打错了', case, (entry,), context, False)
    assert decision.next_state == 'CLOSING'
    assert decision.proposed_action == 'none'
    assert decision.contact_outcome == 'wrong_person'
    assert entry.approved_text not in decision.reply_text


async def test_refusal_closes_and_records_as_is():
    case = make_case()
    entry = make_entry(case)
    context = make_context(case, routing_decision=make_routing(case))
    decision = await turn(SemanticAgent(), '不需要，别再联系了', case, (entry,), context, True)
    assert decision.next_state == 'CLOSING'
    assert decision.proposed_action == 'none'
    assert decision.contact_outcome == 'refused'


async def test_verified_explains_reason_and_remedy_from_approved_knowledge():
    case = make_case()
    entry = make_entry(case)
    context = make_context(case, routing_decision=make_routing(case))
    decision = await turn(SemanticAgent(), '为什么驳回', case, (entry,), context, True)
    assert entry.approved_text in decision.reply_text
    assert decision.next_state == 'EXPLAINING'
    assert decision.proposed_action == 'none'
    assert decision.knowledge_entry_ids == [entry.entry_id]


async def test_guarantee_question_never_promises_approval():
    case = make_case()
    entry = make_entry(case)
    context = make_context(case, routing_decision=make_routing(case))
    decision = await turn(SemanticAgent(), '补齐后一定必过吗', case, (entry,), context, True)
    assert DEFAULT_TEMPLATES.guarantee_refusal in decision.reply_text
    assert entry.approved_text in decision.reply_text
    assert decision.proposed_action == 'none'
    # 否定式工作法：不得出现"承诺通过"的正向断言（模板本身含"不能保证通过"是合规表述）。
    for forbidden in ('一定能过', '包过', '已通过审核', '已经批准', '已办理成功'):
        assert forbidden not in decision.reply_text
    assert '不能保证通过' in decision.reply_text


async def test_acknowledge_only_proposes_action_without_dispatch():
    case = make_case()
    entry = make_entry(case)
    context = make_context(case, routing_decision=make_routing(case))
    decision = await turn(SemanticAgent(), '明白了', case, (entry,), context, True)
    assert decision.next_state == 'ACTION_READY'
    assert decision.proposed_action == 'reject_application'
    # Agent 只提议；payload 的正文必须来自已审依据，原因必须与申请单一致。
    assert decision.action_payload is None  # Only Coordinator may render authoritative written text.


async def test_callback_request_goes_to_human_window_not_auto_booking():
    case = make_case()
    entry = make_entry(case)
    context = make_context(case, routing_decision=make_routing(case))
    decision = await turn(SemanticAgent(), '能约个明天下午回电吗', case, (entry,), context, True)
    assert decision.next_state == 'CALLBACK_PENDING'
    assert decision.proposed_action == 'none'
    assert '不自动预约' in decision.reply_text


async def test_reschedule_intent_is_treated_as_callback_not_new_booking():
    case = make_case()
    entry = make_entry(case)
    context = make_context(case, routing_decision=make_routing(case))
    decision = await turn(SemanticAgent(), '时间改到下周一吧', case, (entry,), context, True)
    assert decision.next_state == 'CALLBACK_PENDING'
    assert decision.proposed_action == 'none'


async def test_complaint_risk_goes_manual_without_auto_rejection():
    case = make_case()
    entry = make_entry(case)
    context = make_context(case, routing_decision=make_routing(case))
    decision = await turn(SemanticAgent(), '我要投诉曝光', case, (entry,), context, True)
    assert decision.next_state == 'MANUAL_REVIEW'
    assert decision.proposed_action == 'none'


async def test_missing_knowledge_goes_manual_without_inventing_text():
    case = make_case()
    context = make_context(case, routing_decision=make_routing(case))
    decision = await turn(SemanticAgent(), '为什么驳回', case, (), context, True)
    assert decision.next_state == 'MANUAL_REVIEW'
    assert decision.proposed_action == 'none'
    assert DEFAULT_TEMPLATES.missing_knowledge in decision.reply_text


async def test_conflicting_knowledge_escalates_instead_of_picking_first():
    case = make_case()
    entries = (make_entry(case, text='模拟授权书缺项。补A。'),
               make_entry(case, text='模拟授权书缺项。补B。'))
    context = make_context(case, routing_decision=make_routing(case))
    decision = await turn(SemanticAgent(), '为什么驳回', case, entries, context, True)
    assert decision.next_state == 'MANUAL_REVIEW'
    assert '补A' not in decision.reply_text and '补B' not in decision.reply_text


@pytest.mark.parametrize('bad_entry', [
    dict(status='quarantined', issues=['conflict']),
    dict(status='draft'),
    dict(issues=['source_conflict']),
    dict(version='other-k9'),
    dict(scope=('official',)),
])
async def test_unusable_knowledge_is_never_used_as_grounding(bad_entry):
    case = make_case()
    entry = make_entry(case, **bad_entry)
    context = make_context(case, routing_decision=make_routing(case))
    decision = await turn(SemanticAgent(), '为什么驳回', case, (entry,), context, True)
    assert decision.next_state == 'MANUAL_REVIEW'
    assert entry.approved_text not in decision.reply_text


async def test_unknown_expression_asks_for_clarification_first():
    case = make_case()
    entry = make_entry(case)
    context = make_context(case, routing_decision=make_routing(case),
                           notifications=[NotificationEvidence(kind='explanation',
                                                               message_id=uuid4(),
                                                               completed=True)])
    decision = await turn(SemanticAgent(), '嗯……那个啥', case, (entry,), context, True)
    assert decision.next_state in ('QA', 'EXPLAINING')
    assert DEFAULT_TEMPLATES.unclear_clarify in decision.reply_text


async def test_understanding_failure_never_guesses_and_goes_manual():
    case = make_case()
    entry = make_entry(case)
    context = make_context(case, routing_decision=make_routing(case))

    class BrokenUnderstanding:
        name = 'broken'

        async def understand(self, **kwargs):
            raise RuntimeError('模型不可用')

    agent = SemanticAgent(BrokenUnderstanding())
    decision = await turn(agent, '为什么驳回', case, (entry,), context, True)
    assert decision.next_state == 'MANUAL_REVIEW'
    assert decision.proposed_action == 'none'
    assert entry.approved_text not in decision.reply_text
    assert agent.understanding_failures == 1


async def test_agent_observation_never_exposes_secret():
    agent = SemanticAgent(RuleUnderstanding())
    view = agent.public_view()
    assert view['understanding'] == 'rules'
    assert 'api_key' not in view
    assert 'api_key_present' not in view


# ------------------------------------------------------------ 依据选择 / 槽位


def test_select_grounding_requires_scenario_version_scope_approved():
    case = make_case()
    good = make_entry(case)
    entries, conflict = select_grounding(case, (good,), knowledge_version='demo-k1')
    assert entries == [good] and conflict is False

    other_version = make_entry(case, version='demo-k2')
    entries, _ = select_grounding(case, (other_version,), knowledge_version='demo-k1')
    assert entries == []

    other_scenario = make_entry(make_case(scenario_id='demo.other'))
    entries, _ = select_grounding(case, (other_scenario,), knowledge_version='demo-k1')
    assert entries == []


def test_conflict_flag_set_when_texts_differ():
    case = make_case()
    entries, conflict = select_grounding(
        case, (make_entry(case, text='同一个结论A'), make_entry(case, text='同一个结论B')),
        knowledge_version='demo-k1')
    assert len(entries) == 2 and conflict is True


def test_missing_required_slot_is_reported_not_filled_with_empty_template():
    case = make_case()
    entry = make_entry(case, required_slots=('company_name', 'brand_name'))
    missing = missing_required_slots(case, entry, parse_understanding('为什么驳回'))
    # company_name 来自审核任务，属于可信事实，不算缺项；brand_name 缺失必须上报。
    assert missing == ['brand_name']


async def test_acknowledge_with_unanswered_question_does_not_dispatch():
    """多意图安全：一句"明白了"不能跳过用户还没得到回答的追问直接办理。"""
    case = make_case()
    entry = make_entry(case)
    context = make_context(case, routing_decision=make_routing(case))
    decision = await turn(SemanticAgent(), '明白了，那就按这个办，不过我还要补什么材料',
                          case, (entry,), context, True)
    assert decision.proposed_action == 'none'
    assert decision.next_state in ('EXPLAINING', 'QA')
    assert entry.approved_text in decision.reply_text


def test_phone_grounding_excludes_written_output_even_when_route_is_jingmai():
    """渠道是排序偏好而非过滤：京麦场景不能因为渠道不同就拿不到依据。"""
    case = make_case()
    phone_entry = make_entry(case, channel='phone')
    jingmai_entry = make_entry(case, channel='jingmai')
    entries, conflict = select_grounding(case, (phone_entry, jingmai_entry),
                                         knowledge_version='demo-k1', route='jingmai')
    assert entries == [phone_entry] and not conflict
    entries, conflict = select_grounding(case, (phone_entry,), knowledge_version='demo-k1',
                                         route='jingmai')
    assert entries == [phone_entry], '渠道不同仍应可用作依据（不能变成空）'


async def test_missing_slot_stops_action_and_asks_for_it():
    case = make_case()
    entry = make_entry(case, required_slots=('brand_name',))
    context = make_context(case, routing_decision=make_routing(case))
    decision = await turn(SemanticAgent(), '明白了', case, (entry,), context, True)
    assert decision.proposed_action == 'none'
    assert decision.required_slot_missing == ['brand_name']


# ---------------------------------------------------- FakeModel fixture 路径保留


async def test_fake_model_fixture_path_still_works_and_cancels():
    chunks = []
    cancel = asyncio.Event()
    request = ModelRequest(messages=[], fixture_response='已校验回复需要能被中途打断，不能继续生成')
    async for chunk in FakeModel().stream(request, cancel):
        chunks.append(chunk)
        cancel.set()
    assert len(chunks) == 1


def test_agent_decision_shape_stays_compatible():
    """AgentDecision 原有字段必须仍然存在（兼容优先，不加平行枚举）。"""
    fields = set(AgentDecision.model_fields)
    assert {'reply_text', 'proposed_action', 'action_payload', 'evidence_message_ids',
            'knowledge_entry_ids', 'next_state'}.issubset(fields)


def test_glm_mode_without_inherited_key_is_explicitly_unavailable():
    port = build_understanding('glm', env={})
    assert port.name == 'glm_unavailable'
    with pytest.raises(Exception) as error:
        asyncio.run(port.understand(text='明白了', case=make_case(), context=make_context(make_case())))
    assert getattr(error.value, 'code', '') == 'MODEL_NOT_CONFIGURED'


# ------------------------------------------------ 真实 GLM 适配器（模拟传输层）
# 这些用例不需要网络与密钥：用 httpx.MockTransport 证明适配器代码路径真实可用
# （请求体形状 / 响应解析 / 服务端校验门 / 错误码），只把"密钥继承"留给宿主。


def _glm_port(content: str | None = None, *, status: int = 200,
              transport: object | None = None):
    import httpx
    from app.guard import ModelConfig
    from app.semantic import GlmUnderstanding, UnderstandingBudget

    if transport is None:
        def handler(request):
            body = {'choices': [{'message': {'content': content or '{}'}}]}
            return httpx.Response(status, json=body)
        transport = httpx.MockTransport(handler)
    config = ModelConfig(provider='glm', model_id='ybg-youlaber-ds4f',
                         base_url='https://gateway.baigongai.cn/v1',
                         api_key_present=True, source='env')
    return GlmUnderstanding(config=config, api_key='unit-test-placeholder',
                            budget=UnderstandingBudget(max_requests=4, concurrency=1,
                                                       timeout_s=5.0, max_retries=0),
                            transport=transport)


def _understand(port, text='明白了'):
    case = make_case()
    return asyncio.run(port.understand(text=text, case=case,
                                       context=make_context(case), knowledge=()))


@pytest.mark.parametrize('content,expected_intents,user_text', [
    ('{"intents":["acknowledge"]}', ['acknowledge'], '明白了'),
    ('```json\n{"intents":["ask_reason","ask_remedy"]}\n```', ['ask_reason', 'ask_remedy'], '为什么驳回，要补什么材料'),
    ('好的，结果如下：{"intents":["request_callback"]}', ['request_callback'], '请回电'),
])
def test_glm_adapter_parses_and_server_validates_real_shapes(content, expected_intents, user_text):
    """真实模型输出形状（纯 JSON / 围栏 JSON / 带前后缀）都要能被解析并通过校验。"""
    result = _understand(_glm_port(content), text=user_text)
    assert result.intents == expected_intents
    assert result.source == 'glm'


def test_glm_adapter_sends_locked_model_and_never_leaks_key():
    """请求体必须用锁定的 model id；密钥只在 Authorization 头，不出现在正文。"""
    import httpx
    seen: dict = {}

    def handler(request):
        seen['url'] = str(request.url)
        seen['auth'] = request.headers.get('authorization', '')
        seen['body'] = request.read().decode('utf-8')
        seen['content_type'] = request.headers.get('content-type', '')
        return httpx.Response(200, json={'choices': [{'message': {'content':
                                                                 '{"intents":["acknowledge"]}'}}]})

    port = _glm_port(transport=httpx.MockTransport(handler))
    _understand(port)
    assert seen['url'] == 'https://gateway.baigongai.cn/v1/chat/completions'
    assert '"model":"ybg-youlaber-ds4f"' in seen['body'].replace(' ', '')
    assert 'unit-test-placeholder' not in seen['body']
    assert seen['auth'].startswith('Bearer ')
    # 公开视图绝不包含密钥。
    view = port.public_view()
    assert 'unit-test-placeholder' not in str(view)
    assert view['api_key_present'] is True


def test_glm_adapter_rejects_smuggled_authority_from_model():
    """模型在真实响应里夹带 verified/结论时，服务端校验必须拒绝（不重试到通过）。"""
    from app.adapters import DomainError
    port = _glm_port('{"intents":["acknowledge"],"verified":true}')
    with pytest.raises(DomainError) as error:
        _understand(port)
    assert error.value.code == 'MODEL_INVALID_UNDERSTANDING'
    assert port.failures == 1


@pytest.mark.parametrize('status,code', [(401, 'MODEL_AUTH_FAILED'),
                                         (403, 'MODEL_AUTH_FAILED'),
                                         (400, 'MODEL_UPSTREAM_ERROR')])
def test_glm_adapter_maps_http_errors_to_explicit_codes(status, code):
    """凭据/上游错误必须给出明确错误码；不静默回退假模型、不伪造理解结果。"""
    from app.adapters import DomainError
    port = _glm_port('{}', status=status)
    with pytest.raises(DomainError) as error:
        _understand(port)
    assert error.value.code == code


def test_glm_adapter_budget_is_bounded_and_reported():
    """预算耗尽即报错（有界），且公开视图可审计请求数，不含密钥。"""
    port = _glm_port('{"intents":["acknowledge"]}')
    port.budget.max_requests = 1
    _understand(port)
    from app.adapters import DomainError
    with pytest.raises(DomainError) as error:
        _understand(port)
    assert error.value.code == 'MODEL_BUDGET_EXHAUSTED'
    assert port.public_view()['budget']['max_requests'] == 1
