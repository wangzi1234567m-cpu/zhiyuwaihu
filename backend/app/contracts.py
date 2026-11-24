"""公共DTO 0.2.0。只验证结构；授权与规则守卫必须在服务端另行执行。"""
from typing import Annotated, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime, model_validator

class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

class SourceRef(DTO):
    file_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    sheet: str
    row: int = Field(ge=1)
    columns: list[str]

class KnowledgeEntry(DTO):
    entry_id: UUID
    knowledge_version: str
    kind: Literal["routing_rule", "script", "qa", "scenario"]
    scenario_id: str
    taxonomy: list[str] = Field(default_factory=list)
    scope: list[str]
    channel: Literal["phone", "jingmai", "unspecified"]
    required_slots: list[str]
    approved_text: str | None = None
    source_refs: list[SourceRef] = Field(min_length=1)
    status: Literal["draft", "quarantined", "approved"]
    issue_codes: list[str]
    reviewer: str | None = None

class ReviewCase(DTO):
    application_id: UUID
    merchant_id: UUID
    case_revision: int = Field(ge=1)
    scenario_id: str
    review_conclusion: Literal["rejected", "pending", "approved"]
    slots: dict[str, str]
    submission_attempt: int = Field(ge=1)
    call_attempt: int = Field(ge=0)
    callback_requested: bool
    complaint_risk: bool
    synthetic: Literal[True] = True

class RoutingEvidence(DTO):
    """规则命中证据：为什么得到这个 route，可追溯、不含个人信息。"""
    code: str = Field(min_length=1, max_length=80)
    detail: str = Field(default="", max_length=400)
    source: Literal["case", "rule", "knowledge", "contact_history", "authorization", "attempts"] = "rule"
    ref: str | None = Field(default=None, max_length=200)
    knowledge_entry_ids: list[UUID] = Field(default_factory=list)

class RoutingDecision(DTO):
    decision_id: UUID
    application_id: UUID
    case_revision: int = Field(ge=1)
    route: Literal["phone", "jingmai", "manual"]
    reason_codes: list[str]
    rule_version: str
    required_evidence: list[str]
    # 兼容增量：旧样例/调用方不传也能反序列化。followup 是动作不是 route。
    evidence: list[RoutingEvidence] = Field(default_factory=list)
    submission_attempt: int = Field(default=1, ge=0)
    call_attempt: int = Field(default=0, ge=0)

State = Literal["CASE_READY", "ROUTING", "OPENING", "VERIFYING", "EXPLAINING", "QA", "CALLBACK_PENDING", "CLOSING", "ACTION_READY", "EXECUTING", "RECONCILING", "WRITEBACK_PENDING", "CLOSED", "MANUAL_REVIEW"]

#: 真实联系结果。unknown 表示没有可信联系事件，不能当成未接通或已核验。
ContactOutcome = Literal["verified", "wrong_person", "refused", "no_answer", "disconnected", "not_required", "unknown"]

class NotificationEvidence(DTO):
    """已发出的告知证据；由服务端在消息落库后写入，Agent 只读。"""
    kind: Literal["explanation", "reason_notice", "remedy_notice", "callback_notice"]
    message_id: UUID
    knowledge_entry_ids: list[UUID] = Field(default_factory=list)
    completed: bool = False

class CreateSession(DTO):
    application_id: UUID
    mode: Literal["voice", "text"]

class TextTurn(DTO):
    text: str = Field(min_length=1, max_length=8000)
    client_event_id: UUID

class StartSession(DTO):
    expected_revision: int = Field(ge=0)

class InterruptRequest(DTO):
    generation_id: UUID
    client_event_id: UUID

