import asyncio
import os
from sqlalchemy import select
from .config import settings
from .knowledge_templates import render
from .contracts import *
from .db import Session, Application, Knowledge, Effect, Record, Operation, Confirmation, Conversation, Message, now, new_id

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
    """历史演示夹具（关键字版）。**不是生产 Agent**，只用于保留原演示/回归夹具。

    生产使用 `app.agent.SemanticAgent`（结构化理解驱动）。此处保留是为了让原始
    演示行为与旧测试可重复，并且不把它当作"意图理解已完成"的证据。
    """
    async def handle_turn(self, input, context, history, case, knowledge, verified, cancel):
        text = input.text
        state = 'VERIFYING'
        reply = f'请确认您是否是{case.slots["company_name"]}的联系人。请说明您与该公司或店铺的关系。'
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
                payload = None
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
        if session_data.get('mode')=='voice' and not session_data.get('voice_explanation_verified'):
            raise DomainError('VOICE_EXPLANATION_UNVERIFIED','尚无可信语音告知完成证据',403)
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
        # Cancellation before this checkpoint cannot create a business effect.
        await asyncio.sleep(0)
        with Session.begin() as db:
            operation = db.scalar(select(Operation).where(
                Operation.id == str(command.operation_id)).with_for_update())
            if (operation is None or operation.key != command.idempotency_key
                    or operation.session_id != str(command.session_id)):
                raise DomainError('NO_OPERATION', '操作未登记')
            if ActionCommand.model_validate(operation.data['command']) != command:
                raise DomainError('OPERATION_CONFLICT', '操作与已登记命令不一致')

            effect = db.get(Effect, command.idempotency_key)
            if effect is None:
                if 'mock_failure' in operation.data:
                    return OperationResult.model_validate(operation.data['mock_failure'])
                if self.fault == 'persistent_unknown':
                    return OperationResult(operation_id=command.operation_id, status='unknown', updated_at=now())
                result, details = self._apply(db, command)
                if result.status == 'failed':
                    operation.data = {**operation.data, 'mock_failure': result.model_dump(mode='json')}
                    return result
                effect = Effect(key=command.idempotency_key, operation_id=str(command.operation_id),
                    data={'resource_id': result.resource_id, 'result': result.model_dump(mode='json'),
                          'command': command.model_dump(mode='json'), 'details': details})
                db.add(effect)
            else:
                result = self._saved_result(command, effect)
        if self.fault == 'lost_response':
            return OperationResult(operation_id=command.operation_id, status='unknown', updated_at=now())
        return result

    def _apply(self, db, command):
        prefix = {'reject_application': 'rejection', 'create_callback': 'callback',
                  'create_followup': 'followup'}.get(command.kind)
        if prefix is None:
            raise DomainError('INVALID_ACTION', '通话记录须通过 writeback 写入')

        details = {}
        error = None
        session = db.get(Conversation, str(command.session_id))
        application = db.scalar(select(Application).where(
            Application.id == str(command.application_id)).with_for_update())
        case = ReviewCase.model_validate(application.data) if application else None
        if (session is None or case is None or application.owner != session.owner
                or case.application_id != command.application_id or session.application_id != str(command.application_id)
                or session.data['case_revision'] != command.case_revision):
            error = ('CASE_MISMATCH', '操作与会话的申请单或审核版本不一致')
        elif case.case_revision != command.case_revision:
            error = ('STALE_REVISION', '审核任务已更新，需要重新核实')
        elif isinstance(command.payload, RejectPayload) and case.review_conclusion != 'rejected':
            error = ('ACTION_FORBIDDEN', '当前审核结论不允许驳回')
        elif self.fault == 'write_failure':
            error = ('MOCK_WRITE_FAILED', '模拟业务写入失败')
        elif isinstance(command.payload, RejectPayload):
            payload = command.payload
            from .written import WrittenResponseRenderer
            rows=list(db.scalars(select(Knowledge).where(Knowledge.version==session.data['knowledge_version'],
                Knowledge.data['scenario_id'].as_string()==case.scenario_id).with_for_update()))
            try:
                authoritative=WrittenResponseRenderer().render(case,[KnowledgeEntry.model_validate(row.data) for row in rows],session.data['knowledge_version'])
            except DomainError:
                authoritative=None
            if (authoritative is None or payload.knowledge_entry_ids!=[authoritative.entry_id]
                    or payload.written_text!=authoritative.approved_text or payload.reason_codes != [case.scenario_id]):
                error = ('ACTION_FORBIDDEN', '缺少可渲染的已审核依据或书面意见与审核任务不一致')
            else:
                details = payload.model_dump(mode='json', exclude={'type'})
        elif isinstance(command.payload, CallbackPayload):
            confirmation = self._callback_confirmation(db, session, command)
            if confirmation is None:
                error = ('CALLBACK_CONFIRMATION_INVALID', '缺少有效的预约确认记录')
            else:
                details = confirmation.model_dump(mode='json', include={'window_start', 'window_end', 'timezone'})
        elif isinstance(command.payload, FollowupPayload) and command.payload.related_operation_id:
            related = db.get(Operation, str(command.payload.related_operation_id))
            related_command = ActionCommand.model_validate(related.data['command']) if related else None
            if (related_command is None or related_command.application_id != command.application_id
                    or related_command.case_revision != command.case_revision):
                error = ('FOLLOWUP_REFERENCE_INVALID', '跟进引用与当前申请单或审核版本不一致')

        if error:
            code, message = error
            return OperationResult(operation_id=command.operation_id, status='failed', updated_at=now(),
                error=ApiError(code=code, message=message, retryable=False,
                               request_id=new_id(), operation_id=command.operation_id)), details
        return OperationResult(operation_id=command.operation_id, status='succeeded',
                               resource_id=f'mock-{prefix}-{command.operation_id}', updated_at=now()), details

    def _callback_confirmation(self, db, session, command):
        row = db.scalar(select(Confirmation).where(
            Confirmation.id == str(command.payload.confirmation_id)).with_for_update())
        if row is None or row.session_id != session.id or not session.data.get('verified'):
            return None
        confirmation = CallbackConfirmation.model_validate(row.data)
        current = now()
        if (confirmation.application_id != command.application_id
                or confirmation.case_revision != command.case_revision
                or confirmation.revoked_at is not None
                or confirmation.expires_at <= current
                or confirmation.window_start <= current
                or confirmation.evidence_message_id not in command.evidence_refs):
            return None
        evidence = db.get(Message, str(confirmation.evidence_message_id))
        if evidence is None or evidence.session_id != session.id:
            return None
        message = evidence.data['view']
        if message['role'] != 'user' or message['status'] != 'completed':
            return None
        return confirmation

    def _saved_result(self, command, effect):
        if (effect.operation_id != str(command.operation_id)
                or ActionCommand.model_validate(effect.data['command']) != command):
            raise DomainError('OPERATION_CONFLICT', '操作与已登记命令不一致')
        if 'result' in effect.data:
            return OperationResult.model_validate(effect.data['result'])
        return OperationResult(operation_id=command.operation_id, status='succeeded',
                               resource_id=effect.data['resource_id'], updated_at=now())

    async def query_operation(self, command):
        if self.fault == 'query_timeout':
            raise TimeoutError('Mock operation lookup timed out')
        if self.fault == 'query_failure':
            raise DomainError('QUERY_UNAVAILABLE', '模拟查证服务暂不可用', 503)
        with Session() as db:
            effect = db.get(Effect, command.idempotency_key)
            if effect is not None:
                return self._saved_result(command, effect)
            operation = db.get(Operation, str(command.operation_id))
            if operation and 'mock_failure' in operation.data:
                if ActionCommand.model_validate(operation.data['command']) != command:
                    raise DomainError('OPERATION_CONFLICT', '操作与已登记命令不一致')
                return OperationResult.model_validate(operation.data['mock_failure'])
        return OperationResult(operation_id=command.operation_id, status='not_found_yet', updated_at=now())

    async def writeback(self, record):
        if self.fault == 'writeback_failure':
            raise DomainError('WRITEBACK_FAILED', '模拟回写失败', 503)
        with Session.begin() as db:
            existing = db.scalar(select(Record).where(Record.session_id == str(record.session_id)))
            if existing is None:
                db.add(Record(id=str(record.record_id), session_id=str(record.session_id), data=record.model_dump(mode='json')))
            elif (existing.id==str(record.record_id) and record.record_revision>existing.data['record_revision']
                  and existing.data['application_id']==str(record.application_id) and existing.data['case_revision']==record.case_revision):
                existing.data=record.model_dump(mode='json')

