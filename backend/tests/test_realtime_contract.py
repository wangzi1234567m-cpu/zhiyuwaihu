"""Offline contract boundaries; no app import, database, provider or runtime fake."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import get_args
from uuid import uuid4

import pytest
from pydantic import ValidationError
from pydantic.json_schema import models_json_schema

from app.contracts import AcceptedTurn, Interrupted, Understanding
from app.realtime_contract.dto import (
    PUBLIC_MODELS, BusinessSnapshot, BusinessTurnInput, Intent, PlaybackReport,
    PlaybackReportResult, RealtimeBusinessEvent, ReplySegmentReady, TrustedTurnContext,
)
from app.realtime_contract.ports import ContractFailure
from app.realtime_contract.dto import ContractError


def segment(**changes):
    text = '补齐材料后仍需审核，不能保证通过。'
    return dict(message_id=uuid4(), segment_id=uuid4(), segment_index=0, text=text,
                text_sha256=hashlib.sha256(text.encode()).hexdigest()) | changes


def test_existing_result_types_are_reused():
    assert AcceptedTurn in PUBLIC_MODELS and Interrupted in PUBLIC_MODELS
    existing_intents = get_args(get_args(Understanding.model_fields['intents'].annotation)[0])
    assert set(get_args(Intent)) == set(existing_intents)


@pytest.mark.parametrize('change', [
    {'final': False}, {'text': '  '}, {'verified': True},
    {'understanding_candidate': {'intents': ['acknowledge'], 'verified': True}},
    {'understanding_candidate': {'intents': ['execute_reject']}},
    {'understanding_candidate': {'intents': ['acknowledge'], 'source': 'native_qwen'}},
])
def test_untrusted_input_cannot_claim_finality_or_permissions(change):
    with pytest.raises(ValidationError):
        BusinessTurnInput.model_validate(dict(input_id=uuid4(), text='谢谢') | change)


def test_server_context_is_frozen_and_versioned():
    values = dict(owner='demo', session_id=uuid4(), connection_binding=uuid4(),
                  request_id=uuid4(), provider_input_id='provider-1',
                  expected_state_revision=0, expected_case_revision=1, knowledge_version='k1')
    context = TrustedTurnContext(**values)
    with pytest.raises(ValidationError):
        context.owner = 'other'
    with pytest.raises(ValidationError):
        TrustedTurnContext(**values, contract_version='0.5.0')


def test_reviewed_segment_binds_exact_text():
    value = ReplySegmentReady(**segment())
    assert value.speech_policy == 'verbatim'
    with pytest.raises(ValidationError):
        ReplySegmentReady(**segment(text_sha256='0' * 64))


def test_event_discriminator_and_round_trip():
    event = RealtimeBusinessEvent(event_id=uuid4(), session_id=uuid4(), turn_id=uuid4(),
        generation_id=uuid4(), seq=1, state_revision=0, occurred_at=datetime.now(timezone.utc),
        knowledge_version='k1', payload=ReplySegmentReady(**segment()))
    restored = RealtimeBusinessEvent.model_validate_json(event.model_dump_json())
    assert restored == event
    assert isinstance(restored.payload, ReplySegmentReady)
    raw = event.model_dump(mode='json')
    raw['payload']['type'] = 'text.delta'
    with pytest.raises(ValidationError):
        RealtimeBusinessEvent.model_validate(raw)


def test_operation_result_cannot_be_bound_to_another_operation():
    now = datetime.now(timezone.utc)
    operation_id = uuid4()
    valid = dict(event_id=uuid4(), session_id=uuid4(), turn_id=uuid4(),
        generation_id=uuid4(), operation_id=operation_id, seq=1, state_revision=0,
        occurred_at=now, knowledge_version='k1', payload={
            'type': 'operation.updated', 'result': {'operation_id': operation_id,
                'kind': 'reject_application', 'status': 'unknown', 'updated_at': now}})
    assert RealtimeBusinessEvent(**valid).operation_id == operation_id
    with pytest.raises(ValidationError):
        RealtimeBusinessEvent.model_validate(valid | {'operation_id': uuid4()})


@pytest.mark.parametrize('change', [{'verified': True}, {'elapsed_ms': float('nan')}])
def test_client_playback_cannot_assert_trust(change):
    report = dict(report_id=uuid4(), generation_id=uuid4(), message_id=uuid4(),
                  segment_id=uuid4(), playback_instance_id=uuid4(), text_sha256='a' * 64,
                  observation='completed', clock_domain='browser.performance')
    with pytest.raises(ValidationError):
        PlaybackReport.model_validate(report | change)


def test_observation_only_cannot_be_labelled_verified():
    with pytest.raises(ValidationError):
        PlaybackReportResult(report_id=uuid4(), status='evidence_verified',
                             reason_code='OBSERVATION_ONLY')


def test_snapshot_requires_valid_replay_range_and_current_playback():
    values = dict(session_id=uuid4(), state='QA', state_revision=2, case_revision=1,
                  knowledge_version='k1', last_event_seq=4, oldest_available_seq=1)
    assert BusinessSnapshot(**values).last_event_seq == 4
    with pytest.raises(ValidationError):
        BusinessSnapshot.model_validate(values | {'oldest_available_seq': 6})
    with pytest.raises(ValidationError):
        BusinessSnapshot.model_validate(values | {'generation_id': uuid4(), 'playback': {
            'generation_id': uuid4(), 'message_id': uuid4()}})


def test_contract_failure_does_not_copy_private_error_details():
    error = ContractError(code='REQUEST_CONFLICT', message='请求内容冲突', retryable=False)
    failure = ContractFailure(error)
    assert str(failure) == 'REQUEST_CONFLICT' and failure.error == error


def test_committed_schema_matches_dto_source():
    _, schema = models_json_schema([(m, 'validation') for m in PUBLIC_MODELS],
                                  title='Realtime business internal contract 0.4.0')
    schema['$schema'] = 'https://json-schema.org/draft/2020-12/schema'
    path = Path(__file__).resolve().parents[2] / 'docs/design/contracts/realtime-v04.schema.json'
    assert json.loads(path.read_text(encoding='utf-8')) == schema
