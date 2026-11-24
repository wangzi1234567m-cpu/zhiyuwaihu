import asyncio
from uuid import uuid4

import pytest

from app.realtime_business.consumer import FakeRealtimeConsumer
from app.realtime_business.fixtures import Scenario, ScenarioBusiness
from app.realtime_contract.dto import EventSubscription, PlaybackReport
from app.realtime_contract.ports import ContractFailure


@pytest.fixture
async def make_pair():
    pairs = []
    def make(name, delay=.06):
        port = ScenarioBusiness(Scenario(name, delay_seconds=delay))
        consumer = FakeRealtimeConsumer(port, port.context)
        pairs.append((port, consumer))
        return port, consumer
    yield make
    for port, consumer in pairs:
        await consumer.disconnect()
        await port.close()


@pytest.mark.parametrize('scenario', ['normal', 'slow', 'duplicate'])
async def test_independent_segments_and_request_deduplication(make_pair, scenario):
    port, consumer = make_pair(scenario)
    await consumer.connect()
    context, value = await consumer.prepare('合成输入')
    accepted = await consumer.submit(context, value)
    await port.entered.wait()
    assert len(consumer.played) == 0  # acceptance did not await the answer
    assert await consumer.submit(context, value) == accepted
    with pytest.raises(ContractFailure, match='REQUEST_CONFLICT'):
        await port.submit_turn(context, value.model_copy(update={'text': '不同内容'}))
    await consumer.wait_for(lambda: len(consumer.played) == 1)
    assert not any(e.payload.type == 'reply.completed' for e in consumer.events)
    await consumer.wait_for(lambda: any(e.payload.type == 'reply.completed' for e in consumer.events))
    assert len(consumer.played) == 2
    assert len(port.providers) == 1


async def test_slow_work_does_not_block_input_event_loop(make_pair):
    port, consumer = make_pair('slow', delay=.1)
    await consumer.connect()
    context, value = await consumer.prepare('合成输入')
    await consumer.submit(context, value)
    ticks = 0
    while not consumer.played:
        ticks += 1
        await asyncio.sleep(.005)
    assert ticks >= 5


async def test_timeout_keeps_original_operation_unknown(make_pair):
    port, consumer = make_pair('timeout')
    await consumer.connect()
    context, value = await consumer.prepare('合成输入')
    await consumer.submit(context, value)
    await consumer.wait_for(lambda: any(e.payload.type == 'turn.failed' for e in consumer.events))
    assert not consumer.played
    assert [o.status for o in consumer.operations.values()] == ['unknown']
    assert port.effect_count == 0


@pytest.mark.parametrize('scenario', ['late', 'interrupt'])
async def test_interrupted_or_late_generation_never_plays(make_pair, scenario):
    port, consumer = make_pair(scenario)
    await consumer.connect()
    context, value = await consumer.prepare('第一轮')
    first = await consumer.submit(context, value)
    await port.entered.wait()
    assert (await consumer.interrupt()).applied
    context2, value2 = await consumer.prepare('第二轮')
    await consumer.submit(context2, value2)
    await consumer.wait_for(lambda: len(consumer.played) == 2)
    played_ids = {p.segment_id for p in consumer.played}
    assert not any(e.payload.type == 'reply.segment_ready' and e.generation_id == first.generation_id
                   and e.payload.segment_id in played_ids for e in port.events)
    if scenario == 'late':
        assert any(e.payload.type == 'reply.segment_ready' and e.generation_id == first.generation_id for e in port.events)


async def test_disconnect_restores_facts_without_replay_or_redispatch(make_pair):
    port, consumer = make_pair('disconnect')
    await consumer.connect()
    context, value = await consumer.prepare('合成操作')
    await consumer.submit(context, value)
    await consumer.disconnect()
    await asyncio.gather(*port.tasks)
    await consumer.connect()
    assert len(consumer.operations) == 1
    assert next(iter(consumer.operations.values())).status == 'succeeded'
    assert port.effect_count == 1 and not consumer.played
    assert await port.submit_turn(context, value)
    assert port.effect_count == 1


async def test_client_playback_observation_cannot_authorize(make_pair):
    port, consumer = make_pair('normal')
    report = PlaybackReport(report_id=uuid4(), generation_id=uuid4(), message_id=uuid4(),
        segment_id=uuid4(), playback_instance_id=uuid4(), text_sha256='0'*64,
        observation='completed', clock_domain='browser.performance')
    assert (await port.report_playback(port.context, report)).status == 'observation_recorded'
    forged = report.model_copy(update={'report_id': uuid4(), 'evidence_ref': uuid4()})
    assert (await port.report_playback(port.context, forged)).status == 'evidence_rejected'


async def test_owner_revision_provider_and_cursor_boundaries(make_pair):
    port, consumer = make_pair('normal')
    context, value = await consumer.prepare('合成输入')
    with pytest.raises(ContractFailure, match='SESSION_NOT_FOUND'):
        await port.submit_turn(context.model_copy(update={'owner': 'other'}), value)
    with pytest.raises(ContractFailure, match='REVISION_CONFLICT'):
        await port.submit_turn(context.model_copy(update={'expected_case_revision': 2}), value)
    await port.submit_turn(context, value)
    with pytest.raises(ContractFailure, match='REQUEST_CONFLICT'):
        await port.submit_turn(context.model_copy(update={'request_id': uuid4()}), value)
    stream = port.subscribe_events(port.context, EventSubscription(after_seq=999))
    with pytest.raises(ContractFailure, match='CURSOR_AHEAD'):
        await anext(stream)
