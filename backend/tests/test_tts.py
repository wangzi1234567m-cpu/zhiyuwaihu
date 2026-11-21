import asyncio
import base64
import json
from dataclasses import replace
from uuid import uuid4
import httpx
import pytest
from app.config import settings
from app.adapters import DomainError, Services
from app.main import create_app
from app.tts import QwenTTS, SpeechRequest


def configured(**overrides):
    c=replace(settings,tts_provider='qwen',cloud_calls_enabled=True,free_quota_confirmed=True,
        dashscope_api_key='test-private-bailian-key',dashscope_workspace_id='ws-test',
        dashscope_region='cn-beijing',dashscope_tts_http_url='',
        dashscope_tts_model='qwen-audio-3.1-tts-flash',dashscope_tts_voice='longanhuan_v3.1')
    return replace(c,**overrides)


def frame(audio=b'',stop=False):
    return 'data: '+json.dumps({'output':{'finish_reason':'stop' if stop else None,
        'audio':{'data':base64.b64encode(audio).decode()}}})+'\n\n'


async def test_sse_decoding_and_fixed_pcm_parameters():
    calls=[]
    async def upstream(req):
        calls.append(req)
        assert req.headers['Authorization']=='Bearer test-private-bailian-key'
        assert req.headers['X-DashScope-SSE']=='enable'
        assert str(req.url).startswith('https://ws-test.cn-beijing.maas.aliyuncs.com/')
        body=json.loads(req.content)
        assert body['input']['format']=='pcm' and body['input']['sample_rate']==24000
        assert not body['input']['enable_ssml']
        return httpx.Response(200,headers={'Content-Type':'text/event-stream'},text=frame(b'\x00\x01\x02')+frame(b'\x03\x04\x05',True))
    reserved=[]
    tts=QwenTTS(configured(),httpx.MockTransport(upstream),lambda *args:reserved.append(args))
    chunks=[c async for c in tts.stream(SpeechRequest(input='合成测试'),asyncio.Event())]
    assert b''.join(chunks)==bytes(range(6)) and all(len(c)%2==0 for c in chunks)
    assert len(calls)==len(reserved)==1


@pytest.mark.parametrize('status,code',[(401,'TTS_AUTH_FAILED'),(403,'TTS_PERMISSION_DENIED'),(402,'TTS_QUOTA_EXHAUSTED'),(429,'TTS_RATE_LIMITED'),(500,'TTS_UPSTREAM_ERROR')])
async def test_provider_errors_are_redacted_and_not_retried(status,code):
    calls=[]
    async def upstream(req):
        calls.append(req)
        return httpx.Response(status,text='test-private-bailian-key provider private details')
    with pytest.raises(DomainError) as error:
        [c async for c in QwenTTS(configured(),httpx.MockTransport(upstream)).stream(SpeechRequest(input='测试'),asyncio.Event())]
    assert error.value.code==code and 'private' not in error.value.message
    assert len(calls)==1


@pytest.mark.parametrize('values',[{'response_format':'wav'},{'sample_rate':16000},{'speed':1.5},{'model':'unapproved'},{'voice':'unapproved'},{'input':'  '}])
async def test_client_cannot_change_approved_parameters(values):
    called=[]
    async def upstream(req):called.append(req);raise AssertionError('No call allowed')
    body=SpeechRequest.model_validate({'input':'测试',**values})
    with pytest.raises(DomainError) as error:
        [c async for c in QwenTTS(configured(),httpx.MockTransport(upstream)).stream(body,asyncio.Event())]
    assert error.value.code=='TTS_PARAMETERS_REJECTED' and not called


async def test_cancellation_closes_pending_upstream_before_audio():
    entered=asyncio.Event();closed=asyncio.Event();cancel=asyncio.Event()
    async def upstream(req):
        entered.set()
        try:await asyncio.Event().wait()
        finally:closed.set()
    async def collect():
        return [c async for c in QwenTTS(configured(),httpx.MockTransport(upstream)).stream(SpeechRequest(input='测试'),cancel)]
    task=asyncio.create_task(collect())
    await asyncio.wait_for(entered.wait(),1);cancel.set()
    assert await asyncio.wait_for(task,1)==[] and closed.is_set()


