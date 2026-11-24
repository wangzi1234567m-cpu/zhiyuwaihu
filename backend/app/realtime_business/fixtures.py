"""Seven public, synthetic scenarios. No database, media SDK, or cloud calls.

This in-memory double tests the realtime consumer, not production durability or
business authorization. The real facade is tested separately against PostgreSQL.
"""
import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from ..contracts import (AcceptedTurn, GenerationFinished, Interrupted, OperationResult,
                         OperationUpdated, TurnInterrupted, TurnStarted)
from ..realtime_contract.dto import (BusinessSnapshot, ContractError, PlaybackReportResult,
    RealtimeBusinessEvent, ReplyCompleted, ReplySegmentReady, TrustedSessionContext,
    TurnFailed, WritebackCompleted)
from ..realtime_contract.ports import ContractFailure


SCENARIOS = ('normal', 'slow', 'timeout', 'duplicate', 'late', 'interrupt', 'disconnect')


@dataclass(frozen=True)
class Scenario:
    name: str = 'normal'
    delay_seconds: float = 3.0
    segment_gap: float = .02

    def __post_init__(self):
        if self.name not in SCENARIOS or self.delay_seconds < 0 or self.segment_gap < 0:
            raise ValueError('Invalid scenario or delay')


def failure(code):
    raise ContractFailure(ContractError(code=code, message=code, retryable=False))


