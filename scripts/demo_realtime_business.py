"""Run all seven shared scenarios without a database, cloud account or media SDK."""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.realtime_business.consumer import FakeRealtimeConsumer
from app.realtime_business.fixtures import SCENARIOS, Scenario, ScenarioBusiness


async def run(name, delay):
    port = ScenarioBusiness(Scenario(name, delay_seconds=delay))
    consumer = FakeRealtimeConsumer(port, port.context)
    try:
        await consumer.connect()
        context, value = await consumer.prepare('这是合成测试输入')
        accepted = await consumer.submit(context, value)
        if name == 'duplicate':
            assert await consumer.submit(context, value) == accepted
        if name in ('late', 'interrupt'):
            await port.entered.wait()
            await consumer.interrupt()
        if name == 'disconnect':
            await consumer.disconnect()
        await asyncio.gather(*list(port.tasks))
        if name == 'disconnect':
            await consumer.connect()
        else:
            await consumer.wait_for(lambda: consumer.cursor == len(port.events))
        expected_segments = 0 if name in ('late', 'interrupt', 'timeout', 'disconnect') else 2
        assert len(consumer.played) == expected_segments
        assert not consumer.errors
        return {'scenario': name, 'events': len(port.events), 'played_segments': len(consumer.played),
                'operations': [o.status for o in consumer.operations.values()],
                'synthetic_effects': port.effect_count, 'passed': True}
    finally:
        await consumer.disconnect()
        await port.close()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scenario', choices=(*SCENARIOS, 'all'), default='all')
    parser.add_argument('--delay', type=float, default=3.0)
    args = parser.parse_args()
    for name in SCENARIOS if args.scenario == 'all' else (args.scenario,):
        print(json.dumps(await run(name, args.delay), ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
