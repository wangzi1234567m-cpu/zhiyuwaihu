"""Offline ordering, completeness, cancellation and isolated-audio checks."""
import asyncio,base64,json
from dataclasses import replace
from types import SimpleNamespace
import pytest
from app.config import settings
from app.agent.semantic_agent import ReplyTemplates
from app.realtime.reply_strategy import answer_first,semantic_segments
from app.realtime.prepared import PreparedSpeech
from app.realtime.shadow import ShadowVoice,ShadowDenied


def test_semantic_boundaries_keep_every_character_and_only_complete_sentences():
    for text in ['已审核完整正文','首句。第二句。限制条件。','必须整改！不得承诺通过。','第一句。 后续空格保留。']:
        parts=semantic_segments(text)
        assert ''.join(parts)==text and 1<=len(parts)<=2
    assert semantic_segments('原因，整改，限制。')==['原因，整改，限制。']


def test_answer_first_moves_only_existing_complete_approved_clause():
    templates=ReplyTemplates();prefix=templates.guarantee_refusal
    body='品牌A的申请缺少授权材料。'+prefix+'请补齐授权链。'
    revised=answer_first(body,['ask_guarantee'],templates,'演示商家')
    assert revised.startswith(prefix)
    assert sorted(revised.replace(' ',''))==sorted(body.replace(' ',''))
    assert answer_first(body,['ask_source'],templates,'商家')==body
    assert answer_first('未经审核的另一句话。',['ask_guarantee'],templates,'商家')=='未经审核的另一句话。'


async def test_prefetch_is_bounded_ordered_and_cancelled_without_leaking_tasks():
    cancelled=asyncio.Event();tasks=[]
    class Speech:
        async def stream(self,text,cancel):
            try:
                for n in range(30):yield bytes([n])*960
            finally:cancelled.set()
    def spawn(coro):
        task=asyncio.create_task(coro);tasks.append(task);return task
    prepared=PreparedSpeech('已审核正文',Speech(),spawn)
    await asyncio.sleep(.01)
    assert prepared.queue.qsize()==6 and not prepared.task.done()
    chunks=[]
    async for chunk in prepared.stream():chunks.append(chunk)
    assert b''.join(chunks)==b''.join(bytes([n])*960 for n in range(30))
    await prepared.task
    prepared=PreparedSpeech('另一段',Speech(),spawn);await asyncio.sleep(.01);prepared.stop()
    await asyncio.wait_for(asyncio.gather(*tasks,return_exceptions=True),.3)
    assert all(t.done() for t in tasks) and cancelled.is_set()


async def test_prefetch_propagates_failure_without_marking_completion():
    class Speech:
        async def stream(self,text,cancel):
            yield b'\0\1'
            raise ValueError('failed upstream')
    prepared=PreparedSpeech('正文',Speech(),asyncio.create_task)
    with pytest.raises(ValueError,match='failed upstream'):
        _=[chunk async for chunk in prepared.stream()]
    await prepared.task


async def test_shadow_disabled_spends_no_budget_and_has_no_playback_interface():
    calls=[];voice=ShadowVoice(settings,lambda *args:calls.append(args),None)
    assert not hasattr(voice,'stream')
    with pytest.raises(ShadowDenied,match='SHADOW_DISABLED'):
        await voice.generate(None,None,asyncio.Event())
    assert calls==[] and voice.ws is None


async def test_shadow_candidate_audio_never_grants_verified_playback(monkeypatch):
    import app.realtime.shadow as module
    monkeypatch.setattr(module,'approved_shadow_text',lambda *a:('审核正文','digest','generation'))
    config=replace(settings,realtime_shadow_voice_enabled=True,environment='test',voice_writes_enabled=False,cloud_calls_enabled=True,free_quota_confirmed=True,dashscope_api_key='test',dashscope_workspace_id='ws-test',max_daily_native_turns=1)
    events=[{'type':'session.created'},{'type':'session.updated'}, {'type':'response.created','response':{'id':'r1'}}, {'type':'response.audio.delta','response_id':'r1','delta':base64.b64encode(b'\0\1').decode()}, {'type':'response.audio_transcript.done','response_id':'r1','transcript':'审核正文'}, {'type':'response.done','response':{'id':'r1','status':'completed'}}]
    class Socket:
        def __init__(self):self.sent=[];self.closed=False
        async def send(self,raw):self.sent.append(json.loads(raw))
        async def recv(self):return json.dumps(events.pop(0))
        async def close(self):self.closed=True
    socket=Socket();calls=[]
    async def connect(*a,**kw):return socket
    voice=ShadowVoice(config,lambda *a:calls.append(a),None,connect)
    result=await voice.generate(None,None,asyncio.Event())
    assert result['text_matches'] and result['candidate_pcm']==b'\0\1'
    assert result['audio_verified'] is False and result['merchant_playback'] is False and len(calls)==2
    await voice.close();assert socket.closed