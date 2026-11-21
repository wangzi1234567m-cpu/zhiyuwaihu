import asyncio
from uuid import uuid4
from app.adapters import DemoAgent,ToolGuard,DomainError
from app.contracts import *
import pytest

def fixtures():
    case=ReviewCase(application_id=uuid4(),merchant_id=uuid4(),case_revision=1,scenario_id='demo.authorization_missing',slots={'company_name':'演示商家1','reason':'模拟缺项','remedy':'补充模拟材料'},review_conclusion='rejected',submission_attempt=1,call_attempt=0,callback_requested=False,complaint_risk=False)
    entry=KnowledgeEntry(entry_id=uuid4(),knowledge_version='demo-k1',kind='scenario',scenario_id=case.scenario_id,scope=['synthetic'],channel='phone',required_slots=[],approved_text='模拟缺项。补充模拟材料。',source_refs=[SourceRef(file_sha256='0'*64,sheet='synthetic',row=1,columns=['text'])],status='approved',issue_codes=[])
    context=SessionContext(session_id=uuid4(),turn_id=uuid4(),generation_id=uuid4(),generation_epoch=0,application_id=case.application_id,case_revision=1,state_revision=0,strategy_version='demo-p1',rule_version='demo-r1',knowledge_version='demo-k1',model_version='fake-v1')
    return case,entry,context

@pytest.mark.parametrize('text,verified,expected',[('我是演示商家1',False,'VERIFYING'),('不是，打错了',False,'CLOSING'),('不需要，拒绝',True,'CLOSING'),('材料补齐必过吗',True,'QA'),('明白了',True,'ACTION_READY'),('预约回电',True,'MANUAL_REVIEW')])
async def test_agent_branch(text,verified,expected):
    case,entry,context=fixtures()
    result=await DemoAgent().handle_turn(TurnInput(message_id=uuid4(),text=text,source='text'),context,(),case,(entry,),verified,asyncio.Event())
    assert result.next_state==expected
    if not verified: assert '模拟缺项' not in result.reply_text

async def test_missing_knowledge_never_invents_answer():
    case,_,context=fixtures()
    result=await DemoAgent().handle_turn(TurnInput(message_id=uuid4(),text='怎么处理',source='text'),context,(),case,(),True,asyncio.Event())
    assert result.next_state=='MANUAL_REVIEW' and result.proposed_action=='none'

@pytest.mark.parametrize('changes',[{'verified':False},{'explained':False},{'ended':True},{'route':'manual'},{'mode':'voice'}])
def test_guard_requires_server_evidence(changes):
    case,entry,context=fixtures()
    command=ActionCommand(session_id=context.session_id,application_id=case.application_id,case_revision=1,decision_id=uuid4(),operation_id=uuid4(),kind='reject_application',idempotency_key='x',evidence_refs=[],payload=RejectPayload(type='reject_application',reason_codes=[case.scenario_id],written_text=entry.approved_text,knowledge_entry_ids=[entry.entry_id]))
    data={'verified':True,'explained':True,'ended':False,'route':'phone',**changes}
    with pytest.raises(DomainError): ToolGuard().authorize(case,data,command,[entry])
