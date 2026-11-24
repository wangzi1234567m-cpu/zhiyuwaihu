"""Construction validates shape, never authentication or business authorization."""
import hashlib
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, ConfigDict, Field, model_validator

from ..contracts import (AcceptedTurn, DTO, GenerationFinished, Interrupted,
                         OperationResult, OperationUpdated, State, TurnInterrupted, TurnStarted)

CONTRACT_VERSION = '0.4.0'
ContractVersion = Literal['0.4.0']
Intent = Literal['confirm_identity', 'deny_identity', 'ask_reason', 'ask_remedy',
                 'ask_guarantee', 'ask_source', 'ask_outcome', 'refuse_contact',
                 'request_callback', 'acknowledge', 'unclear', 'complaint_risk']


class TrustedSessionContext(DTO):
    """Server-only argument; never bind to a browser or model request body."""
    model_config = ConfigDict(extra='forbid', frozen=True)
    contract_version: ContractVersion = CONTRACT_VERSION
    owner: str = Field(min_length=1, max_length=200)
    session_id: UUID
    connection_binding: UUID


class TrustedTurnContext(TrustedSessionContext):
    request_id: UUID
    provider_input_id: str = Field(min_length=1, max_length=200)
    expected_state_revision: int = Field(ge=0)
    expected_case_revision: int = Field(ge=1)
    knowledge_version: str = Field(min_length=1, max_length=100)


class UnderstandingCandidate(DTO):
    """Untrusted suggestions; grounding and state checks are still required."""
    intents: list[Intent] = Field(min_length=1, max_length=12)
    slots: dict[str, str] = Field(default_factory=dict, max_length=16)
    needs_clarification: bool = False
    negated: bool = False


class BusinessTurnInput(DTO):
    input_id: UUID
    text: str = Field(min_length=1, max_length=8000, pattern=r'\S')
    final: Literal[True] = True
    understanding_candidate: UnderstandingCandidate | None = None


class EventSubscription(DTO):
    after_seq: int = Field(default=0, ge=0)


class GenerationInterrupt(DTO):
    request_id: UUID
    generation_id: UUID


class ReplySegmentReady(DTO):
    type: Literal['reply.segment_ready'] = 'reply.segment_ready'
    message_id: UUID
    segment_id: UUID
    segment_index: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=2000, pattern=r'\S')
    text_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    speech_policy: Literal['verbatim'] = 'verbatim'

    @model_validator(mode='after')
    def digest_matches_text(self):
        if hashlib.sha256(self.text.encode('utf-8')).hexdigest() != self.text_sha256:
            raise ValueError('text_sha256 must match exact UTF-8 text')
        return self


class ReplyCompleted(DTO):
    type: Literal['reply.completed'] = 'reply.completed'
    message_id: UUID
    segment_count: int = Field(ge=0)


ErrorCode = Literal[
    'UNAUTHORIZED', 'SESSION_NOT_FOUND', 'BINDING_EXPIRED',
    'CONTRACT_VERSION_UNSUPPORTED', 'INVALID_INPUT', 'INPUT_NOT_FINAL',
    'REQUEST_CONFLICT', 'REVISION_CONFLICT', 'STALE_GENERATION',
    'CURSOR_EXPIRED', 'CURSOR_AHEAD', 'SUBSCRIBER_LIMIT', 'SLOW_CONSUMER',
    'BUSY', 'BUSINESS_TIMEOUT', 'OPERATION_UNKNOWN', 'SERVICE_UNAVAILABLE',
    'PLAYBACK_EVIDENCE_REJECTED', 'INTERNAL_ERROR',
]


class ContractError(DTO):
    code: ErrorCode
    message: str = Field(min_length=1, max_length=400)
    retryable: bool
    request_id: UUID | None = None
    current_state_revision: int | None = Field(default=None, ge=0)
    retry_after_ms: int | None = Field(default=None, ge=0, le=60000)