class SessionContext(DTO):
    """服务端组装的可信只读上下文。Agent 只能读，不能写入或回填。"""
    session_id: UUID
    turn_id: UUID
    generation_id: UUID
    generation_epoch: int = Field(ge=0)
    application_id: UUID
    case_revision: int = Field(ge=1)
    state_revision: int = Field(ge=0)
    strategy_version: str
    rule_version: str
    knowledge_version: str
    model_version: str
    # 兼容增量：全部有默认值，旧调用方不传也能构造。状态只来自数据库，不从话术推断。
    state: State = "CASE_READY"
    routing_decision: RoutingDecision | None = None
    notifications: list[NotificationEvidence] = Field(default_factory=list)
    contact_outcome: ContactOutcome = "unknown"
    contact_outcome_source: Literal["none", "domain_event", "demo_text_rule"] = "none"
    submission_attempt: int = Field(default=1, ge=0)
    call_attempt: int = Field(default=0, ge=0)
    operation_ids: list[UUID] = Field(default_factory=list)

class TurnInput(DTO):
    message_id: UUID
    text: str = Field(min_length=1, max_length=8000)
    source: Literal["asr", "text", "realtime"]
    final: Literal[True] = True
    provider_binding_id: str | None = None
    provider_turn_id: str | None = None

class CallbackConfirmation(DTO):
    confirmation_id: UUID
    application_id: UUID
    case_revision: int = Field(ge=1)
    evidence_message_id: UUID
    window_start: AwareDatetime
    window_end: AwareDatetime
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    expires_at: AwareDatetime
    revoked_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def ordered_window(self):
        if self.window_end <= self.window_start:
            raise ValueError("window_end must follow window_start")
        return self

class RejectPayload(DTO):
    type: Literal["reject_application"]
    reason_codes: list[str] = Field(min_length=1)
    written_text: str = Field(min_length=1)
    knowledge_entry_ids: list[UUID] = Field(min_length=1)

class CallbackPayload(DTO):
    type: Literal["create_callback"]
    confirmation_id: UUID

class FollowupPayload(DTO):
    type: Literal["create_followup"]
    reason_code: str
    related_operation_id: UUID | None = None
    # 未接通后的系统跟进计划不代表商户已确认预约。
    suggested_at: AwareDatetime | None = None

class WritebackPayload(DTO):
    type: Literal["writeback"]
    record_id: UUID
    record_revision: int = Field(ge=1)

ActionPayload = Annotated[RejectPayload | CallbackPayload | FollowupPayload | WritebackPayload, Field(discriminator="type")]

class ActionCommand(DTO):
    """仅服务端构造；工具守门查库验证decision、confirmation、权限和revision。"""
    session_id: UUID
    application_id: UUID
    case_revision: int = Field(ge=1)
    decision_id: UUID
    operation_id: UUID
    kind: Literal["reject_application", "create_callback", "create_followup", "writeback"]
    idempotency_key: str = Field(min_length=1, max_length=200)
    confirmation_ref: UUID | None = None
    evidence_refs: list[UUID]
    payload: ActionPayload

    @model_validator(mode="after")
    def callback_evidence_required(self):
        if self.kind != self.payload.type:
            raise ValueError("action kind and payload type mismatch")
        if self.kind == "create_callback" and self.confirmation_ref is None:
            raise ValueError("callback requires server confirmation reference")
        if isinstance(self.payload, CallbackPayload) and self.confirmation_ref != self.payload.confirmation_id:
            raise ValueError("callback confirmation mismatch")
        return self

class ApiError(DTO):
    code: str
    message: str
    retryable: bool
    request_id: UUID
    operation_id: UUID | None = None

class OperationResult(DTO):
    kind: Literal["reject_application", "create_callback", "create_followup", "writeback"] | None = None
    operation_id: UUID
    status: Literal["pending", "succeeded", "failed", "unknown", "not_found_yet"]
    resource_id: str | None = None
    error: ApiError | None = None
    updated_at: AwareDatetime

