"""Real HTTP/Coordinator/PostgreSQL acceptance, no fake success projections."""
import asyncio
from uuid import uuid4
import pytest
from sqlalchemy import select,func
from test_integration import setup,pytestmark,opened,say,wait_closed,wait_idle
from app.db import Session,Conversation,Operation,Effect,Knowledge,Application,Record
from app.seed import seed_workbench,fixture_id


async def staff(client,username='demo'):
    response=await client.post('/api/v1/auth/staff/login',json={'username':username,'password':'staff-local-only'})
    assert response.status_code==200,response.text


async def create_case(client,n):
    r=await client.post('/api/v1/sessions',json={'application_id':fixture_id(n),'mode':'text'},headers={'Idempotency-Key':str(uuid4())})
    assert r.status_code==201,r.text
    return r.json()['session_id']


async def preview(client,sid):
    r=await client.get(f'/api/v1/staff/sessions/{sid}/written-preview')
    assert r.status_code==200,r.text
    return r.json()


def command(p):
    return {k:p[k] for k in ('case_revision','knowledge_version','rule_version','body_sha256','preview_id')}|{'expected_revision':p['state_revision']}


async def reject(client,sid,p=None,key=None):
    p=p or await preview(client,sid)
    return await client.post(f'/api/v1/staff/sessions/{sid}/reject',json=command(p),headers={'Idempotency-Key':key or str(uuid4())})


async def contact(client,sid,outcome='no_answer',attempt=0):
    return await client.post(f'/api/v1/staff/sessions/{sid}/contact-events',json={'event_id':str(uuid4()),'case_revision':1,'expected_call_attempt':attempt,'outcome':outcome})


async def test_staff_projection_read_only_and_auth_isolation(setup):
    client,app,services=setup
    assert (await client.get('/api/v1/staff/applications')).status_code==403
    await staff(client)
    for _ in range(2):
        tasks=(await client.get('/api/v1/staff/applications')).json()
        assert len(tasks)==3 and tasks[0]['snapshot'] is None
        assert tasks[0]['brand_name']=='合成品牌1' and tasks[0]['routing_decision']['evidence']
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Conversation))==0
        assert db.scalar(select(func.count()).select_from(Operation))==0
    sid=await create_case(client,1)
    assert (await client.get(f'/api/v1/sessions/{sid}')).json()['reason'] is None
    await staff(client,'other')
    assert (await client.get(f'/api/v1/staff/applications/{fixture_id(1)}')).status_code==404
    assert (await client.get(f'/api/v1/staff/sessions/{sid}/written-preview')).status_code==404
    await client.post('/api/v1/auth/login',json={'username':'demo','password':'demo-local-only'})
    assert (await client.get('/api/v1/staff/applications')).status_code==403


async def test_direct_jingmai_preview_execute_idempotent_and_private(setup):
    client,app,services=setup;seed_workbench();await staff(client)
    sid=await create_case(client,4);p=await preview(client,sid)
    assert p['previewable'] and p['executable'] and p['knowledge_entry_id']
    with Session() as db:assert db.scalar(select(func.count()).select_from(Effect))==0
    key=str(uuid4());r=await reject(client,sid,p,key);assert r.status_code==202,r.text
    assert (await reject(client,sid,p,key)).status_code==202
    final=await wait_closed(client,sid)
    assert final['route']=='jingmai' and not final['verified']
    assert final['record']['reason_codes']==[] and final['record']['source_refs']==[]
    assert final['record']['contact_status']=='unknown'
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Effect))==1
        operation=db.scalar(select(Operation))
        assert operation.data['command']['payload']['written_text']==p['written_text']


async def test_phone_hangup_then_staff_written_reuses_evidence_and_updates_record(setup):
    client,app,services=setup;await staff(client)
    sid=await opened(client);await say(client,sid,'我是演示商家1')
    await client.post(f'/api/v1/sessions/{sid}/end',json={'reason':'user_ended'})
    p=await preview(client,sid);assert p['executable'],p
    assert '模拟书面驳回' not in p['written_text'] or '模拟' in p['written_text']
    r=await reject(client,sid,p);assert r.status_code==202,r.text
    final=await wait_closed(client,sid)
    assert final['record']['record_revision']==2
    assert len(final['record']['operation_refs'])==1
    with Session() as db:assert db.scalar(select(func.count()).select_from(Effect))==1


