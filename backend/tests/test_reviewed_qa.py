from uuid import uuid4
from app.agent import SemanticAgent
from test_semantic import make_case,make_entry,make_context,make_routing,turn


async def test_scoped_fixed_template_answer_has_reviewed_provenance():
    case=make_case();phone=make_entry(case)
    qa=phone.model_copy(update={'entry_id':uuid4(),'kind':'qa','taxonomy':['qa.fixed_template'],
        'approved_text':'无法按照模板开具的，建议联系运营进行特殊报备，报备完成后重新提交申请。'})
    result=await turn(SemanticAgent(),'品牌方使用固定模板，无法改动',case,(phone,qa),make_context(case,routing_decision=make_routing(case)),True)
    assert qa.approved_text in result.reply_text and qa.entry_id in result.knowledge_entry_ids
    assert result.proposed_action=='none'


async def test_reviewed_qa_cannot_disclose_before_identity():
    case=make_case();phone=make_entry(case)
    qa=phone.model_copy(update={'entry_id':uuid4(),'kind':'qa','taxonomy':['qa.fixed_template'],'approved_text':'审核后才能提供的具体整改事实'})
    result=await turn(SemanticAgent(),'固定模板改不了怎么办',case,(phone,qa),make_context(case,routing_decision=make_routing(case)),False)
    assert qa.approved_text not in result.reply_text and result.knowledge_entry_ids==[]


async def test_same_qa_topic_conflict_requires_manual_review():
    case=make_case();phone=make_entry(case)
    first=phone.model_copy(update={'entry_id':uuid4(),'kind':'qa','taxonomy':['qa.contact_upload'],'approved_text':'在申请单上传'})
    second=first.model_copy(update={'entry_id':uuid4(),'approved_text':'禁止在申请单上传'})
    result=await turn(SemanticAgent(),'联系方式在哪里上传',case,(phone,first,second),make_context(case,routing_decision=make_routing(case)),True)
    assert result.next_state=='MANUAL_REVIEW' and result.proposed_action=='none'


async def test_wrong_scenario_qa_is_not_used_as_general_rectification():
    case=make_case();phone=make_entry(case)
    other=phone.model_copy(update={'entry_id':uuid4(),'scenario_id':'other-scenario','kind':'qa','taxonomy':['qa.chain_standard'],'approved_text':'不适用的链路整改'})
    result=await turn(SemanticAgent(),'正规经销商的授权可以吗',case,(phone,other),make_context(case,routing_decision=make_routing(case)),True)
    assert other.approved_text not in result.reply_text
