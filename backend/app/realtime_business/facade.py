"""Single-worker, in-process business facade over the existing Coordinator.

The server authenticates before bind()/record_input(); neither is an HTTP API.
All acceptance receipts and input bindings share the Coordinator transaction.
"""
import asyncio
import hashlib
import json
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import select

from ..adapters import DomainError
from ..contracts import AcceptedTurn, Interrupted
from ..coordinator import owned, operation_view
from ..db import Application, Conversation, Operation, RequestReceipt, now
from ..realtime_contract.dto import (BusinessSnapshot, BusinessTurnInput, ContractError,
    PlaybackProgress, PlaybackReportResult, RealtimeBusinessEvent, TrustedSessionContext)
from ..realtime_contract.ports import ContractFailure
from .candidates import grounded_candidate
from .events import RealtimeEventRow


def fail(code, *, revision=None):
    raise ContractFailure(ContractError(code=code, message=code,
        retryable=code in {'BUSY', 'SERVICE_UNAVAILABLE', 'SLOW_CONSUMER'},
        current_state_revision=revision))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def key(row, kind, identity):
    # Fixed size, and namespace-separated from the existing 0.3 receipt keys.
    return 'v04:' + digest([row.owner, row.id, kind, str(identity)])


def find_receipt(db, row, kind, identity, content):
    receipt_key = key(row, kind, identity)
    value_hash = digest(content)
    previous = db.get(RequestReceipt, receipt_key)
    if previous and previous.payload_hash != value_hash:
        fail('REQUEST_CONFLICT')
    return receipt_key, value_hash, previous


@dataclass(frozen=True)
class VerifiedPlaybackProof:
    """Returned only by a server-owned evidence resolver, never constructed from a report."""
    owner: str
    session_id: UUID
    connection_binding: UUID
    generation_id: UUID
    message_id: UUID
    segment_id: UUID
    playback_instance_id: UUID
    text_sha256: str
    case_revision: int
    complete: bool


class Admission:
    def __init__(self, facade, context, user_input):
        self.facade, self.context, self.user_input = facade, context, user_input

    def check(self, db, row):
        f, c, value = self.facade, self.context, self.user_input
        f.authorize(row, c)
        content = {**c.model_dump(mode='json', exclude={'connection_binding'}),
                   'input': value.model_dump(mode='json')}
        self.receipt_key, self.value_hash, previous = find_receipt(db, row, 'turn', c.request_id, content)
        if previous:
            return AcceptedTurn.model_validate(previous.data)
        provider_key = key(row, 'provider', c.provider_input_id)
        provider = db.get(RequestReceipt, provider_key)
        if provider is None or provider.payload_hash != digest(value.model_dump(mode='json')):
            fail('INVALID_INPUT')
        if provider.data.get('request_id'):
            fail('REQUEST_CONFLICT')
        application = db.scalar(select(Application).where(Application.id == row.application_id).with_for_update())
        if application is None or application.owner != row.owner:
            fail('SESSION_NOT_FOUND')
        if (c.expected_state_revision != row.revision or
            c.expected_case_revision != application.data['case_revision'] or
            c.expected_case_revision != row.data['case_revision'] or
            c.knowledge_version != row.data['knowledge_version']):
            fail('REVISION_CONFLICT', revision=row.revision)
        if len(f.coordinator.all_tasks) >= f.max_tasks:
            fail('BUSY')
        if value.understanding_candidate is not None:
            from ..agent.semantic_agent import SemanticAgent
            if not isinstance(f.coordinator.services.agent, SemanticAgent):
                fail('INVALID_INPUT')
            try:
                grounded_candidate(value.understanding_candidate, value.text)
            except ValueError:
                fail('INVALID_INPUT')
        self.provider = provider
        return None

    def record(self, db, row, result, job):
        if self.user_input.understanding_candidate is not None:
            job['candidate'] = self.user_input.understanding_candidate.model_dump(mode='json')
        row.data = {**row.data, 'realtime_job': {**job, 'status': 'queued'},
                    'realtime_failure': 'INTERNAL_ERROR'}
        self.provider.data = {**self.provider.data, 'request_id': str(self.context.request_id)}
        db.add(RequestReceipt(key=self.receipt_key, payload_hash=self.value_hash,
                              data=result.model_dump(mode='json')))


