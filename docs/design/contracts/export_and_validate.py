"""运行本文件导出 Schema 和合成样例，并验证正反例。无需云凭证。"""
import json
from pathlib import Path
from pydantic import ValidationError
from pydantic.json_schema import models_json_schema
from contracts import *

ROOT = Path(__file__).resolve().parent
def uid(n):
    return f"00000000-0000-4000-8000-{n:012d}"
def write(name, value):
    (ROOT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

_, schema = models_json_schema([(m, "validation") for m in PUBLIC_MODELS], title="JD POP outbound contracts 0.3.0")
schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
write("schema.json", schema)
examples = {
    "CreateSession": {"application_id": uid(1), "mode": "text"},
    "ReviewCase": {"application_id": uid(1), "merchant_id": uid(2), "case_revision": 1,
        "scenario_id": "demo.authorization_missing", "review_conclusion": "rejected",
        "slots": {"company_name": "演示商家甲", "brand_name": "模拟品牌甲"},
        "submission_attempt": 1, "call_attempt": 0, "callback_requested": False,
        "complaint_risk": False, "synthetic": True},
    "RoutingDecision": {"decision_id": uid(3), "application_id": uid(1), "case_revision": 1,
        "route": "phone", "reason_codes": ["PHONE_SCENARIO"], "rule_version": "demo-r1",
        "required_evidence": ["recipient_verified", "reason_explained"]},
    "SessionContext": {"session_id": uid(4), "turn_id": uid(5), "generation_id": uid(6),
        "generation_epoch": 0, "application_id": uid(1), "case_revision": 1, "state_revision": 3,
        "strategy_version": "p1", "rule_version": "demo-r1", "knowledge_version": "demo-k1", "model_version": "mock-v1"},
    "TurnInput": {"message_id": uid(7), "text": "我是演示商家甲的联系人，请介绍需要补充的材料。", "source": "text", "final": True},
    "CallbackConfirmation": {"confirmation_id": uid(8), "application_id": uid(1), "case_revision": 1,
        "evidence_message_id": uid(9), "window_start": "2026-11-17T02:00:00Z",
        "window_end": "2026-11-17T02:30:00Z", "timezone": "Asia/Shanghai", "expires_at": "2026-11-17T02:00:00Z"},
    "ActionCommand": {"session_id": uid(4), "application_id": uid(1), "case_revision": 1,
        "decision_id": uid(3), "operation_id": uid(10), "kind": "create_callback",
        "idempotency_key": "demo:application1:r1:callback:confirmation8", "confirmation_ref": uid(8), "evidence_refs": [uid(9)],
        "payload": {"type": "create_callback", "confirmation_id": uid(8)}},
    "OperationResult": {"operation_id": uid(10), "status": "unknown", "updated_at": "2026-11-16T06:00:00Z"},
    "AgentDecision": {"reply_text": "这里是模拟商户服务演示。", "proposed_action": "none",
        "evidence_message_ids": [], "knowledge_entry_ids": [], "next_state": "VERIFYING"},
    "CallRecord": {"record_id": uid(11), "record_revision": 1, "session_id": uid(4),
        "application_id": uid(1), "case_revision": 1, "contact_status": "verified",
        "explanation_status": "partial", "reason_codes": ["CALLBACK_REQUESTED"], "rectification_items": [],
        "source_refs": [], "operation_refs": [uid(10)], "strategy_version": "p1", "rule_version": "demo-r1", "knowledge_version": "demo-k1"},
    "Event": {"schema_version": "0.3.0", "event_id": uid(12), "session_id": uid(4),
        "turn_id": uid(5), "generation_id": uid(6), "operation_id": uid(10), "seq": 4,
        "state_revision": 3, "occurred_at": "2026-11-16T06:00:00Z", "strategy_version": "p1",
        "knowledge_version": "demo-k1", "payload": {"type": "operation.updated", "result": {"operation_id": uid(10), "status": "unknown", "updated_at": "2026-11-16T06:00:00Z"}}},
    "SessionView": {"session_id": uid(4), "application_id": uid(1), "case_revision": 1,
        "state": "RECONCILING", "state_revision": 3, "route": "phone", "last_event_seq": 4,
        "operations": [{"operation_id": uid(10), "status": "unknown", "updated_at": "2026-11-16T06:00:00Z"}]},
}
for name, value in examples.items():
    globals()[name].model_validate(value)
write("examples.json", examples)
negative = [
    (ActionCommand, {**examples["ActionCommand"], "user_confirmed": True}),
    (ActionCommand, {**examples["ActionCommand"], "confirmation_ref": None}),
    (TurnInput, {**examples["TurnInput"], "final": False}),
    (Event, {**examples["Event"], "payload": {"type": "order.completed"}}),
    (CallbackConfirmation, {**examples["CallbackConfirmation"], "window_end": "2026-11-16T02:00:00Z"}),
]
for model, value in negative:
    try:
        model.model_validate(value)
    except ValidationError:
        continue
    raise AssertionError(f"expected rejection by {model.__name__}")
print(f"PASS: {len(PUBLIC_MODELS)} public models; {len(examples)} valid examples; {len(negative)} invalid inputs rejected")

# Keep the canonical exporter responsible for generated schemas.
import runpy
runpy.run_path(str(ROOT.parents[2] / "scripts" / "export_contracts.py"))