@pytest.mark.parametrize('change',['body','case','knowledge','rule','extra_text'])
async def test_stale_or_tampered_preview_refused(setup,change):
    client,app,services=setup;seed_workbench();await staff(client)
    sid=await create_case(client,4);p=await preview(client,sid);body=command(p)
    if change=='body':body['body_sha256']='f'*64
    if change=='extra_text':body['written_text']='任意正文'
    if change in ('case','knowledge','rule'):
        with Session.begin() as db:
            if change=='case':
                row=db.get(Application,fixture_id(4));row.data={**row.data,'case_revision':2}
            elif change=='knowledge':
                row=db.scalar(select(Knowledge).where(Knowledge.data['scenario_id'].as_string()=='demo.no_brand'));row.data={**row.data,'approved_text':'变更后的正文'}
            else:
                row=db.get(Conversation,sid);row.data={**row.data,'rule_version':'stale-rule'}
    r=await client.post(f'/api/v1/staff/sessions/{sid}/reject',json=body,headers={'Idempotency-Key':str(uuid4())})
    assert r.status_code in (403,409,422),r.text
    with Session() as db:assert db.scalar(select(func.count()).select_from(Effect))==0


@pytest.mark.parametrize('fault',['unreviewed','missing_slot','conflict','phone_only'])
async def test_missing_reviewed_written_knowledge_never_executes(setup,fault):
    client,app,services=setup;await staff(client)
    sid=await create_case(client,1)
    with Session.begin() as db:
        row=db.scalar(select(Knowledge).where(Knowledge.data['scenario_id'].as_string()=='demo.authorization_missing',Knowledge.data['channel'].as_string()=='jingmai'))
        if fault=='unreviewed':row.data={**row.data,'status':'draft'}
        elif fault=='phone_only':row.data={**row.data,'channel':'phone'}
        elif fault=='missing_slot':
            a=db.get(Application,fixture_id(1));a.data={**a.data,'slots':{**a.data['slots'],'brand_name':''}}
        else:
            key=str(uuid4());db.add(Knowledge(id=key,version='demo-k1',data={**row.data,'entry_id':key,'approved_text':'冲突的书面正文'}))
    p=await preview(client,sid);assert not p['previewable'] and not p['executable']
    assert p['unavailable_reasons']


async def test_new_written_entry_does_not_change_default_phone_route(setup):
    client,app,services=setup;await staff(client)
    tasks=(await client.get('/api/v1/staff/applications')).json()
    assert all(t['default_route']=='phone' and t['effective_route']=='phone' for t in tasks)
    assert (await client.get('/api/v1/staff/support-catalog')).json()['written_supported']==3
    sid=await create_case(client,1);assert not (await preview(client,sid))['executable']


async def test_actual_synthetic_no_answer_and_second_contact_loop(setup):
    client,app,services=setup;await staff(client)
    sid=await create_case(client,1)
    r=await contact(client,sid);assert r.status_code==200,r.text
    assert r.json()['route']=='jingmai'
    p=await preview(client,sid);assert p['executable'],p
    assert (await reject(client,sid,p)).status_code==202
    final=await wait_closed(client,sid);assert final['record']['contact_status']=='no_answer'
    with Session.begin() as db:
        a=db.get(Application,fixture_id(2));a.data={**a.data,'submission_attempt':2}
    first=await create_case(client,2)
    assert (await contact(client,first)).status_code==200
    await wait_closed(client,first)
    with Session() as db:
        operation=db.scalar(select(Operation).where(Operation.session_id==first))
        assert operation.data['case_snapshot']['call_attempt']==1
        assert operation.data['case_snapshot']['contact_event_id'] is not None
    second=await create_case(client,2)
    assert (await contact(client,second,attempt=1)).status_code==200
    assert (await preview(client,second))['executable']
    assert (await reject(client,second)).status_code==202
    final=await wait_closed(client,second);assert final['record']['contact_status']=='no_answer'
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Effect))==3  # reject + followup + reject


async def test_voice_write_gate_cannot_be_bypassed_by_staff(setup):
    client,app,services=setup;seed_workbench();await staff(client)
    sid=await create_case(client,4)
    with Session.begin() as db:
        row=db.get(Conversation,sid);row.data={**row.data,'mode':'voice','voice_explanation_verified':True}
    p=await preview(client,sid)
    assert not p['executable'] and any('VOICE_EXPLANATION_UNVERIFIED' in r for r in p['unavailable_reasons'])
    r=await contact(client,sid);assert r.status_code==403


@pytest.mark.parametrize('outcome',['wrong_person','refused','disconnected','unknown','connected'])
async def test_non_no_answer_outcomes_never_get_jingmai_exception(setup,outcome):
    client,app,services=setup;await staff(client)
    sid=await create_case(client,1);r=await contact(client,sid,outcome)
    assert r.status_code==200,r.text
    assert r.json()['route']!='jingmai'
    assert not (await preview(client,sid))['executable']