class CoordinatorBusinessFacade:
    def __init__(self, coordinator, *, proof_resolver=None, max_tasks=32, poll_interval=.02):
        self.coordinator = coordinator
        self.repo = coordinator.repo
        self.proof_resolver = proof_resolver or (lambda reference: None)
        self.max_tasks = max_tasks
        self.poll_interval = poll_interval
        self.subscribers = Counter()
        self.recovery_task = None

    def authorize(self, row, context):
        if context.contract_version != '0.4.0':
            fail('CONTRACT_VERSION_UNSUPPORTED')
        if row.owner != context.owner or row.id != str(context.session_id):
            fail('SESSION_NOT_FOUND')
        if (not row.data.get('realtime_enabled') or
            row.data.get('realtime_binding') != str(context.connection_binding) or
            row.data.get('realtime_binding_expires', 0) <= now().timestamp()):
            fail('BINDING_EXPIRED')

    def session(self, db, context):
        try:
            row = owned(db, context.session_id, context.owner, True)
        except DomainError:
            fail('SESSION_NOT_FOUND')
        self.authorize(row, context)
        return row

    def bind(self, owner, session_id, *, ttl_seconds=3600):
        """Authenticated server only; rebind revokes the prior connection immediately."""
        if not 0 < ttl_seconds <= 86400:
            raise ValueError('binding TTL must be in (0, 86400]')
        with self.repo.transaction() as db:
            row = owned(db, session_id, owner, True)
            if not row.data.get('realtime_enabled') and row.data.get('busy'):
                fail('BUSY')
            binding = uuid4()
            row.data = {**row.data, 'realtime_enabled': True,
                        'realtime_binding': str(binding),
                        'realtime_binding_expires': (now() + timedelta(seconds=ttl_seconds)).timestamp()}
        return TrustedSessionContext(owner=owner, session_id=session_id, connection_binding=binding)

    def record_input(self, context, provider_input_id, value: BusinessTurnInput):
        """Server ingress binds an authenticated final provider/text input before C1.

        Media adapters must verify their provider/audio binding before calling this.
        A caller-supplied DTO alone cannot register evidence through the five public ports.
        """
        if not isinstance(provider_input_id, str) or not 0 < len(provider_input_id) <= 200:
            fail('INVALID_INPUT')
        value = BusinessTurnInput.model_validate(value)
        with self.repo.transaction() as db:
            row = self.session(db, context)
            payload = value.model_dump(mode='json')
            rkey, value_hash, previous = find_receipt(db, row, 'provider', provider_input_id, payload)
            ikey, input_hash, input_previous = find_receipt(db, row, 'input', value.input_id,
                                                          {'provider_input_id': provider_input_id})
            if previous: return
            db.add(RequestReceipt(key=rkey, payload_hash=value_hash, data={}))
            if not input_previous:
                db.add(RequestReceipt(key=ikey, payload_hash=input_hash, data={}))

    async def submit_turn(self, context, user_input):
        with self.repo.transaction() as db:
            row = self.session(db, context)
            source = 'text' if row.data.get('mode', 'text') == 'text' else 'realtime'
        try:
            return await self.coordinator.accept(context.owner, str(context.session_id),
                user_input.text, 'v04-' + str(context.request_id), source=source,
                admission=Admission(self, context, user_input))
        except DomainError as exc:
            fail({'NOT_FOUND': 'SESSION_NOT_FOUND', 'IDEMPOTENCY_CONFLICT': 'REQUEST_CONFLICT',
                  'ACTION_IN_PROGRESS': 'BUSY', 'REVISION_CONFLICT': 'REVISION_CONFLICT'
                  }.get(exc.code, 'INVALID_INPUT'))

    async def get_snapshot(self, context):
        with self.repo.transaction() as db:
            row = self.session(db, context)
            application = db.get(Application, row.application_id)
            operations = list(db.scalars(select(Operation).where(Operation.session_id == row.id)))
            linked = db.get(Operation, row.data['linked_operation_id']) if row.data.get('linked_operation_id') else None
            if linked and all(o.id != linked.id for o in operations): operations.append(linked)
            playback = row.data.get('realtime_playback')
            if playback and (playback['generation_id'] != row.data.get('generation_id') or
                             row.data.get('realtime_playback_revoked') == playback['generation_id'] or
                             application.data['case_revision'] != row.data['case_revision']):
                playback = None
            return BusinessSnapshot(session_id=row.id, state=row.state, state_revision=row.revision,
                case_revision=application.data['case_revision'], knowledge_version=row.data['knowledge_version'],
                generation_id=row.data.get('generation_id'), last_event_seq=row.data.get('realtime_seq', 0),
                oldest_available_seq=1, operations=[operation_view(o) for o in operations], playback=playback)

    async def subscribe_events(self, context, subscription):
        sid = str(context.session_id)
        cursor = subscription.after_seq
        with self.repo.transaction() as db:
            row = self.session(db, context)
            end_of_replay = row.data.get('realtime_seq', 0)
            if cursor > end_of_replay: fail('CURSOR_AHEAD')
            if self.subscribers[sid] >= 2: fail('SUBSCRIBER_LIMIT')
            self.subscribers[sid] += 1
        try:
            while True:
                # Poll the durable log: no replay/listen race and no lost-notification gap.
                with self.repo.transaction() as db:
                    row = self.session(db, context)
                    latest = row.data.get('realtime_seq', 0)
                    if latest - max(cursor, end_of_replay) > 128:
                        fail('SLOW_CONSUMER')
                    batch = [RealtimeBusinessEvent.model_validate(e.data) for e in db.scalars(
                        select(RealtimeEventRow).where(RealtimeEventRow.session_id == sid,
                        RealtimeEventRow.seq > cursor).order_by(RealtimeEventRow.seq).limit(128))]
                for event in batch:
                    # Recheck revocation between yields, not only once per batch.
                    with self.repo.transaction() as db:
                        self.session(db, context)
                    cursor = event.seq
                    yield event
                if not batch:
                    await asyncio.sleep(self.poll_interval)
        finally:
            self.subscribers[sid] -= 1
            if not self.subscribers[sid]: del self.subscribers[sid]

    async def interrupt_generation(self, context, request):
        with self.repo.transaction() as db:
            row = self.session(db, context)
            rkey, value_hash, previous = find_receipt(db, row, 'interrupt', request.request_id,
                                                    request.model_dump(mode='json'))
            if previous: return Interrupted.model_validate(previous.data)
            applied = False
            if str(request.generation_id) == row.data.get('generation_id'):
                row.data = {**row.data, 'realtime_playback_revoked': str(request.generation_id),
                            'voice_explanation_verified': False}
                applied = self.coordinator._invalidate(db, row)
            result = Interrupted(applied=applied)
            db.add(RequestReceipt(key=rkey, payload_hash=value_hash, data=result.model_dump()))
            return result

    async def report_playback(self, context, report):
        with self.repo.transaction() as db:
            row = self.session(db, context)
            rkey, value_hash, previous = find_receipt(db, row, 'playback', report.report_id,
                                                     report.model_dump(mode='json'))
            if previous: return PlaybackReportResult.model_validate(previous.data)
            application = db.get(Application, row.application_id)
            reason = 'OBSERVATION_ONLY'
            if (str(report.generation_id) != row.data.get('generation_id') or
                row.data.get('realtime_playback_revoked') == str(report.generation_id) or
                application.data['case_revision'] != row.data['case_revision']):
                reason = 'STALE_GENERATION'
            elif report.evidence_ref is not None:
                proof = self.proof_resolver(report.evidence_ref)
                if proof is None:
                    reason = 'PROOF_NOT_FOUND'
                elif not isinstance(proof, VerifiedPlaybackProof) or any((
                    proof.owner != context.owner, proof.session_id != context.session_id,
                    proof.connection_binding != context.connection_binding,
                    proof.case_revision != application.data['case_revision'],
                    *(getattr(proof, field) != getattr(report, field) for field in (
                        'generation_id', 'message_id', 'segment_id', 'playback_instance_id', 'text_sha256')))):
                    reason = 'PROOF_BINDING_MISMATCH'
                elif not proof.complete or report.observation != 'completed':
                    reason = 'INCOMPLETE_PLAYBACK'
                else:
                    segment = db.scalar(select(RealtimeEventRow).where(
                        RealtimeEventRow.session_id == row.id,
                        RealtimeEventRow.data['payload']['segment_id'].as_string() == str(report.segment_id)))
                    p = segment.data if segment else {}
                    if (p.get('generation_id') != str(report.generation_id) or
                        p.get('payload', {}).get('message_id') != str(report.message_id) or
                        p.get('payload', {}).get('text_sha256') != report.text_sha256):
                        reason = 'PROOF_BINDING_MISMATCH'
                    else:
                        reason = 'SERVER_PROOF_MATCHED'
            status = ('evidence_verified' if reason == 'SERVER_PROOF_MATCHED' else
                      'observation_recorded' if reason == 'OBSERVATION_ONLY' else 'evidence_rejected')
            result = PlaybackReportResult(report_id=report.report_id, status=status, reason_code=reason)
            db.add(RequestReceipt(key=rkey, payload_hash=value_hash, data=result.model_dump(mode='json')))
            if reason == 'SERVER_PROOF_MATCHED':
                prior = row.data.get('realtime_playback') or {}
                same = prior.get('message_id') == str(report.message_id) and prior.get('generation_id') == str(report.generation_id)
                verified = set(prior.get('verified_segment_ids', []) if same else [])
                verified.add(str(report.segment_id))
                complete = db.scalar(select(RealtimeEventRow).where(
                    RealtimeEventRow.session_id == row.id,
                    RealtimeEventRow.data['payload']['type'].as_string() == 'reply.completed',
                    RealtimeEventRow.data['payload']['message_id'].as_string() == str(report.message_id)))
                progress = PlaybackProgress(generation_id=report.generation_id, message_id=report.message_id,
                    verified_segment_ids=sorted(verified), message_fully_notified=bool(
                        complete and complete.data['payload']['segment_count'] == len(verified)))
                # Does not grant a new voice-write path: media trust integration remains A's responsibility.
                row.data = {**row.data, 'realtime_playback': progress.model_dump(mode='json')}
            return result

    async def startup(self):
        await self.coordinator.startup()
        self.recovery_task = asyncio.create_task(self._recover_queued())

    async def _recover_queued(self):
        while True:
            try:
                with self.repo.transaction() as db:
                    for row in db.scalars(select(Conversation).where(
                        Conversation.data['realtime_job']['status'].as_string() == 'queued').with_for_update()):
                        if not row.data.get('busy') or row.id in self.coordinator.tasks:
                            continue
                        if len(self.coordinator.all_tasks) >= self.max_tasks: break
                        application = db.get(Application, row.application_id)
                        if application.owner != row.owner or application.data['case_revision'] != row.data['case_revision']:
                            row.data = {**row.data, 'realtime_failure': 'REVISION_CONFLICT'}
                            self.coordinator._invalidate(db, row, 'failed')
                            continue
                        job = {k: v for k, v in row.data['realtime_job'].items() if k != 'status'}
                        self.coordinator.launch_generation(job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # A transient database outage leaves the durable queue intact.
                logging.getLogger('zhiyu').warning('realtime_recovery_failed kind=%s', type(exc).__name__)
            await asyncio.sleep(.1)

    async def shutdown(self):
        if self.recovery_task:
            self.recovery_task.cancel()
            await asyncio.gather(self.recovery_task, return_exceptions=True)
        await self.coordinator.shutdown()