class TurnFailed(DTO):
    type: Literal['turn.failed'] = 'turn.failed'
    error: ContractError


class WritebackCompleted(DTO):
    type: Literal['writeback.completed'] = 'writeback.completed'
    record_id: UUID
    record_revision: int = Field(ge=1)


BusinessPayload = Annotated[
    TurnStarted | TurnInterrupted | GenerationFinished | OperationUpdated |
    ReplySegmentReady | ReplyCompleted | TurnFailed | WritebackCompleted,
    Field(discriminator='type'),
]


class RealtimeBusinessEvent(DTO):
    contract_version: ContractVersion = CONTRACT_VERSION
    event_id: UUID
    session_id: UUID
    turn_id: UUID
    generation_id: UUID
    operation_id: UUID | None = None
    seq: int = Field(ge=1)
    state_revision: int = Field(ge=0)
    occurred_at: AwareDatetime
    knowledge_version: str = Field(min_length=1, max_length=100)
    payload: BusinessPayload

    @model_validator(mode='after')
    def operation_binding_matches(self):
        if isinstance(self.payload, OperationUpdated):
            if self.operation_id != self.payload.result.operation_id:
                raise ValueError('operation event must bind result.operation_id')
        return self


class PlaybackReport(DTO):
    report_id: UUID
    generation_id: UUID
    message_id: UUID
    segment_id: UUID
    playback_instance_id: UUID
    text_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    observation: Literal['started', 'completed', 'interrupted', 'failed']
    clock_domain: Literal['browser.performance', 'server.monotonic']
    elapsed_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    # Reference to a SERVER-VERIFIED record, never a client proof assertion.
    evidence_ref: UUID | None = None


class PlaybackReportResult(DTO):
    report_id: UUID
    status: Literal['observation_recorded', 'evidence_verified', 'evidence_rejected']
    reason_code: Literal[
        'OBSERVATION_ONLY', 'SERVER_PROOF_MATCHED', 'PROOF_NOT_FOUND',
        'PROOF_BINDING_MISMATCH', 'INCOMPLETE_PLAYBACK', 'STALE_GENERATION',
    ]

    @model_validator(mode='after')
    def status_matches_reason(self):
        expected = ('evidence_verified' if self.reason_code == 'SERVER_PROOF_MATCHED'
                    else 'observation_recorded' if self.reason_code == 'OBSERVATION_ONLY'
                    else 'evidence_rejected')
        if self.status != expected:
            raise ValueError('playback status and reason_code disagree')
        return self


class PlaybackProgress(DTO):
    generation_id: UUID
    message_id: UUID
    verified_segment_ids: list[UUID] = Field(default_factory=list)
    message_fully_notified: bool = False


class BusinessSnapshot(DTO):
    contract_version: ContractVersion = CONTRACT_VERSION
    session_id: UUID
    state: State
    state_revision: int = Field(ge=0)
    case_revision: int = Field(ge=1)
    knowledge_version: str = Field(min_length=1, max_length=100)
    generation_id: UUID | None = None
    last_event_seq: int = Field(ge=0)
    oldest_available_seq: int = Field(ge=1)
    operations: list[OperationResult] = Field(default_factory=list)
    playback: PlaybackProgress | None = None

    @model_validator(mode='after')
    def cursor_range_is_valid(self):
        if self.oldest_available_seq > self.last_event_seq + 1:
            raise ValueError('invalid retained event range')
        if self.playback and self.playback.generation_id != self.generation_id:
            raise ValueError('snapshot playback must belong to current generation')
        return self


PUBLIC_MODELS = [TrustedSessionContext, TrustedTurnContext, UnderstandingCandidate,
                 BusinessTurnInput, AcceptedTurn, EventSubscription, GenerationInterrupt,
                 Interrupted, RealtimeBusinessEvent, ContractError, PlaybackReport,
                 PlaybackReportResult, BusinessSnapshot]
