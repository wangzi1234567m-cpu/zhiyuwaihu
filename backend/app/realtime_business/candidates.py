"""Validate native understanding locally; a candidate never supplies authority."""
from ..realtime_contract.dto import UnderstandingCandidate
from ..semantic import validate_understanding

# Only user-provided callback preferences can be suggested. Case facts stay server-owned.
ALLOWED_SLOTS = frozenset({'callback_time', 'callback_window', 'timezone'})


def grounded_candidate(candidate, text, *, case=None, context=None):
    value = UnderstandingCandidate.model_validate(candidate).model_dump()
    if any(k not in ALLOWED_SLOTS or not v.strip() or len(v) > 200 or v not in text
           for k, v in value['slots'].items()):
        raise ValueError('Candidate slots must be allowlisted and grounded in the final input')
    value['source'] = 'realtime_candidate'
    return validate_understanding(value, text=text, case=case, context=context)
