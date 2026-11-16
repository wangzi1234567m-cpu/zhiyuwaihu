"""智语外呼 0.1.0 公共契约样例；仅数据结构，不实现服务或权限检查。"""
from __future__ import annotations
from typing import Annotated, Literal
from uuid import UUID
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

VERSION = "0.1.0"
NonEmpty = Annotated[str, Field(min_length=1, max_length=200)]
BusinessState = Literal["OPENING", "VERIFYING", "DISCOVERING", "QUOTING", "AWAITING_CONFIRMATION", "SUBMITTING", "RECONCILING", "RESULT_READY", "MANUAL_REVIEW", "WRITEBACK_PENDING", "CLOSED"]
CallStatus = Literal["created", "connecting", "active", "reconnecting", "ending", "ended", "failed"]
OperationStatus = Literal["reserved", "dispatching", "succeeded", "failed", "unknown", "reconciling", "manual_review", "cancelled"]

class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")

class ProviderTurnRef(Contract):
    binding_id: UUID
    provider_turn_id: NonEmpty

class Needs(Contract):
    desired_plan_id: str | None = None
    max_monthly_price_minor: int | None = Field(default=None, ge=0)
    preference: str | None = Field(default=None, max_length=500)

class IdentityResult(Contract):
    operation_id: UUID
    status: Literal["verified", "mismatch", "locked", "unavailable"]
    verified_subject_ref: str | None = None
    remaining_attempts: int = Field(ge=0, le=2)
    expires_at: AwareDatetime | None = None
    error_code: str | None = None

class WritebackResult(Contract):
    operation_id: UUID
    record_revision: int = Field(ge=1)
    status: Literal["succeeded", "failed", "unknown"]
    provider_reference: str | None = None
    error_code: str | None = None
    retryable: bool
    as_of: AwareDatetime

class Quote(Contract):
    quote_id: UUID
    revision: int = Field(ge=1)
    plan_id: NonEmpty
    plan_name: NonEmpty
    price_minor: int = Field(ge=0)
    currency: Literal["CNY"] = "CNY"
    billing_period: Literal["month"] = "month"
    effective_at: AwareDatetime
    contract_months: int = Field(ge=0)
    one_time_fee_minor: int = Field(ge=0)
    terms_version: NonEmpty
    quote_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    eligibility: bool
    valid_until: AwareDatetime

class OperationView(Contract):
    operation_id: UUID
    session_id: UUID
    kind: Literal["verify_identity", "query_plans", "submit_change", "writeback"]
    status: OperationStatus
    quote_id: UUID | None = None
    order_id: UUID | None = None
    provider_status: str | None = None
    error_code: str | None = None
    retryable: bool = False
    updated_at: AwareDatetime

class SessionSnapshot(Contract):
    session_id: UUID
    mode: Literal["voice", "text"]
    scenario: Literal["mock_package_change"] = "mock_package_change"
    call_status: CallStatus
    business_state: BusinessState
    state_revision: int = Field(ge=0)
    generation_epoch: int = Field(ge=0)
    current_turn_id: UUID | None = None
    current_generation_id: UUID | None = None
    strategy_version_id: NonEmpty
    rule_version: NonEmpty
    data_version: NonEmpty
    model_config_version: NonEmpty
    identity_status: Literal["unverified", "verified", "failed", "expired"] = "unverified"
    disclosure_status: Literal["pending", "complete", "interrupted", "unknown"] = "pending"
    active_quote: Quote | None = None
    submit_operation: OperationView | None = None
    writeback_status: Literal["not_started", "pending", "succeeded", "failed"] = "not_started"
    outcome: str | None = None
    last_event_seq: int = Field(ge=0)
    trace_id: NonEmpty

class CreateSessionRequest(Contract):
    scenario: Literal["mock_package_change"] = "mock_package_change"
    mode: Literal["voice", "text"] = "voice"
    fixture_id: NonEmpty = "success"

class MediaCredentials(Contract):
    app_id: NonEmpty
    channel: NonEmpty
    browser_uid: NonEmpty
    agent_uid: NonEmpty
    rtc_token: str = Field(min_length=1)
    rtm_token: str = Field(min_length=1)
    expires_at: AwareDatetime

class CreateSessionResponse(Contract):
    session: SessionSnapshot
    media: MediaCredentials | None = None

class RevisionRequest(Contract):
    expected_revision: int = Field(ge=0)

class TextTurnRequest(Contract):
    client_event_id: UUID
    expected_revision: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=2000)

class AcceptedTurn(Contract):
    session_id: UUID
    turn_id: UUID
    generation_id: UUID
    status: Literal["accepted"] = "accepted"

