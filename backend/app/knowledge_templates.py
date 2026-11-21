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


def check_template(entry: KnowledgeEntry):
    text = entry.approved_text or ''
    declared = set(entry.required_slots)
    tokens = {left or right for left, right in TOKEN.findall(text)}
    if (not text.strip() or not tokens <= declared or not declared <= SLOT_NAMES
            or UNFILLED.search(TOKEN.sub('', text))):
        raise ValueError('Unresolved or undeclared template variable')


def render(entry: KnowledgeEntry, case: ReviewCase) -> KnowledgeEntry:
    check_template(entry)
    if entry.scenario_id != case.scenario_id:
        raise ValueError('Knowledge scenario does not match application')
    required = set(entry.required_slots).union(REQUIRED_SLOTS.get(case.scenario_id, ()))
    values = {name: case.slots.get(name, '').strip() for name in required}
    if any(not value or UNFILLED.search(value) or TOKEN.search(value) for value in values.values()):
        raise ValueError('Missing or unresolved application facts')
    text = TOKEN.sub(lambda match: values[match[1] or match[2]], entry.approved_text)
    return entry.model_copy(update={'approved_text': text})


def available_entries(entries, case):
    """A partially usable set cannot authorize a different or less specific reply."""
    try:
        return [render(entry, case) for entry in entries]
    except ValueError:
        return []
