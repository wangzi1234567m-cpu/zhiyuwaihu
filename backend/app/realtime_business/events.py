"""Project committed Coordinator facts into a separate, durable v0.4 log.

Called inside the same transaction as the original event and state change.
Legacy token deltas are deliberately not published as approved speech.
"""
import hashlib
from uuid import uuid4

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, Message, now
from ..realtime_contract.dto import (ContractError, RealtimeBusinessEvent,
    ReplyCompleted, ReplySegmentReady, TurnFailed, WritebackCompleted)


class RealtimeEventRow(Base):
    __tablename__ = 'realtime_events'
    __table_args__ = (UniqueConstraint('session_id', 'seq'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey('sessions.id'), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSONB)


def append_event(db, row, payload, *, turn=None, generation=None, operation=None):
    seq = row.data.get('realtime_seq', 0) + 1
    event = RealtimeBusinessEvent(
        event_id=uuid4(), session_id=row.id,
        turn_id=turn or row.data['turn_id'],
        generation_id=generation or row.data['generation_id'],
        operation_id=operation, seq=seq, state_revision=row.revision,
        occurred_at=now(), knowledge_version=row.data['knowledge_version'], payload=payload)
    row.data = {**row.data, 'realtime_seq': seq}
    db.add(RealtimeEventRow(id=str(event.event_id), session_id=row.id, seq=seq,
                           data=event.model_dump(mode='json')))
    return event


def project_event(db, row, event):
    if not row.data.get('realtime_enabled'):
        return
    payload = event.payload
    kind = payload.type
    binding = dict(turn=event.turn_id, generation=event.generation_id,
                   operation=event.operation_id)
    if kind == 'operation.updated':
        # A dispatched operation belongs to its original generation, even after interruption.
        origin = row.data.get('realtime_operation_origins', {}).get(str(event.operation_id))
        if origin:
            binding.update(turn=origin['turn'], generation=origin['generation'])
        else:
            origins = dict(row.data.get('realtime_operation_origins', {}))
            origins[str(event.operation_id)] = {'turn': row.data['turn_id'],
                                               'generation': row.data['generation_id']}
            row.data = {**row.data, 'realtime_operation_origins': origins}
    if kind == 'generation.finished' and payload.status == 'completed':
        message = db.get(Message, str(payload.message_id))
        text = message.data['view']['text'] if message else ''
        if text and len(text) <= 2000:
            from ..realtime.reply_strategy import semantic_segments
            parts=semantic_segments(text) if row.data.get('realtime_segmented_replies') else [text]
            for index,part in enumerate(parts):
                append_event(db, row, ReplySegmentReady(message_id=payload.message_id,
                    segment_id=uuid4(), segment_index=index, text=part,
                    text_sha256=hashlib.sha256(part.encode('utf-8')).hexdigest()), **binding)
            append_event(db, row, ReplyCompleted(message_id=payload.message_id,
                                                segment_count=len(parts)), **binding)
        else:
            append_event(db, row, TurnFailed(error=ContractError(code='INVALID_INPUT',
                message='Reviewed answer is empty or exceeds the segment limit', retryable=False)), **binding)
    if kind == 'generation.finished' and payload.status == 'failed':
        code = row.data.get('realtime_failure', 'INTERNAL_ERROR')
        append_event(db, row, TurnFailed(error=ContractError(code=code,
            message='Business generation did not complete', retryable=False)), **binding)
    if kind in ('turn.started', 'turn.interrupted', 'generation.finished', 'operation.updated'):
        append_event(db, row, payload, **binding)


def publish_writeback(db, row, record):
    # An opening failure can end a session before any business turn exists.
    # Persist its unknown-contact record without inventing a turn/generation binding.
    if not row.data.get('realtime_enabled') or not row.data.get('turn_id') or not row.data.get('generation_id'):
        return
    identity = f'{record.record_id}:{record.record_revision}'
    if row.data.get('realtime_writeback') == identity:
        return
    append_event(db, row, WritebackCompleted(record_id=record.record_id,
                                            record_revision=record.record_revision))
    row.data = {**row.data, 'realtime_writeback': identity}
