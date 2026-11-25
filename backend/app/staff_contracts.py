"""Staff projections and commands; separate from merchant disclosure contracts."""
from typing import Literal
from uuid import UUID
from pydantic import Field
from .contracts import DTO, RoutingDecision, SessionView, SourceRef, ContactOutcome, State, ContactRecorded


class StaffApplication(DTO):
    application_id: UUID
    label: str
    company_hint: str
    brand_name: str | None
    scene_name: str
    business_category: str
    scenario_id: str
    default_route: Literal['phone','jingmai','manual']
    effective_route: Literal['phone','jingmai','manual']
    state: State
    case_revision: int
    submission_attempt: int
    call_attempt: int
    contact_outcome: ContactOutcome
    contact_event_id: UUID | None
    reason: str | None
    remedy: str | None
    routing_decision: RoutingDecision
    knowledge_version: str
    rule_version: str
    source_refs: list[SourceRef]
    snapshot: SessionView | None
    available_actions: list[str]


class WrittenPreview(DTO):
    session_id: UUID
    case_revision: int
    state_revision: int
    knowledge_version: str
    rule_version: str
    written_text: str | None = None
    knowledge_entry_id: UUID | None = None
    source_refs: list[SourceRef] = Field(default_factory=list)
    template_version: str | None = None
    missing_slots: list[str] = Field(default_factory=list)
    body_sha256: str | None = None
    preview_id: str | None = None
    previewable: bool = False
    executable: bool = False
    unavailable_reasons: list[str] = Field(default_factory=list)


class ExecuteWritten(DTO):
    case_revision: int = Field(ge=1)
    expected_revision: int = Field(ge=0)
    knowledge_version: str
    rule_version: str
    body_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    preview_id: str = Field(pattern=r'^[a-f0-9]{64}$')


class SyntheticContact(DTO):
    event_id: UUID
    case_revision: int = Field(ge=1)
    expected_call_attempt: int = Field(ge=0)
    outcome: Literal['connected','no_answer','wrong_person','refused','disconnected','unknown']


class SceneSupport(DTO):
    scenario_id: str
    published: bool = False
    phone: bool
    written: bool
    unavailable_reasons: list[str]


class SupportCatalog(DTO):
    original_total: int = 68
    original_phone: int = 54
    original_jingmai: int = 14
    knowledge_version: str
    published_scenarios: int
    approved_entry_count: int = 0
    quarantined_entry_count: int = 0
    renderable_tasks: int = 0
    phone_supported: int
    written_supported: int
    scenes: list[SceneSupport]
    scope: Literal['synthetic_tasks_only'] = 'synthetic_tasks_only'


PUBLIC_STAFF_MODELS=[StaffApplication,WrittenPreview,ExecuteWritten,SyntheticContact,ContactRecorded,SceneSupport,SupportCatalog]
