"""结构化理解（**委托生产实现**，不在 RSI 内复制第二套规则）与服务端身份核验边界。

本轮修复的缺陷（t4 RSI 保留集识别缺陷）：
    本模块曾经自带一份 `INTENT_RULES` / `detect_intents` / `normalize` 的**副本**。
    生产理解层（`backend/app/semantic.py`）后来泛化了表达识别（词形表 + 句法模式
    `_INTENT_PATTERNS`），而 RSI 副本没有同步，于是 RSI 评测的是**过期的识别层**：
    保留集上 4 条轮次被误判为 `unclear`，指标把"RSI 副本落后"错记成"候选没泛化"。
    修复方式：RSI **只保留提示/表达规格**（baseline/candidate）与**服务端身份核验**，
    识别一律 `from ..semantic import ...` 引用同一对象，并用 `delegation_evidence()`
    在证据里证明 `is` 同一性（不是"抄得像"，而是同一个函数对象）。

职责划分（见 docs/design/08-evaluation-rsi.md 与团队会议）：
- 本模块只做“提示规格”与“服务端核验判定”；理解本身由生产实现负责。
- 理解层永远不能写 `verified`、审核结论、成功状态；`verified` 只能由
  `server_verify_identity` 依据服务端掌握的申请单事实写入。
- 真实 GLM 的理解输出必须落成同一个 `Understanding` DTO 并经过生产
  `validate_understanding` 的 schema/越权键校验，不能直接进入业务。

`PromptSpec` 里 baseline / candidate 两版只描述**表达与理解提示**。候选生成器
（candidates.py）被限制只能改这些键；审核结论/权限/官方知识/路由门槛/工具白名单
一律不可通过提示改动。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: 生产理解层是**唯一权威源**。下面这些符号必须是同一个对象（`is` 成立）。
from ..semantic import (ALLOWED_INTENTS as _ALLOWED_INTENTS,
                        FORBIDDEN_UNDERSTANDING_KEYS as _PROD_FORBIDDEN,
                        INTENT_RULES,
                        contact_outcome_hint,
                        detect_intents,
                        normalize,
                        parse_understanding,
                        validate_understanding)
from ..contracts import ReviewCase, Understanding

__all__ = [
    'INTENT_RULES', 'normalize', 'detect_intents', 'contact_outcome_hint',
    'parse_understanding', 'validate_understanding_payload', 'server_verify_identity',
    'identity_evidence', 'audit_identity_scaffold',
    'delegation_evidence', 'audit_understanding_delegation',
    'FORBIDDEN_UNDERSTANDING_KEYS', 'ALLOWED_INTENTS',
    'PromptSpec', 'BASELINE_PROMPT', 'CANDIDATE_PROMPT',
]

#: 与生产同一对象；保留旧名以免下游 import 断裂。
FORBIDDEN_UNDERSTANDING_KEYS: frozenset[str] = _PROD_FORBIDDEN
ALLOWED_INTENTS: tuple[str, ...] = _ALLOWED_INTENTS


def delegation_evidence() -> dict:
    """证明 RSI 没有第二套识别规则：逐项校验与生产实现是**同一对象**。"""
    from .. import semantic as production

    pairs = {
        'detect_intents': (detect_intents, production.detect_intents),
        'parse_understanding': (parse_understanding, production.parse_understanding),
        'normalize': (normalize, production.normalize),
        'contact_outcome_hint': (contact_outcome_hint, production.contact_outcome_hint),
        'validate_understanding': (validate_understanding, production.validate_understanding),
        'INTENT_RULES': (INTENT_RULES, production.INTENT_RULES),
    }
    symbol_identity = {f'rsi.{name} is production.{name}': (mine is theirs)
                       for name, (mine, theirs) in pairs.items()}
    # 反向证据：RSI 模块里不得再持有自己的句法模式表。
    symbol_identity['rsi.has_own_pattern_table'] = hasattr(
        __import__(__name__, fromlist=['_INTENT_PATTERNS']), '_INTENT_PATTERNS')
    symbol_identity['rsi.intent_rule_row_count'] = len(INTENT_RULES)
    symbol_identity['production.intent_rule_row_count'] = len(production.INTENT_RULES)
    return {
        'authority': 'backend/app/semantic.py（生产理解层）',
        'symbol_identity': symbol_identity,
        'single_source': all(v is True for k, v in symbol_identity.items()
                             if k.startswith('rsi.') and ' is production.' in k)
        and symbol_identity['rsi.has_own_pattern_table'] is False,
    }


def validate_understanding_payload(payload: dict, *, text: str | None = None) -> Understanding:
    """服务器校验（等价于真实 GLM 适配器必须通过的门）；委托生产实现。

    拒绝：未知/越权键、未知意图、非法联系结果、试图声明核验/成功。
    """
    return validate_understanding(payload, text=text)


def _text_digest(text: str) -> str:
    """规范化文本的指纹前 12 位：证据可逐位复核，但不复制原文。"""
    import hashlib
    return hashlib.sha256(normalize(text).encode('utf-8')).hexdigest()[:12]


def audit_understanding_delegation(scenarios_by_split: dict | None = None) -> dict:
    """**脚手架自检**：RSI 是否在评测"当前生产理解层"，而不是一份过期副本。

    为什么必须有它：上一轮 gate 拒绝的直接原因是保留集上 4 条轮次被识别成 `unclear`。
    事后核对发现根因是 **RSI 自带的 INTENT_RULES 副本落后于生产 `app.semantic`**，
    而报告把它写成了"候选没泛化"。如果不把这个判定做进评测流程，同样的误判会再次发生：
    脚手架过期 -> 失败归因到候选 -> 优化器去改提示词 -> 伪造成果。

    本函数不修改任何规则，只做三项**只读**测量：
        1. 符号同一性：`is` 比较（不是"抄得像"）；
        2. 覆盖率：每个分组的标注意图能否被**当前**识别层检出（逐轮列出缺口）；
        3. 判定：`stale_harness = (覆盖率 < 1.0 且 生产层自己也识别不出)` 不成立即视为可用；
           若 RSI 与生产是同一实现，覆盖缺口属于**生产理解层能力缺口**，不是脚手架过期。

    返回结构可直接写进证据 JSON，并由 `runner.run_suite(harness_audit=...)` 传给归因。
    """
    from .. import semantic as production
    import importlib

    delegation = delegation_evidence()
    rsi_module = importlib.import_module(__name__)

    same_object = all(
        getattr(rsi_module, name) is getattr(production, name)
        for name in ('detect_intents', 'parse_understanding', 'normalize',
                     'contact_outcome_hint', 'validate_understanding'))

    if scenarios_by_split is None:
        from .corpus import load_corpus
        scenarios_by_split = {split: load_corpus(split)
                              for split in ('dev', 'holdout') if split != 'holdout-v2'}

    coverage: dict[str, dict] = {}
    missed_total = 0
    for split, scenarios in scenarios_by_split.items():
        missed: list[dict] = []
        turns = 0
        for scenario in scenarios:
            for turn in scenario.turns:
                turns += 1
                detected = detect_intents(turn.text)
                if turn.intent in detected:
                    continue
                missed.append({
                    'scenario_id': scenario.scenario_id,
                    'intent': turn.intent,
                    'detected': detected,
                    # 只给指纹与长度：证据可逐位复核，但**不复制语料正文**。
                    # （上一版这里带遮蔽正文，导致证据文件把保留集题目搬进了可见面，
                    #   下一轮污染审计反过来判它泄漏——审计工具不能污染它要保护的集合。）
                    'text_sha256': _text_digest(turn.text),
                    'text_len': len(normalize(turn.text)),
                })
        missed_total += len(missed)
        coverage[split] = {
            'scenarios': len(scenarios), 'turns': turns, 'missed': len(missed),
            'coverage': round((turns - len(missed)) / turns, 4) if turns else None,
            'missed_turns': missed,
        }

    # 第二类脚手架缺陷：核验是否被理解层卡住（本轮新增）。
    identity = audit_identity_scaffold(scenarios_by_split)

    return {
        'authority': 'backend/app/semantic.py（生产理解层）',
        'same_object_as_production': same_object,
        'rsi_has_own_rule_table': delegation['symbol_identity']['rsi.has_own_pattern_table'],
        'symbol_identity': delegation['symbol_identity'],
        'coverage': coverage,
        'missed_turns_total': missed_total,
        # 判定口径：只有当"RSI 有自己的一套规则 且 它与生产不一致"时才是脚手架过期。
        # 同一实现下的覆盖缺口属于生产理解层能力问题，归因应写 understanding，不能写 harness。
        'stale_harness': (not same_object),
        'identity_scaffold': {
            'decoupled': identity['decoupled'],
            'nlu_has_veto': identity['nlu_has_veto'],
            'nlu_can_only_tighten': identity['nlu_can_only_tighten'],
            'probe_rows': len(identity['probes']),
            'nlu_blind_positives': identity['nlu_blind_positives'],
        },
        'note': ('只读自检：不修改任何识别规则。同一实现下的覆盖缺口归 understanding'
                 '（生产理解层能力），有第二套规则且落后时才归 harness（脚手架缺陷）。'
                 '`identity_scaffold.decoupled=False` 表示身份核验仍被理解层卡住 —— 那也是'
                 '脚手架缺陷，必须先修核验侧，不能拿去当"候选没泛化"的失败证据。'),
    }


# ------------------------------------------------- 服务端身份证据（核验侧，非理解侧）

#: **服务端身份证据规则**：只描述"汉语里怎么算自述本人 / 怎么算否认身份"，
#: 与理解层的 `INTENT_RULES` / `_INTENT_PATTERNS` **无关**，也**不是**评测答案。
#:
#: 为什么必须单独存在（本轮 t4 修复的缺陷）：
#:   修复前 `server_verify_identity` 要求 `'confirm_identity' in parse_understanding(text).intents`。
#:   这等于把**服务端权威判定**（身份是否核验）的否决权交给**理解层**（模型/规则）。
#:   理解层漏识别一个口语自述（例如"对，我就是…的经办人"）时，核验直接失败，
#:   整条主链一次都不派发，指标却把这次失败记成"候选没泛化/理解层缺口"。
#:   契约写的是"`verified` 只能由服务端证据决定，模型不得自造 verified"——
#:   模型不该有**放宽**的权力，也不该有**否决**服务端证据的权力。
#:   现在：核验只看服务端证据（登记主体名 + 自述句式 + 无否认），
#:   理解层只能**收紧**（它说 deny_identity 时一律不核验），不能放宽、不能否决。
_IDENTITY_DENIAL_PATTERNS: tuple[str, ...] = (
    # 否认身份：不是本人/不是这家/不是我们/不是我
    r'不是(?:本人|这家|我们|我|公司|他|她)',
    # 否定 + 角色：我不是负责人/并非经办人/不是对接人
    r'(?:不是|并非|不算|不属于)[^，。！？；,.!?;]{0,6}'
    r'(?:负责人|经办人|对接人|联系人|专员|员工|法人|这家|我们|本人|公司的人)',
    # 拨错/找错/冒名/查无此人
    r'(?:打错|拨错|找错|搞错|弄错)',
    r'没有这个人|没这个人|无此人|查无此人',
    r'冒名|不认识|非本人',
)

#: 自述本人的**句式**（不是词形白名单）。只匹配"我…是…"这一汉语系词家族，
#: 覆盖"我是/我就是/我这边是/我这是/是我/本人"等口语变体，不针对任何具体句子。
_IDENTITY_SELF_ASSERTION_PATTERNS: tuple[str, ...] = (
    r'我(?:就|这边|这边就|这|这是|现在|目前|确实)?是',
    r'是(?:我|本人)',
    r'本人(?:就)?(?:是|这边)',
    r'我(?:就是|是)?[^，。！？；,.!?;]{0,6}'
    r'(?:负责人|经办人|对接人|联系人|专员|员工|法人)',
)


def identity_evidence(text: str, case: ReviewCase) -> dict:
    """服务端身份证据（只读、可复核、可写入证据文件）。

    三类证据分开列出，避免把"理解层漏识别"与"服务端证据不足"混成一个布尔值：
        - `name_present`：本轮文本里是否出现**服务端登记**的主体名称（权威事实）；
        - `self_assertion_hits`：是否出现汉语自述句式（我不会拿公司名当核验凭据）；
        - `denial_hits`：是否出现否认身份句式（一票否决）；
        - `nlu_intents`：理解层给了什么（**仅供证据记录**，不参与放宽判定）。
    """
    company = normalize(case.slots.get('company_name', ''))
    body = normalize(text or '')
    denial = [p for p in _IDENTITY_DENIAL_PATTERNS if re.search(p, body)]
    self_assertion = [p for p in _IDENTITY_SELF_ASSERTION_PATTERNS if re.search(p, body)]
    intents = list(parse_understanding(text or '').intents)
    return {
        'name_present': bool(company) and company in body,
        'self_assertion_hits': self_assertion,
        'denial_hits': denial,
        'nlu_intents': intents,
        # 两者不一致时说明理解层漏了自述：核验仍按服务端证据走，但要把差异记进证据。
        'nlu_missed_self_assertion': bool(self_assertion) and 'confirm_identity' not in intents,
        'nlu_denies': 'deny_identity' in intents,
    }


def server_verify_identity(text: str, case: ReviewCase,
                           identity_mode: str = 'name_match') -> tuple[bool, str]:
    """服务端身份核验：只看**服务端证据**，不接受模型自述，也不接受模型否决。

    返回 (verified, reason)。判定顺序（fail-closed）：
        1. 缺主体名槽位 -> 拒绝；
        2. 出现否认身份句式，或理解层说 deny_identity -> 拒绝（模型只能收紧）；
        3. 未出现自述句式 -> 拒绝（问澄清，不猜）；
        4. 本轮文本未出现服务端登记的主体名称 -> 拒绝（不能凭"我是负责人"核验）；
        5. 以上成立 -> 核验通过。

    `identity_mode` 保留原语义：`self_assertion` 同样要求出现登记主体名（自然身份
    表达本身不足以核验），只是把理由码区分开，便于证据对照。
    """
    company = normalize(case.slots.get('company_name', ''))
    if not company:
        return False, 'missing_company_slot'
    evidence = identity_evidence(text, case)
    # 模型/理解层只能让结果更严格：它说否认就否认；它没说自述也不影响服务端证据。
    if evidence['denial_hits'] or evidence['nlu_denies']:
        return False, 'identity_denied'
    if not evidence['self_assertion_hits']:
        return False, 'no_self_assertion'
    if not evidence['name_present']:
        return False, 'name_mismatch'
    if identity_mode == 'self_assertion':
        return True, 'self_assertion+name_match'
    return True, 'name_match'


def audit_identity_scaffold(scenarios_by_split: dict | None = None) -> dict:
    """**实测**：服务端核验是否已与理解层解耦（模型不能放宽、也不能否决）。

    做法（全部只读）：
        1. 合成探针：构造若干**非语料**表达，逐条记录
           `detect_intents` 结果与 `server_verify_identity` 结果；
           其中必须有"理解层没给 confirm_identity，但服务端证据完整 -> 核验通过"的样例，
           以及"否认 / 无主体名 / 第三方口吻 -> 核验失败"的负例；
        2. 逐分组统计：有多少轮是"理解层漏了自述但服务端仍核验"，如实计数；
        3. 判定 `decoupled = 正例通过 且 全部负例被拒`。

    合成探针使用虚构主体名 `示例商户测`，与任何语料句子不同；这里验证的是**句式家族**，
    不是"某一句评测句能过"。
    """
    from ..contracts import ReviewCase

    probe_case = ReviewCase(
        application_id='00000000-0000-0000-0000-00000000a001',
        merchant_id='00000000-0000-0000-0000-00000000a002',
        case_revision=1, scenario_id='scn.identity-probe', review_conclusion='rejected',
        submission_attempt=1, call_attempt=0, callback_requested=False, complaint_risk=False,
        slots={'company_name': '示例商户测', 'reason': '探针原因', 'remedy': '探针整改'})

    #: (文本, 期望核验结果, 说明)。文本均为人工构造的通用表达，不取自任何语料。
    probes = (
        ('对，我就是示例商户测的经办人', True, '口语自述（"我就是…"），理解层词形易漏'),
        ('我这边是示例商户测', True, '常规自述'),
        ('是我，示例商户测的', True, '倒装自述'),
        ('我就是这样的负责人', False, '有自述但未出现登记主体名'),
        ('示例商户测那边让我打过来的', False, '第三方口吻，不是本人自述'),
        ('不是，我不是示例商户测的人', False, '否认身份优先'),
        ('喂你好哪位啊', False, '无自述'),
        ('我是别家商户的负责人', False, '主体名不匹配'),
        ('我不是示例商户测的负责人', False, '否定 + 角色，不得核验'),
    )
    rows = []
    positives_ok = True
    negatives_ok = True
    for text, expected, why in probes:
        evidence = identity_evidence(text, probe_case)
        ok, reason = server_verify_identity(text, probe_case)
        rows.append({
            'text': text, 'expected_verified': expected, 'verified': ok, 'reason': reason,
            'nlu_intents': evidence['nlu_intents'],
            'nlu_missed_self_assertion': evidence['nlu_missed_self_assertion'],
            'name_present': evidence['name_present'], 'why': why,
            'ok': ok is expected,
        })
        if expected:
            positives_ok = positives_ok and ok is True
            # 至少一条正例必须证明"理解层漏识别也能核验"，否则解耦没被实测到。
        else:
            negatives_ok = negatives_ok and ok is False
    nlu_blind_positives = [r for r in rows
                           if r['expected_verified'] and r['nlu_missed_self_assertion']]
    decoupled = positives_ok and negatives_ok and bool(nlu_blind_positives)

    coverage: dict[str, dict] = {}
    for split, scenarios in (scenarios_by_split or {}).items():
        turns = verified = nlu_blind = 0
        blind_turns: list[dict] = []
        for scenario in scenarios:
            for turn in scenario.turns:
                turns += 1
                evidence = identity_evidence(turn.text, scenario.case)
                ok, reason = server_verify_identity(turn.text, scenario.case,
                                                    scenario.identity_mode)
                verified += int(ok)
                if ok and evidence['nlu_missed_self_assertion']:
                    nlu_blind += 1
                    blind_turns.append({
                        'scenario_id': scenario.scenario_id,
                        'labeled_intent': turn.intent,
                        'nlu_intents': evidence['nlu_intents'],
                        'reason': reason,
                        # 只给指纹与长度，不复制保留集正文。
                        'text_sha256': _text_digest(turn.text),
                        'text_len': len(normalize(turn.text)),
                    })
        coverage[split] = {'scenarios': len(scenarios), 'turns': turns,
                           'verified_turns': verified,
                           'verified_while_nlu_missed': nlu_blind,
                           'blind_turns': blind_turns}

    return {
        'authority': 'backend/app/rsi/understanding.py::identity_evidence（服务端证据）',
        'nlu_has_veto': False,
        'nlu_can_only_tighten': True,
        'decoupled': decoupled,
        'positives_ok': positives_ok,
        'negatives_ok': negatives_ok,
        'nlu_blind_positives': len(nlu_blind_positives),
        'probes': rows,
        'coverage': coverage,
        'note': ('只读实测：合成探针为人工构造的通用表达（虚构主体名），不取自任何语料；'
                 '正例全部通过 + 负例全部被拒 + 至少一条"理解层漏识别仍能核验"才算解耦成立。'),
    }


@dataclass(frozen=True)
class PromptSpec:
    """受约束的提示/表达规格：只允许改表达措辞与理解开关。"""

    spec_id: str
    identity_ask: str
    identity_ask_clarify: str
    guarantee_refusal: str
    source_grounding: str
    conflict_escalation: str
    missing_knowledge: str
    callback_human: str
    complaint_human: str
    unclear_clarify: str
    wrong_person_close: str
    refused_close: str
    action_notice: str
    enabled_understanding: tuple[str, ...]


BASELINE_PROMPT = PromptSpec(
    spec_id='prompt-baseline',
    identity_ask='这里是模拟商户服务AI。请确认您是否是{company}的联系人。',
    identity_ask_clarify='这里是模拟商户服务AI。请确认您是否是{company}的联系人。',
    guarantee_refusal='补齐材料后仍需审核，不能保证通过。',
    source_grounding='以已审核依据为准。',
    conflict_escalation='需要人工核实。',
    missing_knowledge='当前缺少已审核依据，需要人工核实，暂不办理。',
    callback_human='已标记需要人工协调回电。',
    complaint_human='已记录，转人工。',
    unclear_clarify='请再说明一下您的问题。',
    wrong_person_close='好的，停止本次告知，后续交人工核实。再见。',
    refused_close='好的，停止本次告知，后续交人工核实。再见。',
    action_notice='已记录您的反馈，正在提交模拟处理；请以页面业务结果为准。',
    enabled_understanding=(),
)

#: 候选版的表达与理解提示（只改措辞与理解开关，不动规则/权限/知识/门槛）。
CANDIDATE_PROMPT = PromptSpec(
    spec_id='prompt-candidate-v1',
    identity_ask='您好，这里是模拟商户服务AI。为保护商家信息，请先确认您是否是{company}的联系人？',
    identity_ask_clarify='抱歉没听清。为保护商家信息，请确认您是否是{company}的联系人，我再按已审核依据说明。',
    guarantee_refusal='补齐材料后仍需审核，不能保证通过，也不能承诺时限；是否通过以审核结论为准。',
    source_grounding='这条结论来自已审核依据，具体数字和时限未核实前我们不作答复。',
    conflict_escalation='当前知识依据存在冲突，已转人工核实，暂不给出具体标准。',
    missing_knowledge='缺少已审核依据，已转人工核实，暂不能答复具体标准。',
    callback_human='回电或改期需要人工协调确认具体时间窗，本演示不自动预约，也不代表真人已接通。',
    complaint_human='已记录投诉风险，转人工处理，本次不自动办理驳回。',
    unclear_clarify='我没有完全理解，请确认您是想了解驳回原因，还是整改要补的材料？',
    wrong_person_close='已记录本次为非本人接听，不会披露任何申请详情，后续由人工处理。',
    refused_close='已记录您的要求，停止本次告知，后续不再自动联系。',
    action_notice='已记录您的反馈，正在提交模拟处理；请以页面业务结果为准。',
    enabled_understanding=('negation', 'multi_intent', 'clarification', 'guarantee_refusal',
                           'source_grounding', 'conflict_escalation'),
)

#: 显式声明：本文件**不含**任何私有识别规则；所有识别符号来自 ..semantic。
PRIVATE_RULE_TABLES: tuple[str, ...] = ()
