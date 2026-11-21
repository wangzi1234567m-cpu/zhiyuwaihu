"""t1（续）：用**真实 Agent simulator** 复核否定作用域的端到端行为。

上一步 `test_semantic_negation_scope.py` 只锁理解层输出；但理解层变对不等于主链变对。
这里驱动与 RSI 完全相同的 `Simulator`（Agent -> Policy -> Guard -> Tool，复用生产
`guard.py`、服务端身份核验、Ledger 业务事实），检查终态与业务效果：

    "我没投诉你们，只是问原因"   -> 不得转人工（旧行为 MANUAL_REVIEW），应能正常走到派发
    "不用回电，你就说补什么材料" -> 不得进 CALLBACK_PENDING（旧行为），应答整改问题
    "我要投诉你们…"             -> 仍须转人工（收窄不能把真投诉也放掉）
    "我们不需要，别再打了"       -> 仍须拒绝结束（收窄不能把真拒绝也放掉）

场景在**本文件内**构造，不改 `dev.json`（dev 被 `DEV_SIZE=24` 冻结、且是历史归因基线，
往里塞题等于篡改评测集）。所有断言只读 Ledger/状态轨迹，不拿模型措辞判成功。
"""
from __future__ import annotations

import asyncio

import pytest

from app.rsi.candidates import baseline_candidate, reference_candidate
from app.rsi.corpus import Expected, Scenario, Turn, _build_knowledge, _det_uuid
from app.rsi.simulator import Simulator, make_arm
from app.contracts import ReviewCase

COMPANY = '合成商家甲'
REASON = '品牌授权书缺页'
REMEDY = '补充完整品牌授权书并加盖公章'


def _scenario(turns, *, terminal_states, tool_effect) -> Scenario:
    case = ReviewCase(
        application_id=_det_uuid('app', 't1-negation'),
        merchant_id=_det_uuid('merchant', 't1-negation'),
        case_revision=1, scenario_id='scn.t1.negation', review_conclusion='rejected',
        submission_attempt=1, call_attempt=0, callback_requested=False, complaint_risk=False,
        slots={'company_name': COMPANY, 'reason': REASON, 'remedy': REMEDY})
    return Scenario(
        scenario_id='probe.t1.negation', family='unclear_request',
        expression_group='dev.probe.negation', split='dev', channel='phone',
        last_contact_outcome='none', system_flow=False, authorized=True, rule_valid=True,
        case=case,
        knowledge=_build_knowledge('scn.t1.negation', 'approved', case, 'phone'),
        knowledge_mode='approved', identity_mode='name_match',
        expected=Expected(
            route='phone', terminal_states=tuple(terminal_states), tool_effect=tool_effect,
            expected_action='reject_application' if tool_effect == 'one' else 'none',
            identity_required=True, must_not_leak_before_verify=True,
            forbid_reply_claims=('保证通过', '已通过审核'),
            require_reply_terms=(REASON, REMEDY) if tool_effect == 'one' else (),
            completion_relevant=True),
        turns=tuple(Turn(text=t, intent=i, identity_claim=False) for t, i in turns),
        fault=None, repeat_sessions=1, notes='t1 否定作用域探针（合成场景，非评测集）')


def _run(scenario, arm_name):
    arm = make_arm(arm_name, baseline_candidate() if arm_name == 'baseline'
                   else reference_candidate())
    return asyncio.run(Simulator().run(scenario, arm, 1))


@pytest.mark.parametrize('arm_name', ['baseline', 'candidate'])
def test_negated_complaint_does_not_escalate(arm_name):
    """明确否认投诉：不得因为"投诉"二字就转人工，主链应正常派发一次驳回。"""
    scenario = _scenario(
        (('我是合成商家甲的市场负责人', 'confirm_identity'),
         ('我没投诉你们，只是问原因', 'ask_reason'),
         ('明白了，按这个处理吧', 'acknowledge')),
        terminal_states=('CLOSED',), tool_effect='one')
    run = _run(scenario, arm_name)
    assert run.error is None, run.error
    assert 'MANUAL_REVIEW' not in run.states, run.states
    assert run.final_state == 'CLOSED', run.states
    assert run.ledger.effect_count() == 1, run.ledger.as_dict()


@pytest.mark.parametrize('arm_name', ['baseline', 'candidate'])
def test_negated_callback_does_not_enter_callback_pending(arm_name):
    """"不用回电"不得被当成要求回电，因此不得进入 CALLBACK_PENDING。"""
    scenario = _scenario(
        (('我是合成商家甲的市场负责人', 'confirm_identity'),
         ('不用回电，你就说补什么材料', 'ask_remedy'),
         ('明白了，按这个处理吧', 'acknowledge')),
        terminal_states=('CLOSED',), tool_effect='one')
    run = _run(scenario, arm_name)
    assert run.error is None, run.error
    assert 'CALLBACK_PENDING' not in run.states, run.states
    assert run.final_state == 'CLOSED', run.states


@pytest.mark.parametrize('arm_name', ['baseline', 'candidate'])
def test_genuine_complaint_still_escalates(arm_name):
    """收窄只作用于否定作用域：真投诉必须仍然转人工、且不得自动派发驳回。"""
    scenario = _scenario(
        (('我是合成商家甲的市场负责人', 'confirm_identity'),
         ('我要投诉你们，已经找12315了', 'complaint_risk')),
        terminal_states=('MANUAL_REVIEW',), tool_effect='none')
    run = _run(scenario, arm_name)
    assert run.error is None, run.error
    assert run.final_state == 'MANUAL_REVIEW', run.states
    assert run.ledger.effect_count() == 0, run.ledger.as_dict()


@pytest.mark.parametrize('arm_name', ['baseline', 'candidate'])
def test_genuine_refusal_still_stops(arm_name):
    """真拒绝联系必须仍然结束且不派发业务动作（CLOSING 后正常收口到 CLOSED）。"""
    scenario = _scenario(
        (('我是合成商家甲的市场负责人', 'confirm_identity'),
         ('我们不需要，别再打了', 'refuse_contact')),
        terminal_states=('CLOSED',), tool_effect='none')
    run = _run(scenario, arm_name)
    assert run.error is None, run.error
    assert 'CLOSING' in run.states, run.states
    assert run.final_state == 'CLOSED', run.states
    assert run.ledger.effect_count() == 0, run.ledger.as_dict()
