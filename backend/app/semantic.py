"""结构化理解层：确定性规则实现 + 真实 GLM 适配器（单轮一次结构化理解）。

职责边界（见 docs/agent/CONTRACT-CHANGES.md）：
- 本模块只把**用户最终还是 ASR 文本**变成 `Understanding` DTO（意图/槽位/否定/多意图/澄清）。
- 它**不能**写 `verified`、审核结论、成功状态、权限或知识；那些只能由服务端证据决定。
- 模型输出必须经 `validate_understanding` 做 schema / 越权键 / 证据 / 槽位 / 联系结果 校验，
  校验失败一律按"理解不可信"处理（拒绝或降级），绝不把模型自述当成业务事实。
- 槽位（slots）同样是证据：模型填的槽位值必须能在**本轮用户原文**里找到，
  且不得覆盖服务端权威事实（公司名/原因/整改/审核结论/版本号等）。
- 联系结果（contact_outcome）只由**服务端**依据意图与可信事件判定；模型给出的
  `contact_outcome_hint` 只有在与本轮意图一致时才保留，不能单方面结束会话。
- 模型的自由文本一律不进入 `reply_text`；回复只用服务端模板 + 已审核知识正文。
- 真实模型只从**继承的环境变量** `GLM_API_KEY` 取密钥；模型ID/地址锁定为工作台
  glm 组配置（见 `.project-brief/GLM-CONNECTION.json` 的公开连接参数，
  `provider=glm`, `model=ybg-youlaber-ds4f`, `baseUrl=https://gateway.baigongai.cn/v1`）。
  历史脚本里的 `glm-4.6` / `open.bigmodel.cn` 是**错误默认值**，不再使用。
- 密钥不打印、不落盘、不写日志、不进错误信息；只报告"是否存在"。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence
from uuid import UUID

import httpx

from .adapters import DomainError
from .contracts import KnowledgeEntry, ReviewCase, SessionContext, Understanding
from .guard import ModelConfig, load_model_config, require_model_config

# --------------------------------------------------------------- 意图契约

#: 允许意图的唯一来源（与 contracts.Understanding.intents 完全一致）。
ALLOWED_INTENTS: tuple[str, ...] = (
    'confirm_identity', 'deny_identity', 'ask_reason', 'ask_remedy', 'ask_guarantee',
    'ask_source', 'ask_outcome', 'refuse_contact', 'request_callback', 'acknowledge',
    'unclear', 'complaint_risk',
)

#: 确定性规则表（顺序即输出顺序，保证可重复）。
#: 说明：这里只放**词形**，不针对任何具体评测句子；凡是"某个固定句子才能过"的写法都不允许。
INTENT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ('deny_identity', ('打错', '不是本人', '不是这家', '不是我们', '没有这个人', '你搞错',
                       '不是公司的', '冒名', '不认识', '不是我的', '没这个人', '找错了')),
    ('refuse_contact', ('不用再', '不要再打', '别再打', '别打', '不需要', '拒绝', '别联系',
                        '不要再联系', '不处理了', '别烦', '挂了吧', '不必', '不用了',
                        '不用再联系', '别再来电')),
    ('complaint_risk', ('投诉', '曝光', '举报', '12315', '监管', '起诉', '律师函')),
    ('request_callback', ('回电', '回个电话', '回电话', '改期', '换时间', '约个', '预约',
                          '晚点再打', '明天再', '改到', '改一下时间', '推迟', '延后',
                          '换个时间')),
    ('ask_guarantee', ('必过', '包过', '保证', '一定能', '肯定能', '是不是就', '会不会通过',
                       '能不能通过', '能不能过', '一定批', '肯定过')),
    ('ask_source', ('依据', '来源', '哪一条', '凭什么', '出处', '哪份文件', '规则在哪')),
    ('ask_remedy', ('补什么', '整改', '怎么改', '要补', '材料', '怎么处理', '怎么办', '如何补救')),
    ('ask_reason', ('为什么', '原因', '哪里没', '哪里不合格', '驳回', '没过', '没通过', '什么问题')),
    ('ask_outcome', ('重新提交', '还要重来', '结果', '多久能', '什么时候能', '进度')),
    ('confirm_identity', ('我是', '是我', '本人', '就是这家', '对接人', '负责人', '这边')),
    ('acknowledge', ('明白了', '了解了', '知道了', '按这个', '处理吧', '记下了',
                     '同意处理', '那就办', '按你说的')),
)

#: 结构化表达模式：只描述**汉语表达家族**（"那就…办""哪里出的问题""要上传什么"
#: "电话跟我说"这类说法），不针对任何具体评测句子的全文匹配，避免硬编码答案。
#: 与关键词表叠加使用：关键词管词形，模式管句法变体，二者取并集后按固定顺序输出。
_INTENT_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # 承接/认同：那就这么办 / 行，按流程走吧 / 好，按你说的处理吧
    ('acknowledge', (
        r'(那就|就这么|就按|按|照|按照)[^，。！？；,.!?;]{0,8}?(办|走|处理|来做|来|执行)',
        r'(明白|了解|知道|清楚|收到|好(的)?|行|可以)[^，。！？；,.!?;]{0,6}(那就|就按|按|照)[^，。！？；,.!?;]{0,8}?(办|走|处理|来)',
    )),
    # 追问原因：讲一下是哪里出的问题 / 哪一项不合格 / 是哪一个资质出的问题
    ('ask_reason', (
        r'哪(里|儿|个|一[个项条块家份样]?|些)[^，。！？；,.!?;]{0,6}?(出|不|没|错|问题|不合格|不符)',
        r'(讲|说|说明|告诉)[^，。！？；,.!?;]{0,6}?(原因|问题|哪里|哪里出)',
    )),
    # 追问整改：需要我上传些什么 / 要交哪些资料 / 补什么材料
    ('ask_remedy', (
        r'(上传|提交|补交|补充|补|准备|整理)[^，。！？；,.!?;]{0,5}?(什么|哪些|啥|东西)',
        r'(需要|要|得)[^，。！？；,.!?;]{0,6}?(资料|材料|什么|哪些|啥)',
    )),
    # 要求电话沟通：能不能直接电话跟我说 / 电话联系我
    ('request_callback', (
        r'(电话|来电|致电|回电|打过来)[^，。！？；,.!?;]{0,6}?(说|讲|沟通|聊|联系|谈)',
        r'(直接|单独|私下)[^，。！？；,.!?;]{0,4}?(电话|联系我|找我)',
    )),
)

#: 模糊/停顿表达：无强意图且含这些词 → unclear，交澄清话术，不猜。
UNCLEAR_MARKERS = ('那个东西', '怎么说来着', '呃', '啊这个', '那个啥', '嗯……', '嗯...',
                   '就是那个', '你懂的', '啥来着')

NEGATION_MARKERS = ('不是', '不用', '不要', '不需要', '没有', '别', '无需', '不打算', '不能',
                    '不会', '没')

#: 模型绝不能输出的越权键（审核结论/权限/知识/门槛/白名单/成功）。
FORBIDDEN_UNDERSTANDING_KEYS = frozenset({
    'review_conclusion', 'conclusion', 'approved', 'approve', 'verified', 'verify',
    'rule_version', 'route', 'threshold', 'thresholds', 'gate', 'allowlist',
    'tool_allowlist', 'authorization', 'authorized', 'preauthorized', 'knowledge_text',
    'knowledge_entry_ids', 'success', 'succeeded', 'case_revision', 'idempotency_key',
    'sensitive', 'approval', 'permission', 'next_state', 'proposed_action',
})

#: 模型只能提示这两个"方向安全"的负向联系结果；其余必须由服务端/可信事件给出。
SAFE_OUTCOME_HINTS: tuple[str, ...] = ('wrong_person', 'refused')

#: 由**服务端权威事实**拥有的槽位名：模型不得写入/覆盖（审核结论、主体、原因、整改、
#: 版本、路由等都是审核任务与已审知识的产物，不是用户说的话）。
SERVER_AUTHORITY_SLOTS = frozenset({
    'company_name', 'merchant_name', 'reason', 'remedy', 'scenario_id',
    'review_conclusion', 'conclusion', 'case_revision', 'knowledge_version',
    'rule_version', 'strategy_version', 'application_id', 'merchant_id', 'route',
    'submission_attempt', 'call_attempt', 'approved_text', 'verified',
})

#: 允许模型输出的 JSON 键（白名单；未知键直接拒绝，防止夹带）。
ALLOWED_PAYLOAD_KEYS = frozenset({
    'intents', 'slots', 'negated', 'multi_intent', 'needs_clarification',
    'clarification_reason', 'contact_outcome_hint', 'evidence_message_ids', 'source',
})

#: 模型自述意图必须能在原文里找到依据：只用于**降级**（不安全方向），不做关键词计分。
#: 方向安全性：这些意图会结束会话或转人工，误判代价高，必须有原文支撑。
_HIGH_IMPACT_INTENTS = ('deny_identity', 'refuse_contact', 'complaint_risk')


def normalize(text: str) -> str:
    value = unicodedata.normalize('NFKC', text or '')
    value = re.sub(r'\s+', '', value)
    return re.sub(r'[，。！？、,.!?;:；：""\'\'（）()《》\-—…~～]', '', value)


def detect_intents(text: str) -> list[str]:
    """确定性意图抽取（无网络、可重复）。聚合意图保留全部命中项。

    两层叠加：词形表（INTENT_RULES）+ 句法模式（_INTENT_PATTERNS）。
    模式只描述表达家族，不匹配任何评测句子全文，因此不会因为"某句话"才通过。
    """
    raw = text or ''
    hits: list[str] = []
    for intent, words in INTENT_RULES:
        if any(word in raw for word in words):
            hits.append(intent)
    for intent, patterns in _INTENT_PATTERNS:
        if any(re.search(pattern, raw) for pattern in patterns):
            if intent not in hits:
                hits.append(intent)
    # 输出顺序固定为 ALLOWED_INTENTS 顺序，保证可重复且与契约枚举一致。
    order = {name: index for index, name in enumerate(ALLOWED_INTENTS)}
    hits.sort(key=lambda name: order.get(name, len(order)))
    # “我不是这家公司的”同时含“我是”“不是这家”：否认优先，不能当成自然身份表达。
    if 'deny_identity' in hits and 'confirm_identity' in hits:
        hits = [h for h in hits if h != 'confirm_identity']
    if 'refuse_contact' in hits and 'acknowledge' in hits:
        hits = [h for h in hits if h != 'acknowledge']
    filler = any(marker in raw for marker in UNCLEAR_MARKERS)
    strong = [h for h in hits if h not in ('confirm_identity', 'acknowledge', 'unclear')]
    if filler and not strong:
        hits = [h for h in hits if h == 'confirm_identity'] + ['unclear']
    if not hits:
        hits = ['unclear']
    seen: list[str] = []
    for h in hits:
        if h not in seen:
            seen.append(h)
    return seen


def contact_outcome_hint(intents: Sequence[str]) -> str | None:
    """文本层面的联系结果提示；只能给 wrong_person/refused，绝不给 verified。"""
    if 'deny_identity' in intents:
        return 'wrong_person'
    if 'refuse_contact' in intents:
        return 'refused'
    return None


def parse_understanding(text: str) -> Understanding:
    """规则理解（默认路径，测试可重复）。**同样经过服务端校验门**，与真实模型一致。"""
    intents = detect_intents(text)
    return validate_understanding({'intents': intents, 'source': 'rules'}, text=text)


# --------------------------------------------------------------- 服务端校验


def _extract_json_object(content: str) -> dict:
    """从模型输出中取出一个 JSON 对象；容忍 ```json 围栏与前后空白。"""
    if not isinstance(content, str) or not content.strip():
        raise ValueError('empty model content')
    body = content.strip()
    fence = re.match(r'^```(?:json)?\s*(.*?)\s*```$', body, re.S)
    if fence:
        body = fence.group(1)
    start, end = body.find('{'), body.rfind('}')
    if start < 0 or end <= start:
        raise ValueError('no json object in model content')
    payload = json.loads(body[start:end + 1])
    if not isinstance(payload, dict):
        raise ValueError('model json is not an object')
    return payload


def check_slot_evidence(slots: dict, *, text: str | None) -> dict:
    """服务端校验槽位：值必须能在本轮用户原文中找到，且不得覆盖权威事实。

    模型填槽位是"理解"，不是"事实"。公司名/原因/整改/审核结论等由审核任务与已审知识
    提供，模型写了也要丢弃；用户在文本里没提到的值也一律丢弃（不能凭空补公司名）。
    """
    if not slots:
        return {}
    body = normalize(text or '')
    clean: dict[str, str] = {}
    for name, value in slots.items():
        key = str(name).strip()
        if key in SERVER_AUTHORITY_SLOTS:
            continue
        rendered = str(value).strip()
        if not rendered or len(rendered) > 200:
            continue
        if not body:
            continue
        # 值必须真实出现在用户原文中（规范化后），否则视为模型编造。
        if normalize(rendered) not in body:
            continue
        clean[key] = rendered
    return clean


def validate_understanding(payload: dict, *, case: ReviewCase | None = None,
                           context: SessionContext | None = None,
                           message_ids: Sequence[str] | None = None,
                           text: str | None = None) -> Understanding:
    """服务端校验（真实 GLM 与规则实现共用同一道门）。

    拒绝：越权键、未知键、未知意图、`verified` 型联系结果、伪造证据消息、伪造槽位。
    重算：negated / multi_intent / 槽位 / 联系结果由服务端判定，不采信模型自述。
    降级：高风险意图（否认身份/拒绝联系/投诉）必须有原文支撑，否则退回原文可证的意图。
    """
    if not isinstance(payload, dict):
        raise ValueError('understanding 必须是对象')
    smuggled = sorted(FORBIDDEN_UNDERSTANDING_KEYS & set(payload))
    if smuggled:
        raise ValueError(f'understanding 含越权字段: {smuggled}')
    unknown = sorted(set(payload) - ALLOWED_PAYLOAD_KEYS)
    if unknown:
        raise ValueError(f'understanding 含未知字段: {unknown}')

    clean = dict(payload)
    clean['source'] = clean.get('source') or 'glm'
    if clean['source'] not in ('fake', 'glm', 'rules'):
        clean['source'] = 'glm'
    hint = clean.get('contact_outcome_hint')
    if hint is not None and hint not in SAFE_OUTCOME_HINTS:
        # 模型不能自造 verified/no_answer/disconnected/not_required。
        clean['contact_outcome_hint'] = None
    try:
        understanding = Understanding.model_validate(clean)
    except Exception as exc:  # pydantic ValidationError -> 统一 ValueError
        raise ValueError(f'understanding schema 非法: {exc}') from exc

    # 证据消息必须真实存在且属于本会话；模型不能凭空引用消息。
    if message_ids is not None:
        allowed = {str(m) for m in message_ids}
        bad = [str(m) for m in understanding.evidence_message_ids if str(m) not in allowed]
        if bad:
            raise ValueError(f'understanding 引用了不存在的证据消息: {bad[:3]}')
    if case is not None and context is not None and context.application_id != case.application_id:
        raise ValueError('understanding 会话语境与申请单不一致')

    intents = list(understanding.intents)
    # 高风险意图必须有**本轮用户原文**支撑，否则降级——模型不能凭一句话结束会话。
    # fail-closed：没有原文时绝不能反过来把"模型自述的意图"当成依据（旧实现就是
    # `grounded = set(intents)`，等于把模型的 claim 当证据，真实 GLM 适配器漏传 text
    # 时会直接采信无原文支撑的 deny_identity/refuse_contact/complaint_risk）。
    # 原文为空时 `detect_intents('')` 只给 unclear，因此高风险意图一律被降级。
    grounded = set(detect_intents(text or ''))
    unsupported = [i for i in intents
                   if i in _HIGH_IMPACT_INTENTS and i not in grounded]
    if unsupported:
        intents = [i for i in intents if i not in unsupported]
    if 'deny_identity' in intents and 'confirm_identity' in intents:
        intents = [i for i in intents if i != 'confirm_identity']
    if 'refuse_contact' in intents and 'acknowledge' in intents:
        intents = [i for i in intents if i != 'acknowledge']
    if not intents:
        intents = ['unclear']
    substance = [i for i in intents if i != 'unclear']
    negated = understanding.negated
    if text:
        # 否定由服务端按原文再判一次；模型自述只是补充，不能把否定抹掉。
        negated = negated or any(marker in text for marker in NEGATION_MARKERS)
    slots = check_slot_evidence(understanding.slots, text=text)
    # 高风险意图因缺原文依据被降级，且没有留下任何可证诉求时，明确要求澄清/转人工：
    # 不允许"降级"被悄悄当成正常理解继续走到派发。
    grounding_insufficient = bool(unsupported) and not substance
    return understanding.model_copy(update={
        'intents': intents,
        'slots': slots,
        'negated': negated,
        'multi_intent': len(substance) >= 2,
        'needs_clarification': (understanding.needs_clarification
                                or 'unclear' in intents or grounding_insufficient),
        'clarification_reason': understanding.clarification_reason
        or ('grounding_insufficient' if grounding_insufficient
            else ('text_unclear' if 'unclear' in intents else None)),
        # 联系结果只由服务端按（可能已降级的）意图判定：模型提示不能新造方向，
        # 也不能在意图被降级后继续凭原提示结束会话。
        'contact_outcome_hint': contact_outcome_hint(intents),
    })


# --------------------------------------------------------------- 端口与实现


class UnderstandingPort(Protocol):
    """理解端口：只做文本 -> 结构化理解，不碰数据库/工具/权限。"""

    name: str

    async def understand(self, *, text: str, case: ReviewCase, context: SessionContext,
                         knowledge: Sequence[KnowledgeEntry] = (),
                         message_ids: Sequence[str] = ()) -> Understanding: ...


@dataclass
class RuleUnderstanding:
    """确定性替身：留给可重复测试与离线演示，保留 fixture 路径。"""

    name: str = 'rules'

    async def understand(self, *, text: str, case: ReviewCase, context: SessionContext,
                         knowledge: Sequence[KnowledgeEntry] = (),
                         message_ids: Sequence[str] = ()) -> Understanding:
        return parse_understanding(text)


@dataclass
class UnderstandingBudget:
    """有界预算：请求数/并发/超时。耗尽即报错，不无限重试。"""

    max_requests: int = 400
    concurrency: int = 1
    timeout_s: float = 12.0
    max_retries: int = 2
    requests: int = 0
    failures: int = 0
    _sem: asyncio.Semaphore | None = None

    def semaphore(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(max(1, self.concurrency))
        return self._sem

    def take(self) -> None:
        if self.requests >= self.max_requests:
            raise DomainError('MODEL_BUDGET_EXHAUSTED',
                              f'开发模型请求已达上限({self.max_requests})，停止调用并保留证据', 503)
        self.requests += 1

    def public_view(self) -> dict:
        return {'max_requests': self.max_requests, 'requests': self.requests,
                'failures': self.failures, 'concurrency': self.concurrency,
                'timeout_s': self.timeout_s, 'max_retries': self.max_retries}


SYSTEM_PROMPT = (
    '你是京东POP商家资质审核驳回告知场景的**理解层**，只做一次结构化理解，不做业务决定。\n'
    '铁律：你只能输出 JSON；你不能判断身份是否核验、不能给审核结论、不能承诺通过、'
    '不能决定渠道或权限、不能编造知识依据。\n'
    '允许的意图（只能从中选，可多选）：' + ','.join(ALLOWED_INTENTS) + '。\n'
    '输出 JSON 结构（只允许这些键）：\n'
    '{"intents":[...],"slots":{},"negated":true|false,"multi_intent":true|false,'
    '"needs_clarification":true|false,"clarification_reason":"短原因或null",'
    '"contact_outcome_hint":"wrong_person"|"refused"|null}\n'
    '判定要点：\n'
    '- 否认身份/打错电话 → intents 含 deny_identity，contact_outcome_hint=wrong_person；'
    '不要同时给 confirm_identity。\n'
    '- 要求不再联系/拒绝处理 → refuse_contact，contact_outcome_hint=refused。\n'
    '- 只表达“明白了/知道了” → acknowledge；它不代表同意购买，也不代表业务已成功。\n'
    '- 追问是否能保证通过 → ask_guarantee。追问依据/来源 → ask_source。\n'
    '- 回电/改期/预约 → request_callback（预约必须有明确时间窗并由人工确认）。\n'
    '- 一句话里有多个诉求时 multi_intent=true，保留全部意图，不要只留一个。\n'
    '- 听不清/指代不明 → intents 含 unclear，needs_clarification=true，不要猜。\n'
    '- 投诉/曝光/监管风险 → complaint_risk。\n'
    '- 绝不输出 verified、route、approved、success、knowledge、权限或状态字段。'
)


def build_prompt(*, text: str, case: ReviewCase, context: SessionContext,
                 knowledge: Sequence[KnowledgeEntry] = ()) -> list[dict[str, str]]:
    """组装单轮提示：只给最小可信事实，不给可被利用的权限/结论字段。"""
    facts = {
        'scenario_id': case.scenario_id,
        'review_conclusion': case.review_conclusion,
        'case_revision': case.case_revision,
        'submission_attempt': case.submission_attempt,
        'call_attempt': case.call_attempt,
        'server_verified': bool(context.contact_outcome == 'verified'),
        'current_state': context.state,
        'route': context.routing_decision.route if context.routing_decision else None,
        'knowledge_entry_count': len([e for e in knowledge if e.status == 'approved'
                                      and not e.issue_codes]),
    }
    user = ('可信事实(只读，不得改写)：' + json.dumps(facts, ensure_ascii=False) + '\n'
            + '商户/用户本轮最终文本：' + json.dumps(text, ensure_ascii=False) + '\n'
            + '只输出一个 JSON 对象。')
    return [{'role': 'system', 'content': SYSTEM_PROMPT}, {'role': 'user', 'content': user}]


@dataclass
class GlmUnderstanding:
    """真实 GLM 结构化理解（OpenAI 兼容 chat/completions，单轮、温度 0）。

    只读继承的 `GLM_API_KEY`；模型ID/baseURL 锁定工作台 glm 组配置。
    这里的每一次调用都是一次"开发模型请求"，受 `UnderstandingBudget` 约束。
    """

    config: ModelConfig
    api_key: str
    budget: UnderstandingBudget = field(default_factory=UnderstandingBudget)
    transport: httpx.AsyncBaseTransport | None = None
    name: str = 'glm'
    requests: int = 0
    failures: int = 0
    last_latency_ms: float | None = None
    last_status: int | None = None
    last_payload_chars: int | None = None

    async def understand(self, *, text: str, case: ReviewCase, context: SessionContext,
                         knowledge: Sequence[KnowledgeEntry] = (),
                         message_ids: Sequence[str] = ()) -> Understanding:
        messages = build_prompt(text=text, case=case, context=context, knowledge=knowledge)
        payload = {'model': self.config.model_id, 'messages': messages,
                   'temperature': 0, 'max_tokens': 400,
                   'response_format': {'type': 'json_object'}}
        url = self.config.base_url.rstrip('/') + '/chat/completions'
        headers = {'Authorization': f'Bearer {self.api_key}',
                   'Content-Type': 'application/json'}
        last_error: Exception | None = None
        attempts = max(0, self.budget.max_retries) + 1
        for attempt in range(attempts):
            self.budget.take()
            self.requests += 1
            started = time.perf_counter()
            try:
                async with self.budget.semaphore():
                    async with httpx.AsyncClient(
                            timeout=self.budget.timeout_s, transport=self.transport) as client:
                        response = await client.post(url, json=payload, headers=headers)
                self.last_latency_ms = round((time.perf_counter() - started) * 1000, 1)
                self.last_status = response.status_code
                if response.status_code == 429 or response.status_code >= 500:
                    last_error = DomainError(
                        'MODEL_UPSTREAM_BUSY',
                        f'模型服务返回 {response.status_code}，按上限重试', 503)
                    await asyncio.sleep(0.2 * (attempt + 1))
                    continue
                if response.status_code in (401, 403):
                    raise DomainError('MODEL_AUTH_FAILED',
                                      '模型凭据被拒绝（HTTP 401/403）；请宿主确认继承的 GLM_API_KEY 与锁定地址', 503)
                if response.status_code >= 400:
                    raise DomainError('MODEL_UPSTREAM_ERROR',
                                      f'模型服务返回 {response.status_code}', 502)
                body = response.json()
                content = _chat_content(body)
                self.last_payload_chars = len(content or '')
                raw = _extract_json_object(content)
                # 必须把**本轮用户原文**交给服务端校验门：槽位证据、高风险意图的原文
                # 支撑都以它为准。漏传 text 会让 check_slot_evidence 看到空原文而丢掉
                # 一切合法槽位，同时让 grounded 回退成模型自己的 intents（把自述当证据）。
                return validate_understanding(raw, case=case, context=context,
                                              message_ids=message_ids, text=text)
            except (httpx.TimeoutException, asyncio.TimeoutError) as exc:
                last_error = DomainError('MODEL_TIMEOUT',
                                         f'模型单轮理解超时(>{self.budget.timeout_s}s)', 504)
                self.failures += 1
                await asyncio.sleep(0)
                _ = exc
                continue
            except httpx.HTTPError as exc:
                last_error = DomainError('MODEL_TRANSPORT_ERROR',
                                         f'模型连接失败({type(exc).__name__})', 502)
                self.failures += 1
                continue
            except ValueError as exc:
                # schema/越权/证据不合格：不重试同一坏输出，直接如实失败。
                self.failures += 1
                raise DomainError('MODEL_INVALID_UNDERSTANDING',
                                  f'模型理解输出未通过服务端校验: {exc}', 502) from exc
        self.failures += 1
        raise last_error or DomainError('MODEL_UNAVAILABLE', '模型不可用', 503)

    def public_view(self) -> dict:
        """可写入证据/报告的字段：绝不含密钥。"""
        return {**self.config.public_view(), 'requests': self.requests,
                'failures': self.failures, 'last_latency_ms': self.last_latency_ms,
                'last_status': self.last_status, 'last_payload_chars': self.last_payload_chars,
                'budget': self.budget.public_view(),
                'model_id': self.config.model_id, 'base_url': self.config.base_url}


def _chat_content(body: Any) -> str:
    try:
        return body['choices'][0]['message']['content']
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError('模型响应缺少 choices[0].message.content') from exc


@dataclass
class GlmUnavailable:
    """真实模型模式但未继承密钥：每次使用都明确报错，绝不静默回退假模型。

    进程仍可启动（便于宿主/验收脚本读取清晰错误与配置视图），但任何真实理解调用
    都会得到 MODEL_NOT_CONFIGURED，而不是一段编造的理解结果。
    """

    config: ModelConfig
    name: str = 'glm_unavailable'

    async def understand(self, *, text: str, case: ReviewCase, context: SessionContext,
                         knowledge: Sequence[KnowledgeEntry] = (),
                         message_ids: Sequence[str] = ()) -> Understanding:
        raise DomainError('MODEL_NOT_CONFIGURED',
                          '真实模型模式已开启但未继承 GLM_API_KEY；拒绝用假模型冒充真实理解，'
                          '请由宿主注入密钥后重试', 503)

    def public_view(self) -> dict:
        return {**self.config.public_view(), 'requests': 0, 'failures': 0,
                'reason': 'api_key_missing'}


def build_glm_understanding(env: dict[str, str] | None = None) -> GlmUnderstanding:
    """按环境构真实 GLM 理解器；缺继承密钥时抛 MODEL_NOT_CONFIGURED。"""
    env = dict(os.environ if env is None else env)
    config = require_model_config(env)
    return GlmUnderstanding(config=config, api_key=env['GLM_API_KEY'],
                            budget=UnderstandingBudget(
                                max_requests=int(env.get('GLM_MAX_REQUESTS', '400')),
                                concurrency=int(env.get('GLM_CONCURRENCY', '1')),
                                timeout_s=float(env.get('GLM_TIMEOUT_S', '12')),
                                max_retries=int(env.get('GLM_MAX_RETRIES', '2'))))


def build_understanding(mode: str | None = None, env: dict[str, str] | None = None) -> UnderstandingPort:
    """工厂：fake/rules -> 确定性替身；glm -> 真实模型（缺键时返回显式不可用端口）。"""
    env = dict(os.environ if env is None else env)
    chosen = (mode or env.get('ZHIYU_MODEL_MODE') or 'fake').strip().lower()
    if chosen == 'glm':
        config = load_model_config(env)
        if not config.api_key_present:
            return GlmUnavailable(config=config)
        return build_glm_understanding(env)
    return RuleUnderstanding()
