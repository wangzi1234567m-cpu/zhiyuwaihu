"""Recorded-shape provider events; no network calls or cloud budget writes."""
import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
import pytest
from app.config import settings
from app.realtime.provider import NativeInput, ProviderFailure
from app.realtime.speech import StreamingSpeech


class SocketBoundary:
    def __init__(self, events): self.events = iter(events); self.closed = False; self.sent = []
    async def recv(self): return json.dumps(next(self.events))
    async def send(self, body): self.sent.append(json.loads(body))
    async def close(self): self.closed = True


def sequence(item='item-1', rid='r1', text=None):
    text = text or json.dumps({'transcript': '补齐就能通过吗', 'intents': ['ask_guarantee']})
    return [
        {'type': 'input_audio_buffer.speech_started', 'item_id': item},
        {'type': 'input_audio_buffer.committed', 'item_id': item},
        {'type': 'response.created', 'response': {'id': rid}},
        {'type': 'response.audio.delta', 'audio': 'UNTRUSTED'},
        {'type': 'response.text.done', 'response_id': rid, 'text': text},
        {'type': 'response.done', 'response': {'id': rid, 'status': 'completed'}},
    ]


async def test_final_input_is_bound_and_provider_audio_is_ignored():
    calls = []
    provider = NativeInput(settings, lambda *a: calls.append(a))
    provider.ws = SocketBoundary(sequence())
    events = provider.events()
    assert (await anext(events))['type'] == 'speech_started'
    final = await anext(events)
    assert final['type'] == 'final' and final['provider_input_id'] == 'item-1'
    assert final['candidate'].intents == ['ask_guarantee'] and not calls
    await events.aclose(); await provider.close()


async def test_late_old_response_cannot_be_rebound_to_new_speech():
    old = sequence()
    newer = sequence('item-2', 'r2')
    provider = NativeInput(settings, lambda *a: None)
    cancelled={'type':'response.done','response':{'id':'r1','status':'cancelled'}}
    provider.ws = SocketBoundary(old[:3] + newer[:2] + [cancelled] + newer[2:] + old[3:])
    events = provider.events()
    assert (await anext(events))['epoch'] == 1
    assert (await anext(events))['epoch'] == 2
    assert (await anext(events))['provider_input_id'] == 'item-2'
    await events.aclose(); await provider.close()


@pytest.mark.parametrize('text', ['{"transcript":"同意","intents":["acknowledge"],"verified":true}',
                                '{"transcript":"","intents":["acknowledge"]}'])
async def test_untrusted_authorization_fields_and_empty_transcript_are_rejected(text):
    provider = NativeInput(settings, lambda *a: None)
    provider.ws = SocketBoundary(sequence(text=text))
    events = provider.events(); await anext(events)
    with pytest.raises(ProviderFailure, match='NATIVE_INVALID_UNDERSTANDING'): await anext(events)
    await events.aclose(); await provider.close()


async def test_long_approved_text_is_split_without_rewriting_or_extra_model_call():
    reserved, texts = [], []
    speech = StreamingSpeech(settings, lambda *a: reserved.append(a))
    # Substitute only the HTTP adapter boundary; not a business Mock.
    class TTS:
        def validate(self, body): pass
        async def stream(self, body, cancel):
            texts.append(body.input)
            yield b'\0\1'
    speech.tts = TTS()
    text = '已审核文本。'*300
    try:
        chunks = [chunk async for chunk in speech.stream(text, asyncio.Event())]
        assert ''.join(texts) == text and all(len(t) <= 1200 for t in texts)
        assert len(chunks) == len(reserved) == 2
    finally: await speech.close()


