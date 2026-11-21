"""OpenAI-compatible content-only DeepSeek adapter. No automatic paid retries."""
import asyncio
import json
import time
from contextlib import suppress
import httpx
from .adapters import DomainError
from .config import settings

class DeepSeekModel:
    name = 'deepseek'

    def __init__(self, config=settings, transport=None, reserve=None, observe=None):
        self.config=config
        self.transport=transport
        self.reserve=reserve
        self.observe=observe

    async def stream(self, request, cancel):
        cfg=self.config
        if not cfg.cloud_calls_enabled or not cfg.free_quota_confirmed or not cfg.deepseek_api_key:
            raise DomainError('MODEL_NOT_CONFIGURED','请配置模型密钥并确认赠送额度',503)
        if cancel.is_set(): return
        if self.reserve: self.reserve('model',cfg.max_daily_model_calls)
        # The caller owns messages. No browser-provided model/base URL/tools are forwarded.
        body={'model':cfg.deepseek_model,'messages':[m.model_dump() for m in request.messages],
              'stream':True,'thinking':{'type':'disabled'},'max_tokens':256,'temperature':0.2}
        started=time.monotonic()
        first_deadline=started+cfg.model_first_text_timeout
        async def read_stream():
            first=True
            async with httpx.AsyncClient(transport=self.transport,timeout=httpx.Timeout(8,connect=3)) as client:
                async with client.stream('POST','https://api.deepseek.com/chat/completions',json=body,
                    headers={'Authorization':'Bearer '+cfg.deepseek_api_key}) as response:
                    if response.status_code!=200:
                        code={401:'MODEL_AUTH_FAILED',402:'MODEL_QUOTA_EXHAUSTED',429:'MODEL_RATE_LIMITED'}.get(response.status_code,'MODEL_UPSTREAM_ERROR')
                        raise DomainError(code,'模型服务暂不可用，请核对账号状态',503)
                    lines=response.aiter_lines()
                    while True:
                        try:
                            timeout=max(.001,first_deadline-time.monotonic()) if first else cfg.model_total_timeout
                            line=await asyncio.wait_for(anext(lines),timeout)
                        except StopAsyncIteration:
                            raise DomainError('MODEL_TRUNCATED','模型流提前结束',503)
                        if cancel.is_set(): return
                        if first and time.monotonic()>first_deadline: raise TimeoutError()
                        if not line.startswith('data:'): continue
                        data=line[5:].strip()
                        if data=='[DONE]':
                            if first: raise DomainError('MODEL_EMPTY','模型没有返回文本',503)
                            return
                        try:
                            obj=json.loads(data)
                            choices=obj.get('choices',[])
                            delta=choices[0].get('delta',{}) if choices else {}
                        except (ValueError,TypeError,IndexError):
                            raise DomainError('MODEL_BAD_STREAM','模型流格式异常',503)
                        if delta.get('tool_calls'):
                            raise DomainError('MODEL_TOOL_REJECTED','语音输出不允许工具调用',503)
                        content=delta.get('content')
                        if content:
                            if not isinstance(content,str): raise DomainError('MODEL_BAD_STREAM','模型流格式异常',503)
                            if first and self.observe:self.observe(request,(time.monotonic()-started)*1000)
                            first=False
                            yield content
        # Race pending network reads against cancellation, including before the first token.
        iterator=read_stream().__aiter__()
        cancel_task=asyncio.create_task(cancel.wait())
        pending=None
        received=False
        try:
            async with asyncio.timeout(cfg.model_total_timeout):
                while not cancel.is_set():
                    pending=asyncio.create_task(anext(iterator))
                    done,_=await asyncio.wait([pending,cancel_task],return_when=asyncio.FIRST_COMPLETED,
                        timeout=None if received else max(.001,first_deadline-time.monotonic()))
                    if not done: raise TimeoutError()
                    if cancel_task in done: return
                    try: chunk=pending.result()
                    except StopAsyncIteration: return
                    received=True
                    yield chunk
        except (TimeoutError,httpx.TimeoutException):
            raise DomainError('MODEL_TIMEOUT','模型响应超时，请重试',503)
        except httpx.HTTPError:
            raise DomainError('MODEL_NETWORK_ERROR','模型连接失败',503)
        finally:
            if pending and not pending.done():
                pending.cancel()
                with suppress(asyncio.CancelledError): await pending
            cancel_task.cancel()
            with suppress(asyncio.CancelledError): await cancel_task
            await iterator.aclose()
