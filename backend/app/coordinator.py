import asyncio
import hashlib
import json
import logging
import os
from .knowledge_templates import available_entries, task_slot
from uuid import UUID
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy import select
from .contracts import *
from .db import Session, Conversation, Message, EventRow, Operation, Record, RequestReceipt, Application, Confirmation, now, new_id
from .adapters import DomainError, Services, Policy as LegacyPolicy, ToolGuard as LegacyToolGuard
from .guard import (AGENT_PROPOSABLE_STATES, GuardContext, RoutingPolicy, TURN_ALLOWED_STATES,
                    ToolGuard, business_key, may_transition,
                    validate_transition, RULE_VERSION,
                    WRITEBACK_BACKOFF_SECONDS, WRITEBACK_MAX_ATTEMPTS)
from .config import settings

log = logging.getLogger('zhiyu')

#: 允许继续对话的会话状态；业务处理中/终态不接受新轮次（与守卫表同源）。
ALLOWED_TURN_STATES = list(TURN_ALLOWED_STATES)

#: main 的生产模型模式：fake（离线默认）| deepseek。
DEFAULT_MODEL_MODE = 'fake'


def model_mode() -> str:
    '''模型模式只由 MODEL_PROVIDER 决定；不再读取旧 ZHIYU_MODEL_MODE 自动启用 GLM。'''
    return (getattr(settings, 'model_provider', 'fake') or 'fake').strip().lower()


def owned(db, sid, owner, lock=False):
    query = select(Conversation).where(Conversation.id == str(sid), Conversation.owner == owner)
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise DomainError('NOT_FOUND', '会话不存在', 404)
    return row

def emit(db, row, payload, *, turn=None, generation=None, operation=None):
    row.seq += 1
    event = Event(event_id=new_id(), session_id=row.id, seq=row.seq, state_revision=row.revision,
        occurred_at=now(), strategy_version=row.data['strategy_version'], knowledge_version=row.data['knowledge_version'],
        turn_id=turn, generation_id=generation, operation_id=operation, payload=payload)
    db.add(EventRow(id=str(event.event_id), session_id=row.id, seq=row.seq, data=event.model_dump(mode='json')))
    if row.data.get('realtime_enabled'):
        from .realtime_business.events import project_event
        project_event(db, row, event)

def transition(db, row, state):
    """唯一状态写入口：走显式转移表，非法转移拒绝。

    被拒绝的提议由调用方在**独立事务**里写入 `rejected_transitions`（见 record_rejection），
    这样证据不会随当前事务回滚而丢失，也不会制造成功回执。
    """
    if state != row.state:
        validate_transition(row.state, state)
        row.state = state
        row.revision += 1
        emit(db, row, StateChanged(type='state.changed', state=state))


def record_rejection(owner, sid, from_state, to_state, source):
    """在独立事务中留证：非法转移/非法提议必须可追溯，不能被回滚吞掉。"""
    try:
        with Session.begin() as db:
            row = owned(db, sid, owner, True)
            rejected = list(row.data.get('rejected_transitions') or [])
            rejected.append({'from': from_state, 'to': to_state,
                             'at': now().isoformat(), 'source': source})
            row.data = {**row.data, 'rejected_transitions': rejected[-20:]}
        log.warning('transition_rejected session=%s from=%s to=%s source=%s',
                    sid, from_state, to_state, source)
    except Exception as exc:  # noqa: BLE001 - 留证失败不能掩盖原错误
        log.warning('rejection_record_failed kind=%s session=%s', type(exc).__name__, sid)

def receipt(db, key, payload):
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    old = db.get(RequestReceipt, key)
    if old and old.payload_hash != digest:
        raise DomainError('IDEMPOTENCY_CONFLICT', '相同幂等键的参数不同')
    return digest, old.data if old else None

def messages(db, sid):
    return [MessageView(message_id=m.id, **m.data['view']) for m in db.scalars(
        select(Message).where(Message.session_id == sid).order_by(Message.position))]

def operation_view(operation):
    return OperationResult.model_validate({
        **operation.data['result'], 'kind': operation.data['command']['kind']
    })


def snapshot(db, row):
    ops = [operation_view(o) for o in db.scalars(select(Operation).where(Operation.session_id == row.id))]
    linked = db.get(Operation,row.data['linked_operation_id']) if row.data.get('linked_operation_id') else None
    if linked and all(str(o.operation_id)!=linked.id for o in ops):
        ops.append(operation_view(linked))
    record = db.scalar(select(Record).where(Record.session_id == row.id))
    verified = row.data.get('verified', False)
    payload = EventRow.data['payload']
    facts = select(payload).where(EventRow.session_id == row.id)
    refs = []
    if verified:
        knowledge = db.scalar(facts.where(payload['type'].as_string() == 'knowledge.used')
                              .order_by(EventRow.seq.desc()).limit(1))
        if knowledge:
            refs = knowledge['source_refs']
    metric_name = payload['name'].as_string()
    metrics = db.scalars(facts.where(payload['type'].as_string() == 'metric.recorded')
                         .ext(distinct_on(metric_name)).order_by(metric_name, EventRow.seq.desc())).all()
    return SessionView(session_id=row.id, application_id=row.application_id, case_revision=row.data['case_revision'],
        state=row.state, state_revision=row.revision, route=row.data['route'], last_event_seq=row.seq,
        source_refs=refs, metrics=metrics, operations=ops, verified=verified, busy=row.data.get('busy',False), ended=row.data.get('ended',False),
        generation_id=row.data.get('generation_id'), reason=row.data.get('reason') if verified else None,
        remedy=row.data.get('remedy') if verified else None, messages=messages(db,row.id), record=record.data if record else None, mode=row.data.get('mode','text'),
        voice_transport=row.data.get('voice_transport','agora'))

def published_channel(entries):
    """发布渠道来自已审核知识条目，不由模型或用户文本猜测。"""
    channels = {e.channel for e in entries if e.status == 'approved' and not e.issue_codes}
    if channels == {'jingmai'}:
        return 'jingmai'
    if 'phone' in channels:
        return 'phone'
    return None

