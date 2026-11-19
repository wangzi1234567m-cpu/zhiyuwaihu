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

#: **否定作用域**（本轮 t1 最小改进）：下面这些意图是高代价动作（转人工/回电/口径承诺），
#: 只要触发词在本句内被否定，就不能成立——否则"我没投诉你们，只是问原因"会被判成投诉风险，
#: "不用回电，你就说补什么材料"会被判成要求回电，把一次正常追问强行升级成人工/预约。
#: 只收窄这两个意图；`deny_identity`/`refuse_contact` 本身就是否定式语义，不在此列（它们由
#: 各自的规则与作用域词表负责），审核结论/权限/知识/阈值一律不在此处改动。
NEGATION_SCOPED_INTENTS: tuple[str, ...] = ('complaint_risk', 'request_callback')

#: 否定词向前回溯的窗口（字符）与句读边界：跨句读即视为不在同一否定作用域。
NEGATION_WINDOW = 6
CLAUSE_BREAK = re.compile(r'[，。！？；、,.!?;:：\n]')

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


def _clause_prefix(text: str, start: int, window: int) -> str:
    """取触发点前、**同一句读内**的至多 window 个字符（跨句读不算同域）。"""
    begin = max(0, start - window)
    prefix = text[begin:start]
    cut = 0
    for match in CLAUSE_BREAK.finditer(prefix):
        cut = match.end()
    return prefix[cut:]


def _clause_tail(text: str, start: int, window: int) -> str:
    """取触发点后、**同一句读内**的至多 window 个字符。"""
    tail = text[start:start + window]
    match = CLAUSE_BREAK.search(tail)
    return tail[:match.start()] if match else tail


#: 汉语**正反问句**（"能不能/可不可以/会不会/要不要"）里含否定字，但语义是询问不是否定。
#: 不先剥离它，\"能不能回电跟我说一下\"会被当成"否定回电"，把正常预约请求误降级成 unclear。
_QUESTION_FORM = re.compile(r'[能可会要](?:不|没)[能可会要]'
                            r'|是(?:不|没)是|有(?:不|没)有|是否')

#: **直接否定**：否定词紧贴在触发词前面（`不投诉` / `没投诉` / `别回电` / `无需回电`）。
#: 这是最强的否定证据，不受 6 字窗口与连接词影响；距离稍远就必须靠上面的窗口与对象作用域。
_DIRECT_NEGATION_PREFIXES = ('不', '没', '别', '无', '未')


#: 双重否定 / 反诘：`不得不投诉` `不能不投诉` `没办法只能投诉` 表面两个否定字，语义是**肯定**。
#: 不先认出来，紧邻否定规则会把真投诉当成"否认投诉"抹掉（比修复前更糟）。
_DOUBLE_NEGATION_TAILS = ('不得不', '不能不', '不可能不', '不是不', '没有不', '不会不',
                          '无不', '没办法不', '又不能')

#: 触发点前的"只能/只好"类**被迫**标记：`没办法只能投诉` 是被迫投诉，不是否认投诉。
#: 只用于**截断**：这些标记之前的内容不再参与否定作用域判定。
_FORCED_MARKERS = ('只能', '只好', '只得', '必须')


def _strip_double_negation(prefix: str) -> str:
    """去掉双重否定结构（`不得不`/`不能不`/`不可能不`…），它们整体表肯定。"""
    for tail in _DOUBLE_NEGATION_TAILS:
        prefix = prefix.replace(tail, '')
    return prefix


def _apply_forced_cut(prefix: str) -> str:
    """`只好/只能/必须` 之后是新谓述，之前的内容不再属于本触发点的否定作用域。"""
    cut = -1
    for marker in _FORCED_MARKERS:
        found = prefix.rfind(marker)
        if found >= 0:
            cut = max(cut, found + len(marker))
    return prefix[cut:] if cut >= 0 else prefix


def _directly_negated(text: str, start: int) -> bool:
    """触发词是否被**紧邻**其前的否定词直接否定（`不投诉`、`没回电`）。

    双重否定与"只能/只好"被迫式不算否定：`不得不投诉` 是肯定投诉。
    """
    if start <= 0:
        return False
    head = text[:start]
    if head.endswith(_DOUBLE_NEGATION_TAILS):
        return False
    if any(head.endswith(marker) for marker in _FORCED_MARKERS):
        return False
    if text[start - 1] in _DIRECT_NEGATION_PREFIXES:
        return True
    # `没有投诉`：否定词是两个字的"没有"。
    return text[max(0, start - 2):start] == '没有'


