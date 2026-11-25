"""固定合成场景语料：dev 24 条 / holdout 12 条，按规则家族与表达分组隔离。

设计约束（见 docs/design/08-evaluation-rsi.md §RSI）：
- 场景全部为合成数据，不含任何原始比赛数据；source_refs 指向 synthetic-rsi sheet。
- 每个场景标注 family（规则家族）与 expression_group（表达模板组）。
- dev 与 holdout 的 expression_group 命名空间互斥，且规范化后的用户话术不得完全相同，
  保证近似改写不跨组泄漏。
- 保留集（holdout）只由 gate 阶段读取一次；生成器只能读 dev，见 ReadLog。

`channel` 与 `last_contact_outcome` 是“已发布业务事实”，用于路由决策表；
它们不从对话文本推断（拒听/错人/静音不等于未接通）。
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5, UUID

from ..contracts import KnowledgeEntry, ReviewCase, SourceRef

SCENARIO_DIR = Path(__file__).resolve().parent / 'scenarios'
#: mission 仓库根（backend/app/rsi/corpus.py -> 上溯 3 层）。污染审计需要按仓库相对路径扫描。
MISSION_ROOT = Path(__file__).resolve().parents[3]
CORPUS_VERSION = 'rsi-corpus-v1'

DEV_SIZE = 24
HOLDOUT_SIZE = 12
DEV_GROUP_PREFIX = 'dev.'
HOLDOUT_GROUP_PREFIX = 'hold.'

#: 保留集版本注册表。**为什么需要两个**：`holdout.json` 的 4 条缺口在上一轮被写进
#: 缺陷报告，随后又被 `backend/tests/test_semantic.py` 的表达家族用例逐字引用，
#: 于是它已经"被看过"，不能再当作无偏提升证据（见 scripts/rsi_fix_contamination.py）。
#: `holdout-v2.json` 是本轮修复后新建的全新表达组，仅用于修复后的复验；
#: 它同样受"生成器只读 dev"约束，且必须在证据里如实标注作者与残留泄漏风险。
HOLDOUT_V2_GROUP_PREFIX = 'holdv2.'
CORPUS_SPLITS: tuple[str, ...] = ('dev', 'holdout', 'holdout-v2')
GROUP_PREFIX_BY_SPLIT: dict[str, str] = {
    'dev': DEV_GROUP_PREFIX,
    'holdout': HOLDOUT_GROUP_PREFIX,
    'holdout-v2': HOLDOUT_V2_GROUP_PREFIX,
}
SPLIT_SIZE_BY_SPLIT: dict[str, int] = {
    'dev': DEV_SIZE, 'holdout': HOLDOUT_SIZE, 'holdout-v2': HOLDOUT_SIZE,
}

KNOWLEDGE_MODES = ('approved', 'missing', 'conflict', 'quarantined')
IDENTITY_MODES = ('name_match', 'self_assertion')
ALLOWED_ROUTES = ('phone', 'jingmai', 'manual')
ALLOWED_CHANNELS = ('phone', 'jingmai')
ALLOWED_OUTCOMES = ('none', 'no_answer', 'refused', 'wrong_person', 'verified')
ALLOWED_EFFECTS = ('none', 'one')
ALLOWED_FAULTS = (None, 'writeback_failure', 'lost_response', 'timeout')
ACTION_KINDS = ('none', 'reject_application', 'create_followup')


class CorpusError(RuntimeError):
    """语料结构不合法。"""


class HoldoutLeakError(RuntimeError):
    """生成阶段试图读取保留集。"""


@dataclass
class ReadLog:
    """记录语料读取路径，用于证明生成器未读保留集。"""

    reads: list[tuple[str, str]] = field(default_factory=list)

    def record(self, split: str, path: Path) -> None:
        entry = (split, str(path.name))
        if entry not in self.reads:
            self.reads.append(entry)

    def splits(self) -> set[str]:
        return {split for split, _ in self.reads}

    def only_dev(self) -> bool:
        return bool(self.reads) and self.splits() == {'dev'}

    def as_dict(self) -> dict:
        return {'reads': [{'split': s, 'file': f} for s, f in self.reads]}


READ_LOG = ReadLog()


def _norm(text: str) -> str:
    """规范化比较键：去空白与标点、全半角统一，用于跨组泄漏检测。"""
    value = unicodedata.normalize('NFKC', text)
    value = re.sub(r'\s+', '', value)
    return re.sub(r'[，。！？、,.!?;:；：""\'\'（）()《》\-—…~～]', '', value)


def _det_uuid(kind: str, name: str) -> str:
    return str(uuid5(NAMESPACE_URL, f'rsi:{kind}:{name}'))


@dataclass(frozen=True)
class Expected:
    route: str
    terminal_states: tuple[str, ...]
    tool_effect: str
    expected_action: str
    identity_required: bool
    must_not_leak_before_verify: bool
    forbid_reply_claims: tuple[str, ...]
    require_reply_terms: tuple[str, ...]
    completion_relevant: bool


@dataclass(frozen=True)
class Turn:
    text: str
    intent: str
    identity_claim: bool


def published_channel(entries) -> str | None:
    """已发布渠道：只认 approved 且无 issue 的知识条目（与 coordinator.published_channel 同义）。"""
    channels = {e.channel for e in entries if e.status == 'approved' and not e.issue_codes}
    if channels == {'jingmai'}:
        return 'jingmai'
    if 'phone' in channels:
        return 'phone'
    return None


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    family: str
    expression_group: str
    split: str
    channel: str
    last_contact_outcome: str
    system_flow: bool
    authorized: bool
    rule_valid: bool
    case: ReviewCase
    knowledge: tuple[KnowledgeEntry, ...]
    knowledge_mode: str
    identity_mode: str
    expected: Expected
    turns: tuple[Turn, ...]
    fault: str | None
    repeat_sessions: int
    notes: str

    @property
    def turn_intents(self) -> tuple[str, ...]:
        return tuple(t.intent for t in self.turns)


def _source_refs(scenario_id: str) -> list[SourceRef]:
    digest = hashlib.sha256(f'synthetic-rsi:{scenario_id}'.encode()).hexdigest()
    return [SourceRef(file_sha256=digest, sheet='synthetic-rsi', row=1, columns=['reason', 'remedy'])]


def _build_knowledge(scenario_id: str, mode: str, case: ReviewCase, channel: str) -> tuple[KnowledgeEntry, ...]:
    company = case.slots['company_name']
    reason = case.slots['reason']
    remedy = case.slots['remedy']
    base = dict(
        knowledge_version='rsi-k1',
        kind='scenario',
        scenario_id=case.scenario_id,
        scope=['synthetic'],
        channel=channel,
        required_slots=['company_name'],
        taxonomy=['rsi.synthetic'],
        reviewer='synthetic-rsi',
        source_refs=_source_refs(scenario_id),
    )
    if mode == 'missing':
        return ()
    if mode == 'quarantined':
        return (KnowledgeEntry(
            entry_id=_det_uuid('knowledge', f'{scenario_id}:0'),
            approved_text=f'{reason}。{remedy}',
            status='quarantined',
            issue_codes=['QA_SEMANTIC_CONFLICT'],
            **base,
        ),)
    if mode == 'conflict':
        # 两条互相矛盾但都标记 approved 的条目：模拟上游未解决冲突，Agent 必须转人工而不是挑第一条。
        return (
            KnowledgeEntry(
                entry_id=_det_uuid('knowledge', f'{scenario_id}:0'),
                approved_text=f'{reason}。{remedy}',
                status='approved',
                issue_codes=[],
                **base,
            ),
            KnowledgeEntry(
                entry_id=_det_uuid('knowledge', f'{scenario_id}:1'),
                approved_text=f'{reason}。请按另一份来源在 30 天内补齐{company}的品牌授权。',
                status='approved',
                issue_codes=[],
                **base,
            ),
        )
    phone_or_written=KnowledgeEntry(
        entry_id=_det_uuid('knowledge', f'{scenario_id}:0'),approved_text=f'{reason}。{remedy}',
        status='approved',issue_codes=[],**base)
    # Explicitly separate synthetic written fixture; never a production fallback.
    if channel=='phone':
        written=phone_or_written.model_copy(update={'entry_id':UUID(_det_uuid('knowledge',f'{scenario_id}:written')),'channel':'jingmai','kind':'script'})
        return (phone_or_written,written)
    return (phone_or_written,)


def _require(mapping: dict, key: str, where: str):
    if key not in mapping:
        raise CorpusError(f'{where}: 缺少字段 {key}')
    return mapping[key]


def _build_scenario(raw: dict, split: str, index: int) -> Scenario:
    where = f'{split}[{index}]'
    scenario_id = _require(raw, 'scenario_id', where)
    if not isinstance(scenario_id, str) or not scenario_id:
        raise CorpusError(f'{where}: scenario_id 非法')
    family = _require(raw, 'family', where)
    group = _require(raw, 'expression_group', where)
    prefix = GROUP_PREFIX_BY_SPLIT[split]
    if not group.startswith(prefix):
        raise CorpusError(f'{where}: {scenario_id} 的 expression_group 必须属于 {prefix}*')
    case_raw = dict(_require(raw, 'case', where))
    for key in ('scenario_id', 'review_conclusion', 'slots'):
        _require(case_raw, key, f'{where}.case')
    slots = dict(case_raw['slots'])
    for key in ('company_name', 'reason', 'remedy'):
        _require(slots, key, f'{where}.case.slots')

    channel = raw.get('channel', 'phone')
    if channel not in ALLOWED_CHANNELS:
        raise CorpusError(f'{where}: 非法 channel {channel}')
    outcome = raw.get('last_contact_outcome', 'none')
    if outcome not in ALLOWED_OUTCOMES:
        raise CorpusError(f'{where}: 非法 last_contact_outcome {outcome}')
    system_flow = bool(raw.get('system_flow', False))

    knowledge_mode = raw.get('knowledge', 'approved')
    if knowledge_mode not in KNOWLEDGE_MODES:
        raise CorpusError(f'{where}: 未知 knowledge 模式 {knowledge_mode}')
    identity_mode = raw.get('identity_mode', 'name_match')
    if identity_mode not in IDENTITY_MODES:
        raise CorpusError(f'{where}: 未知 identity_mode {identity_mode}')
    fault = raw.get('fault')
    if fault not in ALLOWED_FAULTS:
        raise CorpusError(f'{where}: 未知 fault {fault}')

    expected_raw = _require(raw, 'expected', where)
    route = _require(expected_raw, 'route', f'{where}.expected')
    if route not in ALLOWED_ROUTES:
        raise CorpusError(f'{where}: 非法 route {route}')
    effect = expected_raw.get('tool_effect', 'none')
    if effect not in ALLOWED_EFFECTS:
        raise CorpusError(f'{where}: 非法 tool_effect {effect}')
    action = str(expected_raw.get('expected_action', 'none'))
    if action not in ACTION_KINDS:
        raise CorpusError(f'{where}: 非法 expected_action {action}')

    case = ReviewCase(
        application_id=_det_uuid('case', scenario_id),
        merchant_id=_det_uuid('merchant', scenario_id),
        case_revision=int(case_raw.get('case_revision', 1)),
        scenario_id=case_raw['scenario_id'],
        review_conclusion=case_raw['review_conclusion'],
        slots=slots,
        submission_attempt=int(case_raw.get('submission_attempt', 1)),
        call_attempt=int(case_raw.get('call_attempt', 0)),
        callback_requested=bool(case_raw.get('callback_requested', False)),
        complaint_risk=bool(case_raw.get('complaint_risk', False)),
    )

    knowledge = _build_knowledge(scenario_id, knowledge_mode, case, channel)

    turns_raw = raw.get('turns', [])
    if not isinstance(turns_raw, list):
        raise CorpusError(f'{where}: turns 必须是数组')
    if not turns_raw and not system_flow:
        raise CorpusError(f'{where}: 非 system_flow 场景必须至少一轮对话')
    turns: list[Turn] = []
    for n, turn in enumerate(turns_raw):
        text = _require(turn, 'text', f'{where}.turns[{n}]').replace('{{company}}', slots['company_name'])
        intent = _require(turn, 'intent', f'{where}.turns[{n}]')
        if not isinstance(text, str) or not text.strip():
            raise CorpusError(f'{where}.turns[{n}]: text 为空')
        turns.append(Turn(text=text, intent=intent, identity_claim=bool(turn.get('identity', False))))

    expected = Expected(
        route=route,
        terminal_states=tuple(expected_raw.get('terminal_states', [])),
        tool_effect=effect,
        expected_action=action,
        identity_required=bool(expected_raw.get('identity_required', True)),
        must_not_leak_before_verify=bool(expected_raw.get('must_not_leak_before_verify', True)),
        forbid_reply_claims=tuple(expected_raw.get('forbid_reply_claims', [])),
        require_reply_terms=tuple(expected_raw.get('require_reply_terms', [])),
        completion_relevant=bool(expected_raw.get('completion_relevant', True)),
    )
    if not expected.terminal_states:
        raise CorpusError(f'{where}.expected: terminal_states 不能为空')
    if system_flow and expected.identity_required and route == 'jingmai':
        raise CorpusError(f'{where}: 京麦-only 系统流程不应要求语音身份核验')

    repeat_sessions = int(raw.get('repeat_sessions', 1))
    if repeat_sessions < 1 or repeat_sessions > 2:
        raise CorpusError(f'{where}: repeat_sessions 只允许 1 或 2')

    authorized = bool(raw.get('authorized', True))
    rule_valid = bool(raw.get('rule_valid', True))

    return Scenario(
        scenario_id=scenario_id,
        family=family,
        expression_group=group,
        split=split,
        channel=channel,
        last_contact_outcome=outcome,
        system_flow=system_flow,
        authorized=authorized,
        rule_valid=rule_valid,
        case=case,
        knowledge=tuple(knowledge),
        knowledge_mode=knowledge_mode,
        identity_mode=identity_mode,
        expected=expected,
        turns=tuple(turns),
        fault=fault,
        repeat_sessions=repeat_sessions,
        notes=str(raw.get('notes', '')),
    )


def load_corpus(split: str) -> list[Scenario]:
    """读取一个分组。每次读取都会被 ReadLog 记录（holdout 只允许 gate 阶段读）。"""
    if split not in CORPUS_SPLITS:
        raise CorpusError(f'未知分组 {split}')
    path = SCENARIO_DIR / f'{split}.json'
    if not path.exists():
        raise CorpusError(f'缺失语料文件 {path}')
    raw = json.loads(path.read_text(encoding='utf-8'))
    if raw.get('corpus_version') != CORPUS_VERSION:
        raise CorpusError(f'{path.name}: corpus_version 必须是 {CORPUS_VERSION}')
    if raw.get('split') != split:
        raise CorpusError(f'{path.name}: split 字段与文件名不一致')
    items = raw.get('scenarios')
    if not isinstance(items, list):
        raise CorpusError(f'{path.name}: scenarios 必须是数组')
    READ_LOG.record(split, path)
    scenarios = [_build_scenario(item, split, i) for i, item in enumerate(items)]
    ids = [s.scenario_id for s in scenarios]
    if len(set(ids)) != len(ids):
        raise CorpusError(f'{path.name}: scenario_id 重复')
    return scenarios


def assert_isolated(dev: list[Scenario], holdout: list[Scenario],
                    *, holdout_split: str = 'holdout') -> dict:
    """校验分组隔离，返回可写入报告的证据字典。

    `holdout_split` 允许对 `holdout-v2`（修复后新建的复验保留集）做同样的隔离校验，
    并额外记录**跨保留集**重叠：v2 不得复用 v1 的任何话术，否则"新保留集"是假的。
    """
    problems: list[str] = []
    expected_holdout_size = SPLIT_SIZE_BY_SPLIT.get(holdout_split, HOLDOUT_SIZE)
    expected_prefix = GROUP_PREFIX_BY_SPLIT.get(holdout_split, HOLDOUT_GROUP_PREFIX)
    if len(dev) != DEV_SIZE:
        problems.append(f'dev 场景数 {len(dev)} != {DEV_SIZE}')
    if len(holdout) != expected_holdout_size:
        problems.append(f'{holdout_split} 场景数 {len(holdout)} != {expected_holdout_size}')

    dev_ids = {s.scenario_id for s in dev}
    hold_ids = {s.scenario_id for s in holdout}
    if dev_ids & hold_ids:
        problems.append(f'scenario_id 重叠 {sorted(dev_ids & hold_ids)}')

    dev_groups = {s.expression_group for s in dev}
    hold_groups = {s.expression_group for s in holdout}
    if dev_groups & hold_groups:
        problems.append(f'expression_group 重叠 {sorted(dev_groups & hold_groups)}')
    bad = {g for g in dev_groups if not g.startswith(DEV_GROUP_PREFIX)} | {
        g for g in hold_groups if not g.startswith(expected_prefix)}
    if bad:
        problems.append(f'expression_group 命名空间错误 {sorted(bad)}')

    dev_texts = {_norm(t.text) for s in dev for t in s.turns}
    hold_texts = {_norm(t.text) for s in holdout for t in s.turns}
    overlap = dev_texts & hold_texts
    if overlap:
        problems.append(f'话术文本完全相同 {sorted(overlap)[:5]}')

    hold_families = {s.family for s in holdout}
    dev_families = {s.family for s in dev}
    if not hold_families <= dev_families:
        problems.append(f'holdout 家族未出现在 dev {sorted(hold_families - dev_families)}')

    if problems:
        raise CorpusError('分组隔离校验失败: ' + '; '.join(problems))

    return {
        'holdout_split': holdout_split,
        'dev_scenarios': len(dev),
        'holdout_scenarios': len(holdout),
        'dev_families': sorted(dev_families),
        'holdout_families': sorted(hold_families),
        'dev_expression_groups': sorted(dev_groups),
        'holdout_expression_groups': sorted(hold_groups),
        'group_namespaces_disjoint': not (dev_groups & hold_groups),
        'identical_turn_texts': 0,
        'scenario_id_overlap': 0,
        'dev_turn_count': sum(len(s.turns) for s in dev),
        'holdout_turn_count': sum(len(s.turns) for s in holdout),
    }


#: 调参可见面：任何"人可以看、并据此改规则/提示"的目录。保留集话术只要逐字出现在
#: 这些位置，该场景就不能再算"没被看过"，其提升也不能作为采纳证据。
CONTAMINATION_SURFACE_DIRS: tuple[str, ...] = ('backend/tests', 'scripts', 'docs',
                                               'frontend/qa')
CONTAMINATION_SUFFIXES: tuple[str, ...] = ('.py', '.mjs', '.js', '.ts', '.tsx', '.json',
                                           '.md', '.txt')
_CONTAMINATION_CACHE: dict[tuple[str, tuple[str, ...]], dict] = {}


def _surface_files(roots: tuple[str, ...]) -> list[Path]:
    out: list[Path] = []
    for rel in roots:
        base = MISSION_ROOT / rel
        if not base.exists():
            continue
        for path in base.rglob('*'):
            if not path.is_file() or path.suffix.lower() not in CONTAMINATION_SUFFIXES:
                continue
            # 语料文件本身不是"可见面"，否则每一轮都会把自己算成泄漏。
            if path.parent == SCENARIO_DIR:
                continue
            out.append(path)
    return sorted(out)


def audit_holdout_contamination(split: str, *,
                                surface_dirs: tuple[str, ...] | None = None) -> dict:
    """**实测**保留集污染：统计每个保留场景的话术是否已出现在调参可见面。

    为什么必须是实测而不是声明：上一轮 gate 拒绝后，缺陷报告与
    `backend/tests/test_semantic.py` 逐字引用了 v1 的缺口话术，之后又新建了
    `holdout-v2`；如果只在代码里写一句"v2 无偏"，那就是自欺。这里把命中逐条列出来，
    并且返回 `seen_scenario_ids`，让 gate 能把"已看过"的场景从提升证据里剔除。

    **本轮修复（t4 第二轮）——审计工具自己也曾是污染源**：
        上一版 hits 里带 `text`（遮蔽公司名后的**逐字话术**）。于是为了让缺陷可追溯，
        证据文件必须引用那句话；而证据文件本身又落在调参可见面（`docs/`）里，
        下一轮扫描就把它算成"保留集已泄漏"——审计工具用自己的输出污染了它要保护的集合。
        证据文件（`rsi-fix2-status.txt` / `rsi-fix3-probe.json`）实测就是这样被命中的。

        修复发生在**识别/评测逻辑侧**，而不是去删证据或放宽判定：
        本审计现在只输出 `text_sha256`（规范化文本的指纹前 12 位）与 `text_len`，
        **不再复制任何保留集正文**。这样证据可以照样可复核（指纹可逐位比对），
        但不会把题目搬进可见面。已经写下的历史证据仍如实计为命中，不删改。

    返回结构（可直接写进证据 JSON）：
        seen_scenario_ids / hits[{scenario_id,text_sha256,text_len,found_in}] /
        contaminated_turns / scanned_files / unbiased_usable
    """
    roots = tuple(surface_dirs or CONTAMINATION_SURFACE_DIRS)
    key = (split, roots)
    if key in _CONTAMINATION_CACHE:
        return _CONTAMINATION_CACHE[key]

    scenarios = load_corpus(split)
    surfaces: dict[str, str] = {}
    for path in _surface_files(roots):
        try:
            surfaces[str(path.relative_to(MISSION_ROOT))] = _norm(
                path.read_text(encoding='utf-8', errors='replace'))
        except (OSError, ValueError):
            continue

    hits: list[dict] = []
    seen_ids: list[str] = []
    total = 0
    for scenario in scenarios:
        for turn in scenario.turns:
            total += 1
            needle = _norm(turn.text)
            if len(needle) < 4:
                continue
            found_in = next((name for name, blob in surfaces.items() if needle in blob), None)
            if found_in is None:
                continue
            # 只暴露指纹与长度：可逐位复核，但不把保留集正文复制到可见面。
            hits.append({'scenario_id': scenario.scenario_id,
                         'text_sha256': hashlib.sha256(needle.encode('utf-8')).hexdigest()[:12],
                         'text_len': len(needle),
                         'intent': turn.intent,
                         'found_in': found_in})
            if scenario.scenario_id not in seen_ids:
                seen_ids.append(scenario.scenario_id)

    report = {
        'split': split,
        'scenarios': len(scenarios),
        'turns': total,
        'scanned_files': len(surfaces),
        'surface_dirs': list(roots),
        'contaminated_turns': len(hits),
        'contaminated_ratio': round(len(hits) / total, 4) if total else 0.0,
        'seen_scenario_ids': sorted(seen_ids),
        'hits': hits,
        'unbiased_usable': not hits,
        'text_redaction': 'sha256+len（不再复制保留集正文，避免审计工具自己污染可见面）',
        'note': ('只读实测：保留集话术与调参可见面逐字比对；命中即该场景已被看过，'
                 '不能再计入提升证据。命中项只给指纹与长度，不给正文。'),
    }
    _CONTAMINATION_CACHE[key] = report
    return report


def assert_holdouts_disjoint(new_holdout: list[Scenario],
                             prior_holdout: list[Scenario]) -> dict:
    """两个保留集之间必须话术零重叠，否则"新保留集"没有新增无偏信息。"""
    new_texts = {_norm(t.text) for s in new_holdout for t in s.turns}
    old_texts = {_norm(t.text) for s in prior_holdout for t in s.turns}
    overlap = sorted(new_texts & old_texts)
    new_groups = {s.expression_group for s in new_holdout}
    old_groups = {s.expression_group for s in prior_holdout}
    group_overlap = sorted(new_groups & old_groups)
    return {
        'new_turns': len(new_texts),
        'prior_turns': len(old_texts),
        'turn_text_overlap': overlap,
        'expression_group_overlap': group_overlap,
        'disjoint': not overlap and not group_overlap,
    }
