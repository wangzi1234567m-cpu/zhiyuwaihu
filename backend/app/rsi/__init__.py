"""RSI（回归与策略迭代）最小完整实现。

边界（本包只读依赖，不改契约）：
- 只读 `backend/app/contracts.py`、`ports.py`、`adapters.py`、`guard.py`；
  不修改它们，也不改 coordinator/repository。
- 不连数据库：用进程内 ledger 记录“工具/业务事实”，断言只看状态机、守卫结果和
  ledger 事实，不看 LLM 措辞。
- 生成器只接受 dev 证据；holdout 只在 gate 阶段读取一次（`ReadLog` 留证）。
"""
from .corpus import (Scenario, load_corpus, assert_isolated, READ_LOG, CorpusError,
                     HoldoutLeakError, DEV_SIZE, HOLDOUT_SIZE, assert_holdouts_disjoint,
                     audit_holdout_contamination)
from .budget import RequestBudget, BudgetExhausted, RequestTimeout
from .understanding import (server_verify_identity, parse_understanding, PromptSpec,
                            BASELINE_PROMPT, CANDIDATE_PROMPT, validate_understanding_payload,
                            INTENT_RULES, audit_understanding_delegation, delegation_evidence,
                            identity_evidence, audit_identity_scaffold)
from .candidates import (Candidate, ExpressionRule, IllegalCandidateError, validate_candidate,
                         baseline_candidate, reference_candidate, propose, diff_fields,
                         EDITABLE_FIELDS, FORBIDDEN_FIELDS)
from .simulator import (Simulator, Arm, ScenarioRun, Ledger, SimulatedAgent, SimulatedBusiness,
                        compare_with_production, make_arm)
from .assertions import evaluate_assertions
from .attribution import attribute, summarize
from .gate import (evaluate_gate, GateResult, adopt, rollback, record_rejection,
                   load_version_store, save_version_store, GATE_VERSION, HARD_SAFETY_ASSERTIONS,
                   HOLDOUT_PROVENANCE, resolve_holdout_provenance)
from .runner import (run_dev_and_candidate, run_holdout_gate, run_suite, write_report, SuiteReport)

__all__ = [
    'Scenario', 'load_corpus', 'assert_isolated', 'READ_LOG', 'CorpusError', 'HoldoutLeakError',
    'DEV_SIZE', 'HOLDOUT_SIZE', 'assert_holdouts_disjoint', 'audit_holdout_contamination',
    'RequestBudget', 'BudgetExhausted', 'RequestTimeout',
    'server_verify_identity', 'parse_understanding', 'PromptSpec', 'INTENT_RULES',
    'BASELINE_PROMPT', 'CANDIDATE_PROMPT', 'validate_understanding_payload',
    'audit_understanding_delegation', 'delegation_evidence',
    'identity_evidence', 'audit_identity_scaffold',
    'Candidate', 'ExpressionRule', 'IllegalCandidateError', 'validate_candidate',
    'baseline_candidate', 'reference_candidate', 'propose', 'diff_fields',
    'EDITABLE_FIELDS', 'FORBIDDEN_FIELDS',
    'Simulator', 'Arm', 'ScenarioRun', 'Ledger', 'SimulatedAgent', 'SimulatedBusiness',
    'compare_with_production', 'make_arm',
    'evaluate_assertions', 'attribute', 'summarize',
    'evaluate_gate', 'GateResult', 'adopt', 'rollback', 'record_rejection',
    'load_version_store', 'save_version_store', 'GATE_VERSION', 'HARD_SAFETY_ASSERTIONS',
    'HOLDOUT_PROVENANCE', 'resolve_holdout_provenance',
    'run_dev_and_candidate', 'run_holdout_gate', 'run_suite', 'write_report', 'SuiteReport',
]
