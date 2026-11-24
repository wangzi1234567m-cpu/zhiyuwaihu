"""Opt-in session registry and short-lived ICE credentials; no business implementation."""
import asyncio
import base64
import hashlib
import hmac
import importlib.util
import json
import time
from collections import deque
from datetime import datetime, timedelta, timezone

from ..adapters import DomainError
from .session import RealtimeSession
from .wire import IceServer


class RealtimeManager:
    def __init__(self, config, business=None, input_factory=None, speech_factory=None, media_factory=None):
        self.config, self.business = config, business
        self.sessions, self.offer_lock = {}, asyncio.Lock()
        self.completed = deque(maxlen=20)
        self.custom_factories = input_factory is not None and speech_factory is not None
        if input_factory is None:
            from .provider import NativeInput
            from ..cloud_budget import reserve_call
            input_factory = lambda: NativeInput(config, reserve_call)
        if speech_factory is None:
            from .speech import StreamingSpeech
            from ..cloud_budget import reserve_call
            speech_factory = lambda: StreamingSpeech(config, reserve_call)
        self.input_factory, self.speech_factory, self.media_factory = input_factory, speech_factory, media_factory

    def unavailable_reason(self):
        c = self.config
        if not c.realtime_enabled:
            return '实时WebRTC模式未启用'
        if self.business is None:
            return '等待业务公共接口或队友Mock注入'
        if c.voice_writes_enabled:
            return '独立WebRTC尚无可信播放凭据，必须关闭语音业务写入'
        if self.media_factory is None and any(importlib.util.find_spec(m) is None for m in ('aiortc', 'av')):
            return '未安装可选WebRTC依赖'
        if not self.custom_factories:
            try:
                provider = self.input_factory()
                provider.require()
                from ..tts import QwenTTS
                QwenTTS(c).require()
            except Exception:
                return '原生理解或流式TTS尚未配置'
        try:
            self.ice_servers('capability')
        except (ValueError, TypeError):
            return 'ICE配置不正确'
        return None

    def require(self):
        reason = self.unavailable_reason()
        if reason:
            raise DomainError('REALTIME_NOT_CONFIGURED', reason, 503)

    def allowed_application(self, application_id):
        return str(application_id) in {v.strip() for v in self.config.realtime_application_ids.split(',') if v.strip()}

    def ice_servers(self, owner):
        c = self.config
        servers = []
        stun_urls = json.loads(c.realtime_stun_urls)
        if not isinstance(stun_urls, list) or len(stun_urls) > 8:
            raise ValueError('invalid STUN list')
        for url in stun_urls:
            if not isinstance(url, str) or not url.startswith(('stun:', 'stuns:')):
                raise ValueError('invalid STUN URL')
            servers.append(IceServer(urls=[url]))
        if c.realtime_turn_urls:
            urls = json.loads(c.realtime_turn_urls)
            if not isinstance(urls, list) or not urls or len(urls)>8 or not c.realtime_turn_secret or any(not isinstance(u,str) or not u.startswith(('turn:', 'turns:')) for u in urls):
                raise ValueError('invalid TURN configuration')
            # Standard TURN REST time-limited credentials, never the signing secret.
            username = f'{int(time.time())+300}:{hashlib.sha256(owner.encode()).hexdigest()[:16]}'
            credential = base64.b64encode(hmac.new(c.realtime_turn_secret.encode(), username.encode(), hashlib.sha1).digest()).decode()
            servers.append(IceServer(urls=urls, username=username, credential=credential))
        return servers

    async def offer(self, owner, sid, body):
        self.require()
        key = str(sid)
        # A registry lock prevents concurrent offers from creating orphan resources.
        async with self.offer_lock:
            if key in self.sessions:
                raise DomainError('REALTIME_ALREADY_CONNECTED', '请先结束旧媒体连接', 409)
            if len(self.sessions) >= 1:
                raise DomainError('REALTIME_CONCURRENCY_LIMIT', '当前联调只允许一个实时会话', 429)
            session = RealtimeSession(self, owner, sid)
            try:
                snapshot = await asyncio.wait_for(self.business.get_snapshot(session.context), 20)
                if snapshot.session_id != sid or snapshot.state_revision != body.expected_state_revision:
                    raise DomainError('REVISION_CONFLICT', '请刷新会话版本', 409)
                if snapshot.state in ('CLOSED', 'MANUAL_REVIEW'):
                    raise DomainError('SESSION_ENDED', '会话已结束', 409)
                factory = self.media_factory
                if factory is None:
                    from .media import WebRTCMedia
                    factory = WebRTCMedia
                session.media = factory(session, self.ice_servers(owner))
                self.sessions[key] = session
                async with asyncio.timeout(max(.001, 20-(time.monotonic()-session.created))):
                    sdp = await session.media.answer(body.sdp)
                session.spawn(session.start())
                session.spawn(session.watchdog())
                from .wire import RealtimeAnswer
                return RealtimeAnswer(sdp=sdp, connection_binding=session.context.connection_binding,
                    expires_at=datetime.now(timezone.utc)+timedelta(seconds=180))
            except BaseException:
                await session.close()
                raise

    def bound(self, owner, sid, binding):
        session = self.sessions.get(str(sid))
        if not session or session.closed or session.context.owner != owner or session.context.connection_binding != binding:
            raise DomainError('REALTIME_BINDING_INVALID', '实时连接不存在或已失效', 403)
        return session

    async def close_session(self, sid):
        session = self.sessions.get(str(sid))
        if session:
            await session.close()

    async def close(self):
        await asyncio.gather(*(s.close() for s in list(self.sessions.values())))