class CallRecord(DTO):
    record_id: UUID
    record_revision: int = Field(ge=1)
    session_id: UUID
    application_id: UUID
    case_revision: int = Field(ge=1)
    # 兼容增量：新增 "unknown"。没有可信联系事件时必须如实记录 unknown，
    # 不能用 not_required（代表无需核验）或 disconnected（代表断线）冒充。
    # 既有取值与消费者语义不变。
    contact_status: Literal["verified", "wrong_person", "refused", "no_answer", "disconnected", "not_required", "unknown"]
    explanation_status: Literal["not_started", "partial", "completed", "unknown", "not_required"]
    reason_codes: list[str]
    rectification_items: list[str]
    source_refs: list[SourceRef]
    operation_refs: list[UUID]
    strategy_version: str
    rule_version: str
    knowledge_version: str

class Understanding(DTO):
    """结构化理解结果（内部 DTO，服务器再校验）。模型不能写 verified/审核结论/成功。"""
    intents: list[Literal["confirm_identity", "deny_identity", "ask_reason", "ask_remedy",
                          "ask_guarantee", "ask_source", "ask_outcome", "refuse_contact",
                          "request_callback", "acknowledge", "unclear", "complaint_risk"]]
    slots: dict[str, str] = Field(default_factory=dict)
    evidence_message_ids: list[UUID] = Field(default_factory=list)
    needs_clarification: bool = False
    clarification_reason: str | None = Field(default=None, max_length=200)
    negated: bool = False
    multi_intent: bool = False
    contact_outcome_hint: ContactOutcome | None = None
    source: Literal["fake", "glm", "rules", "realtime_candidate"] = "fake"

class AgentDecision(DTO):
    reply_text: str
    proposed_action: Literal["none", "reject_application", "create_callback", "create_followup", "writeback"]
    evidence_message_ids: list[UUID]
    knowledge_entry_ids: list[UUID]
    next_state: State
    action_payload: ActionPayload | None = None
    # 兼容增量：默认值保证旧实现/旧样例仍合法。
    understanding: Understanding | None = None
    contact_outcome: ContactOutcome | None = None
    required_slot_missing: list[str] = Field(default_factory=list)

class StateChanged(DTO):
    type: Literal["state.changed"]
    state: State

class TurnStarted(DTO):
    type: Literal["turn.started"]
    message_id: UUID

class OperationUpdated(DTO):
    type: Literal["operation.updated"]
    result: OperationResult

class KnowledgeUsed(DTO):
    type: Literal["knowledge.used"]
    entry_ids: list[UUID]
    source_refs: list[SourceRef]

class TextDelta(DTO):
    type: Literal["text.delta"]
    text: str
    channel: Literal["text_mode", "voice_transport"] = "text_mode"

class TurnInterrupted(DTO):
    type: Literal["turn.interrupted"]
    invalidated_generation_id: UUID
    new_epoch: int = Field(ge=1)

class MetricRecorded(DTO):
    type: Literal["metric.recorded"]
    name: Literal["model_ttft", "speech_end_to_text", "speech_end_to_playback"]
    duration_ms: float | None = Field(default=None, ge=0)
    outcome: Literal["success", "timeout", "cancelled", "missing"]
    clock_domain: str
    measurement_method: str

class SessionEnded(DTO):
    type: Literal["session.ended"]
    reason: str

class GenerationFinished(DTO):
    type: Literal['generation.finished']
    status: Literal['completed', 'interrupted', 'failed']
    message_id: UUID

Payload = Annotated[StateChanged | TurnStarted | OperationUpdated | KnowledgeUsed | TextDelta | TurnInterrupted | MetricRecorded | SessionEnded | GenerationFinished, Field(discriminator="type")]

class Event(DTO):
    schema_version: Literal["0.3.0"] = "0.3.0"
    event_id: UUID
    session_id: UUID
    turn_id: UUID | None = None
    generation_id: UUID | None = None
    operation_id: UUID | None = None
    seq: int = Field(ge=1)
    state_revision: int = Field(ge=0)
    occurred_at: AwareDatetime
    strategy_version: str
    knowledge_version: str
    payload: Payload

