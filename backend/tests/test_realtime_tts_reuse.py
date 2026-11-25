import asyncio
import httpx
import pytest
from app.tts import QwenTTS, SpeechRequest
from test_tts import configured,frame


@pytest.mark.parametrize('drain,expected_connections',[(False,2),(True,1)])
async def test_actual_local_http_connection_reuse_after_final_sse(drain,expected_connections):
    connections=[];workers=set()
    async def handle(reader,writer):
        task=asyncio.current_task();workers.add(task);connections.append(1)
        try:
            while True:
                try:headers=await reader.readuntil(b'\r\n\r\n')
                except (asyncio.IncompleteReadError,ConnectionError):return
                length=next(int(line.split(b':',1)[1]) for line in headers.split(b'\r\n') if line.lower().startswith(b'content-length:'))
                await reader.readexactly(length)
                payload=(frame(b'\0\1'*32,True)+'data: [DONE]\n\n').encode()
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n'+f'{len(payload):x}\r\n'.encode()+payload+b'\r\n0\r\n\r\n');await writer.drain()
        finally:
            workers.discard(task);writer.close();await writer.wait_closed()
    server=await asyncio.start_server(handle,'127.0.0.1',0);port=server.sockets[0].getsockname()[1]
    class LocalBoundary(httpx.AsyncBaseTransport):
        def __init__(self):self.actual=httpx.AsyncHTTPTransport(retries=0)
        async def handle_async_request(self,request):
            local=httpx.Request(request.method,f'http://127.0.0.1:{port}/',headers=request.headers,content=await request.aread(),extensions=request.extensions)
            return await self.actual.handle_async_request(local)
        async def aclose(self):await self.actual.aclose()
    try:
        async with httpx.AsyncClient(transport=LocalBoundary()) as client:
            tts=QwenTTS(configured(realtime_tts_drain_enabled=drain,realtime_diagnostics_enabled=True),client=client)
            for _ in range(2):
                audio=b''.join([pcm async for pcm in tts.stream(SpeechRequest(input='已审核原因和整改'),asyncio.Event())])
                assert audio==b'\0\1'*32
            assert len(connections)==expected_connections
            headers=[e for e in tts.probe.events if e['event']=='tts_response_headers']
            assert headers[-1]['connection_reused_observed']==drain
    finally:
        server.close();await server.wait_closed()
        for task in list(workers):task.cancel()
        await asyncio.gather(*workers,return_exceptions=True)


async def test_tail_drain_is_bounded_when_supplier_never_finishes_body():
    closed=asyncio.Event()
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield frame(b'\0\1',True).encode();await asyncio.Event().wait()
        async def aclose(self):closed.set()
    async def upstream(request):return httpx.Response(200,headers={'Content-Type':'text/event-stream'},stream=Stream())
    tts=QwenTTS(configured(realtime_tts_drain_enabled=True,realtime_diagnostics_enabled=True),httpx.MockTransport(upstream))
    async with asyncio.timeout(1):
        result=[pcm async for pcm in tts.stream(SpeechRequest(input='原因和整改'),asyncio.Event())]
    assert result==[b'\0\1'] and closed.is_set()
    assert any(e['event']=='tts_tail_drained' and not e['eof'] for e in tts.probe.events)
