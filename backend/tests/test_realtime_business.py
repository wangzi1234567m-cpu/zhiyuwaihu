"""Facade behavior against real PostgreSQL and the existing Coordinator."""
import asyncio
from uuid import uuid4
import pytest
from sqlalchemy import select, func
from test_integration import setup, opened, wait_idle, pytestmark
from app.db import Session, Effect, Conversation
from app.realtime_business.facade import CoordinatorBusinessFacade
from app.realtime_business.consumer import FakeRealtimeConsumer
from app.realtime_contract.dto import EventSubscription, GenerationInterrupt, PlaybackReport
from app.realtime_contract.ports import ContractFailure


async def pair(setup):
    client, app, services = setup
    sid = await opened(client)
    facade = CoordinatorBusinessFacade(app.state.coordinator)
    context = facade.bind('demo', sid)
    return facade, FakeRealtimeConsumer(facade, context)


async def submit(facade, consumer, text):
    context, value = await consumer.prepare(text)
    facade.record_input(consumer.context, context.provider_input_id, value)
    result = await consumer.submit(context, value)
    async with asyncio.timeout(6):
        while True:
            with Session() as db:
                if not db.get(Conversation, str(context.session_id)).data['busy']: break
            await asyncio.sleep(.01)
    return context, value, result


async def test_facade_closure_dedup_and_durable_replay(setup):
    facade, consumer = await pair(setup)
    await submit(facade, consumer, '我是演示商家1')
    await submit(facade, consumer, '补齐材料一定必过吗')
    context, value, accepted = await submit(facade, consumer, '明白了')
    async with asyncio.timeout(8):
        while (await facade.get_snapshot(consumer.context)).state != 'CLOSED':
            await asyncio.sleep(.02)
    restarted = CoordinatorBusinessFacade(facade.coordinator)
    assert await restarted.submit_turn(context, value) == accepted
    snap = await facade.get_snapshot(consumer.context)
    stream = facade.subscribe_events(consumer.context, EventSubscription())
    events = [await anext(stream) for _ in range(snap.last_event_seq)]
    await stream.aclose()
    assert [e.seq for e in events] == list(range(1, snap.last_event_seq+1))
    assert any(e.payload.type == 'writeback.completed' for e in events)
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Effect)) == 1


async def test_facade_input_revision_binding_and_playback_guards(setup):
    facade, consumer = await pair(setup)
    context, value = await consumer.prepare('我是演示商家1')
    with pytest.raises(ContractFailure, match='INVALID_INPUT'):
        await facade.submit_turn(context, value)
    facade.record_input(consumer.context, context.provider_input_id, value)
    with pytest.raises(ContractFailure, match='REVISION_CONFLICT'):
        await facade.submit_turn(context.model_copy(update={'expected_case_revision': 99}), value)
    await consumer.submit(context, value)
    with pytest.raises(ContractFailure, match='REQUEST_CONFLICT'):
        await facade.submit_turn(context.model_copy(update={'request_id': uuid4()}), value)
    report = PlaybackReport(report_id=uuid4(), generation_id=consumer.generation,
        message_id=uuid4(), segment_id=uuid4(), playback_instance_id=uuid4(),
        text_sha256='0'*64, observation='completed', clock_domain='browser.performance')
    assert (await facade.report_playback(consumer.context, report)).status == 'observation_recorded'
    assert (await facade.get_snapshot(consumer.context)).playback is None
    rebound = facade.bind('demo', context.session_id)
    with pytest.raises(ContractFailure, match='BINDING_EXPIRED'):
        await facade.get_snapshot(consumer.context)
    assert await facade.get_snapshot(rebound)


async def test_cancel_late_result_and_durable_queued_job(setup, monkeypatch):
    facade, consumer = await pair(setup)
    coordinator = facade.coordinator
    original = coordinator.launch_generation
    def unavailable(job):
        raise RuntimeError('scheduling unavailable')
    monkeypatch.setattr(coordinator, 'launch_generation', unavailable)
    context, value = await consumer.prepare('我是演示商家1')
    facade.record_input(consumer.context, context.provider_input_id, value)
    accepted = await consumer.submit(context, value)
    with Session() as db:
        assert db.get(Conversation, str(context.session_id)).data['realtime_job']['status'] == 'queued'
    request = GenerationInterrupt(request_id=uuid4(), generation_id=accepted.generation_id)
    assert (await facade.interrupt_generation(consumer.context, request)).applied
    assert (await facade.interrupt_generation(consumer.context, request)).applied
    monkeypatch.setattr(coordinator, 'launch_generation', original)
    await submit(facade, consumer, '我是演示商家1')
    assert not (await facade.interrupt_generation(consumer.context,
        GenerationInterrupt(request_id=uuid4(), generation_id=accepted.generation_id))).applied
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Effect)) == 0


async def test_queued_job_recovers_without_duplicate_acceptance(setup, monkeypatch):
    facade, consumer = await pair(setup)
    coordinator = facade.coordinator
    original = coordinator.launch_generation
    def unavailable(job): raise RuntimeError('scheduling unavailable')
    monkeypatch.setattr(coordinator, 'launch_generation', unavailable)
    context, value = await consumer.prepare('我是演示商家1')
    facade.record_input(consumer.context, context.provider_input_id, value)
    accepted = await facade.submit_turn(context, value)
    monkeypatch.setattr(coordinator, 'launch_generation', original)
    recovery = asyncio.create_task(facade._recover_queued())
    try:
        async with asyncio.timeout(5):
            while True:
                with Session() as db:
                    if not db.get(Conversation, str(context.session_id)).data['busy']: break
                await asyncio.sleep(.01)
        assert await facade.submit_turn(context, value) == accepted
    finally:
        recovery.cancel()
        await asyncio.gather(recovery, return_exceptions=True)


