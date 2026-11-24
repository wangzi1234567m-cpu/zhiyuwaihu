"""Server-only WebRTC ingress connecting the two v0.4 modules.

No client endpoint accepts trusted contexts, provider inputs or opening commands.
Playback remains observation-only; this adapter never grants voice write permission.
"""
from ..contracts import AcceptedTurn
from ..db import Application, RequestReceipt
from .facade import CoordinatorBusinessFacade, find_receipt, fail


class OpeningAdmission:
    def __init__(self, facade, context):
        self.facade, self.context = facade, context

    def check(self, db, row):
        self.facade.authorize(row, self.context)
        self.receipt_key, self.value_hash, previous = find_receipt(
            db, row, 'opening', row.id, {'session_id': row.id})
        if previous:
            return AcceptedTurn.model_validate(previous.data)
        application = db.get(Application, row.application_id)
        if application.data['case_revision'] != row.data['case_revision']:
            fail('REVISION_CONFLICT', revision=row.revision)
        if len(self.facade.coordinator.all_tasks) >= self.facade.max_tasks:
            fail('BUSY')

    def record(self, db, row, result, job):
        row.data = {**row.data, 'realtime_job': {**job, 'status': 'queued'},
                    'realtime_failure': 'INTERNAL_ERROR'}
        db.add(RequestReceipt(key=self.receipt_key, payload_hash=self.value_hash,
                              data=result.model_dump(mode='json')))


class WebRTCBusinessAdapter(CoordinatorBusinessFacade):
    async def open_connection(self, owner, session_id):
        return self.bind(owner, session_id)

    async def start_opening(self, context):
        snapshot = await self.get_snapshot(context)
        if snapshot.state != 'CASE_READY':
            return None  # Reconnect never replays an old opening.
        return await self.coordinator.accept(context.owner, str(context.session_id),
            '__opening__', 'v04-opening-' + str(context.session_id),
            start_revision=snapshot.state_revision, source='realtime',
            admission=OpeningAdmission(self, context))

    async def submit_turn(self, context, user_input):
        # Only RealtimeSession calls this after its provider item/epoch validation.
        self.record_input(context, context.provider_input_id, user_input)
        return await super().submit_turn(context, user_input)
