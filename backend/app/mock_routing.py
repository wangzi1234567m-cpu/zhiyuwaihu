"""Read-only previews of the competition routing principles for the demo operator."""
import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

Channel = Literal['phone', 'jingmai']
Action = Literal['phone', 'jingmai', 'followup', 'manual']
DEFAULT_CATALOG = Path(__file__).resolve().parents[2] / 'private-evidence/pop-v1-clean.json'
DEMO_ROWS = {'demo.authorization_missing': 17, 'demo.authorization_expired': 21, 'demo.authorization_chain': 22, 'demo.no_brand': 24}


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
    action: Action
    reason: str


def catalog():
    path = Path(os.environ.get('MOCK_CLEAN_CATALOG', DEFAULT_CATALOG))
    if not path.exists() and 'MOCK_CLEAN_CATALOG' not in os.environ:
        examples = [(17, '授权书缺项', 'phone'), (21, '授权过期', 'phone'),
                    (22, '授权链不完整', 'phone'), (24, '无品牌申请', 'jingmai')]
        return Catalog(source='synthetic', scenarios=[Scenario(row=r, label=l, channel=c) for r,l,c in examples])
    data = json.loads(path.read_text(encoding='utf-8'))
    if data['version'] != 'pop-v1-clean-1':
        raise ValueError('Unsupported routing dataset')
    return Catalog(source='workbook', scenarios=[
        Scenario(row=s['source_ref']['row'], label=' / '.join(s['taxonomy']), channel=s['channel'],
                 missing_template=not s['written_template']) for s in data['scenarios']])


def evaluate(channel, submission, missed, callback, risk):
    from uuid import UUID
    from .contracts import ReviewCase
    from .guard import BRANCHES, BRANCH_RULES, route_branch

    case = ReviewCase(application_id=UUID(int=0), merchant_id=UUID(int=0), case_revision=1,
                      scenario_id='preview', review_conclusion='rejected', slots={},
                      submission_attempt=submission, call_attempt=missed, callback_requested=callback,
                      complaint_risk=risk)
    branch = route_branch(case, knowledge_channel=channel, contact_outcome='no_answer' if missed else 'unknown')
    action = 'followup' if branch == 'followup_second_submission' else BRANCH_RULES[branch][0]
    return Outcome(action=action, reason=BRANCHES[branch])
