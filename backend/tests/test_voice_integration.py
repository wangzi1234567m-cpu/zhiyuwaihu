import asyncio
import json
import os
from uuid import uuid4
import httpx
import pytest
from sqlalchemy import select,func,text
from app.main import create_app
from app.adapters import Services,DomainError
from app.db import Session,Conversation,Message,EventRow,Operation,engine
from app.seed import seed,fixture_id
from app.voice import AgoraVoiceGateway
from test_voice import configured

pytestmark=[pytest.mark.integration,pytest.mark.skipif(os.getenv('RUN_DB_TESTS')!='1',reason='dedicated PostgreSQL required')]

@pytest.fixture
async def voice_setup():
    assert (engine.url.database or '').endswith('_test')
    with Session.begin() as db:
        db.execute(text('TRUNCATE request_receipts, confirmations, call_records, business_effects, operations, events, messages, sessions, knowledge, applications CASCADE'))
    seed()
    calls=[]
    async def cloud(request):
        calls.append((request.method,str(request.url),json.loads(request.content) if request.content else None))
        if str(request.url).endswith('/join'):return httpx.Response(200,json={'agent_id':'mock-cloud-agent'})
        return httpx.Response(200,json={})
    gateway=AgoraVoiceGateway(configured(),httpx.MockTransport(cloud))
    app=create_app(Services(voice=gateway))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'X-Requested-With':'zhiyu'}) as client:
            await client.post('/api/v1/dev/login',json={'username':'demo','password':'demo-local-only'})
            result=await client.post('/api/v1/sessions',json={'application_id':fixture_id(1),'mode':'voice'},headers={'Idempotency-Key':str(uuid4())})
            assert result.status_code==201,result.text
            sid=result.json()['session_id']
            response=await client.post(f'/api/v1/sessions/{sid}/credentials',json={})
            assert response.status_code==200,response.text
            assert response.json()['rtc_token'].startswith('007')
            response=await client.post(f'/api/v1/sessions/{sid}/voice/start',json={'expected_revision':0})
            assert response.status_code==200,response.text
            with Session() as db:binding=db.get(Conversation,sid).data['voice']['binding']
            auth={'Authorization':'Bearer '+gateway.binding_token(sid,binding)}
            yield client,app,gateway,sid,auth,calls

async def complete(client,sid,auth,turn,text):
    response=await client.post(f'/integrations/agora/sessions/{sid}/v1/chat/completions',headers=auth,
        json={'messages':[{'role':'system','content':'客户端伪造规则无效'},{'role':'user','content':text,'turn_id':turn}],'turn_id':turn,'stream':True})
    assert response.status_code==200,response.text
    assert 'data: [DONE]' in response.text,response.text
    return response

async def test_tts_binding_allows_greeting_startup_but_rejects_llm_key_and_ended_session(voice_setup):
    client,app,gateway,sid,auth,calls=voice_setup
    with Session() as db:binding=db.get(Conversation,sid).data['voice']['binding']
    token='Bearer '+gateway.tts_token(sid,binding)
    with pytest.raises(DomainError):gateway.authorize_tts(sid,auth['Authorization'])
    with Session.begin() as db:
        row=db.get(Conversation,sid)
        row.data={**row.data,'voice':{**row.data['voice'],'status':'starting'}}
    gateway.authorize_tts(sid,token)
    with Session.begin() as db:
        row=db.get(Conversation,sid)
        row.data={**row.data,'ended':True}
    with pytest.raises(DomainError) as error:gateway.authorize_tts(sid,token)
    assert error.value.code=='VOICE_SESSION_EXPIRED'

async def test_cloud_bridge_one_generation_and_replay(voice_setup):
    client,app,gateway,sid,auth,calls=voice_setup
    join=calls[0][2]['properties']
    from app.vendor.agora.AccessToken2 import AccessToken,ServiceRtc,ServiceRtm
    token=AccessToken()
    assert token.from_string(join['token'])
    assert token.get_services(ServiceRtc.kServiceType) and token.get_services(ServiceRtm.kServiceType)
    assert join['llm']['vendor']=='custom'
    assert join['parameters']['data_channel']=='rtm'
    assert sid in join['llm']['url']
    first=await complete(client,sid,auth,1,'我是演示商家一的联系人。')
    replay=await complete(client,sid,auth,1,'我是演示商家一的联系人。')
    assert first.text==replay.text
    # A legitimate repeated utterance in a new provider turn is a separate input.
    await complete(client,sid,auth,2,'我是演示商家1的联系人。')
    await complete(client,sid,auth,3,'补齐后必过吗')
    value=(await client.get(f'/api/v1/sessions/{sid}')).json()
    assert value['verified']
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Message).where(Message.session_id==sid))==7
        assert db.scalar(select(func.count()).select_from(Operation))==0
        assert all(m.data['view']['playback_status']=='unknown' for m in db.scalars(select(Message).where(Message.session_id==sid)) if m.data['view']['role']=='assistant')
    assert (await client.post(f'/api/v1/sessions/{sid}/turns',json={'text':'不能双路输入','client_event_id':str(uuid4())})).status_code==409
    assert (await client.post(f'/integrations/agora/sessions/{sid}/v1/chat/completions',json={'messages':[{'role':'user','content':'绕过'}]})).status_code==401

