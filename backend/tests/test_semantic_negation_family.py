"""t1：否定作用域的**表达家族**回归（含两个已复现漏识别用例）。

已知薄弱点（真实复现，不是为凑分）：`detect_intents` 的否定作用域只看"触发点前 6 个字里有没有
否定词"，于是把**否定作用在自己身上**的短语当成了对后文动作的否定：

    '我不能等了要投诉'        -> ['unclear']   （漏：真投诉被抹掉）
    '我没空请回电'            -> ['unclear']   （漏：真回电请求被抹掉）

修复方向（只动 semantic.py 的语法/对象作用域）：把"否定 + 自身对象/状态"（没空/没有空/
不会操作/不能等）识别为**封闭短语**，它只否定自己的对象；一旦后文出现新的请/帮/要/就等
谓述标记，否定就不再延伸到后面的投诉或回电。同时补上紧邻否定（`不投诉`）与正反问
（`是不是/有没有/是否`），避免新作用域把真否定和正反问一起破掉。

断言只读理解层输出（intents / contact_outcome_hint / multi_intent / needs_clarification），
不涉及审核结论、权限、知识发布、路由阈值或工具白名单。
"""
from __future__ import annotations

import pytest

from app.semantic import contact_outcome_hint, detect_intents, parse_understanding

#: 两个已复现的漏识别用例：(原文, 必须保留的意图, 必须缺席的意图)
REPRODUCED_MISSES = (
    ('我不能等了要投诉', 'complaint_risk', 'unclear'),
    ('我没空请回电', 'request_callback', 'unclear'),
)


@pytest.mark.parametrize('text,keep,absent', REPRODUCED_MISSES)
def test_reproduced_misses_now_detected(text, keep, absent):
    """两个漏识别用例现在必须识别出真实诉求，而不是降级成 unclear。"""
    intents = detect_intents(text)
    assert keep in intents, (text, intents)
    assert absent not in intents, (text, intents)


#: 同家族：被否定的是**说话人自己的**空闲/能力/时间，不是后文的动作。
OBJECT_NEGATION_FAMILY = (
    ('我没空请回电', 'request_callback'),
    ('没有空，麻烦你回电', 'request_callback'),
    ('我没空，你们帮我投诉', 'complaint_risk'),
    ('不会操作，帮我约个时间', 'request_callback'),
    ('我不会用电脑，麻烦回电', 'request_callback'),
    ('我不能等，你回电给我', 'request_callback'),
    ('我不能等了，我要投诉并且要求回电', 'complaint_risk'),
    ('没时间了我要投诉', 'complaint_risk'),
)


@pytest.mark.parametrize('text,expected', OBJECT_NEGATION_FAMILY)
def test_object_scoped_negation_keeps_positive_request(text, expected):
    """"没空/没有空/不会操作/不能等"只否定自身对象，不得吞掉后文的投诉或回电。"""
    intents = detect_intents(text)
    assert expected in intents, (text, intents)
    assert intents != ['unclear'], (text, intents)


def test_object_negation_family_keeps_multi_intent():
    """同一句里投诉与回电并存时两个都要保留（多意图不能被压成一个）。"""
    intents = detect_intents('我不能等了，我要投诉并且要求回电')
    assert 'complaint_risk' in intents, intents
    assert 'request_callback' in intents, intents
    understanding = parse_understanding('我不能等了，我要投诉并且要求回电')
    assert understanding.multi_intent is True, understanding.intents
    assert understanding.needs_clarification is False, understanding.intents


#: 真否定反例：否定直接作用在投诉/回电上，必须继续被摘掉（这次修改不能破掉它们）。
GENUINE_NEGATION = (
    ('我没投诉你们，只是问原因', 'complaint_risk'),
    ('我不投诉，只要补什么材料', 'complaint_risk'),
    ('我没有投诉，只是想问原因', 'complaint_risk'),
    ('不是要投诉，就想问问凭什么', 'complaint_risk'),
    ('不用回电，你就说补什么材料', 'request_callback'),
    ('我不需要回电，就说说怎么整改', 'request_callback'),
    ('别回电了，说原因就行', 'request_callback'),
    ('我不用回电了，直接说原因', 'request_callback'),
)