class Coordinator:
    def __init__(self, services: Services):
        self.services = services
        self.repo = services.repository
        self.tasks: dict[str, asyncio.Task] = {}
        self.all_tasks: set[asyncio.Task] = set()
        self.cancels: dict[str, asyncio.Event] = {}
        self.operation_worker = None

    # ---- 守卫/策略装配：默认使用规则层实现；显式注入的自定义实现优先 ----
    def _guard(self):
        guard = self.services.guard
        return ToolGuard() if isinstance(guard, LegacyToolGuard) else guard

    def _policy(self):
        policy = self.services.policy
        return RoutingPolicy() if isinstance(policy, LegacyPolicy) else policy

    def model_info(self) -> dict:
        '''只报告 MODEL_PROVIDER 与配置版本，不读取或输出工作台 GLM key。'''
        provider = (getattr(settings, 'model_provider', 'fake') or 'fake').strip().lower()
        model_id = getattr(settings, 'deepseek_model', 'fake-v1') if provider == 'deepseek' else 'fake-v1'
        view = {'mode': provider, 'provider': provider, 'model_id': model_id,
                'config_version': getattr(settings, 'config_version', 'env')}
        port = getattr(self.services.understanding, 'public_view', None)
        if callable(port):
            view['understanding'] = port()
        return view

    def require_model(self) -> dict:
        '''模型版本由 MODEL_PROVIDER 决定；不读取工作台 GLM key，也不静默启用 GLM。'''
        provider = (getattr(settings, 'model_provider', 'fake') or 'fake').strip().lower()
        if provider == 'fake':
            return {'mode': 'fake', 'model_version': 'fake-v1'}
        if provider == 'deepseek':
            model_id = getattr(settings, 'deepseek_model', 'deepseek')
            return {'mode': 'deepseek', 'model_version': f'deepseek:{model_id}'}
        raise DomainError('MODEL_NOT_CONFIGURED',
                          '未知 MODEL_PROVIDER；请使用 fake/deepseek 或显式注入测试 understanding', 503)

    def decide(self, case, *, channel=None, entry_ids=(), contact_outcome='unknown',
               outcome_source='none', authorized=True, rule_valid=True):
        policy = self._policy()
        if hasattr(policy, 'decide'):
            return policy.decide(case, knowledge_channel=channel, knowledge_entry_ids=entry_ids,
                                 authorized=authorized, rule_valid=rule_valid,
                                 contact_outcome=contact_outcome, contact_outcome_source=outcome_source)
        # 自定义策略只返回 route 时，包装成完整决策，行为可追溯。
        route = policy.decide_route(case)
        if route not in ('phone', 'jingmai', 'manual'):
            route = 'manual'
        return RoutingDecision(decision_id=UUID(new_id()), application_id=case.application_id,
            case_revision=case.case_revision, route=route, reason_codes=['custom_policy'],
            rule_version=getattr(policy, 'version', RULE_VERSION),
            required_evidence=[], evidence=[], submission_attempt=case.submission_attempt,
            call_attempt=case.call_attempt)

    async def startup(self):
        with self.repo.transaction() as db:
            for row in db.scalars(select(Conversation).with_for_update()):
                if row.data.get('busy') and row.data.get('realtime_job', {}).get('status') != 'queued':
                    if row.data.get('realtime_enabled'):
                        row.data = {**row.data, 'realtime_failure': 'SERVICE_UNAVAILABLE'}
                        self._invalidate(db, row, 'failed')
                        continue
                    self._invalidate(db, row, 'interrupted')
        self.operation_worker = asyncio.create_task(self.reconcile_loop())

    async def shutdown(self):
        for cancel in self.cancels.values(): cancel.set()
        pending=list(self.all_tasks)
        for task in pending: task.cancel()
        if self.operation_worker: self.operation_worker.cancel()
        await asyncio.gather(*pending, *([self.operation_worker] if self.operation_worker else []), return_exceptions=True)

    def create(self, owner, request, key, *, realtime_available=False):
        if request.voice_transport == 'native_webrtc' and not realtime_available:
            raise DomainError('REALTIME_NOT_CONFIGURED', '实时传输尚未配置', 503)
        if request.mode == 'voice' and request.voice_transport == 'agora' and not self.services.voice.available():
            raise DomainError('VOICE_NOT_CONFIGURED', '语音配置尚未完成', 503)
        # 模型版本只用于证据留档；真实模型配置由 MODEL_PROVIDER 决定，不读工作台 GLM key。
        model = ({'mode':'native_qwen','model_version':'qwen3.8-omni-flash-realtime'}
                 if request.voice_transport=='native_webrtc' else self.require_model())
        case = self.services.cases.get_case(owner, str(request.application_id))
        knowledge_version = os.getenv('ZHIYU_KNOWLEDGE_VERSION', 'demo-k1')
        entries = self.services.knowledge.lookup(case.scenario_id, '', ['synthetic'], knowledge_version)
        decision = self.decide(case, channel=published_channel(entries),
                               entry_ids=tuple(e.entry_id for e in entries))
        with self.repo.transaction() as db:
            # Serialize creation receipts across requests/process restarts.
            from sqlalchemy import text
            db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'), {'key': owner+key})
            rkey = f'{owner}:create:{key}'
            creation_payload=request.model_dump(mode='json')
            # Preserve pre-V0.4 receipt hashes for the default transport.
            if request.voice_transport=='agora':creation_payload.pop('voice_transport')
            digest, previous = receipt(db,rkey,creation_payload)
            if previous: return snapshot(db,owned(db,previous['session_id'],owner))
            row = Conversation(id=new_id(),owner=owner,application_id=str(case.application_id), state='CASE_READY',revision=0,seq=0,
                data={'case_revision':case.case_revision,'strategy_version':'demo-p1','knowledge_version':knowledge_version,
                      'rule_version':decision.rule_version,'route':decision.route,
                      'routing_decision':decision.model_dump(mode='json'),
                      'verified':False,'explained':False,'ended':False,'busy':False,'epoch':0,'message_count':0,
                      'notifications':[],'contact_outcome':'unknown','contact_outcome_source':'none',
                      'submission_attempt':case.submission_attempt,'call_attempt':case.call_attempt,
                      'model_version':model['model_version'],'model_mode':model['mode'],
                      'mode':request.mode,'voice_transport':request.voice_transport})
            db.add(row)
            db.flush()
            emit(db,row,StateChanged(type='state.changed',state='CASE_READY'))
            db.add(RequestReceipt(key=rkey,payload_hash=digest,data={'session_id':row.id}))
            db.flush()
            return snapshot(db,row)

    def _invalidate(self,db,row,status='interrupted'):
        old = row.data.get('generation_id')
        if not old or not row.data.get('busy'): return False
        for m in db.scalars(select(Message).where(Message.session_id==row.id)):
            if m.data['view']['generation_id']==old and m.data['view']['status']=='generating':
                m.data={**m.data,'view':{**m.data['view'],'status':status}}
                emit(db,row,GenerationFinished(type='generation.finished',status=status,message_id=m.id),generation=old,turn=m.data['view']['turn_id'])
        row.data={**row.data,'busy':False,'epoch':row.data['epoch']+1}
        emit(db,row,TurnInterrupted(type='turn.interrupted',invalidated_generation_id=old,new_epoch=row.data['epoch']),generation=old)
        if row.id in self.cancels: self.cancels[row.id].set()
        return True

    async def accept(self,owner,sid,text,key,*,start_revision=None,source='text',provider_turn=None,admission=None):
        with self.repo.transaction() as db:
            row=owned(db,sid,owner,True)
            if admission is not None:
                previous = admission.check(db, row)
                if previous is not None: return previous
            elif row.data.get('realtime_enabled') or row.data.get('voice_transport') == 'native_webrtc':
                raise DomainError('WRONG_INPUT_MODE', 'Use the bound realtime business adapter', 409)
            kind='start' if start_revision is not None else 'turn'
            rkey=f'{sid}:{kind}:{key}'
            digest,old=receipt(db,rkey,{'text':text,'revision':start_revision})
            if old: return AcceptedTurn(**old)
            if row.data.get('mode','text')=='voice' and source not in ('asr', 'realtime'):
                raise DomainError('WRONG_INPUT_MODE','语音会话只能接收绑定的声网输入',409)
            if provider_turn is not None and provider_turn<=row.data.get('last_provider_turn',-1):
                raise DomainError('STALE_PROVIDER_TURN','收到过期语音轮次',409)
            if row.data.get('ended') or row.state in ['CLOSED','MANUAL_REVIEW']:
                raise DomainError('SESSION_ENDED','会话已结束或转人工')
            if start_revision is not None:
                if row.revision != start_revision or row.state != 'CASE_READY':
                    raise DomainError('REVISION_CONFLICT','会话已启动或版本不一致')
            elif row.state == 'CASE_READY':
                raise DomainError('NOT_STARTED','请先开始会话')
            elif row.state not in ALLOWED_TURN_STATES:
                raise DomainError('ACTION_IN_PROGRESS','业务处理中，请等待查证或结束会话')
            self._invalidate(db,row)
            if provider_turn is not None: row.data={**row.data,'last_provider_turn':provider_turn}
            turn,generation,mid,assistant_id=new_id(),new_id(),new_id(),new_id()
            row.data={**row.data,'generation_id':generation,'turn_id':turn,'busy':True}
            row.revision+=1
            if start_revision is not None: transition(db,row,'OPENING')
            else:
                row.data={**row.data,'message_count':row.data['message_count']+1}
                db.add(Message(id=mid,session_id=sid,position=row.data['message_count'],data={'view':{'turn_id':turn,'generation_id':generation,'role':'user','text':text,'status':'completed'}}))
            row.data={**row.data,'message_count':row.data['message_count']+1}
            db.add(Message(id=assistant_id,session_id=sid,position=row.data['message_count'],data={'view':{'turn_id':turn,'generation_id':generation,'role':'assistant','text':'','status':'generating','playback_status':'unknown' if source=='asr' else 'not_applicable'}}))
            emit(db,row,TurnStarted(type='turn.started',message_id=mid),turn=turn,generation=generation)
            result=AcceptedTurn(turn_id=turn,generation_id=generation)
            db.add(RequestReceipt(key=rkey,payload_hash=digest,data=result.model_dump(mode='json')))
            job = dict(owner=owner, sid=sid, text=text, mid=mid, assistant_id=assistant_id,
                       turn=turn, generation=generation, opening=start_revision is not None, source=source)
            if admission is not None:
                admission.record(db, row, result, job)
        try:
            self.launch_generation(job)
        except Exception:
            # v0.4 admission persists the queued job in the acceptance transaction.
            if admission is None: raise
            log.warning('realtime_launch_deferred session=%s', sid)
        return result

    def launch_generation(self, job):
        sid = job['sid']
        cancel=asyncio.Event()
        self.cancels[sid]=cancel
        # Existing work receives cancel; its finally blocks may finish without blocking this request.
        async def run():
            with self.repo.transaction() as db:
                row = owned(db, sid, job['owner'], True)
                if not self.current(row, job['generation']): return
                if row.data.get('realtime_enabled'):
                    row.data = {**row.data, 'realtime_job': {**job, 'status': 'running'}}
            await self.generate(**job, cancel=cancel)
        task=asyncio.create_task(run())
        self.tasks[sid]=task
        self.all_tasks.add(task)
        def done(t):
            self.all_tasks.discard(t)
            if self.tasks.get(sid) is t:
                self.tasks.pop(sid,None)
                self.cancels.pop(sid,None)
            if not t.cancelled(): t.exception()
        task.add_done_callback(done)

    def build_context(self, db, row, turn, generation):
        '''可信只读上下文：状态、路由决策、告知证据都取自数据库，不从话术推断。'''
        stored = row.data.get('routing_decision')
        op_ids = list(db.scalars(select(Operation.id).where(Operation.session_id == row.id)))
        return SessionContext(
            session_id=row.id, turn_id=turn, generation_id=generation,
            generation_epoch=row.data.get('epoch', 0),
            application_id=row.application_id, case_revision=row.data['case_revision'],
            state_revision=row.revision,
            strategy_version=row.data['strategy_version'], rule_version=row.data['rule_version'],
            knowledge_version=row.data['knowledge_version'],
            model_version=row.data.get('model_version', 'fake-v1'),
            state=row.state,
            routing_decision=RoutingDecision.model_validate(stored) if stored else None,
            notifications=[NotificationEvidence.model_validate(n) for n in row.data.get('notifications') or []],
            contact_outcome=row.data.get('contact_outcome', 'unknown'),
            contact_outcome_source=row.data.get('contact_outcome_source', 'none'),
            submission_attempt=row.data.get('submission_attempt', 1),
            call_attempt=row.data.get('call_attempt', 0),
            operation_ids=op_ids)

    def snapshot_context(self, owner, sid, turn=None, generation=None):
        '''只读快照，供验收脚本核对 Agent 实际看到的上下文。'''
        with self.repo.read() as db:
            row = owned(db, sid, owner)
            return self.build_context(db, row, turn or UUID(new_id()),
                                      generation or UUID(row.data.get('generation_id') or new_id()))

    def _refresh_route(self, db, row, case, entries, *, contact_outcome=None, outcome_source=None):
        '''每轮用最新可信事实重算路由并持久化；证据随之留档。'''
        outcome = contact_outcome if contact_outcome is not None else row.data.get('contact_outcome', 'unknown')
        source = outcome_source if outcome_source is not None else row.data.get('contact_outcome_source', 'none')
        decision = self.decide(case, channel=published_channel(entries),
                               entry_ids=tuple(e.entry_id for e in entries),
                               contact_outcome=outcome, outcome_source=source)
        row.data = {**row.data, 'route': decision.route,
                    'routing_decision': decision.model_dump(mode='json'),
                    'rule_version': decision.rule_version}
        return decision

    def _contact_from_text(self, text, verified):
        '''合成演示的文本判定，仅作 demo 证据；绝不把沉默推断成未接通。

        使用结构化规则理解（而非裸子串），避免把不是这样的，我问材料误判成错人。
        '''
        from .semantic import detect_intents
        intents = detect_intents(text)
        if 'deny_identity' in intents:
            return 'wrong_person', 'demo_text_rule'
        if 'refuse_contact' in intents:
            return 'refused', 'demo_text_rule'
        if verified:
            return 'verified', 'demo_text_rule'
        return 'unknown', 'none'

    #: Agent/模型只允许提示这两个方向安全的负向结果（否认身份/拒绝联系），
    #: 其余（verified / no_answer / disconnected / not_required）必须由服务端身份核验
    #: 或可信领域事件提供，模型不能自造，也不能用沉默推断。
    MODEL_ALLOWED_OUTCOMES = ('wrong_person', 'refused')

    def resolve_contact_outcome(self, *, verified, claimed, text,
                               stored_outcome='unknown', stored_source='none'):
        '''服务端裁决本次联系结果，返回 (outcome, source)。

        规则（与契约 docs/agent/CONTRACT-CHANGES.md 一致）：
        1. 已记录的可信领域事件（source=domain_event）优先，不被话术覆盖。
        2. Agent 提示 wrong_person/refused 方向安全，采纳并如实标 demo_text_rule。
        3. Agent 提示 verified 只有在服务端已完成身份核验时才接受；否则拒绝并留日志，
           降级为服务端自身可得的结论（不能产生 call_records.contact_status=verified）。
        4. 其余模型提示（no_answer 等）一律拒绝：未接通只能来自可信联系事件。
        5. 没有可信事件时如实记 unknown / source=none，不伪造 demo_text_rule 证据。
        '''
        text_outcome, text_source = self._contact_from_text(text, verified)
        if claimed is not None:
            if claimed in self.MODEL_ALLOWED_OUTCOMES:
                return claimed, 'demo_text_rule'
            if claimed == 'verified':
                if verified:
                    return 'verified', (stored_source if stored_source != 'none' else 'demo_text_rule')
                log.warning('agent_outcome_rejected reason=verified_without_server_identity')
            else:
                log.warning('agent_outcome_rejected reason=not_from_trusted_event code=%s', claimed)
        if stored_source == 'domain_event':
            return stored_outcome, stored_source
        return text_outcome, text_source

    def _validate_understanding(self, decision, session_message_ids, entry_ids):
        '''服务端校验模型理解结果：证据必须存在、知识必须已审、缺槽位不播模板。'''
        understanding = decision.understanding
        if understanding is None:
            return decision
        if any(str(m) not in session_message_ids for m in understanding.evidence_message_ids):
            log.warning('understanding_evidence_rejected')
            return AgentDecision(reply_text='需要先核实您的问题，已转人工确认后再答复。',
                                 proposed_action='none', evidence_message_ids=[], knowledge_entry_ids=[],
                                 next_state='MANUAL_REVIEW', understanding=understanding)
        if any(str(k) not in entry_ids for k in decision.knowledge_entry_ids):
            return AgentDecision(reply_text='缺少已审核依据，已转人工核实，暂不能答复具体标准。',
                                 proposed_action='none', evidence_message_ids=[], knowledge_entry_ids=[],
                                 next_state='MANUAL_REVIEW', understanding=understanding)
        if decision.required_slot_missing:
            slots = '、'.join(decision.required_slot_missing)
            return AgentDecision(reply_text=f'还需要确认{slots}，请补充后我再按已审核依据说明。',
                                 proposed_action='none', evidence_message_ids=list(decision.evidence_message_ids),
                                 knowledge_entry_ids=[], next_state='QA', understanding=understanding)
        return decision

    async def generate(self,owner,sid,text,mid,assistant_id,turn,generation,cancel,opening,source='text',candidate=None):
        try:
            case=self.services.cases.get_case(owner,self.application_id(owner,sid))
            with self.repo.transaction() as db:
                row=owned(db,sid,owner,True)
                if not self.current(row,generation): return
                # Demo identity evidence is validated by code against the assigned synthetic entity.
                import re
                normalized=re.sub(r'[\s，。！？,.!?、]','',text)
                company=task_slot(case.slots, 'company_name')
                aliases={company}
                if source in ('asr', 'realtime'):
                    for digit,han in [('1','一'),('2','二'),('3','三')]:aliases.add(company.replace(digit,han))
                identities={phrase for alias in aliases for phrase in [f'我是{alias}',f'我是{alias}的联系人',f'对我是{alias}的联系人']}
                if company and not opening and normalized in identities:
                    row.data={**row.data,'verified':True,'identity_evidence':mid,
                              'contact_outcome':'verified','contact_outcome_source':'demo_text_rule'}
                verified=row.data.get('verified',False)
                row_data_outcome=row.data.get('contact_outcome','unknown')
                row_data_source=row.data.get('contact_outcome_source','none')
                context=self.build_context(db,row,turn,generation)
                history=tuple(messages(db,sid))
            entries=self.services.knowledge.lookup(case.scenario_id,text,['synthetic'],context.knowledge_version)
            entries=available_entries([e for e in entries if e.channel == row.data['route']], case)
            if not company:
                decision=AgentDecision(reply_text='审核任务缺少核验主体，已转人工。',proposed_action='none',evidence_message_ids=[],knowledge_entry_ids=[],next_state='MANUAL_REVIEW')
            elif opening:
                decision=AgentDecision(reply_text=f'您好，这里是模拟商户服务AI演示。请确认您是否是{company}的联系人。',proposed_action='none',evidence_message_ids=[],knowledge_entry_ids=[],next_state='VERIFYING')
            else:
                turn_input = TurnInput(message_id=mid,text=text,source=source)
                if candidate is not None:
                    from .realtime_business.candidates import grounded_candidate
                    understanding = grounded_candidate(candidate, text, case=case, context=context)
                    decision = self.services.agent.decide(input=turn_input, context=context,
                        case=case, knowledge=tuple(entries), verified=verified, understanding=understanding)
                else:
                    decision=await asyncio.wait_for(self.services.agent.handle_turn(turn_input,context,history,case,tuple(entries),verified,cancel),8)
            # Pre-identity and missing-source output cannot bypass the coordinator via a replacement Agent.
            if company and not verified and decision.next_state != 'CLOSING':
                decision=AgentDecision(reply_text=f'这里是模拟商户服务AI。请确认身份：输入“我是{company}”。',proposed_action='none',evidence_message_ids=[],knowledge_entry_ids=[],next_state='VERIFYING')
            if verified and not entries:
                decision=AgentDecision(reply_text='缺少已审核依据，已转人工核实。',proposed_action='none',evidence_message_ids=[],knowledge_entry_ids=[],next_state='MANUAL_REVIEW')
            session_message_ids={m.message_id for m in messages_db_ids(self, sid)}
            entry_ids={str(e.entry_id) for e in entries}
            decision=self._validate_understanding(decision, session_message_ids, entry_ids)
            # 错人/拒绝结束：绝不自动驳回，也不伪造告知完成。
            # 联系结果由服务端裁决：模型提示只有在服务端核验/可信事件支持下才被采纳。
            outcome, outcome_source = self.resolve_contact_outcome(
                verified=verified, claimed=decision.contact_outcome, text=text,
                stored_outcome=row_data_outcome, stored_source=row_data_source)
            if outcome in ('wrong_person','refused'):
                decision=decision.model_copy(update={'proposed_action':'none','action_payload':None})
                if decision.next_state not in ('CLOSING','MANUAL_REVIEW'):
                    decision=decision.model_copy(update={'next_state':'CLOSING'})
            if source in ('asr', 'realtime') and decision.proposed_action!='none' and (not settings.voice_writes_enabled or not row.data.get('voice_explanation_verified')):
                decision=AgentDecision(reply_text='本次语音链路演示已完成，业务写入尚未通过语音告知验收。后续由人工核实处理。',proposed_action='none',evidence_message_ids=[],knowledge_entry_ids=[],next_state='MANUAL_REVIEW')
            violation=None
            with self.repo.transaction() as db:
                row=owned(db,sid,owner,True)
                if not self.current(row,generation): return
                # 显式状态机校验：非法提议不做任何写入，稍后在独立事务留证。
                violation=self._agent_violation(row.state, decision)
                if violation is None:
                    row.data={**row.data,'contact_outcome':outcome,
                              'contact_outcome_source':outcome_source}
            if violation is not None:
                rule, from_state, to_state = violation
                record_rejection(owner, sid, from_state, to_state, rule)
                with self.repo.transaction() as db:
                    row=owned(db,sid,owner,True)
                    if self.current(row,generation): self._invalidate(db,row,'failed')
                return
            # 派发前预检：守卫拒绝就不播报已提交，如实转人工并留证（不制造成功回执）。
            if decision.proposed_action != 'none':
                with self.repo.read() as db:
                    row=owned(db,sid,owner)
                    projected=self.project_guard_view(row,decision,entries,assistant_id)
                with self.repo.transaction() as db:
                    row=owned(db,sid,owner,True)
                    if not self.current(row,generation): return
                    denied=self.precheck_action(db,row,case,decision,entries,projected)
                    if denied is not None:
                        decision=self.deny_action(row,decision,denied,row.state)
            # Fixed identity, rectification and action messages must retain exact reviewed wording.
            rephrase=(verified and decision.next_state=='QA' and decision.proposed_action=='none'
                and entries and entries[0].approved_text not in decision.reply_text)
            prior=[ModelMessage(role=m.role,content=m.text) for m in history if m.status=='completed' and m.text and m.generation_id!=UUID(generation)]
            model_request=ModelRequest(messages=[ModelMessage(role='system',content='模拟商户服务。只改写以下已校验答复，不增加任何业务事实、价格、期限或通过承诺：'+decision.reply_text),
                *prior[-12:],ModelMessage(role='user',content=text)],fixture_response=decision.reply_text,allow_rephrase=bool(rephrase),session_id=sid,turn_id=turn,generation_id=generation)
            from .adapters import FakeModel
            output_model=FakeModel() if getattr(self.services.model,'name','fake')=='deepseek' and not model_request.allow_rephrase else self.services.model
            if row.data.get('realtime_enabled'):
                # The adapter publishes an exact reviewed answer, never arbitrary token deltas.
                output_model = FakeModel()
            import time
            model_started=time.monotonic()
            first_content=True
            async def output_stream():
                if row.data.get('realtime_enabled'):
                    # Already reviewed deterministic text: no simulated token delay
                    # or second model call before the speech adapter can begin.
                    yield decision.reply_text
                    return
                if getattr(output_model,'name','fake')!='deepseek':
                    async for piece in output_model.stream(model_request,cancel):yield piece
                    return
                # Temporary DemoAgent has only this non-authoritative QA. Do not let an
                # unconstrained paraphrase invent commitments while the real Agent is unfinished.
                parts=[]
                async for piece in output_model.stream(model_request,cancel):parts.append(piece)
                if cancel.is_set():return
                content=''.join(parts).strip()
                approved={decision.reply_text,'补齐材料后仍需审核，无法保证通过。','材料补齐后还需要审核，不能保证一定通过。'}
                yield content if content in approved else decision.reply_text
            async with asyncio.timeout(8):
                async for chunk in output_stream():
                    with self.repo.transaction() as db:
                        row=owned(db,sid,owner,True)
                        if not self.current(row,generation) or cancel.is_set(): return
                        msg=db.get(Message,assistant_id)
                        if first_content and getattr(output_model,'name','fake')!='deepseek':
                            emit(db,row,MetricRecorded(type='metric.recorded',name='model_ttft',duration_ms=(time.monotonic()-model_started)*1000,
                                outcome='success',clock_domain='server.monotonic',measurement_method='first_content:fake'),
                                turn=turn,generation=generation)
                            first_content=False
                        msg.data={**msg.data,'view':{**msg.data['view'],'text':msg.data['view']['text']+chunk}}
                        emit(db,row,TextDelta(type='text.delta',text=chunk,channel='voice_transport' if source=='asr' else 'text_mode'),turn=turn,generation=generation)
            with self.repo.transaction() as db:
                row=owned(db,sid,owner,True)
                if not self.current(row,generation) or cancel.is_set(): return
                if row.data.get('realtime_enabled'):
                    application = db.scalar(select(Application).where(Application.id == row.application_id).with_for_update())
                    if application.owner != owner or application.data['case_revision'] != row.data['case_revision']:
                        row.data = {**row.data, 'realtime_failure': 'REVISION_CONFLICT'}
                        self._invalidate(db, row, 'failed')
                        return
                case=self.services.cases.get_case(owner,row.application_id)
                entries=self.services.knowledge.lookup(case.scenario_id,text,['synthetic'],row.data['knowledge_version'])
                entries=available_entries([e for e in entries if e.channel == row.data['route']], case)
                msg=db.get(Message,assistant_id)
                msg.data={**msg.data,'view':{**msg.data['view'],'status':'completed'}}
                row.data={**row.data,'busy':False}
                used=[e for e in entries if e.approved_text and e.approved_text in decision.reply_text]
                if verified and used and source != 'realtime':
                    notifications=list(row.data.get('notifications') or [])
                    notifications.append(NotificationEvidence(kind='explanation',message_id=assistant_id,
                        knowledge_entry_ids=[e.entry_id for e in used],completed=True).model_dump(mode='json'))
                    row.data={**row.data,'explained':True,'reason':case.slots['reason'],'remedy':case.slots['remedy'],
                              'explanation_message_id':assistant_id,'notifications':notifications[-20:]}
                # 每轮刷新路由证据（渠道来自已审知识，次数/联系结果来自服务器事实）。
                self._refresh_route(db,row,case,entries)
                transition(db,row,decision.next_state)
                if used: emit(db,row,KnowledgeUsed(type='knowledge.used',entry_ids=[e.entry_id for e in used],
                                                   source_refs=[ref for e in used for ref in e.source_refs]),turn=turn,generation=generation)
                if decision.proposed_action != 'none':
                    self.enqueue(db,row,case,decision,entries)
                if decision.next_state in ['CLOSING','MANUAL_REVIEW']:
                    row.data={**row.data,'ended':True}
                    transition(db,row,'MANUAL_REVIEW')
                    self.save_non_action_record(db,row,case,row.data.get('contact_outcome','unknown'))
                    emit(db,row,SessionEnded(type='session.ended',reason='manual_review'))
                emit(db,row,GenerationFinished(type='generation.finished',status='completed',message_id=assistant_id),turn=turn,generation=generation)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning('generation_failed kind=%s session=%s',type(exc).__name__,sid)
            with self.repo.transaction() as db:
                row=owned(db,sid,owner,True)
                if self.current(row,generation):
                    if row.data.get('realtime_enabled'):
                        row.data = {**row.data, 'realtime_failure': 'BUSINESS_TIMEOUT' if isinstance(exc, TimeoutError) else 'INTERNAL_ERROR'}
                    emit(db,row,MetricRecorded(type='metric.recorded',name='model_ttft',outcome='timeout' if getattr(exc,'code','')=='MODEL_TIMEOUT' or isinstance(exc,TimeoutError) else 'missing',
                        clock_domain='server.monotonic',measurement_method='failed_generation_no_latency_claim'),turn=turn,generation=generation)
                    self._invalidate(db,row,'failed')

    def _agent_violation(self, current_state, decision):
        """显式状态机校验：返回 (规则, from, to) 或 None，不做任何写入。

        扩展后的允许集合是 AGENT_PROPOSABLE_STATES，但仍必须通过 TRANSITIONS 校验：
        Agent 不能跳步（例如 VERIFYING -> ACTION_READY），也不能提议执行态。
        """
        target = decision.next_state
        if target not in AGENT_PROPOSABLE_STATES:
            return 'agent_state_not_proposable', current_state, target
        if target == 'ACTION_READY' and decision.proposed_action == 'none':
            return 'action_ready_without_action', current_state, target
        if decision.proposed_action != 'none' and target != 'ACTION_READY':
            # 动作只能从 ACTION_READY 经守卫派发；Agent 不能自带动作跳到别的状态。
            return 'action_without_action_ready', current_state, target
        if not may_transition(current_state, target):
            return 'agent_transition_denied', current_state, target
        return None

    def application_id(self,owner,sid):
        with self.repo.read() as db: return owned(db,sid,owner).application_id

    def current(self,row,generation):
        return row.data.get('generation_id')==generation and row.data.get('busy') and not row.data.get('ended')

    def guard_view(self, row_data, entries=None):
        """把会话数据整理成守卫视图：告知证据只取已完成的消息，路由取自存储的决策。

        协调器派发前与验收脚本共用这一个转换，避免两处口径漂移。
        """
        decision = row_data.get('routing_decision') or {}
        return {**row_data,
                'route': decision.get('route') or row_data.get('route'),
                'routing_decision': decision,
                'notification_message_ids': [n['message_id'] for n in row_data.get('notifications') or []
                                             if n.get('completed')],
                'operation_ids': list(row_data.get('operation_ids') or []),
                'knowledge_version': row_data.get('knowledge_version')}

    def _guard_view(self, row, command, entries):
        return self.guard_view(row.data, entries)

    def enqueue(self,db,row,case,decision,entries):
        kind=decision.proposed_action
        key=business_key(case,kind)
        # Lock application so sessions sharing an application cannot race the unique business key.
        db.scalar(select(Application).where(Application.id==row.application_id).with_for_update())
        fresh=self.services.cases.get_case(row.owner,row.application_id)
        command=self.build_command(db,row,fresh,decision,entries,key)
        confirmations={c.id:c.data for c in db.scalars(select(Confirmation).where(Confirmation.session_id==row.id))}
        operation_ids=tuple(db.scalars(select(Operation.id).where(Operation.session_id==row.id)))
        guard=self._guard()
        guard.authorize(fresh,self._guard_view(row,command,entries),command,entries,
                        GuardContext(confirmations=confirmations,operation_ids=operation_ids,now=now()))
        existing=db.scalar(select(Operation).where(Operation.key==key))
        if not existing and kind=='reject_application':
            from sqlalchemy import or_
            existing=db.scalar(select(Operation).where(or_(Operation.key==f"{case.application_id}:{case.case_revision}:reject_application",Operation.key.startswith(key+":",autoescape=True))))
        if existing:
            # 一次业务效果：换会话/重启后命中同一业务键，只回放事实。
            row.data={**row.data,'linked_operation_id':existing.id}
            emit(db,row,OperationUpdated(type='operation.updated',result=OperationResult.model_validate(existing.data['result'])),operation=existing.id)
            transition(db,row,'MANUAL_REVIEW')
            row.data={**row.data,'ended':True}
            return
        result=OperationResult(operation_id=command.operation_id,status='pending',updated_at=now())
        db.add(Operation(id=str(command.operation_id),session_id=row.id,key=key,status='pending',
            data={'command':command.model_dump(mode='json'),'result':result.model_dump(mode='json'),'attempts':0,'next_at':0,'writeback_done':False,
                  'case_revision':fresh.case_revision,'case_snapshot':fresh.model_dump(mode='json'),'business_key':key,'kind':kind}))
        row.data={**row.data,'operation_ids':list(row.data.get('operation_ids') or [])+[str(command.operation_id)]}
        transition(db,row,'EXECUTING')
        emit(db,row,OperationUpdated(type='operation.updated',result=result),operation=command.operation_id)

    def build_command(self,db,row,case,decision,entries,key=None):
        """服务器构造 ActionCommand（客户端不能提交）。协调器与预检共用同一份构造。"""
        kind=decision.proposed_action
        key=key or business_key(case,kind)
        stored=RoutingDecision.model_validate(row.data['routing_decision'])
        if stored.case_revision != case.case_revision:
            raise DomainError('STALE_REVISION', '路由决策版本与当前审核版本不一致', 409)
        if str(stored.application_id) != str(case.application_id):
            raise DomainError('ACTION_FORBIDDEN', '路由决策与申请单不一致', 403)
        payload=decision.action_payload
        confirmation_ref=payload.confirmation_id if isinstance(payload,CallbackPayload) else None
        evidence_refs=[UUID(str(v)) for v in (row.data.get('identity_evidence'),row.data.get('explanation_message_id')) if v]
        return ActionCommand(session_id=row.id,application_id=case.application_id,case_revision=case.case_revision,
            decision_id=stored.decision_id,operation_id=UUID(new_id()),kind=kind,idempotency_key=key,
            confirmation_ref=confirmation_ref,evidence_refs=evidence_refs,payload=payload)

    def precheck_action(self,db,row,case,decision,entries,guard_data=None):
        """派发前的守卫预检（只读，不写库、不派发）。

        守卫拒绝时返回 DomainError 供协调器如实降级并留证；绝不让"未通过校验的动作"
        先被播报成已提交，也绝不把拒绝吞掉。
        """
        if decision.proposed_action == 'none':
            return None
        try:
            command=self.build_command(db,row,case,decision,entries)
            confirmations={c.id:c.data for c in db.scalars(select(Confirmation).where(Confirmation.session_id==row.id))}
            operation_ids=tuple(db.scalars(select(Operation.id).where(Operation.session_id==row.id)))
            data=guard_data if guard_data is not None else self._guard_view(row,command,entries)
            self._guard().authorize(case,data,command,entries,
                GuardContext(confirmations=confirmations,operation_ids=operation_ids,now=now()))
            return None
        except DomainError as exc:
            return exc

    def project_guard_view(self,row,decision,entries,assistant_id):
        """预演本轮告知证据：只有回复里确实播出了已审正文才能算已告知。

        与落库时的判定共用同一条件（verified + approved_text 出现在回复中），
        这样派发前预检与真正派发不会口径漂移。
        """
        data=dict(row.data)
        used=[e for e in entries if e.approved_text and e.approved_text in decision.reply_text]
        if used and row.data.get('verified'):
            notifications=list(row.data.get('notifications') or [])
            notifications.append({'kind':'explanation','message_id':str(assistant_id),
                'knowledge_entry_ids':[str(e.entry_id) for e in used],'completed':True})
            data['notifications']=notifications
            data['explained']=True
            data['explanation_message_id']=str(assistant_id)
            data['reason']=data.get('reason')
        return self.guard_view(data,entries)


    def deny_action(self,row,decision,error,state):
        """守卫拒绝：改成如实告知 + 转人工，并在会话数据留证（不制造成功回执）。"""
        denied=list(row.data.get('denied_actions') or [])
        denied.append({'kind':decision.proposed_action,'code':error.code,'message':error.message,
                       'at':now().isoformat(),'from_state':state})
        row.data={**row.data,'denied_actions':denied[-20:],'proposed_action':'none'}
        reply='本次操作未满足服务端校验条件，已转人工核实，未提交任何业务处理。'
        log.warning('action_denied kind=%s code=%s session=%s',decision.proposed_action,error.code,row.id)
        return AgentDecision(reply_text=reply,proposed_action='none',action_payload=None,
            evidence_message_ids=list(decision.evidence_message_ids),knowledge_entry_ids=[],
            next_state='MANUAL_REVIEW',understanding=decision.understanding,
            contact_outcome=decision.contact_outcome,required_slot_missing=[])


    def interrupt(self,owner,sid,request):
        with self.repo.transaction() as db:
            row=owned(db,sid,owner,True)
            key=f'{sid}:interrupt:{request.client_event_id}'
            digest,old=receipt(db,key,request.model_dump(mode='json'))
            if old: return Interrupted(**old)
            applied=False
            if str(request.generation_id)==row.data.get('generation_id'): applied=self._invalidate(db,row)
            result=Interrupted(applied=applied)
            db.add(RequestReceipt(key=key,payload_hash=digest,data=result.model_dump()))
            return result

    def end(self,owner,sid):
        with self.repo.transaction() as db:
            row=owned(db,sid,owner,True)
            if not row.data.get('ended'):
                self._invalidate(db,row)
                row.data={**row.data,'ended':True}
                has_operations=db.scalar(select(Operation).where(Operation.session_id==row.id)) is not None
                # 已派发业务操作必须继续查证/回写，不能转入不可回写的 CLOSING。
                if not (has_operations and row.state in ('EXECUTING','RECONCILING','WRITEBACK_PENDING')):
                    transition(db,row,'CLOSING')
                emit(db,row,SessionEnded(type='session.ended',reason='user_ended'))
                if not has_operations:
                    case=self.services.cases.get_case(owner,row.application_id)
                    # 没有可信联系事件就记 unknown，不用沉默推断未接通。
                    self.save_non_action_record(db,row,case,row.data.get('contact_outcome','unknown'))
            db.flush()
            return snapshot(db,row)

    def make_record(self,row,case,operation_ids,contact='verified'):
        entries=self.services.knowledge.lookup(case.scenario_id,'',['synthetic'],row.data['knowledge_version']) if row.data.get('explained') else []
        return CallRecord(record_id=UUID(new_id()),record_revision=1,session_id=row.id,application_id=row.application_id,
            case_revision=row.data['case_revision'],contact_status=contact,
            explanation_status='completed' if row.data.get('explained') else 'not_started',reason_codes=[case.scenario_id] if row.data.get('verified') else [],
            rectification_items=[case.slots['remedy']] if row.data.get('explained') else [],source_refs=[ref for e in entries for ref in e.source_refs],operation_refs=operation_ids,
            strategy_version=row.data['strategy_version'],rule_version=row.data['rule_version'],knowledge_version=row.data['knowledge_version'])

    def save_non_action_record(self,db,row,case,contact):
        if not db.scalar(select(Record).where(Record.session_id==row.id)):
            record=self.make_record(row,case,[],contact)
            db.add(Record(id=str(record.record_id),session_id=row.id,data=record.model_dump(mode='json')))
            from .realtime_business.events import publish_writeback
            publish_writeback(db, row, record)

    async def reconcile_loop(self):
        while True:
            try:
                with self.repo.read() as db:
                    ids=[o.id for o in db.scalars(select(Operation)) if not o.data.get('writeback_done') and not o.data.get('manual') and o.data.get('next_at',0)<=now().timestamp()]
                for oid in ids: await self.process_operation(oid)
            except asyncio.CancelledError: raise
            except Exception as exc: log.warning('reconcile_failed kind=%s',type(exc).__name__)
            await asyncio.sleep(0.2)

    async def process_operation(self,oid):
        with self.repo.transaction() as db:
            op=db.scalar(select(Operation).where(Operation.id==oid).with_for_update())
            if op.data.get('manual') or op.data.get('writeback_done'):
                return
            command=ActionCommand.model_validate(op.data['command'])
            prior=op.status
            # Persist dispatch intent BEFORE external I/O. A restart queries instead of resubmitting.
            if prior=='pending':
                op.status='unknown'
                op.data={**op.data,'result':OperationResult(operation_id=UUID(oid),status='unknown',updated_at=now()).model_dump(mode='json')}
        if prior=='pending':
            try: result=await asyncio.wait_for(self.services.business.execute(command),3)
            except (TimeoutError,DomainError): result=OperationResult(operation_id=UUID(oid),status='unknown',updated_at=now())
        elif prior in ['unknown','not_found_yet']:
            try: result=await asyncio.wait_for(self.services.business.query_operation(command),3)
            except (TimeoutError,DomainError): result=OperationResult(operation_id=UUID(oid),status='unknown',updated_at=now())
        else:
            with self.repo.read() as db: result=OperationResult.model_validate(db.get(Operation,oid).data['result'])
        with self.repo.transaction() as db:
            op=db.scalar(select(Operation).where(Operation.id==oid).with_for_update())
            if op.data.get('manual') or op.data.get('writeback_done'):
                return
            row=db.scalar(select(Conversation).where(Conversation.id==op.session_id).with_for_update())
            attempts=op.data['attempts']+1
            op.status=result.status
            op.data={**op.data,'result':result.model_dump(mode='json'),'attempts':attempts,'next_at':now().timestamp()+min(10,attempts)}
            emit(db,row,OperationUpdated(type='operation.updated',result=result),operation=oid)
            if result.status not in ['succeeded','failed']:
                transition(db,row,'RECONCILING')
                if attempts>=6:
                    op.data={**op.data,'manual':True}
                    row.data={**row.data,'ended':True}
                    transition(db,row,'MANUAL_REVIEW')
                    emit(db,row,SessionEnded(type='session.ended',reason='operation_unknown'))
                return
            if 'writeback_record' in op.data:
                record=CallRecord.model_validate(op.data['writeback_record'])
            elif 'case_snapshot' in op.data:
                case=ReviewCase.model_validate(op.data['case_snapshot'])
                record=self.make_record(row,case,[UUID(oid)])
                op.data={**op.data,'writeback_record':record.model_dump(mode='json')}
            else:
                case=self.services.cases.get_case(row.owner,row.application_id)
                if (case.application_id!=getattr(command,'application_id',None) or
                    case.case_revision!=getattr(command,'case_revision',None) or
                    case.case_revision!=row.data.get('case_revision')):
                    op.data={**op.data,'manual':True,'writeback_last_error':'STALE_REVISION'}
                    row.data={**row.data,'ended':True}
                    transition(db,row,'MANUAL_REVIEW')
                    emit(db,row,SessionEnded(type='session.ended',reason='stale_revision'))
                    return
                record=self.make_record(row,case,[UUID(oid)])
                op.data={**op.data,'writeback_record':record.model_dump(mode='json')}
            transition(db,row,'WRITEBACK_PENDING')
            writeback_attempts=op.data.get('writeback_attempts',0)
        try:
            await asyncio.wait_for(self.services.business.writeback(record),3)
        except (TimeoutError,DomainError) as exc:
            # 回写失败是**独立**于业务效果的状态：业务效果已经发生，绝不能重发驳回，
            # 也不能把失败吞掉或谎报 CLOSED。有界重试后如实转人工。
            writeback_attempts+=1
            _writeback_retry(oid,writeback_attempts,getattr(exc,'code',type(exc).__name__))
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 非预期异常同样必须留证并转人工
            writeback_attempts+=1
            _writeback_retry(oid,writeback_attempts,type(exc).__name__)
            return
        with self.repo.transaction() as db:
            op=db.scalar(select(Operation).where(Operation.id==oid).with_for_update())
            if op.data.get('manual') or op.data.get('writeback_done'):
                return
            row=db.scalar(select(Conversation).where(Conversation.id==op.session_id).with_for_update())
            op.data={**op.data,'writeback_done':True,'writeback_attempts':writeback_attempts+1}
            from .realtime_business.events import publish_writeback
            publish_writeback(db, row, record)
            transition(db,row,'CLOSED' if result.status=='succeeded' else 'MANUAL_REVIEW')
            row.data={**row.data,'ended':True}
            emit(db,row,SessionEnded(type='session.ended',reason='business_completed'))