async def test_candidate_uses_local_decision_without_understanding_model(setup):
    from app.realtime_contract.dto import UnderstandingCandidate
    facade, consumer = await pair(setup)
    await submit(facade, consumer, '我是演示商家1')
    class ForbiddenModel:
        async def understand(self, **kwargs):
            raise AssertionError('second understanding call forbidden')
    facade.coordinator.services.agent.understanding = ForbiddenModel()
    context, value = await consumer.prepare('补齐材料一定必过吗',
        candidate=UnderstandingCandidate(intents=['ask_guarantee']))
    facade.record_input(consumer.context, context.provider_input_id, value)
    await consumer.submit(context, value)
    async with asyncio.timeout(5):
        while True:
            with Session() as db:
                row = db.get(Conversation, str(context.session_id))
                if not row.data['busy']:
                    assert row.state == 'QA'
                    break
            await asyncio.sleep(.01)


async def test_parallel_accept_event_receive_and_interrupt_during_held_understanding(setup):
    """Causal gates prove overlap; acceptance cannot wait for business completion."""
    facade, consumer = await pair(setup)
    entered, release = asyncio.Event(), asyncio.Event()
    original = facade.coordinator.services.agent
    class HeldAgent:
        async def handle_turn(self, *args):
            entered.set()
            await release.wait()
            # Simulate an upstream that returns late despite cancellation.
            return await original.handle_turn(*args[:-1], asyncio.Event())
    facade.coordinator.services.agent = HeldAgent()
    await consumer.connect()
    try:
        context, value = await consumer.prepare('我是演示商家1')
        facade.record_input(consumer.context, context.provider_input_id, value)
        accepted = await asyncio.wait_for(consumer.submit(context, value), 1)
        await asyncio.wait_for(entered.wait(), 1)
        assert not release.is_set()
        # Model/audio receive loops are represented by an independent input heartbeat.
        received = []
        async def receiver():
            for i in range(10):
                received.append(i)
                await asyncio.sleep(.005)
        await asyncio.wait_for(receiver(), 1)
        await consumer.wait_for(lambda: any(e.payload.type == 'turn.started' for e in consumer.events))
        assert len(received) == 10 and not consumer.played and not release.is_set()
        assert (await asyncio.wait_for(consumer.interrupt(), 1)).applied
        # A new input is accepted and answered before the old understanding is released.
        facade.coordinator.services.agent = original
        await submit(facade, consumer, '补齐材料一定必过吗')
        await consumer.wait_for(lambda: bool(consumer.played))
        assert not release.is_set()
        release.set()
        await asyncio.gather(*list(facade.coordinator.all_tasks))
        assert not any(e.generation_id == accepted.generation_id and
                       e.payload.type == 'reply.segment_ready' for e in consumer.events)
    finally:
        release.set()
        await consumer.disconnect()


async def test_parallel_reply_delivery_before_held_operation_and_writeback(setup):
    """Playback-ready output is consumed while independent business I/O is pending."""
    facade, consumer = await pair(setup)
    await submit(facade, consumer, '我是演示商家1')
    business = facade.coordinator.services.business
    execute_entered, execute_release = asyncio.Event(), asyncio.Event()
    writeback_entered, writeback_release = asyncio.Event(), asyncio.Event()
    class HeldBusiness:
        async def execute(self, command):
            execute_entered.set()
            await execute_release.wait()
            return await business.execute(command)
        async def query_operation(self, command):
            return await business.query_operation(command)
        async def writeback(self, record):
            writeback_entered.set()
            await writeback_release.wait()
            return await business.writeback(record)
    facade.coordinator.services.business = HeldBusiness()
    await consumer.connect()
    try:
        context, value, accepted = await submit(facade, consumer, '明白了')
        await asyncio.wait_for(execute_entered.wait(), 2)
        await consumer.wait_for(lambda: any(e.generation_id == accepted.generation_id and
            e.payload.type == 'reply.completed' for e in consumer.events))
        assert consumer.played and not execute_release.is_set()
        # Snapshot and cancellation remain callable while a dispatched operation waits.
        assert (await asyncio.wait_for(facade.get_snapshot(consumer.context), 1)).operations
        await asyncio.wait_for(consumer.interrupt(), 1)
        execute_release.set()
        await asyncio.wait_for(writeback_entered.wait(), 2)
        await consumer.wait_for(lambda: any(o.status == 'succeeded' for o in consumer.operations.values()))
        assert not writeback_release.is_set()
        with Session() as db:
            assert db.scalar(select(func.count()).select_from(Effect)) == 1
        # Disconnect does not cancel the accepted operation or its writeback.
        await consumer.disconnect()
        writeback_release.set()
        async with asyncio.timeout(4):
            while (await facade.get_snapshot(consumer.context)).state != 'CLOSED':
                await asyncio.sleep(.01)
        await consumer.connect()
        assert len(consumer.operations) == 1
        assert next(iter(consumer.operations.values())).status == 'succeeded'
    finally:
        execute_release.set()
        writeback_release.set()
        await consumer.disconnect()