@pytest.mark.parametrize('text,absent', GENUINE_NEGATION)
def test_genuine_negation_still_suppressed(text, absent):
    """真否定必须继续生效：不得因为新加了对象作用域就退化成"有否定词也照判"。"""
    intents = detect_intents(text)
    assert absent not in intents, (text, intents)


def test_object_negation_does_not_open_genuine_refusal():
    """`我没空你不用回电`：后一个否定换了主语，不能因为前面有对象否定就当成放开。"""
    assert 'request_callback' not in detect_intents('我没空你不用回电')
    # 但拒接联系仍要有真实依据，不能凭对象否定就判 refused。
    assert contact_outcome_hint(detect_intents('我没空你不用回电')) is None


#: 正反问句：句中有否定字，语义是询问，不是否定。不能被作用域当成"否定"摘掉。
POLAR_QUESTIONS = (
    ('能不能回电跟我说一下', 'request_callback'),
    ('可否回电', 'request_callback'),
    ('你们是不是要给我回电', 'request_callback'),
    ('是否回电', 'request_callback'),
    ('是不是我投诉了你们才处理', 'complaint_risk'),
    ('会不会通过', 'ask_guarantee'),
)


@pytest.mark.parametrize('text,expected', POLAR_QUESTIONS)
def test_polar_question_is_not_treated_as_negation(text, expected):
    """正反问（能不能/是不是/有没有/是否）里含否定字，但不是否定。"""
    intents = detect_intents(text)
    assert expected in intents, (text, intents)


def test_negation_scope_stops_at_clause_boundary():
    """前一分句的否定不得跨句读作用到后一分句的真实投诉/回电。"""
    for text, expected in (('这个我不清楚，我要投诉', 'complaint_risk'),
                           ('我没空，请回电给我', 'request_callback'),
                           ('不会操作，你们帮我投诉', 'complaint_risk')):
        intents = detect_intents(text)
        assert expected in intents, (text, intents)


def test_genuine_refusal_and_denial_still_intact():
    """真拒绝联系与否认身份不在这条作用域链上，必须保持原判。"""
    refused = detect_intents('我们不需要，别再打了')
    assert 'refuse_contact' in refused and contact_outcome_hint(refused) == 'refused'
    assert contact_outcome_hint(detect_intents('打错了，我不是这家')) == 'wrong_person'
    # \"我不需要改期\"只否定对象，不构成拒接联系。
    assert contact_outcome_hint(detect_intents('我不需要改期，直接告诉我原因')) is None


#: 双重否定 / 被迫式：含两个否定字或"只能/只好"，语义是**肯定**。
DOUBLE_NEGATION = (
    '我不得不投诉了',
    '我不能不投诉了',
    '我不可能不投诉',
    '没办法只能投诉',
    '只好投诉了',
)


def test_double_negation_is_affirmative_not_denial():
    """双重否定/被迫式必须判成真投诉；不能被"紧邻否定"规则当成否认投诉。"""
    for text in DOUBLE_NEGATION:
        intents = detect_intents(text)
        assert 'complaint_risk' in intents, (text, intents)
        assert intents != ['unclear'], (text, intents)


def test_forced_marker_cut_does_not_leak_earlier_negation():
    """`只能/只好/必须` 之前的内容不参与否定判定；标记后的真投诉必须保留。"""
    from app.semantic import _apply_forced_cut
    assert _apply_forced_cut('没办法只能') == ''
    assert _apply_forced_cut('不想拖只好') == ''
    assert _apply_forced_cut('我没有') == '我没有'
    # 空前缀与无标记前缀都不能被误截成非空/空。
    assert _apply_forced_cut('') == ''


def test_understanding_gate_keeps_family_results():
    """服务端校验门（模型与规则共用）不得把家族结果又扩回去或收回去。"""
    for text, keep in (('我不能等了要投诉', 'complaint_risk'),
                       ('我没空请回电', 'request_callback')):
        understanding = parse_understanding(text)
        assert keep in understanding.intents, (text, understanding.intents)
        assert understanding.needs_clarification is False, (text, understanding.intents)
    negated = parse_understanding('不用回电，你就说补什么材料')
    assert 'request_callback' not in negated.intents, negated.intents
    assert negated.contact_outcome_hint is None, negated.intents
