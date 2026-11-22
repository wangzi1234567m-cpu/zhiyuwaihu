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
                       'brand_name': f'合成品牌{i}', 'missing_items': '授权期限',
                       'authorization_from': '模拟品牌方', 'authorization_to': '模拟经销商'},
                submission_attempt=1, call_attempt=0, callback_requested=False, complaint_risk=False)
            entry = KnowledgeEntry(entry_id=fixture_id(200+i), knowledge_version='demo-k1',
                kind='scenario', scenario_id=case.scenario_id, scope=['synthetic'], channel='jingmai' if scenario == 'no_brand' else 'phone',
                required_slots=['company_name'], approved_text=f'{reason}。{remedy}',
                source_refs=[SourceRef(file_sha256='0'*64, sheet='synthetic-fixtures-not-official', row=i, columns=['reason','remedy'])],
                status='approved', issue_codes=[], reviewer='synthetic-fixture')
            db.execute(insert(Application).values(id=str(case.application_id), owner='demo', data=case.model_dump(mode='json')).on_conflict_do_nothing())
            db.execute(insert(Knowledge).values(id=str(entry.entry_id), version='demo-k1', data=entry.model_dump(mode='json')).on_conflict_do_nothing())

        if not include_routing_fixtures:
            return

        # Historical contact facts belong to the synthetic task, never to browser input.
        for i, submission, calls in [(5, 1, 1), (6, 2, 1), (7, 2, 2)]:
            case = ReviewCase(application_id=fixture_id(i), merchant_id=fixture_id(100+i),
                case_revision=1, scenario_id='demo.unanswered_authorization', review_conclusion='rejected',
                slots={'company_name': f'演示商家{i}', 'reason':'模拟授权书缺项',
                       'remedy':'请补齐模拟授权材料。', 'mock_contact_outcome':'no_answer'},
                submission_attempt=submission, call_attempt=calls, callback_requested=False, complaint_risk=False)
            db.execute(insert(Application).values(id=str(case.application_id),owner='demo',
                       data=case.model_dump(mode='json')).on_conflict_do_nothing())
        for i, channel in enumerate(['phone','jingmai'], 5):
            entry = KnowledgeEntry(entry_id=fixture_id(200+i), knowledge_version='demo-k1',
                kind='scenario' if channel=='phone' else 'script', scenario_id='demo.unanswered_authorization',
                scope=['synthetic'], channel=channel, required_slots=['company_name'],
                approved_text='模拟授权书缺项。请补齐模拟授权材料。',
                source_refs=[SourceRef(file_sha256='0'*64, sheet='synthetic-fixtures-not-official',
                                       row=i, columns=['reason','remedy'])],
                status='approved', issue_codes=[], reviewer='synthetic-fixture')
            db.execute(insert(Knowledge).values(id=str(entry.entry_id),version='demo-k1',
                       data=entry.model_dump(mode='json')).on_conflict_do_nothing())

if __name__ == '__main__':
    seed()
    print('Synthetic seed ready (idempotent).')
