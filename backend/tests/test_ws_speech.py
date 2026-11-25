import asyncio,json
from dataclasses import replace
import pytest
from app.realtime.ws_speech import WebSocketSpeech
from app.realtime.speech import create_speech
from app.adapters import DomainError
from test_tts import configured

class TaskSocket:
    def __init__(self,stall=False,wrong=False):self.queue=asyncio.Queue();self.sent=[];self.closed=False;self.stall=stall;self.wrong=wrong
    async def send(self,raw):
        value=json.loads(raw);self.sent.append(value);h=value['header'];task=h['task_id']
        def ack(name):return json.dumps({'header':{'task_id':'wrong' if self.wrong else task,'event':name},'payload':{}})
        if h['action']=='run-task':await self.queue.put(ack('task-started'))
        elif h['action']=='continue-task' and not self.stall:
            await self.queue.put(bytes([0,1,2]));await self.queue.put(bytes([3]))
        elif h['action']=='finish-task':
            if value['payload']['input'].get('directive')=='cancel' or not self.stall:await self.queue.put(ack('task-finished'))
    async def recv(self):return await self.queue.get()
    async def close(self):self.closed=True

@pytest.mark.parametrize('backend,model,voice',[('qwen_ws','qwen-audio-3.1-tts-flash','longanhuan_v3.1'),('cosyvoice_ws','cosyvoice-v3-flash','longanyang')])
async def test_ws_profiles_exact_pcm_and_connection_reuse(backend,model,voice):
    socket=TaskSocket();connections=[];reserved=[]
    async def connect(*args,**kwargs):connections.append(args[0]);return socket
    speech=WebSocketSpeech(replace(configured(),realtime_speech_backend=backend),lambda *args:reserved.append(args),connect)
    try:
        for _ in range(2):assert b''.join([c async for c in speech.stream('审核正文',asyncio.Event())])==bytes(range(4))
        assert len(connections)==1 and len(reserved)==2
        tasks=[s for s in socket.sent if s['header']['action']=='run-task']
        assert len({s['header']['task_id'] for s in tasks})==2
        assert all(s['payload']['model']==model and s['payload']['parameters']['voice']==voice and s['payload']['parameters']['sample_rate']==24000 and s['payload']['parameters']['format']=='pcm' for s in tasks)
    finally:await speech.close()
    assert socket.closed

async def test_ws_cancel_before_first_audio_acknowledges_and_keeps_next_task_isolated():
    socket=TaskSocket(stall=True);reserved=[]
    async def connect(*args,**kwargs):return socket
    speech=WebSocketSpeech(replace(configured(),realtime_speech_backend='qwen_ws'),lambda *a:reserved.append(a),connect)
    cancel=asyncio.Event()
    async def run():return [c async for c in speech.stream('旧审核正文',cancel)]
    task=asyncio.create_task(run())
    while len(socket.sent)<3:await asyncio.sleep(.001)
    cancel.set();assert await asyncio.wait_for(task,1)==[]
    assert any(s['payload'].get('input',{}).get('directive')=='cancel' for s in socket.sent)
    socket.stall=False
    assert b''.join([c async for c in speech.stream('新审核正文',asyncio.Event())])==bytes(range(4))
    assert len(reserved)==2
    await speech.close()

async def test_ws_wrong_task_id_is_rejected_and_closes_socket():
    socket=TaskSocket(wrong=True)
    async def connect(*args,**kwargs):return socket
    speech=WebSocketSpeech(replace(configured(),realtime_speech_backend='qwen_ws'),lambda *a:None,connect)
    with pytest.raises(DomainError,match='TTS_BAD_STREAM'):
        [c async for c in speech.stream('审核正文',asyncio.Event())]
    assert socket.closed
    await speech.close()

async def test_ws_connect_can_be_cancelled_without_waiting_for_connect_timeout():
    entered=asyncio.Event();closed=asyncio.Event()
    async def connect(*args,**kwargs):
        entered.set()
        try:await asyncio.Event().wait()
        finally:closed.set()
    speech=WebSocketSpeech(replace(configured(),realtime_speech_backend='qwen_ws'),lambda *a:None,connect)
    cancel=asyncio.Event()
    async def run():return [c async for c in speech.stream('审核正文',cancel)]
    task=asyncio.create_task(run());await entered.wait();cancel.set()
    assert await asyncio.wait_for(task,.2)==[] and closed.is_set()
    await speech.close()

async def test_unknown_backend_and_invalid_config_cannot_spend_budget():
    with pytest.raises(DomainError):create_speech(replace(configured(),realtime_speech_backend='browser-chosen-model'),lambda *a:None)