@pytest.mark.parametrize('fault',['lost_response','writeback_failure'])
async def test_staff_dispatch_recovers_without_repeating_effect(setup,fault):
    client,app,services=setup;seed_workbench();await staff(client)
    sid=await create_case(client,4);services.business.fault=fault
    assert (await reject(client,sid)).status_code==202
    if fault=='writeback_failure':
        for _ in range(100):
            with Session() as db:
                op=db.scalar(select(Operation).where(Operation.session_id==sid))
                attempts=op.data.get('writeback_attempts',0)
            if attempts:break
            await asyncio.sleep(.02)
        assert attempts==1
        with Session() as db:
            assert db.scalar(select(func.count()).select_from(Effect))==1
            assert db.scalar(select(func.count()).select_from(Record))==0
        services.business.fault='none'
    await wait_closed(client,sid)
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Effect))==1
        assert db.scalar(select(func.count()).select_from(Operation))==1
        assert db.scalar(select(func.count()).select_from(Record))==1


async def test_parallel_staff_submissions_share_one_business_effect(setup):
    client,app,services=setup;seed_workbench();await staff(client)
    sid=await create_case(client,4);p=await preview(client,sid);key=str(uuid4())
    results=await asyncio.gather(reject(client,sid,p,key),reject(client,sid,p,key))
    assert [r.status_code for r in results]==[202,202]
    await wait_closed(client,sid)
    # A different session cannot repeat the same application/revision rejection.
    other=await create_case(client,4)
    response=await reject(client,other)
    assert response.status_code in (202,403,409)
    with Session() as db:assert db.scalar(select(func.count()).select_from(Effect))==1


async def test_contact_event_receipt_prevents_duplicate_or_changed_fact(setup):
    client,app,services=setup;await staff(client)
    sid=await create_case(client,1)
    body={'event_id':str(uuid4()),'case_revision':1,'expected_call_attempt':0,'outcome':'no_answer'}
    endpoint=f'/api/v1/staff/sessions/{sid}/contact-events'
    assert (await client.post(endpoint,json=body)).status_code==200
    assert (await client.post(endpoint,json=body)).status_code==200
    assert (await client.post(endpoint,json={**body,'outcome':'connected'})).status_code==409
    task=(await client.get(f'/api/v1/staff/applications/{fixture_id(1)}')).json()
    assert task['call_attempt']==1 and task['contact_outcome']=='no_answer'


async def test_callback_and_complaint_priority_do_not_use_no_answer_rejection(setup):
    client,app,services=setup;seed_workbench();await staff(client)
    with Session.begin() as db:
        row=db.get(Application,fixture_id(4));row.data={**row.data,'callback_requested':True}
    sid=await create_case(client,4)
    assert (await contact(client,sid)).json()['route']=='phone'
    assert not (await preview(client,sid))['executable']


async def test_no_brand_template_rejects_contradictory_application_facts(setup):
    client,app,services=setup;seed_workbench();await staff(client)
    with Session.begin() as db:
        row=db.get(Application,fixture_id(4));row.data={**row.data,'slots':{**row.data['slots'],'brand_name':'其他具体品牌'}}
    sid=await create_case(client,4);p=await preview(client,sid)
    assert not p['previewable'] and any('WRITTEN_FACT_CONFLICT' in r for r in p['unavailable_reasons'])


async def test_old_revision_completion_is_history_not_current_task_success(setup):
    client,app,services=setup;seed_workbench();await staff(client)
    sid=await create_case(client,4)
    assert (await reject(client,sid)).status_code==202
    await wait_closed(client,sid)
    with Session.begin() as db:
        row=db.get(Application,fixture_id(4));row.data={**row.data,'case_revision':2}
    task=(await client.get(f'/api/v1/staff/applications/{fixture_id(4)}')).json()
    assert task['snapshot'] is None and task['state']=='CASE_READY' and task['case_revision']==2
    assert (await client.get(f'/api/v1/staff/sessions/{sid}')).json()['state']=='CLOSED'


async def test_historical_attempt_count_is_not_followup_evidence(setup):
    client,app,services=setup;seed_workbench();await staff(client)
    sid=await create_case(client,6)  # historical count=1, no trusted prior missed-call event
    assert (await contact(client,sid,attempt=1)).status_code==200
    p=await preview(client,sid)
    assert p['previewable'] and not p['executable']
    assert any('FOLLOWUP_EVIDENCE_REQUIRED' in r for r in p['unavailable_reasons'])
    with Session() as db:assert db.scalar(select(func.count()).select_from(Effect))==0
