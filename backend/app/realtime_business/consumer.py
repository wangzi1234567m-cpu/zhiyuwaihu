"""B-side fake realtime consumer, reusable against either public port implementation."""
import asyncio
from uuid import uuid4

from ..realtime_contract.dto import (BusinessTurnInput, EventSubscription,
                                    GenerationInterrupt, TrustedTurnContext)


class FakeRealtimeConsumer:
    def __init__(self, port, context):
        self.port, self.context = port, context
        self.cursor = 0
        self.generation = None
        self.played = []
        self.seen_segments = set()
        self.operations = {}
        self.events = []
        self.task = None
        self.errors = []

    async def connect(self):
        snapshot = await self.port.get_snapshot(self.context)
        self.cursor = snapshot.last_event_seq
        self.generation = snapshot.generation_id
        self.operations = {o.operation_id: o for o in snapshot.operations}
        self.task = asyncio.create_task(self._listen())

    async def _listen(self):
        try:
            async for event in self.port.subscribe_events(self.context, EventSubscription(after_seq=self.cursor)):
                self.consume(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.errors.append(exc)

    def consume(self, event):
        if event.seq <= self.cursor: return
        self.cursor = event.seq
        self.events.append(event)
        payload = event.payload
        if payload.type == 'operation.updated':
            self.operations[payload.result.operation_id] = payload.result
        if (payload.type == 'turn.interrupted' and
            payload.invalidated_generation_id == self.generation):
            self.generation = None
        if (payload.type == 'reply.segment_ready' and event.generation_id == self.generation and
            payload.segment_id not in self.seen_segments):
            self.seen_segments.add(payload.segment_id)
            self.played.append(payload)

    async def prepare(self, text, *, candidate=None):
        snapshot = await self.port.get_snapshot(self.context)
        value = BusinessTurnInput(input_id=uuid4(), text=text, understanding_candidate=candidate)
        context = TrustedTurnContext(**self.context.model_dump(), request_id=uuid4(),
            provider_input_id=str(value.input_id), expected_state_revision=snapshot.state_revision,
            expected_case_revision=snapshot.case_revision, knowledge_version=snapshot.knowledge_version)
        return context, value

    async def submit(self, context, value):
        result = await self.port.submit_turn(context, value)
        self.generation = result.generation_id
        return result

    async def interrupt(self):
        old = self.generation
        self.generation = None  # Stop local output before awaiting business cancellation.
        if old is not None:
            return await self.port.interrupt_generation(self.context,
                GenerationInterrupt(request_id=uuid4(), generation_id=old))

    async def disconnect(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    async def wait_for(self, predicate, *, timeout=5):
        async with asyncio.timeout(timeout):
            while not predicate():
                if self.errors: raise self.errors[0]
                await asyncio.sleep(.005)