async def test_native_connection_reserves_bounded_call_before_audio_and_requests_no_speech():
    c = replace(settings, cloud_calls_enabled=True, free_quota_confirmed=True,
        dashscope_api_key='test-only-key', dashscope_workspace_id='ws-test', max_daily_native_turns=2)
    socket = SocketBoundary([{'type':'session.created'}, {'type':'session.updated'}])
    reservations = []
    async def connector(*args, **kwargs): return socket
    provider = NativeInput(c, lambda *args: reservations.append(args), connector)
    try:
        await provider.open(SimpleNamespace(state='VERIFYING'))
        assert reservations == [('native_realtime_session', c.max_daily_native_sessions),
                                ('native_realtime_turn', 2), ('native_realtime_turn', 2)]
        assert socket.sent[0]['session']['modalities'] == ['text']
        assert socket.sent[0]['session']['tools'] == []
    finally: await provider.close()


async def test_tts_http_pool_survives_multiple_segments_and_closes_with_session():
    import httpx
    from app.tts import QwenTTS
    from test_tts import configured, frame
    c = configured()
    requests = []
    async def upstream(request):
        requests.append(request)
        return httpx.Response(200, headers={'Content-Type':'text/event-stream'}, text=frame(b'\0\1'*480, True))
    speech = StreamingSpeech(c, lambda *args: None)
    await speech.client.aclose()
    speech.client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    speech.tts = QwenTTS(c, client=speech.client)
    try:
        for _ in range(2):
            assert [chunk async for chunk in speech.stream('审核文本', asyncio.Event())]
            assert not speech.client.is_closed
        assert len(requests) == 2
    finally: await speech.close()
    assert speech.client.is_closed


async def test_two_committed_inputs_cannot_be_guessed_by_fifo():
    a,b=sequence(),sequence('item-2','r2')
    provider=NativeInput(settings,lambda *args:None);provider.ws=SocketBoundary(a[:2]+b[:2]+a[2:])
    events=provider.events();await anext(events);await anext(events)
    with pytest.raises(ProviderFailure,match='NATIVE_INPUT_ASSOCIATION_UNCERTAIN'):await anext(events)
    await events.aclose();await provider.close()


async def test_repeated_onset_same_item_stays_one_logical_turn_and_replay_is_ignored():
    events=sequence();start={**events[0],'event_id':'start-1'}
    provider=NativeInput(replace(settings,max_daily_native_turns=1),lambda *args:None)
    provider.ws=SocketBoundary([start,start,{**start,'event_id':'start-2'}]+events[1:])
    receiver=provider.events();first=await anext(receiver);again=await anext(receiver);final=await anext(receiver)
    assert first['epoch']==again['epoch']==final['epoch']==1 and again['continuation'] and provider.turns==1
    await receiver.aclose();await provider.close()


@pytest.mark.parametrize('actual,accepted',[( {'type':'semantic_vad_ec','silence_duration_ms':300},True),({'type':'server_vad','silence_duration_ms':400},False),({},False)])
async def test_semantic_endpoint_must_be_acknowledged_by_provider(actual,accepted):
    c=replace(settings,realtime_vad_mode='semantic_vad_ec',realtime_vad_silence_ms=300,cloud_calls_enabled=True,free_quota_confirmed=True,dashscope_api_key='test',dashscope_workspace_id='ws-test',max_daily_native_turns=1)
    socket=SocketBoundary([{'type':'session.created'},{'type':'session.updated','session':{'turn_detection':actual}}])
    async def connect(*a,**kw):return socket
    provider=NativeInput(c,lambda *a:None,connect)
    try:
        if accepted:await provider.open(SimpleNamespace(state='VERIFYING'))
        else:
            with pytest.raises(ProviderFailure,match='NATIVE_VAD_CONFIG_REJECTED'):await provider.open(SimpleNamespace(state='VERIFYING'))
        assert socket.sent[0]['session']['turn_detection']['silence_duration_ms']==300
    finally:await provider.close()


async def test_repeated_onset_after_commit_cannot_cancel_or_rebind_final_understanding():
    events=sequence();provider=NativeInput(settings,lambda *a:None)
    provider.ws=SocketBoundary(events[:3]+[dict(events[0],event_id='late-repeat')]+events[3:])
    receiver=provider.events();await anext(receiver)
    assert (await anext(receiver))['type']=='final' and provider.turns==1
    await receiver.aclose();await provider.close()
