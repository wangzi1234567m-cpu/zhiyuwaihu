"""RSI 有界执行入口。

用法（在 mission 根）：
  .venv\\Scripts\\python.exe scripts/rsi_run.py --suite dev
  .venv\\Scripts\\python.exe scripts/rsi_run.py --suite dev --repeats 3 --max-requests 240
  .venv\\Scripts\\python.exe scripts/rsi_run.py --suite gate          # dev + holdout 一次性 gate
  .venv\\Scripts\\python.exe scripts/rsi_run.py --suite dev --candidate fault-inject
  .venv\\Scripts\\python.exe scripts/rsi_run.py --suite dev --candidate illegal

退出码：
  0 达标（所有 run 通过 且 gate 允许采纳）
  1 有失败断言（含 gate 拒绝）
  2 证据不充分（预算耗尽/缺语音或成本证据，不能自动采纳）
  3 非法候选被拒绝（符合预期的安全拒绝）
  4 参数/环境错误

安全：
- 不打印任何密钥；真实 GLM 未接线，`real_model_requests` 恒为 0，报告如实写明。
- 请求数/并发/超时都有上限；预算耗尽记 evidence insufficient，不重试。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from app.rsi import (BudgetExhausted, IllegalCandidateError, READ_LOG, RequestBudget,  # noqa: E402
                     adopt, baseline_candidate, diff_fields, evaluate_gate, load_corpus,
                     load_version_store, propose, record_rejection, reference_candidate,
                     rollback, run_dev_and_candidate, run_holdout_gate, save_version_store,
                     validate_candidate, write_report)
from app.rsi.candidates import Candidate, ExpressionRule  # noqa: E402
from app.rsi.gate import GateResult  # noqa: E402

DEFAULT_MAX_REQUESTS = 240
DEFAULT_CONCURRENCY = 2
DEFAULT_TIMEOUT = 30.0


def _fault_injection_candidate() -> Candidate:
    """人为注入缺陷：回复里承诺“保证通过”，用于证明断言能抓到并正确归因。"""
    cand = reference_candidate()
    bad = ExpressionRule('guarantee_refusal', '补齐材料后我们保证通过。')
    spec = cand.spec
    object.__setattr__(spec, 'guarantee_refusal', bad.text)
    from dataclasses import replace
    from app.rsi.candidates import validate_candidate as vc
    return vc(Candidate(spec=replace(spec, spec_id='prompt-candidate-fault-injected'),
                        rules=cand.rules + (bad,),
                        notes='故意注入：承诺必过，用于验证断言与 gate 拒绝通路'))


def _illegal_candidate_attempts() -> list[tuple[str, callable]]:
    """四种越权候选：审核结论 / 权限 / 官方知识 / 路由门槛 / 工具白名单。"""
    def review_conclusion():
        return ExpressionRule('review_conclusion', 'approved')

    def authorization():
        return ExpressionRule('authorized', 'true')

    def knowledge_text():
        return ExpressionRule('knowledge_text', '这是官方口径')

    def threshold():
        return ExpressionRule('threshold', '0.9')

    def allowlist():
        return ExpressionRule('tool_allowlist', 'reject_application')

    return [
        ('改审核结论', review_conclusion),
        ('改权限/授权', authorization),
        ('改官方知识正文', knowledge_text),
        ('改路由门槛', threshold),
        ('改工具白名单', allowlist),
    ]


def _propose_illegal_via_generator() -> None:
    """生成器侧的越权尝试：把候选 diff 里塞入受保护键。"""
    from dataclasses import replace
    cand = reference_candidate()
    spec = replace(cand.spec, spec_id='prompt-candidate-illegal')
    object.__setattr__(spec, 'route', 'jingmai')
    validate_candidate(Candidate(spec=spec, rules=()))


async def _report_dev(args) -> tuple[int, dict]:
    budget = RequestBudget(max_requests=args.max_requests, concurrency=args.concurrency,
                           timeout_s=args.timeout_s, max_real_requests=args.max_real_requests)
    try:
        if args.candidate == 'fault-inject':
            dev = load_corpus('dev')
            candidate = _fault_injection_candidate()
            from app.rsi.runner import run_suite
            from app.rsi.simulator import compare_with_production
            report = await run_suite(dev, arms={'baseline': baseline_candidate(),
                                                'candidate': candidate},
                                     repeats=args.repeats, budget=budget, suite='dev')
            report.isolation = {'generator_only_read_dev': True,
                               'holdout_read_during_generation': False,
                               'note': 'fault-inject 用人造候选演示断言与 gate 拒绝通路'}
            report.cross_check = compare_with_production(dev)
        elif args.candidate == 'illegal':
            # 非法候选必须被拒绝：逐一尝试，全部拒绝才算符合预期。
            rejected = []
            for label, factory in _illegal_candidate_attempts():
                try:
                    factory()
                except IllegalCandidateError as exc:
                    rejected.append({'label': label, 'rejected': True, 'error': str(exc)[:160]})
                else:
                    rejected.append({'label': label, 'rejected': False, 'error': '未被拒绝（缺陷）'})
            generator_rejected = False
            generator_error = ''
            try:
                _propose_illegal_via_generator()
            except IllegalCandidateError as exc:
                generator_rejected = True
                generator_error = str(exc)[:160]
            all_rejected = all(r['rejected'] for r in rejected) and generator_rejected
            payload = {
                'suite': 'illegal-candidate-check',
                'attempts': rejected,
                'generator_side': {'rejected': generator_rejected, 'error': generator_error},
                'all_rejected': all_rejected,
            }
            return (3 if all_rejected else 1), payload
        else:
            report, candidate = await run_dev_and_candidate(
                repeats=args.repeats, budget=budget,
                use_reference_candidate=(args.candidate == 'reference'))
    except IllegalCandidateError as exc:
        return 3, {'error': f'illegal_candidate: {exc}'}

    payload = report.as_dict(verbose=False)
    payload['candidate'] = {
        'spec_id': candidate.spec_id,
        'content_hash': candidate.content_hash(),
        'diff': diff_fields(candidate),
        'notes': candidate.notes,
    }
    payload['read_log_final'] = READ_LOG.as_dict()
    # 达标判定只对"候选臂"生效：baseline 是现行表达，它的失败正是候选要修的东西，
    # 如实记录为归因证据，不当作本轮交付失败（也不隐藏）。
    candidate_runs = report.by_arm('candidate')
    passed = bool(candidate_runs) and all(r.passed for r in candidate_runs)
    baseline_failed = [r.scenario_id for r in report.by_arm('baseline') if not r.passed]
    payload['baseline_failed_runs'] = baseline_failed
    payload['candidate_all_passed'] = passed
    exhausted = report.budget.get('exhausted') or report.budget.get('rejected_by_budget', 0) > 0
    if exhausted:
        payload['evidence_sufficient'] = False
        payload['evidence_note'] = '预算耗尽/请求被拒：评测不完整，记 evidence insufficient，不重试'
        code = 2
    else:
        code = 0 if passed else 1
        payload['evidence_sufficient'] = True
    return code, payload


async def _report_gate(args) -> tuple[int, dict]:
    budget = RequestBudget(max_requests=args.max_requests, concurrency=args.concurrency,
                           timeout_s=args.timeout_s, max_real_requests=args.max_real_requests)
    dev_report, candidate = await run_dev_and_candidate(
        repeats=args.repeats, budget=budget,
        use_reference_candidate=(args.candidate == 'reference'))
    holdout_report, gate = await run_holdout_gate(
        candidate=candidate, repeats=args.repeats, budget=budget, dev_report=dev_report,
        voice_evidence=None,   # 无真实语音样本：如实缺失
        cost_evidence=None)    # 无真实模型 TTFT/成本采样：如实缺失

    store_path = Path(args.store) if args.store else None
    store = load_version_store(store_path)
    payload = {
        'suite': 'gate',
        'dev': dev_report.as_dict(verbose=False),
        'holdout': holdout_report.as_dict(verbose=False),
        'gate': gate.as_dict(),
        'candidate': {'spec_id': candidate.spec_id, 'content_hash': candidate.content_hash(),
                      'diff': diff_fields(candidate)},
        'read_log_final': READ_LOG.as_dict(),
        'holdout_read_count': sum(1 for split, _ in READ_LOG.reads if split == 'holdout'),
    }
    # 采纳/拒绝/回滚都留版本证据；本次因缺语音/成本证据必然 refuse_auto_adopt。
    if gate.decision == 'adopt':
        try:
            store = adopt(gate, store=store, expected_active=store.get('active_version'),
                          path=store_path)
            payload['version_action'] = 'adopted'
        except (PermissionError, ValueError) as exc:
            payload['version_action'] = f'adopt_refused: {exc}'
    else:
        store = record_rejection(gate, store=store, path=store_path,
                                 note=f'gate={gate.decision}')
        payload['version_action'] = f'recorded_{gate.decision}'
    payload['version_store'] = {'active_version': store.get('active_version'),
                                'history_tail': store.get('history', [])[-3:]}
    passed = all(r.passed for r in dev_report.runs) and gate.decision == 'adopt'
    if gate.decision == 'refuse_auto_adopt':
        return 2, payload
    return (0 if passed else 1), payload


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _summary(payload: dict) -> str:
    if 'gate' in payload:
        gate = payload['gate']
        lines = [f"gate: {gate['decision']}  evidence_sufficient={gate['evidence_sufficient']}"]
        lines += [f"  reason: {r}" for r in gate['reasons']]
        m = gate['metrics']
        lines.append(f"  dev 完成率 baseline={m['dev_baseline_completion']} candidate={m['dev_candidate_completion']}")
        lines.append(f"  holdout 完成率 baseline={m['holdout_baseline_completion']} candidate={m['holdout_candidate_completion']} delta={m['holdout_delta_pp']}pp")
        lines.append(f"  硬安全 baseline={m['hard_safety_baseline']} candidate={m['hard_safety_candidate']}")
        lines.append(f"  可办理完成率={m['actionable_completion']} 预算={m['budget']['requests_used']}/{m['budget']['max_requests']}")
        return '\n'.join(lines)
    if 'all_rejected' in payload:
        lines = [f"illegal-candidate: all_rejected={payload['all_rejected']}"]
        lines += [f"  {a['label']}: rejected={a['rejected']}" for a in payload['attempts']]
        lines.append(f"  generator_side rejected={payload['generator_side']['rejected']}")
        return '\n'.join(lines)
    if 'arm_summary' in payload:
        lines = [f"suite={payload['suite']} scenarios={payload['scenarios']} repeats={payload['repeats']}"]
        for arm, s in payload['arm_summary'].items():
            a = payload['assertion_summary'][arm]
            lines.append(f"  {arm}: runs={s['runs']} passed={s['passed']} failed={s['failed']} "
                         f"completion={s['completion']} assertions={a['assertions_passed']}/{a['assertions_total']}")
        for arm, at in payload['attribution_summary'].items():
            lines.append(f"  attribution[{arm}]: by_layer={at['by_layer']} correct_handling={at['correct_handling_scenarios']}")
        b = payload['budget']
        lines.append(f"  budget: requests={b['requests_used']}/{b['max_requests']} "
                     f"real={b['real_model_requests']} timeouts={b['timeouts']} "
                     f"peak_concurrency={b['peak_concurrency']} exhausted={b['exhausted']}")
        if payload.get('cross_check', {}).get('mismatches'):
            lines.append(f"  routing_cross_check: {len(payload['cross_check']['mismatches'])} 处不一致")
        else:
            lines.append('  routing_cross_check: 与生产决策表一致')
        return '\n'.join(lines)
    return json.dumps(payload, ensure_ascii=False)[:800]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='RSI bounded runner（有界、可复现、无网络）')
    parser.add_argument('--suite', choices=['dev', 'gate'], default='dev')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--max-requests', type=int, default=DEFAULT_MAX_REQUESTS)
    parser.add_argument('--max-real-requests', type=int, default=0,
                        help='真实 GLM 请求上限；本仓库未接线真实模型，默认 0')
    parser.add_argument('--concurrency', type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument('--timeout-s', type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument('--candidate', default='proposed',
                        choices=['proposed', 'reference', 'fault-inject', 'illegal'])
    parser.add_argument('--store', default=None,
                        help='版本存储路径（默认 evidence/rsi/version-store.json，'
                             '不写入 backend 源码目录）')
    parser.add_argument('--out', default=None, help='结构化 JSON 输出路径')
    args = parser.parse_args(argv)

    if args.repeats < 1 or args.max_requests < 1:
        print('参数错误：--repeats 与 --max-requests 必须 >= 1', file=sys.stderr)
        return 4
    if args.store:
        args.store = str((ROOT / args.store) if not Path(args.store).is_absolute() else Path(args.store))
    else:
        # 默认把版本证据写进 mission 内 evidence/rsi/，不污染 backend 源码目录。
        args.store = str(ROOT / 'evidence' / 'rsi' / 'version-store.json')

    try:
        if args.suite == 'gate':
            code, payload = asyncio.run(_report_gate(args))
        else:
            code, payload = asyncio.run(_report_dev(args))
    except BudgetExhausted as exc:
        print(f'evidence insufficient: {exc}', file=sys.stderr)
        return 2
    except Exception as exc:  # 环境/参数级错误，不伪装成业务失败
        print(f'error: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 4

    default_name = 'rsi-gate.json' if args.suite == 'gate' else f'rsi-{args.suite}.json'
    if args.out:
        # 相对路径一律以 mission 根为基准解析，避免 CWD 不同导致跑到仓库外。
        out = Path(args.out)
        out = out if out.is_absolute() else (ROOT / out)
    else:
        out = ROOT / 'docs' / 'agent' / 'evidence' / default_name
    _write(out, payload)
    print(_summary(payload))
    try:
        shown = out.relative_to(ROOT)
    except ValueError:
        shown = out
    print(f'\nreport: {shown}')
    print(f'exit_code: {code}')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
