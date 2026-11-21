"""Read-only previews of the competition routing principles for the demo operator."""
import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

Channel = Literal['phone', 'jingmai']
Action = Literal['phone', 'jingmai', 'followup', 'manual']
DEFAULT_CATALOG = Path(__file__).resolve().parents[2] / 'private-evidence/rework-v2/clean.json'


class Scenario(BaseModel):
    row: int
    label: str
    channel: Channel
    missing_template: bool = False


class Catalog(BaseModel):
    source: Literal['workbook', 'synthetic']
    scenarios: list[Scenario]


class Conditions(BaseModel):
    row: int
    submission: int = Field(ge=1)
    missed: int = Field(ge=0, le=2)
    callback: bool = False
    risk: bool = False


class Outcome(BaseModel):
    action: Action | None
    reason: str


def catalog():
    path = Path(os.environ.get('MOCK_CLEAN_CATALOG', DEFAULT_CATALOG))
    if not path.exists() and 'MOCK_CLEAN_CATALOG' not in os.environ:
        examples = [(17, '授权书缺项', 'phone'), (21, '授权过期', 'phone'),
                    (22, '授权链不完整', 'phone'), (24, '无品牌申请', 'jingmai')]
        return Catalog(source='synthetic', scenarios=[Scenario(row=r, label=l, channel=c) for r,l,c in examples])
    data = json.loads(path.read_text(encoding='utf-8'))
    if data['version'] != 'pop-v1-clean-2':
        raise ValueError('Unsupported routing dataset')
    return Catalog(source='workbook', scenarios=[
        Scenario(row=s['source_ref']['row'], label=' / '.join(s['taxonomy']), channel=s['channel'],
                 missing_template=not s['written_template']) for s in data['scenarios']])


def evaluate(policy, scenario, conditions):
    """Show the injected policy's decision only when its preview interface is available."""
    from uuid import UUID
    from .contracts import ReviewCase

    decide = getattr(policy, 'decide', None)
    if not callable(decide):
        return Outcome(action=None, reason='当前主干仅提供基础渠道，联系策略待接入。')
    case = ReviewCase(application_id=UUID(int=0), merchant_id=UUID(int=0), case_revision=1,
                      scenario_id='preview', review_conclusion='rejected', slots={},
                      submission_attempt=conditions.submission, call_attempt=conditions.missed,
                      callback_requested=conditions.callback, complaint_risk=conditions.risk)
    decision = decide(case, knowledge_channel=scenario.channel,
                      contact_outcome='no_answer' if conditions.missed else 'unknown',
                      contact_outcome_source='domain_event' if conditions.missed else 'none')
    action = 'followup' if 'no_answer_second_submission_followup' in decision.reason_codes else decision.route
    return Outcome(action=action, reason='；'.join(item.detail for item in decision.evidence))
