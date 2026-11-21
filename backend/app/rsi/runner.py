"""RSI runner：3 次重复、交错执行 baseline/candidate，dev 与 holdout 严格分段。

隔离规则（docs/design/08-evaluation-rsi.md §RSI + 用户任务书）：
- 生成器只能读 dev：`propose()` 之前断言 `READ_LOG.splits() == {'dev'}`。
- holdout 只在 gate 阶段读取一次；本模块记录读取次数并在报告中输出。
- 交错：同一 repeat 内先 baseline 后 candidate，逐场景交错，避免时间/顺序偏差。
- 每个 (scenario, arm, repeat) 独立 Ledger；请求计数/并发/超时来自同一个 RequestBudget。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from .assertions import evaluate_assertions
from .attribution import attribute, summarize
from .budget import RequestBudget
from .candidates import Candidate, baseline_candidate, propose, reference_candidate, validate_candidate
from .corpus import (DEV_SIZE, HOLDOUT_SIZE, READ_LOG, HoldoutLeakError, Scenario,
                     assert_holdouts_disjoint, assert_isolated, audit_holdout_contamination,
                     load_corpus)
from .gate import (evaluate_gate, GateResult, HARD_SAFETY_ASSERTIONS)
from .simulator import Arm, ScenarioRun, Simulator, compare_with_production, make_arm


@dataclass
class SuiteReport:
    suite: str
    scenarios: int
    repeats: int
    arms: list[str]
    runs: list[ScenarioRun] = field(default_factory=list)
    isolation: dict = field(default_factory=dict)
    budget: dict = field(default_factory=dict)
    cross_check: dict = field(default_factory=dict)
    read_log: dict = field(default_factory=dict)
    harness_audit: dict = field(default_factory=dict)

    def by_arm(self, arm: str) -> list[ScenarioRun]:
        return [r for r in self.runs if r.arm == arm]

    def arm_summary(self) -> dict:
        out = {}
        for arm in self.arms:
            runs = self.by_arm(arm)
            passed = sum(1 for r in runs if r.passed)
            out[arm] = {
                'runs': len(runs),
                'passed': passed,
                'failed': len(runs) - passed,
                'completion': round(passed / len(runs), 4) if runs else 0.0,
                'timeouts': sum(r.timeouts for r in runs),
                'errors': [{'scenario_id': r.scenario_id, 'repeat': r.repeat, 'error': r.error}
                           for r in runs if r.error],
            }
        return out

    def assertion_summary(self) -> dict:
        out = {}
        for arm in self.arms:
            runs = self.by_arm(arm)
            total = sum(len(r.assertions) for r in runs)
            passed = sum(1 for r in runs for a in r.assertions if a['passed'])
            failed_ids: dict[str, int] = {}
            for r in runs:
                for a in r.assertions:
                    if not a['passed']:
                        failed_ids[a['id']] = failed_ids.get(a['id'], 0) + 1
            out[arm] = {
                'assertions_total': total,
                'assertions_passed': passed,
                'assertion_pass_rate': round(passed / total, 4) if total else 0.0,
                'failed_by_assertion': failed_ids,
            }
        return out

    def attribution_summary(self) -> dict:
        out = {}
        for arm in self.arms:
            runs = self.by_arm(arm)
            items = [i for r in runs for i in r.attribution]
            out[arm] = summarize(items, runs)
        return out

    def failed_cases(self) -> list[dict]:
        return [r.as_dict() for r in self.runs if not r.passed]

    def as_dict(self, *, verbose: bool = False) -> dict:
        return {
            'suite': self.suite,
            'scenarios': self.scenarios,
            'repeats': self.repeats,
            'arms': self.arms,
            'isolation': self.isolation,
            'budget': self.budget,
            'cross_check': self.cross_check,
            'read_log': self.read_log,
            'harness_audit': getattr(self, 'harness_audit', {}),
            'arm_summary': self.arm_summary(),
            'assertion_summary': self.assertion_summary(),
            'attribution_summary': self.attribution_summary(),
            'failed_cases': self.failed_cases(),
            'runs': [r.as_dict(verbose=verbose) for r in self.runs],
        }


async def run_suite(scenarios: list[Scenario], *, arms: dict[str, Candidate],
                    repeats: int = 3, budget: RequestBudget | None = None,
                    suite: str = 'dev', harness_audit: dict | None = None) -> SuiteReport:
    """交错执行：repeat 外层、scenario 中层、arm 内层。

    `harness_audit` 是可选的"脚手架自检"结果（见 `audit_understanding_delegation`）。
    当它判定 RSI 与生产理解层是同一实现且覆盖全部标注意图时，归因里不会误报 `harness`。
    """
    sim = Simulator(budget=budget)
    built = {name: make_arm(name, validate_candidate(cand)) for name, cand in arms.items()}
    report = SuiteReport(suite=suite, scenarios=len(scenarios), repeats=repeats,
                         arms=list(arms.keys()))
    # 脚手架自检：判定"这条失败到底是评测脚手架过期，还是候选/生产理解层的能力问题"。
    # 未显式传入时用**当前场景集合**做只读自检（不读其他分组，不改进任何规则）。
    if harness_audit is None:
        from .understanding import audit_understanding_delegation
        harness_audit = audit_understanding_delegation({suite: scenarios})
    report.harness_audit = harness_audit
    harness_defect = bool(harness_audit.get('stale_harness'))
    for repeat in range(1, repeats + 1):
        for scenario in scenarios:
            for name, arm in built.items():
                run = await sim.run(scenario, arm, repeat)
                run.assertions = evaluate_assertions(run, scenario)
                run.attribution = attribute(run, scenario, harness_defect=harness_defect)
                report.runs.append(run)
    report.budget = sim.budget.snapshot()
    report.read_log = READ_LOG.as_dict()
    return report


def assert_only_dev_reads() -> None:
    """生成器前置断言：ReadLog 必须只包含 dev。"""
    if not READ_LOG.only_dev():
        raise HoldoutLeakError(f'生成器读取了非 dev 分组：{sorted(READ_LOG.splits())}')


async def run_dev_and_candidate(*, repeats: int = 3, budget: RequestBudget | None = None,
                                candidate: Candidate | None = None,
                                use_reference_candidate: bool = False) -> tuple[SuiteReport, Candidate]:
    """1) 只读 dev；2) 生成候选；3) 跑 dev 基线+候选（交错 3 次）。"""
    import copy
    READ_LOG.reads.clear()
    dev = load_corpus('dev')
    holdout_for_isolation = None  # 生成阶段绝不读 holdout

    assert_only_dev_reads()
    baseline = baseline_candidate()
    if candidate is None:
        candidate = reference_candidate() if use_reference_candidate else propose(dev)
    validate_candidate(candidate)
    # 候选生成后再次确认没有读 holdout
    if not READ_LOG.only_dev():
        raise HoldoutLeakError('候选生成后出现非 dev 读取')

    report = await run_suite(dev, arms={'baseline': baseline, 'candidate': candidate},
                             repeats=repeats, budget=budget, suite='dev')
    report.isolation = {'generator_only_read_dev': True,
                        'holdout_read_during_generation': False}
    report.cross_check = compare_with_production(dev)
    report.candidate = candidate  # type: ignore[attr-defined]
    return report, candidate


async def run_holdout_gate(*, candidate: Candidate, repeats: int = 3,
                           budget: RequestBudget | None = None,
                           dev_report: SuiteReport | None = None,
                           voice_evidence: dict | None = None,
                           cost_evidence: dict | None = None,
                           holdout_split: str = 'holdout') -> tuple[SuiteReport, GateResult]:
    """gate 阶段：**只在此处**读取一次保留集，并做隔离校验与无偏性登记。

    `holdout_split` 允许选择 `holdout`（v1，已泄漏，仅回归对照）或
    `holdout-v2`（修复后新建，可用于提升证据）。两份都不会在读 dev 之前被读。
    """
    from .gate import HOLDOUT_PROVENANCE

    dev = load_corpus('dev')       # 已读（ReadLog 记一次，去重）
    holdout = load_corpus(holdout_split)  # 唯一一次保留集读取
    isolation = assert_isolated(dev, holdout, holdout_split=holdout_split)
    # 跨保留集重叠：v2 不得复用 v1 的话术，否则"新保留集"是假的。
    if holdout_split != 'holdout':
        isolation['cross_holdout'] = assert_holdouts_disjoint(holdout, load_corpus('holdout'))
    # 泄漏**实测**：不靠声明。命中就写进 isolation，gate 会据此把提升降级为 reference_only。
    isolation['measured_contamination'] = audit_holdout_contamination(holdout_split)
    baseline = baseline_candidate()
    report = await run_suite(holdout, arms={'baseline': baseline, 'candidate': candidate},
                             repeats=repeats, budget=budget, suite=holdout_split)
    report.isolation = isolation

    dev_baseline = [r for r in (dev_report.runs if dev_report else []) if r.arm == 'baseline']
    dev_candidate = [r for r in (dev_report.runs if dev_report else []) if r.arm == 'candidate']
    hold_baseline = report.by_arm('baseline')
    hold_candidate = report.by_arm('candidate')
    gate = evaluate_gate(
        dev_baseline=dev_baseline, dev_candidate=dev_candidate,
        holdout_baseline=hold_baseline, holdout_candidate=hold_candidate,
        repeats=repeats, budget_snapshot=report.budget,
        voice_evidence=voice_evidence, cost_evidence=cost_evidence,
        baseline_arm=make_arm('baseline', baseline),
        candidate_arm=make_arm('candidate', candidate),
        holdout_split=holdout_split,
        holdout_provenance=HOLDOUT_PROVENANCE.get(holdout_split),
        contamination={'cross_holdout': isolation.get('cross_holdout') or {},
                       'measured': isolation.get('measured_contamination') or {}})
    return report, gate


def write_report(report: SuiteReport, path: Path, *, verbose: bool = True) -> Path:
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.as_dict(verbose=verbose), ensure_ascii=False, indent=2) + '\n',
                    encoding='utf-8')
    return path
