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


@pytest.mark.parametrize('text,intent', (
    ('我不想投诉，只想问原因', 'complaint_risk'),
    ('我不愿投诉，只想问原因', 'complaint_risk'),
    ('我不愿意投诉', 'complaint_risk'),
    ('我不想回电，直接告诉我原因', 'request_callback'),
    ('我没空再回电', 'request_callback'),
    ('我没时间再回电', 'request_callback'),
))
def test_modal_or_lack_of_time_negation_does_not_trigger_action(text, intent):
    assert intent not in detect_intents(text)


@pytest.mark.parametrize('text,intent', (
    ('我不想再解释要举报你们', 'complaint_risk'),
    ('我没空请回电', 'request_callback'),
    ('我不是不想投诉', 'complaint_risk'),
))
def test_unrelated_predicate_and_double_modal_negation_remain_affirmative(text, intent):
    assert intent in detect_intents(text)


@pytest.mark.parametrize('text,intent', (
    ('我并非不投诉', 'complaint_risk'),
    ('我并非不想投诉', 'complaint_risk'),
    ('我并非不愿投诉', 'complaint_risk'),
    ('我并非不愿意投诉', 'complaint_risk'),
    ('我绝非不想投诉', 'complaint_risk'),
    ('我并非不想回电', 'request_callback'),
    ('我绝非不需要回电', 'request_callback'),
))
def test_contrary_prefix_negates_unwillingness_before_action_checks(text, intent):
    assert intent in detect_intents(text)


@pytest.mark.parametrize('text,intent', (
    ('我并非要投诉，只想问原因', 'complaint_risk'),
    ('我绝非想回电，直接告诉我原因', 'request_callback'),
))
def test_single_contrary_prefix_still_negates_action(text, intent):
    assert intent not in detect_intents(text)