#: \"否定 + 自身对象/状态\"的**封闭短语**：被否定的是说话人自己的空闲、能力或便利，
#: 不是后文那个动作。`我没空请回电` / `我不能等了要投诉` / `我不会操作，你们帮我投诉`
#: 里的否定都只闭合在自己的对象上；旧实现只看"前 6 字里有没有否定词"，于是把后面的
#: 真实回电请求、真实投诉一并抹掉，降级成 unclear。
_OBJECT_NEGATION = re.compile(
    r'(?:没有?|不)(?:空|时间|工夫|功夫)'
    r'|不会(?:操作|用|弄|搞|填|传|上网|电脑|手机|打字)'
    r'|不能(?:等|上网|过来|再等)')

#: 出现在"对象否定短语"与触发点之间的**新谓述标记**：请/帮/就/了/要… 说明话已经翻到
#: 下一个请求动作上，否定不再延伸到后文。收紧到"请求/谓述"字，**不含人称代词**：
#: `我没空你不用回电` 的 `你` 之后是新的否定（真的不用回电），不能因为换主语就当成放开。
#: 定语紧贴触发点（`没时间回电`）不留间隔，仍算同域否定。
_DETACHING_CONNECTORS = frozenset('请帮给烦就再要也想说问直先快赶马上立了着的啊呀吧呢哦嘛')


def _drop_object_scoped_negations(prefix: str) -> str:
    """把已被后文新谓述隔开的"否定+自身对象"短语从作用域前文中去掉。"""
    kept: list[str] = []
    last = 0
    for match in _OBJECT_NEGATION.finditer(prefix):
        gap = prefix[match.end():]
        if not gap:
            continue
        if gap[0] not in _DETACHING_CONNECTORS and not any(w in gap for w in ("请", "麻烦", "烦请")):
            continue
        kept.append(prefix[last:match.start()])
        last = match.end()
    kept.append(prefix[last:])
    return ''.join(kept)


def _span_negated(text: str, start: int) -> bool:
    """该触发点是否落在否定作用域内（只看同句读内前文，且先剥离正反问句）。"""
    if _directly_negated(text, start):
        return True
    prefix = _QUESTION_FORM.sub('', _clause_prefix(text, start, NEGATION_WINDOW))
    prefix = _strip_double_negation(prefix)
    prefix = _apply_forced_cut(prefix)
    prefix = _drop_object_scoped_negations(prefix)
    return any(marker in prefix for marker in NEGATION_MARKERS)


#: 裸否定词（本身不是"拒绝联系"的强表述）后接这些**具体对象**时，否定作用在对象上，
#: 而不是作用在"是否继续联系"上：\"我不需要改期\"是拒绝改期，不是拒绝接听。
_BARE_REFUSAL_WORDS = ('不需要', '不用', '不必', '无需')
_SCOPED_REFUSAL_OBJECTS = ('改期', '换时间', '回电', '回电话', '电话', '预约', '邮件')


def _refusal_targets_object(text: str, start: int) -> bool:
    """该 refusal 触发点是否只否定了一个具体对象（改期/回电…），而非拒绝继续联系。"""
    for word in _BARE_REFUSAL_WORDS:
        if not text.startswith(word, start):
            continue
        tail = _clause_tail(text, start + len(word), NEGATION_WINDOW)
        return any(tail.startswith(obj) for obj in _SCOPED_REFUSAL_OBJECTS)
    return False


def detect_intents(text: str) -> list[str]:
    """确定性意图抽取（无网络、可重复）。聚合意图保留全部命中项。

    两层叠加：词形表（INTENT_RULES）+ 句法模式（_INTENT_PATTERNS）。
    模式只描述表达家族，不匹配任何评测句子全文，因此不会因为"某句话"才通过。
    """
    raw = text or ''
    # 先按"触发位置"收集，而不是只问"出现了没"：否定作用域要按**每个**触发点判断，
    # 只要有一个触发点没被否定，该意图仍然成立（聚合语义不变）。
    spans: dict[str, list[int]] = {}
    for intent, words in INTENT_RULES:
        for word in words:
            idx = raw.find(word)
            while idx != -1:
                spans.setdefault(intent, []).append(idx)
                idx = raw.find(word, idx + 1)
    for intent, patterns in _INTENT_PATTERNS:
        for pattern in patterns:
            for match in re.finditer(pattern, raw):
                spans.setdefault(intent, []).append(match.start())
    # 否定作用域：仅对高代价意图收窄（投诉/回电），全部触发点都被否定才摘除意图。
    for intent in NEGATION_SCOPED_INTENTS:
        if intent in spans and all(_span_negated(raw, start) for start in spans[intent]):
            spans.pop(intent, None)
    # 拒绝联系的对象作用域：\"我不需要改期\"只否定改期这一具体对象，不构成拒绝继续联系。
    if 'refuse_contact' in spans and all(_refusal_targets_object(raw, start)
                                         for start in spans['refuse_contact']):
        spans.pop('refuse_contact', None)
    hits: list[str] = []
    for intent, _words in INTENT_RULES:
        if intent in spans:
            hits.append(intent)
    for intent, _patterns in _INTENT_PATTERNS:
        if intent in spans and intent not in hits:
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