class DisabledVoice:
    def available(self): return False

    async def credentials(self, session_id):
        raise DomainError('VOICE_NOT_CONFIGURED', '语音未配置', 503)

class Services:
    '''依赖装配点：所有内部端口在此注入，模块之间不建 HTTP。

    默认是**可重复的确定性实现**（FakeModel 流式 + 规则理解）；真实模型只由
    MODEL_PROVIDER 决定，不读取旧 ZHIYU_MODEL_MODE 自动启用 GLM，也不把团队 model
    替换为工作台网关。understanding 可显式注入以便 MockTransport 测试；生产结构化
    模型端口留待架构协商，不新增公共 ModelPort。
    '''

    def __init__(self, *, agent=None, model=None, cases=None, knowledge=None, business=None,
                 policy=None, guard=None, voice=None, repository=None, understanding=None,
                 model_mode=None):
        from .repository import Repository
        from .guard import RoutingPolicy, ToolGuard as BusinessToolGuard
        from .agent import SemanticAgent
        from .semantic import RuleUnderstanding
        self.repository = repository or Repository()
        if model_mode not in (None, 'fake'):
            raise DomainError('MODEL_MODE_UNSUPPORTED',
                              'model_mode 仅兼容 fake/None；生产模型请使用 MODEL_PROVIDER，测试 understanding 请显式注入', 503)
        self.understanding = understanding or RuleUnderstanding()
        self.agent = agent or SemanticAgent(self.understanding)
        if model is not None:
            self.model = model
        elif settings.model_provider == 'deepseek':
            from .deepseek import DeepSeekModel
            from .cloud_budget import reserve_call, observe_model_first_content
            self.model = DeepSeekModel(reserve=reserve_call, observe=observe_model_first_content)
        else:
            self.model = FakeModel()
        self.cases = cases or DatabaseCases()
        self.knowledge = knowledge or DatabaseKnowledge()
        self.business = business or MockBusiness()
        self.policy = policy or RoutingPolicy()
        self.guard = guard or BusinessToolGuard()
        if voice is not None:
            self.voice = voice
        else:
            from .voice import AgoraVoiceGateway
            self.voice = AgoraVoiceGateway()

    def model_info(self) -> dict:
        '''只报告 MODEL_PROVIDER 与配置版本，不读取或输出工作台 GLM key。'''
        provider = (getattr(settings, 'model_provider', 'fake') or 'fake').strip().lower()
        model_id = getattr(settings, 'deepseek_model', 'fake-v1') if provider == 'deepseek' else 'fake-v1'
        view = {'mode': provider, 'provider': provider, 'model_id': model_id,
                'config_version': getattr(settings, 'config_version', 'env')}
        port = getattr(self.understanding, 'public_view', None)
        if callable(port):
            view['understanding'] = port()
        return view
