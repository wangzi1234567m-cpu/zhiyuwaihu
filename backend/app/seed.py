"""Only synthetic fixtures. Run after alembic upgrade head."""
from uuid import UUID
from sqlalchemy.dialects.postgresql import insert
from .db import Session, Application, Knowledge
from .contracts import ReviewCase, KnowledgeEntry, SourceRef

def fixture_id(n: int) -> str:
    return str(UUID(int=n))

def seed(*, include_routing_fixtures=False):
    with Session.begin() as db:
        for i, (scenario, reason, remedy) in enumerate([
            ('authorization_missing', '模拟授权书缺项', '请在模拟业务入口补充完整授权书。'),
            ('authorization_expired', '模拟授权期限已过', '请提供覆盖当前申请日期的有效授权。'),
            ('authorization_chain', '模拟授权链不完整', '请补齐品牌方至申请主体之间的授权链。'),
            ('no_brand', '模拟类目不接受无品牌申请', '请核对所选类目的品牌要求。'),
        ], 1):
            if scenario == 'no_brand' and not include_routing_fixtures:
                continue
            case = ReviewCase(application_id=fixture_id(i), merchant_id=fixture_id(100+i),
                case_revision=1, scenario_id=f'demo.{scenario}', review_conclusion='rejected',
                slots={'company_name': f'演示商家{i}', 'reason': reason, 'remedy': remedy,
                       'brand_name': '无品牌' if scenario=='no_brand' else f'合成品牌{i}', 'missing_items': '授权期限',
                       'authorization_from': '模拟品牌方', 'authorization_to': '模拟经销商',
                       'missing_document':'被授权方盖章','authorization_expiry':'2026-10-31','qualification_name':'资质信息','invalid_fact':'修改失真'},
                submission_attempt=1, call_attempt=0, callback_requested=False, complaint_risk=False,default_route='jingmai' if scenario=='no_brand' else 'phone',
                scene_name={'authorization_missing':'授权书内容不完整','authorization_expired':'授权时间到期','authorization_chain':'授权链路不完整','no_brand':'无品牌申请'}[scenario],business_category='品牌 / 授权书-文审')
            entry = KnowledgeEntry(entry_id=fixture_id(200+i), knowledge_version='demo-k1',
                kind='scenario', scenario_id=case.scenario_id, scope=['synthetic'], channel='jingmai' if scenario == 'no_brand' else 'phone',
                required_slots=['company_name'], approved_text=f'{reason}。{remedy}',
                source_refs=[SourceRef(file_sha256='0'*64, sheet='synthetic-fixtures-not-official', row=i, columns=['reason','remedy'])],
                status='approved', issue_codes=[], reviewer='synthetic-fixture')
            db.execute(insert(Application).values(id=str(case.application_id), owner='demo', data=case.model_dump(mode='json')).on_conflict_do_nothing())
            db.execute(insert(Knowledge).values(id=str(entry.entry_id), version='demo-k1', data=entry.model_dump(mode='json')).on_conflict_do_nothing())

        _written_fixtures(db)
        if not include_routing_fixtures:
            return

        # Historical contact facts belong to the synthetic task, never to browser input.
        for i, submission, calls in [(5, 1, 1), (6, 2, 1), (7, 2, 2)]:
            case = ReviewCase(application_id=fixture_id(i), merchant_id=fixture_id(100+i),
                case_revision=1, scenario_id='demo.authorization_missing', review_conclusion='rejected',
                slots={'company_name': f'演示商家{i}', 'brand_name':f'合成品牌{i}', 'missing_items':'授权期限','missing_document':'被授权方盖章','authorization_expiry':'2026-10-31','reason':'模拟授权书缺项',
                       'remedy':'请补齐模拟授权材料。', 'mock_contact_outcome':'no_answer'},
                submission_attempt=submission, call_attempt=calls, callback_requested=False, complaint_risk=False,
                scene_name='授权书内容不完整（联系分流演示）',business_category='品牌 / 授权书-文审')
            db.execute(insert(Application).values(id=str(case.application_id),owner='demo',
                       data=case.model_dump(mode='json')).on_conflict_do_nothing())
        _written_fixtures(db)


def _written_fixtures(db):
    from sqlalchemy import select
    for row in db.scalars(select(Application).where(Application.owner=='demo')):
        case=ReviewCase.model_validate(row.data)
        # Synthetic fixture only; official Excel candidates require the review pipeline.
        key=fixture_id(1000+int(case.application_id))
        entry=KnowledgeEntry(entry_id=key,knowledge_version='demo-k1',kind='script',scenario_id=case.scenario_id,
            scope=['synthetic'],channel='jingmai',required_slots=['company_name','brand_name'],
            approved_text='【模拟演示】尊敬的{company_name}，关于{brand_name}的资质申请：'+case.slots['reason']+'。'+case.slots['remedy'],
            source_refs=[SourceRef(file_sha256='0'*64,sheet='synthetic-fixtures-not-official',row=int(case.application_id),columns=['reason','remedy'])],
            status='approved',issue_codes=[],reviewer='synthetic-fixture')
        # A scenario shared by several tasks has exactly one written template.
        existing=db.scalar(select(Knowledge).where(Knowledge.version=='demo-k1',Knowledge.data['scenario_id'].as_string()==case.scenario_id,Knowledge.data['channel'].as_string()=='jingmai'))
        if existing is None:
            db.execute(insert(Knowledge).values(id=key,version='demo-k1',data=entry.model_dump(mode='json')).on_conflict_do_nothing())


def seed_workbench():
    seed(include_routing_fixtures=True)
    with Session.begin() as db:
        for i,scenario,name,reason,remedy,route,submission in [
            (8,'demo.qualification_fraud','资质造假','模拟审核结论为资质信息失真','请提供真实有效的资质资料。','jingmai',1),
            (9,'demo.governance_disabled','治理停用','模拟业务治理停用','请按当前治理要求处理，本演示不承诺恢复时限。','jingmai',1),
            (11,'demo.authorization_missing','二次错误提交及再次联系','模拟授权书缺项','请在模拟业务入口补充完整授权书。','phone',2),
            (10,'demo.authorization_missing','三次错误提交直接京麦','模拟授权书缺项','请在模拟业务入口补充完整授权书。','phone',3),
        ]:
            case=ReviewCase(application_id=fixture_id(i),merchant_id=fixture_id(100+i),case_revision=1,scenario_id=scenario,
                review_conclusion='rejected',slots={'company_name':f'演示商家{i}','brand_name':f'合成品牌{i}',
                'reason':reason,'remedy':remedy,'missing_items':'授权期限','authorization_from':'模拟品牌方','authorization_to':'模拟经销商','missing_document':'被授权方盖章','authorization_expiry':'2026-10-31','qualification_name':'资质信息','invalid_fact':'修改失真'},
                submission_attempt=submission,call_attempt=0,callback_requested=False,complaint_risk=False,
                default_route=route,scene_name=name,business_category='品牌 / 商标注册证' if i in (8,9) else '品牌 / 授权书-文审')
            db.execute(insert(Application).values(id=fixture_id(i),owner='demo',data=case.model_dump(mode='json')).on_conflict_do_nothing())
        _written_fixtures(db)


if __name__ == '__main__':
    seed()
    print('Synthetic seed ready (idempotent).')
