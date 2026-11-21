import asyncio
import json
from dataclasses import replace
import httpx
import pytest
from app.config import settings
from app.deepseek import DeepSeekModel
from app.contracts import ModelRequest,ModelMessage
from app.adapters import DomainError

def config(**values):
    return replace(settings,cloud_calls_enabled=True,free_quota_confirmed=True,deepseek_api_key='test-secret-never-log',**values)

def request():return ModelRequest(messages=[ModelMessage(role='user',content='演示测试')],fixture_response='测试',allow_rephrase=True)

async def test_stream_content_only_and_disabled_thinking():
    async def handle(req):
        body=json.loads(req.content)
        assert body['thinking']=={'type':'disabled'} and body['stream']
        assert 'tools' not in body
        assert req.headers['authorization']=='Bearer test-secret-never-log'
        return httpx.Response(200,text='data: {"choices":[{"delta":{"reasoning_content":"hidden"}}]}\n\ndata: {"choices":[{"delta":{"content":"连接成功"}}]}\n\ndata: [DONE]\n\n')
    chunks=[chunk async for chunk in DeepSeekModel(config(),httpx.MockTransport(handle)).stream(request(),asyncio.Event())]
    assert chunks==['连接成功']

@pytest.mark.parametrize('status,code',[(401,'MODEL_AUTH_FAILED'),(402,'MODEL_QUOTA_EXHAUSTED'),(429,'MODEL_RATE_LIMITED'),(500,'MODEL_UPSTREAM_ERROR')])
async def test_no_secret_or_provider_body_in_errors(status,code):
    async def handle(req):return httpx.Response(status,text='test-secret-never-log private provider body')
    with pytest.raises(DomainError) as error:
        [c async for c in DeepSeekModel(config(),httpx.MockTransport(handle)).stream(request(),asyncio.Event())]
    assert error.value.code==code
    assert 'test-secret' not in error.value.message

async def test_cancel_while_waiting_for_first_token():
    entered=asyncio.Event();closed=asyncio.Event();cancel=asyncio.Event()
    async def handle(req):
        entered.set()
        try:await asyncio.Event().wait()
        finally:closed.set()
    async def collect():return [c async for c in DeepSeekModel(config(),httpx.MockTransport(handle)).stream(request(),cancel)]
    task=asyncio.create_task(collect())
    await entered.wait();cancel.set()
    assert await asyncio.wait_for(task,1)==[]
    assert closed.is_set()

async def test_total_timeout_and_closed_request():
    async def handle(req):await asyncio.sleep(10)
    with pytest.raises(DomainError) as error:
        [c async for c in DeepSeekModel(config(model_total_timeout=.02),httpx.MockTransport(handle)).stream(request(),asyncio.Event())]
    assert error.value.code=='MODEL_TIMEOUT'

async def test_credit_confirmation_required():
    cfg=replace(config(),free_quota_confirmed=False)
    with pytest.raises(DomainError) as error:
        [c async for c in DeepSeekModel(cfg).stream(request(),asyncio.Event())]
    assert error.value.code=='MODEL_NOT_CONFIGURED'

async def test_first_content_timeout_includes_wait_for_headers():
    async def handle(req):await asyncio.sleep(10)
    with pytest.raises(DomainError) as error:
        [c async for c in DeepSeekModel(config(model_first_text_timeout=.02),httpx.MockTransport(handle)).stream(request(),asyncio.Event())]
    assert error.value.code=='MODEL_TIMEOUT'

async def test_observer_measures_first_network_content():
    metrics=[]
    async def handle(req):return httpx.Response(200,text='data: {"choices":[{"delta":{"content":"文本"}}]}\n\ndata: [DONE]\n\n')
    [c async for c in DeepSeekModel(config(),httpx.MockTransport(handle),observe=lambda req,ms:metrics.append(ms)).stream(request(),asyncio.Event())]
    assert len(metrics)==1 and metrics[0]>=0
