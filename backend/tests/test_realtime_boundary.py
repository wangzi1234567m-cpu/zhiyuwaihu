"""Signaling authentication and registry tests; business boundary fixture is test-only."""
import asyncio
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, Request, WebSocket
from fastapi.testclient import TestClient
from app.adapters import DomainError
from app.config import settings
from app.main import create_app
from app.realtime.manager import RealtimeManager
from app.realtime.routes import mount_realtime
from app.realtime.wire import RealtimeOffer
from test_realtime_runtime import BoundaryPort, MediaBoundary, InputBoundary, SpeechBoundary


class NegotiationBoundary(MediaBoundary):
    def __init__(self, session, ice): super().__init__(); self.session = session
    async def answer(self, sdp): return 'v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n'


async def test_default_application_keeps_new_mode_disabled_without_database_or_rtc_import():
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        await client.post('/api/v1/dev/login', json={'username':'demo','password':'demo-local-only'}, headers={'X-Requested-With':'zhiyu'})
        caps = (await client.get('/api/v1/capabilities')).json()
        assert caps['realtime_webrtc'] is False
        response = await client.post(f'/api/v1/sessions/{uuid4()}/realtime/offer',
            json={'type':'offer','sdp':'v=0','expected_state_revision':0}, headers={'X-Requested-With':'zhiyu'})
        assert response.status_code == 503


async def test_write_switch_and_missing_business_port_prevent_readiness():
    assert RealtimeManager(replace(settings, realtime_enabled=True)).unavailable_reason()
    manager = RealtimeManager(replace(settings, realtime_enabled=True, voice_writes_enabled=True),
        BoundaryPort(), InputBoundary, SpeechBoundary, NegotiationBoundary)
    assert '写入' in manager.unavailable_reason()


async def test_duplicate_offers_and_stale_revision_leave_no_orphan_resources():
    port = BoundaryPort()
    manager = RealtimeManager(replace(settings, realtime_enabled=True, voice_writes_enabled=False),
        port, InputBoundary, SpeechBoundary, NegotiationBoundary)
    try:
        with pytest.raises(DomainError, match='刷新'):
            await manager.offer('demo', port.sid, RealtimeOffer(sdp='v=0', expected_state_revision=0))
        assert not manager.sessions
        body = RealtimeOffer(sdp='v=0', expected_state_revision=1)
        answer = await manager.offer('demo', port.sid, body)
        with pytest.raises(DomainError): await manager.offer('demo', port.sid, body)
        with pytest.raises(DomainError): manager.bound('other', port.sid, answer.connection_binding)
        with pytest.raises(DomainError): manager.bound('demo', port.sid, uuid4())
        assert len(manager.sessions) == 1
    finally: await manager.close()
    assert not manager.sessions


def test_turn_credentials_are_short_lived_and_never_reveal_signing_secret():
    config = replace(settings, realtime_turn_urls='["turn:relay.example:3478"]', realtime_turn_secret='test-only-signing-secret')
    server = RealtimeManager(config).ice_servers('demo')[0]
    assert server.username and server.credential and config.realtime_turn_secret not in server.model_dump_json()
    assert int(server.username.split(':')[0]) > 0


def test_control_rejects_wrong_origin_before_authentication_or_database():
    app = FastAPI()
    def owner(connection): raise AssertionError('must reject origin first')
    async def authorize(*args): raise AssertionError('must reject origin first')
    mount_realtime(app, RealtimeManager(settings), owner, authorize, 'http://allowed')
    with TestClient(app) as client:
        from starlette.websockets import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f'/api/v1/sessions/{uuid4()}/realtime/control?binding={uuid4()}', headers={'origin':'http://wrong'}):
                pass
