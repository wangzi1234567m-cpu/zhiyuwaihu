import asyncio
from sqlalchemy import select
from .config import settings
from .contracts import *
from .db import Session, Application, Knowledge, Effect, Record, Operation, now

class DomainError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        self.code, self.message, self.status = code, message, status

class DatabaseCases:
    def get_case(self, owner, application_id):
        with Session() as db:
            row = db.get(Application, str(application_id))
            if row is None or row.owner != owner:
                raise DomainError('NOT_FOUND', '申请单不存在', 404)
            return ReviewCase.model_validate(row.data)

class DatabaseKnowledge:
    def lookup(self, scenario_id, query, scope, knowledge_version):
        with Session() as db:
            entries = [KnowledgeEntry.model_validate(r.data) for r in db.scalars(select(Knowledge).where(Knowledge.version == knowledge_version))]
        return [e for e in entries if e.scenario_id == scenario_id and e.status == 'approved'
                and not e.issue_codes and set(scope).issubset(e.scope) and e.approved_text]

class FakeModel:
    async def stream(self, request, cancel):
        text = request.fixture_response
        for i in range(0, len(text), 4):
            await asyncio.sleep(settings.token_delay)
            if cancel.is_set():
                return
            yield text[i:i+4]

class DemoAgent:
    async def handle_turn(self, input, context, history, case, knowledge, verified, cancel):
        text = input.text
        state = 'VERIFYING'
        reply = f'请确认您是否是{case.slots["company_name"]}的联系人。演示中请输入“我是{case.slots["company_name"]}”。'
        action = 'none'
        payload = None
        if any(w in text for w in ['拒绝','不需要','不要联系','不是','打错']):
            state, reply = 'CLOSING', '好的，停止本次告知，后续交人工核实。再见。'
        elif verified:
            if not knowledge:
                state, reply = 'MANUAL_REVIEW', '当前缺少已审核依据，需要人工核实，暂不办理。'
            elif '必过' in text or '保证' in text:
                state, reply = 'QA', '补齐材料后仍需审核，不能保证通过。'
            elif '回电' in text or '预约' in text:
                state, reply = 'MANUAL_REVIEW', '本骨架暂不自动预约，已标记需要人工协调回电。'
            elif any(w in text for w in ['明白了','了解了','处理吧']):
                state, reply, action = 'ACTION_READY', '已记录您的反馈，正在提交模拟处理；请以页面业务结果为准。', 'reject_application'
                payload = RejectPayload(type=action, reason_codes=[case.scenario_id], written_text=knowledge[0].approved_text,
                    knowledge_entry_ids=[knowledge[0].entry_id])
            else:
                state, reply = 'QA', f'{knowledge[0].approved_text}这些均为合成演示规则。您还有什么问题？'
        return AgentDecision(reply_text=reply, proposed_action=action, action_payload=payload,
            evidence_message_ids=[input.message_id], knowledge_entry_ids=[k.entry_id for k in knowledge] if verified else [], next_state=state)

class Policy:
    def decide_route(self, case):
        # Demo only: all three reviewed fixtures require phone explanation.
        return 'phone' if case.scenario_id.startswith('demo.') else 'manual'

class ToolGuard:
    def authorize(self, case, session_data, command, entries):
        if (command.kind != 'reject_application' or case.review_conclusion != 'rejected'
            or command.case_revision != case.case_revision
            or str(command.application_id) != str(case.application_id)
            or not session_data.get('verified') or not session_data.get('explained')
            or session_data.get('ended') or session_data.get('route') != 'phone'):
            raise DomainError('ACTION_FORBIDDEN', '未满足模拟驳回条件', 403)
        allowed = {e.entry_id: e for e in entries if e.status=='approved' and not e.issue_codes
                   and e.scenario_id==case.scenario_id and e.knowledge_version==session_data.get('knowledge_version','demo-k1')}
        if not isinstance(command.payload, RejectPayload) or any(i not in allowed for i in command.payload.knowledge_entry_ids):
            raise DomainError('INVALID_EVIDENCE', '缺少已审核依据', 403)
        if command.payload.written_text not in [e.approved_text for e in entries]:
            raise DomainError('INVALID_FACTS', '书面意见与依据不一致', 403)
        if command.payload.reason_codes != [case.scenario_id]:
            raise DomainError('INVALID_FACTS', '驳回原因与申请单不一致', 403)

class MockBusiness:
    def __init__(self):
        self.fault = 'none'  # Only tests/dev administration may set this.

    async def execute(self, command):
        await asyncio.sleep(0)
        if self.fault == 'persistent_unknown':
            return OperationResult(operation_id=command.operation_id, status='unknown', updated_at=now())
        if self.fault == 'write_failure':
            return OperationResult(operation_id=command.operation_id, status='failed', updated_at=now())
        with Session.begin() as db:
            operation = db.scalar(select(Operation).where(Operation.id == str(command.operation_id)).with_for_update())
            if operation is None:
                raise DomainError('NO_OPERATION', '操作未登记')
            effect = db.get(Effect, command.idempotency_key)
            if effect is None:
                effect = Effect(key=command.idempotency_key, operation_id=str(command.operation_id),
                    data={'resource_id': 'mock-rejection-' + str(command.operation_id), 'command': command.model_dump(mode='json')})
                db.add(effect)
            resource = effect.data['resource_id']
        if self.fault == 'lost_response':
            return OperationResult(operation_id=command.operation_id, status='unknown', updated_at=now())
        return OperationResult(operation_id=command.operation_id, status='succeeded', resource_id=resource, updated_at=now())

    async def query_operation(self, command):
        with Session() as db:
            effect = db.get(Effect, command.idempotency_key)
            return OperationResult(operation_id=command.operation_id,
                status='succeeded' if effect else 'not_found_yet',
                resource_id=effect.data['resource_id'] if effect else None, updated_at=now())

    async def writeback(self, record):
        if self.fault == 'writeback_failure':
            raise DomainError('WRITEBACK_FAILED', '模拟回写失败', 503)
        with Session.begin() as db:
            existing = db.scalar(select(Record).where(Record.session_id == str(record.session_id)))
            if existing is None:
                db.add(Record(id=str(record.record_id), session_id=str(record.session_id), data=record.model_dump(mode='json')))

class DisabledVoice:
    async def credentials(self, session_id):
        raise DomainError('VOICE_NOT_CONFIGURED', '语音未配置', 503)

class Services:
    def __init__(self, *, agent=None, model=None, cases=None, knowledge=None, business=None, policy=None, guard=None, voice=None, repository=None):
        from .repository import Repository
        self.repository = repository or Repository()
        self.agent = agent or DemoAgent()
        self.model = model or FakeModel()
        self.cases = cases or DatabaseCases()
        self.knowledge = knowledge or DatabaseKnowledge()
        self.business = business or MockBusiness()
        self.policy = policy or Policy()
        self.guard = guard or ToolGuard()
        self.voice = voice or DisabledVoice()