class ScenarioBusiness:
    """A-side substitute implementing the same five public interfaces as B."""
    def __init__(self, scenario=Scenario()):
        self.scenario = scenario
        self.context = TrustedSessionContext(owner='synthetic-demo', session_id=uuid4(),
                                             connection_binding=uuid4())
        self.events = []
        self.receipts = {}
        self.providers = set()
        self.tasks = set()
        self.generation = None
        self.active = set()
        self.revision = 0
        self.operations = {}
        self.subscribers = 0
        self.effect_count = 0
        self.closed = False
        self.entered = asyncio.Event()

    def authorize(self, context):
        if context.owner != self.context.owner or context.session_id != self.context.session_id:
            failure('SESSION_NOT_FOUND')
        if context.connection_binding != self.context.connection_binding:
            failure('BINDING_EXPIRED')

    def _emit(self, payload, accepted, operation=None):
        event = RealtimeBusinessEvent(event_id=uuid4(), session_id=self.context.session_id,
            turn_id=accepted.turn_id, generation_id=accepted.generation_id,
            seq=len(self.events)+1, state_revision=self.revision,
            occurred_at=datetime.now(timezone.utc), knowledge_version='synthetic-v04',
            operation_id=operation, payload=payload)
        self.events.append(event)
        return event

    async def submit_turn(self, context, user_input):
        self.authorize(context)
        content = json.dumps({'context': context.model_dump(mode='json', exclude={'connection_binding'}),
                              'input': user_input.model_dump(mode='json')}, sort_keys=True)
        rkey = ('turn', context.request_id)
        if rkey in self.receipts:
            old_content, accepted = self.receipts[rkey]
            if content != old_content: failure('REQUEST_CONFLICT')
            return accepted
        if context.provider_input_id in self.providers: failure('REQUEST_CONFLICT')
        if (context.expected_state_revision != self.revision or context.expected_case_revision != 1 or
            context.knowledge_version != 'synthetic-v04'): failure('REVISION_CONFLICT')
        if len(self.tasks) >= 4 or len(self.events) > 1000: failure('BUSY')
        self.active.clear()
        result = AcceptedTurn(turn_id=uuid4(), generation_id=uuid4())
        self.generation = result.generation_id
        self.active.add(result.generation_id)
        self.revision += 1
        self.providers.add(context.provider_input_id)
        self.receipts[rkey] = (content, result)
        self._emit(TurnStarted(type='turn.started', message_id=user_input.input_id), result)
        task = asyncio.create_task(self._run(result))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return result

    async def _run(self, accepted):
        self.entered.set()
        name = self.scenario.name
        if name in ('slow', 'timeout', 'late', 'interrupt', 'disconnect'):
            await asyncio.sleep(self.scenario.delay_seconds)
        if name == 'timeout':
            operation = OperationResult(operation_id=uuid4(), status='unknown',
                                        updated_at=datetime.now(timezone.utc))
            self.operations[operation.operation_id] = operation
            self._emit(OperationUpdated(type='operation.updated', result=operation), accepted, operation.operation_id)
            self._emit(TurnFailed(error=ContractError(code='BUSINESS_TIMEOUT',
                message='Synthetic business timeout; original operation remains unknown', retryable=False)), accepted)
            self.active.discard(accepted.generation_id)
            return
        if accepted.generation_id not in self.active and name != 'late': return
        message = uuid4()
        for index, text in enumerate(('这是已审核的合成测试答复。', '补齐材料后仍需审核，不能保证通过。')):
            if accepted.generation_id not in self.active and name != 'late': return
            self._emit(ReplySegmentReady(message_id=message, segment_id=uuid4(), segment_index=index,
                text=text, text_sha256=hashlib.sha256(text.encode()).hexdigest()), accepted)
            await asyncio.sleep(self.scenario.segment_gap)
        self._emit(ReplyCompleted(message_id=message, segment_count=2), accepted)
        self._emit(GenerationFinished(type='generation.finished', status='completed', message_id=message), accepted)
        self.active.discard(accepted.generation_id)
        if name == 'disconnect':
            # The consumer disconnects; the accepted synthetic operation still finishes once.
            operation = OperationResult(operation_id=uuid4(), status='succeeded', updated_at=datetime.now(timezone.utc))
            self.operations[operation.operation_id] = operation
            self.effect_count += 1
            self._emit(OperationUpdated(type='operation.updated', result=operation), accepted, operation.operation_id)
            self._emit(WritebackCompleted(record_id=uuid4(), record_revision=1), accepted)

    async def subscribe_events(self, context, subscription):
        self.authorize(context)
        if subscription.after_seq > len(self.events): failure('CURSOR_AHEAD')
        if self.subscribers >= 2: failure('SUBSCRIBER_LIMIT')
        self.subscribers += 1
        cursor = subscription.after_seq
        replay_end = len(self.events)
        try:
            while not self.closed:
                self.authorize(context)
                if len(self.events) - max(cursor, replay_end) > 128: failure('SLOW_CONSUMER')
                if cursor < len(self.events):
                    event = self.events[cursor]
                    cursor += 1
                    yield event
                    if self.scenario.name == 'duplicate':
                        self.authorize(context)
                        yield event
                else:
                    await asyncio.sleep(.005)
        finally:
            self.subscribers -= 1

    async def interrupt_generation(self, context, request):
        self.authorize(context)
        rkey = ('interrupt', request.request_id)
        if rkey in self.receipts:
            prior, result = self.receipts[rkey]
            if prior != request.generation_id: failure('REQUEST_CONFLICT')
            return result
        applied = request.generation_id == self.generation and request.generation_id in self.active
        if applied:
            self.active.remove(request.generation_id)
            accepted = next(value[1] for k, value in self.receipts.items()
                            if k[0] == 'turn' and value[1].generation_id == request.generation_id)
            self._emit(TurnInterrupted(type='turn.interrupted', invalidated_generation_id=request.generation_id,
                                       new_epoch=self.revision), accepted)
        result = Interrupted(applied=applied)
        self.receipts[rkey] = (request.generation_id, result)
        return result

    async def get_snapshot(self, context):
        self.authorize(context)
        return BusinessSnapshot(session_id=context.session_id, state='QA', state_revision=self.revision,
            case_revision=1, knowledge_version='synthetic-v04', generation_id=self.generation,
            last_event_seq=len(self.events), oldest_available_seq=1, operations=list(self.operations.values()))

    async def report_playback(self, context, report):
        self.authorize(context)
        rkey = ('playback', report.report_id)
        content = report.model_dump(mode='json')
        if rkey in self.receipts:
            previous, result = self.receipts[rkey]
            if previous != content: failure('REQUEST_CONFLICT')
            return result
        result = PlaybackReportResult(report_id=report.report_id,
            status='evidence_rejected' if report.evidence_ref else 'observation_recorded',
            reason_code='PROOF_NOT_FOUND' if report.evidence_ref else 'OBSERVATION_ONLY')
        self.receipts[rkey] = (content, result)
        return result

    async def close(self):
        self.closed = True
        pending = list(self.tasks)
        for task in pending: task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