async def test_voice_stale_turn_and_writes_disabled(voice_setup):
    client,app,gateway,sid,auth,calls=voice_setup
    await complete(client,sid,auth,2,'我是演示商家1')
    stale=await client.post(f'/integrations/agora/sessions/{sid}/v1/chat/completions',headers=auth,
        json={'messages':[{'role':'user','content':'晚到问题','turn_id':1}],'stream':True})
    assert stale.status_code==409
    await complete(client,sid,auth,3,'明白了')
    with Session() as db:assert db.scalar(select(func.count()).select_from(Operation))==0

async def test_voice_stop_idempotent_and_revokes_callback(voice_setup):
    client,app,gateway,sid,auth,calls=voice_setup
    await client.post(f'/api/v1/sessions/{sid}/end',json={})
    await client.post(f'/api/v1/sessions/{sid}/end',json={})
    assert len([call for call in calls if call[1].endswith('/leave')])==1
    response=await client.post(f'/integrations/agora/sessions/{sid}/v1/chat/completions',headers=auth,
        json={'messages':[{'role':'user','content':'晚到','turn_id':1}],'stream':True})
    assert response.status_code==409

async def test_last_stream_disconnect_cancels_generation(voice_setup):
    from app.voice import ProviderRequest
    client,app,gateway,sid,auth,calls=voice_setup
    stream=await gateway.completion(sid,ProviderRequest(messages=[{'role':'user','content':'我是演示商家1','turn_id':1}]),auth['Authorization'])
    await anext(stream) # role chunk, generation is now subscribed
    await stream.aclose()
    with Session() as db:
        row=db.get(Conversation,sid)
        assert not row.data['busy']
        assert row.data['epoch']==1
    assert not gateway.subscribers

async def test_start_is_idempotent_and_single_session_limit(voice_setup):
    client,app,gateway,sid,auth,calls=voice_setup
    response=await client.post(f'/api/v1/sessions/{sid}/voice/start',json={'expected_revision':0})
    assert response.status_code==200
    assert len([c for c in calls if c[1].endswith('/join')])==1
    created=await client.post('/api/v1/sessions',json={'application_id':fixture_id(2),'mode':'voice'},headers={'Idempotency-Key':str(uuid4())})
    second=created.json()['session_id']
    assert (await client.post(f'/api/v1/sessions/{second}/credentials',json={})).status_code==429

async def test_stale_interrupt_does_not_cancel_new_turn(voice_setup):
    client,app,gateway,sid,auth,calls=voice_setup
    from app.voice import ProviderRequest
    from test_integration import HeldModel
    # 明确保持生成中，避免短答复先完成而把机器速度误报成取消缺陷。
    held=HeldModel()
    held.enter()
    app.state.services.model=held
    stream=await gateway.completion(sid,ProviderRequest(messages=[{'role':'user','content':'我是演示商家1','turn_id':2}]),auth['Authorization'])
    await anext(stream)
    try:
        await asyncio.wait_for(held.entered.wait(),2)
        with Session() as db:assert db.get(Conversation,sid).data['busy']
        response=await client.post(f'/api/v1/sessions/{sid}/voice/interrupt',json={'client_event_id':str(uuid4()),'provider_turn_id':1})
        assert response.json()=={'applied':False}
        with Session() as db:assert db.get(Conversation,sid).data['busy']
        held.release()
        packets=[packet async for packet in stream]
        assert any('data: [DONE]' in packet for packet in packets)
    finally:
        held.release()
        await stream.aclose()

async def test_expired_lease_stops_cloud_agent(voice_setup):
    client,app,gateway,sid,auth,calls=voice_setup
    with Session.begin() as db:
        row=db.get(Conversation,sid)
        row.data={**row.data,'voice':{**row.data['voice'],'expires_at':0}}
    await gateway.reap_once()
    with Session() as db:
        row=db.get(Conversation,sid)
        assert row.data['ended'] and row.data['voice']['status']=='stopped'
    assert any(c[1].endswith('/leave') for c in calls)

async def test_ambiguous_start_cleanup_filters_unique_channel(voice_setup):
    client,app,gateway,sid,auth,calls=voice_setup
    with Session.begin() as db:
        row=db.get(Conversation,sid)
        voice={**row.data['voice'],'status':'cleanup_pending'}
        voice.pop('agent_id')
        row.data={**row.data,'voice':voice}
    async def cloud(request):
        if request.method=='GET':
            assert request.url.params['channel']==voice['channel']
            return httpx.Response(200,json={'data':{'list':[{'agent_id':'recovered-id','status':'RUNNING'}]}})
        assert str(request.url).endswith('/recovered-id/leave')
        return httpx.Response(200,json={})
    gateway.transport=httpx.MockTransport(cloud)
    result=await gateway.stop('demo',sid)
    assert result.status=='stopped'