class InterruptRequest(Contract):
    client_event_id: UUID
    generation_id: UUID | None = None
    provider_turn: ProviderTurnRef | None = None
    reason: Literal["user_barge_in", "button", "connection_lost"]

    @model_validator(mode="after")
    def require_target(self):
        if self.generation_id is None and self.provider_turn is None:
            raise ValueError("generation_id or provider_turn is required")
        return self

class InterruptResponse(Contract):
    applied: bool
    target_generation_id: UUID | None = None
    reason_code: Literal["CANCEL_REQUESTED", "ALREADY_INTERRUPTED", "STALE_TARGET", "TARGET_UNKNOWN"]

class EndRequest(Contract):
    reason: Literal["user_hangup", "session_timeout", "demo_finished"]

class TelemetrySample(Contract):
    measurement_id: UUID
    kind: Literal["heartbeat", "user_speech_end", "first_text", "audio_output_start", "audio_output_stop", "playback_observed"]
    turn_id: UUID | None = None
    generation_id: UUID | None = None
    provider_turn: ProviderTurnRef | None = None
    client_clock_id: NonEmpty
    monotonic_ms: float = Field(ge=0)
    method: Literal["audio_probe", "performance", "sdk_event", "manual_annotation"]
    uncertainty_ms: float = Field(ge=0)

class TelemetryBatch(Contract):
    client_event_id: UUID
    samples: list[TelemetrySample] = Field(min_length=1, max_length=50)

class Receipt(Contract):
    accepted: bool
    request_id: UUID

class ErrorDetail(Contract):
    code: NonEmpty
    message: str = Field(max_length=500)
    retryable: bool
    request_id: UUID
    operation_id: UUID | None = None
    details: dict[str, str] = Field(default_factory=dict)

class ErrorResponse(Contract):
    error: ErrorDetail

class TurnContext(Contract):
    session_id: UUID
    turn_id: UUID
    generation_id: UUID
    generation_epoch: int = Field(ge=0)
    strategy_version_id: NonEmpty
    rule_version: NonEmpty
    data_version: NonEmpty
    model_config_version: NonEmpty
    trace_id: NonEmpty
    deadline_at: AwareDatetime

class TurnInput(Contract):
    message_id: UUID
    text: str = Field(min_length=1, max_length=2000)
    source: Literal["agora_authenticated_final", "authorized_text_simulator"]
    revision: int = Field(ge=1)
    provider_turn: ProviderTurnRef | None = None

class ToolContext(TurnContext):
    operation_id: UUID
    verified_subject_ref: str | None = None
    environment: Literal["dev", "eval", "demo"]

class ConfirmationRecord(Contract):
    confirmation_id: UUID
    session_id: UUID
    quote_id: UUID
    quote_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    verification_id: UUID
    evidence_message_id: UUID
    evidence_turn_id: UUID
    confirmation_rule_version: NonEmpty
    created_at: AwareDatetime
    expires_at: AwareDatetime
    revoked_at: AwareDatetime | None = None
    revoke_reason: str | None = None
    consumed_operation_id: UUID | None = None

class SubmissionResult(Contract):
    operation_id: UUID
    status: Literal["succeeded", "failed", "unknown", "not_found_yet", "definitively_not_submitted"]
    provider_reference: str | None = None
    order_id: UUID | None = None
    provider_status: str | None = None
    error_code: str | None = None
    retryable: bool
    as_of: AwareDatetime

    @model_validator(mode="after")
    def success_requires_order(self):
        if self.status == "succeeded" and self.order_id is None:
            raise ValueError("successful submission requires order_id")
        return self

class BusinessRecord(Contract):
    session_id: UUID
    record_revision: int = Field(ge=1)
    outcome: NonEmpty
    business_state: BusinessState
    identity_status: Literal["unverified", "verified", "failed", "expired"]
    needs: Needs | None = None
    quote_id: UUID | None = None
    confirmation_id: UUID | None = None
    submit_operation_id: UUID | None = None
    order_id: UUID | None = None
    order_status: str | None = None
    result_delivery_status: Literal["complete", "partial", "unknown", "not_attempted"]
    strategy_version_id: NonEmpty
    rule_version: NonEmpty
    data_version: NonEmpty
    model_config_version: NonEmpty
    state_path: list[BusinessState]
    failure_categories: list[str]
    pending_actions: list[str]
    compliance_checks: dict[str, bool]

class StateChanged(Contract):
    type: Literal["state.changed"] = "state.changed"
    business_state: BusinessState
    call_status: CallStatus
    reason_code: NonEmpty

