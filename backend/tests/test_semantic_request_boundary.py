"""宿主独立回归：无标点转写中的后续请求，不能被前面描述自身状态的否定抹掉。"""
import pytest
from app.semantic import detect_intents


@pytest.mark.parametrize('text', (
    '我没空看邮件请回个电话',
    '我没空看表请回电',
    '我没有时间看材料麻烦回电',
    '我不会操作请回个电话',
))
def test_new_request_after_negative_self_description_is_preserved(text):
    assert 'request_callback' in detect_intents(text)


@pytest.mark.parametrize('text', (
    '我没空你不用回电',
    '我没空看表请不要回电',
    '我没有空不需要回电',
    '没时间回电',
))
def test_closer_negative_request_is_not_erased(text):
    assert 'request_callback' not in detect_intents(text)


@pytest.mark.parametrize('text,intent', (
    ('我又不能投诉', 'complaint_risk'),
    ('我又不能回电', 'request_callback'),
    ('你不必回电', 'request_callback'),
    ('你不必改期', 'request_callback'),
))
def test_explicit_single_negation_is_not_affirmative(text, intent):
    assert intent not in detect_intents(text)
