"""原生审查独立边界：挂起回写、远端成功丢响应、最终派发时版本竞态。"""
import asyncio

import httpx
import pytest
from sqlalchemy import func, select

from test_review_regressions import dedicated_db
from test_integration import opened, say
from app.adapters import DomainError, MockBusiness, Services
from app.coordinator import Coordinator
from app.db import Application, Effect, Operation, Session
from app.main import create_app
from app.seed import fixture_id


async def stop_operation_worker(coordinator):
    coordinator.operation_worker.cancel()
    await asyncio.gather(coordinator.operation_worker, return_exceptions=True)


async def prepare_operation(client):
    assert (await client.post('/api/v1/dev/login', json={'username':'demo','password':'demo-local-only'})).status_code == 200
    sid = await opened(client)
    await say(client, sid, '我是演示商家1')
    await say(client, sid, '明白了')
    with Session() as db:
        return sid, db.scalar(select(Operation.id).where(Operation.session_id == sid))


@pytest.mark.asyncio
async def test_hanging_writeback_has_bounded_timeout_and_retains_effect(dedicated_db):
    class HangingWriteback(MockBusiness):
        async def writeback(self, record):
            await asyncio.Event().wait()
    app = create_app(Services(business=HangingWriteback()))
    async with app.router.lifespan_context(app):
        coordinator = app.state.coordinator
        await stop_operation_worker(coordinator)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test', headers={'X-Requested-With':'zhiyu'}) as client:
            sid, oid = await prepare_operation(client)
            assert oid
            await asyncio.wait_for(coordinator.process_operation(oid), timeout=4.2)
            with Session() as db:
                op = db.get(Operation, oid)
                assert op.status == 'succeeded'
                assert op.data.get('writeback_attempts') == 1
                assert op.data.get('writeback_done') is False
                assert db.scalar(select(func.count()).select_from(Effect)) == 1
            value = (await client.get(f'/api/v1/sessions/{sid}')).json()
            assert value['state'] == 'WRITEBACK_PENDING' and value['record'] is None


@pytest.mark.asyncio
async def test_ambiguous_writeback_reuses_exact_record_across_coordinator_restart(dedicated_db):
    class RemoteWriteback(MockBusiness):
        def __init__(self):
            super().__init__()
            self.payloads = []
            self.remote_records = {}
        async def writeback(self, record):
            payload = record.model_dump(mode='json')
            self.payloads.append(payload)
            self.remote_records.setdefault(str(record.record_id), payload)
            if len(self.payloads) == 1:
                raise DomainError('RESPONSE_LOST', '合成远端已提交但响应丢失', 503)
            await super().writeback(record)
    business = RemoteWriteback()
    services = Services(business=business)
    app = create_app(services)
    async with app.router.lifespan_context(app):
        coordinator = app.state.coordinator
        await stop_operation_worker(coordinator)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test', headers={'X-Requested-With':'zhiyu'}) as client:
            sid, oid = await prepare_operation(client)
            await coordinator.process_operation(oid)
            restarted = Coordinator(services)
            await restarted.process_operation(oid)
            assert len(business.payloads) == 2
            assert business.payloads[0] == business.payloads[1], 'Retry changed writeback identity or content'
            assert len(business.remote_records) == 1
            value = (await client.get(f'/api/v1/sessions/{sid}')).json()
            assert value['state'] == 'CLOSED'
            assert value['record']['record_id'] == business.payloads[0]['record_id']
            with Session() as db:
                assert db.scalar(select(func.count()).select_from(Effect)) == 1


@pytest.mark.asyncio
async def test_final_enqueue_revision_race_rolls_back_completion_and_marks_failed(dedicated_db):
    """检查审查中的竞态推断：事务应回滚，旧消息不能被标为completed。"""
    app = create_app(Services())
    async with app.router.lifespan_context(app):
        coordinator = app.state.coordinator
        await stop_operation_worker(coordinator)
        original = coordinator.enqueue
        called = []
        def concurrent_revision(db, row, case, decision, entries):
            with Session.begin() as other:
                application = other.get(Application, fixture_id(1))
                application.data = {**application.data, 'case_revision':2}
            called.append(True)
            return original(db, row, case, decision, entries)
        coordinator.enqueue = concurrent_revision
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test', headers={'X-Requested-With':'zhiyu'}) as client:
            await client.post('/api/v1/dev/login', json={'username':'demo','password':'demo-local-only'})
            sid = await opened(client)
            await say(client, sid, '我是演示商家1')
            value = await say(client, sid, '明白了')
            assert called
            assert value['state'] not in ('ACTION_READY','EXECUTING','CLOSED')
            assert not value['busy'] and not value['operations']
            assert value['messages'][-1]['status'] == 'failed'
            with Session() as db:
                assert db.scalar(select(func.count()).select_from(Effect)) == 0