class TurnStarted(Contract):
    type: Literal["turn.started"] = "turn.started"
    provider_turn: ProviderTurnRef | None = None

class TextDelta(Contract):
    type: Literal["text.delta"] = "text.delta"
    message_id: UUID
    chunk_index: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=2000)

class ToolFinished(Contract):
    type: Literal["tool.finished"] = "tool.finished"
    tool_name: Literal["verify_identity", "query_plans", "submit_change", "query_operation", "writeback"]
    status: Literal["succeeded", "failed", "unknown", "cancelled"]
    error_code: str | None = None
    summary: str = Field(max_length=500)

class OrderUpdated(Contract):
    type: Literal["order.updated"] = "order.updated"
    operation: OperationView

class TurnInterrupted(Contract):
    type: Literal["turn.interrupted"] = "turn.interrupted"
    reason: NonEmpty
    playback_status: Literal["partial", "unknown"]

class MetricRecorded(Contract):
    type: Literal["metric.recorded"] = "metric.recorded"
    measurement_id: UUID
    metric: Literal["llm_ttft_ms", "llm_first_tool_ms", "user_end_to_text_ms", "user_end_to_audio_ms", "interrupt_stop_ms", "generation_cancel_ms"]
    value_ms: float | None = Field(default=None, ge=0)
    sample_status: Literal["measured", "timeout", "interrupted", "invalid"]
    measurement_source: Literal["server", "browser_probe", "vendor", "external_recording", "official_tool"]
    clock_domain: NonEmpty
    uncertainty_ms: float = Field(ge=0)
    exclusion_reason: str | None = None

    @model_validator(mode="after")
    def measured_requires_value(self):
        if self.sample_status == "measured" and self.value_ms is None:
            raise ValueError("measured metric requires value_ms")
        return self

class SessionEnded(Contract):
    type: Literal["session.ended"] = "session.ended"
    outcome: NonEmpty
    pending_operation_ids: list[UUID]

EventPayload = Annotated[StateChanged | TurnStarted | TextDelta | ToolFinished | OrderUpdated | TurnInterrupted | MetricRecorded | SessionEnded, Field(discriminator="type")]

class EventEnvelope(Contract):
    schema_version: Literal["0.1.0"] = VERSION
    event_id: UUID
    session_id: UUID
    seq: int = Field(ge=1)
    occurred_at: AwareDatetime
    state_revision: int = Field(ge=0)
    turn_id: UUID | None = None
    generation_id: UUID | None = None
    operation_id: UUID | None = None
    trace_id: NonEmpty
    strategy_version_id: NonEmpty
    source: Literal["server"] = "server"
    payload: EventPayload

class EvaluationRequest(Contract):
    baseline_version_id: NonEmpty
    candidate_version_id: NonEmpty
    dataset_id: NonEmpty
    repetitions: int = Field(default=3, ge=1, le=3)
    seed: int = Field(default=42, ge=0)
    gate_version: Literal["gate-v1"] = "gate-v1"

class EvaluationRunView(Contract):
    run_id: UUID
    status: Literal["queued", "running", "completed", "failed"]
    baseline_version_id: NonEmpty
    candidate_version_id: NonEmpty
    dataset_id: NonEmpty
    gate_passed: bool | None = None
    reasons: list[str]
    evidence_uri: str | None = None
    evidence_sha256: str | None = None

class StrategyDecisionRequest(Contract):
    action: Literal["adopt", "reject", "rollback"]
    target_version_id: NonEmpty
    expected_active_version_id: NonEmpty
    evaluation_run_id: UUID | None = None
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def adopt_requires_evidence(self):
        if self.action == "adopt" and self.evaluation_run_id is None:
            raise ValueError("adopt requires evaluation_run_id")
        return self

class StrategyDecisionView(Contract):
    decision_id: UUID
    action: Literal["adopt", "reject", "rollback"]
    previous_version_id: NonEmpty
    active_version_id: NonEmpty
    reasons: list[str]
    created_at: AwareDatetime

PUBLIC_MODELS = [Needs, IdentityResult, WritebackResult, CreateSessionRequest, CreateSessionResponse, SessionSnapshot, MediaCredentials,
    RevisionRequest, TextTurnRequest, AcceptedTurn, InterruptRequest, InterruptResponse,
    EndRequest, TelemetryBatch, Receipt, ErrorResponse, ProviderTurnRef, Quote,
    OperationView, TurnContext, TurnInput, ToolContext, ConfirmationRecord,
    SubmissionResult, BusinessRecord, EventEnvelope, EvaluationRequest,
    EvaluationRunView, StrategyDecisionRequest, StrategyDecisionView]
