"""t1：否定作用域与对象作用域（最小算法改进）的回归测试。

背景（真实薄弱点，不是为凑分）：生产理解层 `detect_intents` 只问"触发词出现了没"，
不看**否定作用域**，于是把用户明确否认的高代价意图留在结果里：

    "我没投诉你们，只是问原因"      -> [ask_reason, complaint_risk]  （错：判成投诉风险 -> 转人工）
    "不用回电，你就说补什么材料"    -> [ask_remedy, request_callback]（错：被判成要求回电 -> CALLBACK_PENDING）
    "我不需要改期，直接告诉我原因"  -> [ask_reason, refuse_contact, request_callback]

本文件锁住三件事：
1) 否定作用域只收窄 `complaint_risk` / `request_callback` 这两个高代价意图；
2) 拒绝联系只在"否定的是具体对象（改期/回电…）"时收窄，真的说"别再打了"必须仍然 refused；
3) 正反问句（能不能/会不会/要不要）不得被当成否定——否则正常预约请求会被降级。
不涉及审核结论、权限、知识发布、路由门槛或工具白名单。
"""
from __future__ import annotations

import pytest

from app.semantic import (NEGATION_SCOPED_INTENTS, contact_outcome_hint, detect_intents,
                          parse_understanding, validate_understanding)


def test_negated_complaint_is_not_complaint_risk():
    """明确否认投诉時，不得留下 complaint_risk（否则会被强制转人工）。"""
    for text in ('我没投诉你们，只是问原因',
                 '我不是要投诉，我就是想知道为什么驳回',
                 '我不打算投诉，你告诉我原因就行'):
        intents = detect_intents(text)
        assert 'complaint_risk' not in intents, (text, intents)
        assert 'ask_reason' in intents, (text, intents)


def test_negated_callback_is_not_callback_request():
    """明确说不用回电时，不得留下 request_callback（否则会进 CALLBACK_PENDING）。"""
    for text in ('不用回电，你就说补什么材料',
                 '不需要回电，别再打了',
                 '没必要回电，直接说原因'):
        assert 'request_callback' not in detect_intents(text), text


def test_positive_complaint_and_callback_still_detected():
    """收窄只对否定作用域生效：肯定表达必须原样保留。"""
    assert 'complaint_risk' in detect_intents('我要投诉你们，已经找12315了')
    assert 'complaint_risk' in detect_intents('你们不给我处理，我要投诉')
    assert 'request_callback' in detect_intents('能不能回电跟我说一下')
    assert 'request_callback' in detect_intents('帮我改期到下周一')


def test_polar_question_is_not_negation():
    """正反问句（能不能/可否/会不会/要不要）含否定字但不是否定。"""
    assert 'request_callback' in detect_intents('可否回电')
    assert 'request_callback' in detect_intents('要不要改期')
    assert 'ask_guarantee' in detect_intents('会不会通过')


def test_negation_does_not_cross_clause_boundary():
    """否定作用域不跨句读：前一句的否定不能把后一句的投诉/回电也否定掉。"""
    text = '这个我不清楚，我要投诉'
    assert 'complaint_risk' in detect_intents(text), detect_intents(text)


def test_refusal_targeting_object_does_not_refuse_contact():
    """\"我不需要改期\"是否定改期这一对象，不是拒绝继续联系（不能写成 refused）。"""
    intents = detect_intents('我不需要改期，直接告诉我原因')
    assert 'refuse_contact' not in intents, intents
    assert contact_outcome_hint(intents) is None


def test_genuine_refusal_still_refused():
    """真正的拒绝联系必须保持 refused —— 上面那条收窄不得把它一起放掉。"""
    for text in ('我不需要，别再打了', '我们不需要，别再打了', '不需要，拒绝', '别联系我了'):
        intents = detect_intents(text)
        assert 'refuse_contact' in intents, (text, intents)
        assert contact_outcome_hint(intents) == 'refused', (text, intents)


def test_scope_list_is_minimal_and_protected_surfaces_untouched():
    """作用域只覆盖两个高代价意图；高风险身份/拒绝意图仍由各自规则负责。"""
    assert set(NEGATION_SCOPED_INTENTS) == {'complaint_risk', 'request_callback'}
    # 否认身份不得因新作用域被抹掉（它本身就是否定式语义，不能走"否定即摘除"）。
    assert 'deny_identity' in detect_intents('打错了，我不是这家')
    assert 'deny_identity' in detect_intents('我不是这家公司的')


def test_understanding_gate_keeps_scope_result():
    """服务端校验门不得把收窄后的结果又扩回去（模型/规则走同一道门）。"""
    understanding = parse_understanding('我没投诉你们，只是问原因')
    assert 'complaint_risk' not in understanding.intents
    assert understanding.contact_outcome_hint is None
    revalidated = validate_understanding(
        {'intents': ['ask_reason'], 'source': 'glm'}, text='我没投诉你们，只是问原因')
    assert 'complaint_risk' not in revalidated.intents


def test_model_claiming_negated_complaint_is_downgraded():
    """模型即便自述 complaint_risk，原文是否定作用域时也会被降级（不采信 claim）。"""
    payload = {'intents': ['complaint_risk', 'ask_reason'], 'source': 'glm'}
    understanding = validate_understanding(payload, text='我没投诉你们，只是问原因')
    assert 'complaint_risk' not in understanding.intents
    assert understanding.needs_clarification is False