async def test_first_audio_timeout_includes_headers():
    async def upstream(req):await asyncio.sleep(10)
    with pytest.raises(DomainError) as error:
        [c async for c in QwenTTS(configured(tts_first_audio_timeout=.01),httpx.MockTransport(upstream)).stream(SpeechRequest(input='测试'),asyncio.Event())]
    assert error.value.code=='TTS_TIMEOUT'


@pytest.mark.parametrize('response,code',[
    ('data: {"output":{"finish_reason":"stop","audio":{"data":"@invalid"}}}\n\n','TTS_BAD_STREAM'),
    (frame(stop=True),'TTS_EMPTY'),
    (frame(b'\x01\x02'),'TTS_TRUNCATED'),
    (frame(b'\x01',True),'TTS_EMPTY'),
    ('data: {"code":"InvalidApiKey","message":"private"}\n\n','TTS_UPSTREAM_ERROR'),
])
async def test_invalid_or_truncated_audio(response,code):
    async def upstream(req):return httpx.Response(200,headers={'Content-Type':'text/event-stream'},text=response)
    with pytest.raises(DomainError) as error:
        [c async for c in QwenTTS(configured(),httpx.MockTransport(upstream)).stream(SpeechRequest(input='测试'),asyncio.Event())]
    assert error.value.code==code


def test_endpoint_cannot_redirect_keys_to_another_host():
    with pytest.raises(DomainError):QwenTTS(configured(dashscope_tts_http_url='https://attacker.test')).require()
    with pytest.raises(DomainError):QwenTTS(configured(free_quota_confirmed=False)).require()

async def test_cancel_after_first_audio_closes_transport_and_registration():
    closed=asyncio.Event()
    class AudioStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield frame(b'\x00\x01').encode()
            await asyncio.Event().wait()
        async def aclose(self):closed.set()
    async def upstream(req):return httpx.Response(200,headers={'Content-Type':'text/event-stream'},stream=AudioStream())
    tts=QwenTTS(configured(),httpx.MockTransport(upstream));cancel=asyncio.Event()
    tts.register('session',cancel)
    stream=tts.stream(SpeechRequest(input='测试'),cancel)
    assert await anext(stream)==b'\x00\x01'
    pending=asyncio.create_task(anext(stream))
    await asyncio.sleep(0)
    tts.cancel_session('session')
    with pytest.raises(StopAsyncIteration):await asyncio.wait_for(pending,1)
    assert closed.is_set()
    tts.unregister('session',cancel)
    assert not tts.active


class BoundVoice:
    def authorize_tts(self,sid,authorization):
        if authorization!='Bearer session-bound-token':raise DomainError('VOICE_UNAUTHENTICATED','入口鉴权失败',401)


async def test_http_endpoint_auth_pcm_and_errors_before_headers():
    calls=[]
    async def upstream(req):
        calls.append(req)
        return httpx.Response(200,headers={'Content-Type':'text/event-stream'},text=frame(b'\x00\x01',True))
    app=create_app(Services(voice=BoundVoice()),tts=QwenTTS(configured(),httpx.MockTransport(upstream)))
    url=f'/integrations/agora/sessions/{uuid4()}/v1/audio/speech'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        rejected=await client.post(url,json={'input':'测试'})
        assert rejected.status_code==401 and not calls
        response=await client.post(url,json={'input':'测试'},headers={'Authorization':'Bearer session-bound-token'})
        assert response.status_code==200 and response.content==b'\x00\x01'
        assert response.headers['content-type'].startswith('audio/pcm')
        assert response.headers['x-audio-sample-rate']=='24000'
    async def denied(req):return httpx.Response(401,text='test-private-bailian-key')
    app=create_app(Services(voice=BoundVoice()),tts=QwenTTS(configured(),httpx.MockTransport(denied)))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        response=await client.post(url,json={'input':'测试'},headers={'Authorization':'Bearer session-bound-token'})
        assert response.status_code==503 and response.json()['code']=='TTS_AUTH_FAILED'
        assert 'test-private' not in response.text
