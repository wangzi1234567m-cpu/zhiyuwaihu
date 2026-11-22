"""PR3返修独立回归：真实理解适配器+MockTransport+完整写入链路；零真实供应商请求。"""
import asyncio
import json
import os
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func,select,text
from app.adapters import Services
from app.main import create_app
from app.db import Session,Effect,Operation,engine
from app.guard import ModelConfig
from app.seed import seed
from app.semantic import GlmUnderstanding,UnderstandingBudget,parse_understanding
from test_integration import opened,say,wait_closed
from test_semantic import make_case,make_context,make_entry,make_routing,turn
from app.agent import SemanticAgent


def model_port(payloads):
    replies=iter(payloads)
    def handler(request):
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(next(replies),ensure_ascii=False)}}]})
    return GlmUnderstanding(ModelConfig('glm','ybg-youlaber-ds4f','https://gateway.baigongai.cn/v1',True,'mock_transport'),
        'not-a-real-key',UnderstandingBudget(max_requests=4,max_retries=0),httpx.MockTransport(handler))


@pytest.fixture
def dedicated_db():
    assert os.getenv('RUN_DB_TESTS')=='1','Run against an explicitly enabled dedicated PostgreSQL test DB'
    assert (engine.url.database or '').endswith('_test')
    with Session.begin() as db:
        db.execute(text('TRUNCATE request_receipts,confirmations,call_records,business_effects,operations,events,messages,sessions,knowledge,applications CASCADE'))
    seed()


async def exercise(payload,user_text):
    port=model_port([{'intents':['confirm_identity'],'source':'glm'},payload])
    app=create_app(Services(understanding=port))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'X-Requested-With':'zhiyu'}) as client:
            assert (await client.post('/api/v1/dev/login',json={'username':'demo','password':'demo-local-only'})).status_code==200
            sid=await opened(client)
            verified=await say(client,sid,'我是演示商家1')
            assert verified['verified'] and verified['reason']
            result=await say(client,sid,user_text)
            if result['state'] in ('ACTION_READY','EXECUTING','QUERYING','WRITEBACK_PENDING'):
                result=await wait_closed(client,sid)
            with Session() as db:
                effects=db.scalar(select(func.count()).select_from(Effect))
                kinds=[r.data['command']['kind'] for r in db.scalars(select(Operation))]
            assert port.requests==2 and port.budget.max_retries==0
            return result,effects,kinds


@pytest.mark.asyncio
@pytest.mark.parametrize('payload',[
    {'intents':['acknowledge'],'source':'glm'},
    {'intents':['acknowledge','complaint_risk'],'source':'glm'},
    {'intents':['acknowledge'],'needs_clarification':True,'source':'glm'},
    {'intents':['acknowledge','deny_identity'],'source':'glm'},
    {'intents':['acknowledge'],'verified':True,'source':'glm'},
    {'intents':[],'source':'glm'},
])
async def test_explicit_complaint_cannot_be_lost_before_business_write(dedicated_db,payload):
    result,effects,kinds=await exercise(payload,'明白了，但我要投诉')
    assert effects==0 and 'reject_application' not in kinds
    assert result['state']!='CLOSED'


@pytest.mark.asyncio
async def test_plain_acknowledgement_still_completes_when_all_guards_hold(dedicated_db):
    result,effects,kinds=await exercise({'intents':['acknowledge'],'source':'glm'},'明白了')
    assert result['state']=='CLOSED' and effects==1 and kinds==['reject_application']


@pytest.mark.asyncio
@pytest.mark.parametrize('utterance',['不用回电，直接说原因','我不想回电','我不愿意回电'])
async def test_model_callback_false_positive_never_creates_callback(dedicated_db,utterance):
    payload={'intents':['request_callback','ask_reason'],'source':'glm'}
    result,effects,kinds=await exercise(payload,utterance)
    assert result['state']!='CALLBACK_PENDING'
    assert 'create_callback' not in kinds and effects==0
    assert result['state']!='CLOSED'


@pytest.mark.asyncio
@pytest.mark.parametrize('utterance,intent,keep',[
    ('不用回电，直接说原因','request_callback',False),
    ('我不想回电','request_callback',False),
    ('我不愿意回电','request_callback',False),
    ('我没空请回电','request_callback',True),
    ('能不能回电','request_callback',True),
    ('不得不投诉','complaint_risk',True),
    ('不用回电但请回电','request_callback',True),
    ('不用回电，明天再联系','request_callback',True),
    ('我并非不想回电','request_callback',True),
    ('我没空但请不要回电','request_callback',False),
])
async def test_scope_is_consistent_for_rule_and_actual_model_adapter(utterance,intent,keep):
    case=make_case();port=model_port([{'intents':[intent],'source':'glm'}])
    result=await port.understand(text=utterance,case=case,context=make_context(case))
    assert (intent in result.intents)==keep
    assert (intent in parse_understanding(utterance).intents)==keep


@pytest.mark.asyncio
@pytest.mark.parametrize('utterance',['打错了，我不是这家公司的人','别再打了'])
async def test_explicit_safe_negative_outcome_overrides_model_acknowledgement(dedicated_db,utterance):
    result,effects,kinds=await exercise({'intents':['acknowledge'],'source':'glm'},utterance)
    assert effects==0 and 'reject_application' not in kinds


@pytest.mark.asyncio
async def test_unrendered_approved_template_is_not_spoken_or_written():
    case=make_case(slots={'company_name':'演示商家1','brand_name':'合成品牌','reason':'合成缺项','remedy':'补齐后提交'})
    entry=make_entry(case,text='您申请的{brand_name}品牌缺少{missing_items}。',required_slots=('company_name','brand_name'))
    context=make_context(case,routing_decision=make_routing(case))
    decision=await turn(SemanticAgent(),'明白了',case,(entry,),context,True)
    assert decision.proposed_action=='none'
    assert '{brand_name}' not in decision.reply_text and '{missing_items}' not in decision.reply_text
