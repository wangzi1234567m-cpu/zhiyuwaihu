"""导出结构和设计样例；不启动服务器、不调用云服务。"""
from __future__ import annotations
import json
from pathlib import Path
from uuid import UUID
from pydantic import ValidationError
from pydantic.json_schema import models_json_schema
from contracts import (PUBLIC_MODELS, CreateSessionRequest, CreateSessionResponse, EventEnvelope,
    InterruptRequest, OperationView, Quote, SessionSnapshot, SubmissionResult,
    StrategyDecisionRequest, TextTurnRequest, ConfirmationRecord, BusinessRecord, MetricRecorded)

ROOT = Path(__file__).resolve().parent

def uid(n: int) -> str:
    return str(UUID(f"00000000-0000-4000-8000-{n:012d}"))

def export():
    _, schema = models_json_schema([(m, "validation") for m in PUBLIC_MODELS], title="Zhiyu Shared Contracts 0.1.0")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["description"] = "Definitions bundle; select a specific model via $defs. Not a running API."
    (ROOT / "shared.schema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    quote = Quote(quote_id=uid(4), revision=1, plan_id="family_59", plan_name="模拟家庭套餐",
        price_minor=5900, effective_at="2026-12-01T00:00:00+08:00", contract_months=0,
        one_time_fee_minor=0, terms_version="mock-terms-1", quote_hash="a"*64,
        eligibility=True, valid_until="2026-11-16T10:05:00Z")
    session = SessionSnapshot(session_id=uid(1), mode="voice", call_status="created", business_state="OPENING",
        state_revision=0, generation_epoch=0, strategy_version_id="strategy-baseline-1",
        rule_version="mock-rules-1", data_version="mock-data-1", model_config_version="deepseek-flash-nonthinking-1",
        last_event_seq=0, trace_id="trace-demo")
    created = CreateSessionResponse(session=session, media={
        "app_id":"PLACEHOLDER_APP_ID", "channel":"demo-session-1", "browser_uid":"1001", "agent_uid":"2001",
        "rtc_token":"PLACEHOLDER_NOT_A_TOKEN", "rtm_token":"PLACEHOLDER_NOT_A_TOKEN", "expires_at":"2026-11-16T10:10:00Z"})
    operation = OperationView(operation_id=uid(5), session_id=uid(1), kind="submit_change", status="unknown",
        quote_id=uid(4), error_code="SUBMISSION_OUTCOME_UNKNOWN", updated_at="2026-11-16T10:01:03Z")
    confirmation = ConfirmationRecord(confirmation_id=uid(6), session_id=uid(1), quote_id=uid(4), quote_hash="a"*64,
        verification_id=uid(7), evidence_message_id=uid(8), evidence_turn_id=uid(2),
        confirmation_rule_version="explicit-quote-confirm-1", created_at="2026-11-16T10:01:00Z",
        expires_at="2026-11-16T10:05:00Z", consumed_operation_id=uid(5))
    def event(seq, payload, **extra):
        return EventEnvelope(event_id=uid(100+seq), session_id=uid(1), seq=seq,
            occurred_at="2026-11-16T10:01:03Z", state_revision=5, turn_id=uid(2), generation_id=uid(3),
            trace_id="trace-demo", strategy_version_id="strategy-baseline-1", payload=payload, **extra)
    examples = {
        "create-session.request.json":CreateSessionRequest(),
        "create-session.response.json":created,
        "text-turn.request.json":TextTurnRequest(client_event_id=uid(20),expected_revision=3,text="我想了解家庭套餐。"),
        "quote.json":quote,
        "confirmation-record.json":confirmation,
        "interrupt.request.json":InterruptRequest(client_event_id=uid(21),generation_id=uid(3),reason="user_barge_in"),
        "operation-unknown.json":operation,
        "submission-reconciled.json":SubmissionResult(operation_id=uid(5),status="succeeded",order_id=uid(9),
            provider_reference="mock-order-1",provider_status="submitted",retryable=False,as_of="2026-11-16T10:01:04Z"),
        "state.event.json":event(18,{"type":"state.changed","business_state":"AWAITING_CONFIRMATION","call_status":"active","reason_code":"QUOTE_READY"}),
        "turn.event.json":event(19,{"type":"turn.started","provider_turn":{"binding_id":uid(30),"provider_turn_id":"3"}}),
        "order.event.json":event(20,{"type":"order.updated","operation":operation},operation_id=uid(5)),
        "interrupt.event.json":event(21,{"type":"turn.interrupted","reason":"user_barge_in","playback_status":"unknown"}),
        "metric.event.json":event(22,{"type":"metric.recorded","measurement_id":uid(40),"metric":"user_end_to_audio_ms",
            "sample_status":"timeout","value_ms":None,"measurement_source":"browser_probe","clock_domain":"browser-demo-1",
            "uncertainty_ms":10,"exclusion_reason":None}),
        "business-record.json":BusinessRecord(session_id=uid(1),record_revision=1,outcome="submitted",business_state="WRITEBACK_PENDING",
            identity_status="verified",quote_id=uid(4),confirmation_id=uid(6),submit_operation_id=uid(5),order_id=uid(9),
            order_status="submitted",result_delivery_status="unknown",strategy_version_id="strategy-baseline-1",
            rule_version="mock-rules-1",data_version="mock-data-1",model_config_version="deepseek-flash-nonthinking-1",
            needs={"desired_plan_id":"family_59","max_monthly_price_minor":5900},
            state_path=["OPENING","VERIFYING","DISCOVERING","QUOTING","AWAITING_CONFIRMATION","SUBMITTING","RECONCILING","RESULT_READY","WRITEBACK_PENDING"],
            failure_categories=["tool_provider"],pending_actions=["writeback"],
            compliance_checks={"verified_before_sensitive_query":True,"confirmed_before_dispatch":True}),
        "adopt.request.json":StrategyDecisionRequest(action="adopt",target_version_id="strategy-candidate-1",
            expected_active_version_id="strategy-baseline-1",evaluation_run_id=uid(50),reason="示意请求，服务端仍须检查实际 gate-v1 证据。"),
    }
    dest = ROOT / "examples"
    dest.mkdir(exist_ok=True)
    manifest = {}
    for filename, obj in examples.items():
        encoded = obj.model_dump_json(indent=2)
        type(obj).model_validate_json(encoded)
        (dest / filename).write_text(encoded + "\n",encoding="utf-8")
        manifest[filename] = type(obj).__name__
    (dest / "manifest.json").write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
    invalid = [
        (CreateSessionRequest,{"user_confirmed":True}),
        (TextTurnRequest,{"client_event_id":uid(20),"expected_revision":0,"text":"确认","user_confirmed":True}),
        (InterruptRequest,{"client_event_id":uid(20),"reason":"button"}),
        (SubmissionResult,{"operation_id":uid(5),"status":"succeeded","retryable":False,"as_of":"2026-11-16T10:01:04Z"}),
        (StrategyDecisionRequest,{"action":"adopt","target_version_id":"v2","expected_active_version_id":"v1","reason":"no evidence"}),
        (MetricRecorded,{"measurement_id":uid(40),"metric":"llm_ttft_ms","sample_status":"measured","measurement_source":"server","clock_domain":"server-1","uncertainty_ms":0}),
    ]
    for model, data in invalid:
        try:
            model.model_validate(data)
        except ValidationError:
            continue
        raise AssertionError(f"Invalid contract unexpectedly accepted: {model.__name__}")
    print(f"Exported {len(PUBLIC_MODELS)} public roots, {len(examples)} examples; {len(invalid)} negative checks passed.")

if __name__ == "__main__":
    export()