class SessionView(DTO):
    session_id: UUID
    application_id: UUID
    case_revision: int = Field(ge=1)
    state: State
    state_revision: int = Field(ge=0)
    route: Literal["phone", "jingmai", "manual"]
    last_event_seq: int = Field(ge=0)
    operations: list[OperationResult]
    verified: bool = False
    busy: bool = False
    ended: bool = False
    generation_id: UUID | None = None
    reason: str | None = None
    remedy: str | None = None
    source_refs: list[SourceRef] = Field(default_factory=list)
    metrics: list[MetricRecorded] = Field(default_factory=list)
    messages: list['MessageView'] = Field(default_factory=list)
    record: CallRecord | None = None
    mode: Literal['text','voice'] = 'text'

class MessageView(DTO):
    message_id: UUID
    turn_id: UUID
    generation_id: UUID
    role: Literal['user', 'assistant']
    text: str
    status: Literal['completed', 'generating', 'interrupted', 'failed']
    playback_status: Literal['not_applicable', 'unknown'] = 'not_applicable'

class AcceptedTurn(DTO):
    turn_id: UUID
    generation_id: UUID

class Interrupted(DTO):
    applied: bool

class EndRequest(DTO):
    reason: str = Field(default='user_ended', max_length=100)

class LoginRequest(DTO):
    username: Literal['demo', 'other'] = 'demo'
    password: str

class LoginResult(DTO):
    owner: str

class ApplicationView(DTO):
    application_id: UUID
    label: str
    company_hint: str

class Capabilities(DTO):
    routing_preview: bool = False
    text: bool = True
    voice: bool = False
    model: Literal['fake','deepseek'] = 'fake'
    schema_version: Literal['0.3.0'] = '0.3.0'
    voice_unavailable_reason: str | None = None

class ModelMessage(DTO):
    role: Literal['system','user','assistant']
    content: str

class ModelRequest(DTO):
    messages: list[ModelMessage]
    fixture_response: str
    allow_rephrase: bool = False
    session_id: UUID | None = None
    turn_id: UUID | None = None
    generation_id: UUID | None = None
    # Fake adapter uses fixture_response; a future LLM adapter uses messages.
    # Tool suggestions are handled by AgentPort, not by this speech-text stream.

SessionView.model_rebuild()

PUBLIC_MODELS = [SourceRef, KnowledgeEntry, ReviewCase, RoutingDecision, RoutingEvidence, NotificationEvidence, CreateSession, TextTurn, StartSession, InterruptRequest, SessionContext, TurnInput, CallbackConfirmation, ActionCommand, ApiError, OperationResult, CallRecord, AgentDecision, Understanding, Event, SessionView]
PUBLIC_MODELS += [MessageView, AcceptedTurn, Interrupted, EndRequest, LoginRequest, LoginResult, ApplicationView, Capabilities]
PUBLIC_MODELS += [ModelRequest, ModelMessage]

class MediaCredentials(DTO):
    app_id: str
    channel: str
    rtc_uid: int
    agent_uid: int
    rtc_token: str
    rtm_token: str
    expires_at: AwareDatetime

class VoiceStartResult(DTO):
    status: Literal['starting','running','stopped','cleanup_pending']
    agent_id: str | None = None

class VoiceInterruptRequest(DTO):
    client_event_id: UUID
    provider_turn_id: int | None = Field(default=None,ge=0)

class VoiceHeartbeat(DTO):
    client_event_id: UUID

class VoiceTelemetry(DTO):
    client_event_id: UUID
    provider_turn_id: int | None = Field(default=None,ge=0)
    kind: Literal['interrupted','playback_observed','speech_end_observed','sdk_metric']
    clock_domain: str = Field(max_length=80)
    monotonic_ms: float = Field(ge=0)
    duration_ms: float | None = Field(default=None,ge=0)
    measurement_method: str = Field(max_length=150)
    provider_metric_name: str | None = Field(default=None,max_length=80)
    provider_metric_value: float | None = None

PUBLIC_MODELS += [MediaCredentials,VoiceStartResult,VoiceInterruptRequest,VoiceHeartbeat,VoiceTelemetry]
