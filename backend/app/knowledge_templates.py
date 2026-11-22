"""Render reviewed knowledge exclusively from the server's review task."""
import re

from .contracts import KnowledgeEntry, ReviewCase

REQUIRED_SLOTS = {
    'demo.authorization_missing': ('company_name', 'brand_name', 'missing_items'),
    'demo.authorization_expired': ('company_name', 'brand_name'),
    'demo.authorization_chain': ('company_name', 'brand_name', 'authorization_from', 'authorization_to'),
}
SLOT_NAMES = {name for names in REQUIRED_SLOTS.values() for name in names} | {'shop_name', 'missing_document'}
TOKEN = re.compile(r'\{([a-z][a-z0-9_]*)\}|【([a-z][a-z0-9_]*)】')
UNFILLED = re.compile(r'[{}]|【\s*(?:[a-zA-Z_][\w ]*|待填[^】]*|请填写[^】]*|品牌名(?:称)?|公司名(?:称)?|缺失材料|授权方|被授权方|[＊*＿_…]*)\s*】|(?<![a-zA-Z])[xX]{2,}(?![a-zA-Z])')
EMPTY_CLASSIFICATION = re.compile(
    r'(?:类目|正确类似群)\s*[:：]\s*(?=(?:类目|正确类似群)\s*[:：]|[，,；;、]|$)'
)


def has_unfilled_text(text: str) -> bool:
    return bool(UNFILLED.search(text)) or any(
        EMPTY_CLASSIFICATION.search(body) for body in re.findall(r'【([^【】]*)】', text)
    )


def task_slot(slots: dict[str, str], name: str) -> str:
    value = slots.get(name, '').strip()
    return '' if TOKEN.search(value) or has_unfilled_text(value) else value


def check_template(entry: KnowledgeEntry):
    text = entry.approved_text or ''
    declared = set(entry.required_slots)
    tokens = {left or right for left, right in TOKEN.findall(text)}
    if (not text.strip() or not tokens <= declared or not declared <= SLOT_NAMES
            or has_unfilled_text(TOKEN.sub('实填值', text))):
        raise ValueError('Unresolved or undeclared template variable')


def render(entry: KnowledgeEntry, case: ReviewCase) -> KnowledgeEntry:
    check_template(entry)
    if entry.scenario_id != case.scenario_id:
        raise ValueError('Knowledge scenario does not match application')
    required = set(entry.required_slots).union(REQUIRED_SLOTS.get(case.scenario_id, ()))
    values = {name: task_slot(case.slots, name) for name in required}
    if not all(values.values()):
        raise ValueError('Missing or unresolved application facts')
    text = TOKEN.sub(lambda match: values[match[1] or match[2]], entry.approved_text)
    if has_unfilled_text(text):
        raise ValueError('Unresolved template after rendering')
    return entry.model_copy(update={'approved_text': text})


def available_entries(entries, case):
    """A partially usable set cannot authorize a different or less specific reply."""
    try:
        return [render(entry, case) for entry in entries]
    except ValueError:
        return []