def messages_db_ids(coordinator, sid):
    with coordinator.repo.read() as db:
        return [MessageView(message_id=m.id, **m.data['view']) for m in db.scalars(
            select(Message).where(Message.session_id == sid).order_by(Message.position))]


def _writeback_retry(oid, attempts, code):
    """记录一次回写失败；有界重试后如实转人工。

    - 业务效果已发生（operation 已 succeeded），因此**绝不重发驳回**，只重试回写。
    - 失败不能被吞掉：每次失败都累加 writeback_attempts 并重新排期。
    - 达到 WRITEBACK_MAX_ATTEMPTS 后置 manual 并转 MANUAL_REVIEW，不谎报 CLOSED，
      也不伪造 call_records（回写没有成功就不能有本地记录）。
    """
    log.warning('writeback_failed operation=%s attempt=%s code=%s', oid, attempts, code)
    exhausted = attempts >= WRITEBACK_MAX_ATTEMPTS
    try:
        with Session.begin() as db:
            op = db.scalar(select(Operation).where(Operation.id == oid).with_for_update())
            if op is None:
                return
            if op.data.get('writeback_done'):
                return
            row = db.scalar(select(Conversation).where(Conversation.id == op.session_id).with_for_update())
            op.data = {**op.data, 'writeback_attempts': attempts,
                       'writeback_done': False, 'writeback_last_error': code,
                       'next_at': now().timestamp() + min(WRITEBACK_BACKOFF_SECONDS, max(attempts, 1))}
            if exhausted and row is not None:
                op.data = {**op.data, 'manual': True}
                row.data = {**row.data, 'ended': True,
                            'writeback_failure': {'code': code, 'attempts': attempts,
                                                  'at': now().isoformat()}}
                if row.state == 'WRITEBACK_PENDING':
                    transition(db, row, 'MANUAL_REVIEW')
                emit(db, row, SessionEnded(type='session.ended', reason='writeback_failed'))
    except Exception as exc:  # noqa: BLE001 - 留证失败不得掩盖原回写失败
        log.warning('writeback_retry_record_failed kind=%s operation=%s', type(exc).__name__, oid)
