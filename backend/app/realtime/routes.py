"""Authenticated signaling/control endpoints. Audio never travels over this socket."""
import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from uuid import UUID
from fastapi import Depends, WebSocket
from pydantic import ValidationError

from ..adapters import DomainError
from .wire import RealtimeOffer, RealtimeAnswer, RealtimeIce, RealtimeControl


def mount_realtime(app, manager, owner, authorize_session, public_origin):
    @app.get('/api/v1/sessions/{sid}/realtime/ice', response_model=RealtimeIce)
    async def ice(sid: UUID, user=Depends(owner)):
        manager.require()
        await authorize_session(user, sid)
        return RealtimeIce(ice_servers=manager.ice_servers(user), expires_at=datetime.now(timezone.utc)+timedelta(seconds=300))

    @app.post('/api/v1/sessions/{sid}/realtime/offer', response_model=RealtimeAnswer)
    async def offer(sid: UUID, body: RealtimeOffer, user=Depends(owner)):
        manager.require()
        await authorize_session(user, sid)
        try:
            return await manager.offer(user, sid, body)
        except (ValueError, TimeoutError):
            raise DomainError('WEBRTC_NEGOTIATION_FAILED', '媒体协商失败或超时', 422) from None

    @app.websocket('/api/v1/sessions/{sid}/realtime/control')
    async def control(sid: UUID, websocket: WebSocket, binding: UUID):
        session = None
        attached_here = False
        workers = []
        try:
            if websocket.headers.get('origin') != public_origin:
                await websocket.close(code=1008)
                return
            user = owner(websocket)
            await authorize_session(user, sid)
            session = manager.bound(user, sid, binding)
            if session.attached:
                await websocket.close(code=1008)
                return
            session.attached = True
            attached_here = True
            await websocket.accept()

            async def send():
                while not session.closed:
                    try:
                        message = await asyncio.wait_for(session.notices.get(), 1)
                    except TimeoutError:
                        continue
                    async with asyncio.timeout(2):
                        await websocket.send_json(message)

            async def receive():
                count, window = 0, asyncio.get_running_loop().time()
                while not session.closed:
                    try:
                        packet = await asyncio.wait_for(websocket.receive_text(), 1)
                    except TimeoutError:
                        continue
                    if len(packet) > 4096:
                        raise ValueError('control frame too large')
                    now = asyncio.get_running_loop().time()
                    if now-window >= 1:
                        count, window = 0, now
                    count += 1
                    if count > 40:
                        raise ValueError('control rate exceeded')
                    manager.bound(user, sid, binding)
                    command = RealtimeControl.model_validate_json(packet)
                    await session.control(command)

            workers = [asyncio.create_task(send()), asyncio.create_task(receive())]
            done, _ = await asyncio.wait(workers, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except (DomainError, ValueError, ValidationError):
            pass
        except Exception:
            # Never return raw SDK messages, cookies or provider details.
            pass
        finally:
            for task in workers:
                task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            # An unauthorized/repeated attach must not close the legitimate connection.
            if session and attached_here:
                await session.close()
            with suppress(Exception):
                await websocket.close()
