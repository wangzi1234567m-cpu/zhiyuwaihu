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
from .db import Session, Conversation, Message, EventRow, Operation, Record, RequestReceipt, now, new_id
from .adapters import DomainError, Services
from .config import settings

log = logging.getLogger('zhiyu')

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

def transition(db, row, state):
    row.state = state
    row.revision += 1
    emit(db, row, StateChanged(type='state.changed', state=state))

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
        remedy=row.data.get('remedy') if verified else None, messages=messages(db,row.id), record=record.data if record else None, mode=row.data.get('mode','text'))

class Coordinator:
    def __init__(self, services: Services):
        self.services = services
        self.repo = services.repository
        self.tasks: dict[str, asyncio.Task] = {}
        self.all_tasks: set[asyncio.Task] = set()
        self.cancels: dict[str, asyncio.Event] = {}
        self.operation_worker = None

    async def startup(self):
        with self.repo.transaction() as db:
            for row in db.scalars(select(Conversation).with_for_update()):
                if row.data.get('busy'):
                    self._invalidate(db, row, 'interrupted')
        self.operation_worker = asyncio.create_task(self.reconcile_loop())

    async def shutdown(self):
        for cancel in self.cancels.values(): cancel.set()
        pending=list(self.all_tasks)
        for task in pending: task.cancel()
        if self.operation_worker: self.operation_worker.cancel()
        await asyncio.gather(*pending, *([self.operation_worker] if self.operation_worker else []), return_exceptions=True)

    def create(self, owner, request, key):
        if request.mode=='voice' and not self.services.voice.available():
            raise DomainError('VOICE_NOT_CONFIGURED', '语音配置尚未完成', 503)
        case = self.services.cases.get_case(owner, str(request.application_id))
        with self.repo.transaction() as db:
            # Serialize creation receipts across requests/process restarts.
            from sqlalchemy import text
            db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'), {'key': owner+key})
            rkey = f'{owner}:create:{key}'
            digest, previous = receipt(db,rkey,request.model_dump(mode='json'))
            if previous: return snapshot(db,owned(db,previous['session_id'],owner))
            row = Conversation(id=new_id(),owner=owner,application_id=str(case.application_id), state='CASE_READY',revision=0,seq=0,
                data={'case_revision':case.case_revision,'strategy_version':'demo-p1','knowledge_version':os.getenv('ZHIYU_KNOWLEDGE_VERSION', 'demo-k1'),'rule_version':'demo-r1',
                      'route':self.services.policy.decide_route(case),'verified':False,'explained':False,'ended':False,'busy':False,'epoch':0,'message_count':0,'mode':request.mode})
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

    async def accept(self,owner,sid,text,key,*,start_revision=None,source='text',provider_turn=None):
        with self.repo.transaction() as db:
            row=owned(db,sid,owner,True)
            kind='start' if start_revision is not None else 'turn'
            rkey=f'{sid}:{kind}:{key}'
            digest,old=receipt(db,rkey,{'text':text,'revision':start_revision})
            if old: return AcceptedTurn(**old)
            if row.data.get('mode','text')=='voice' and source!='asr':
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
            elif row.state not in ['OPENING','VERIFYING','EXPLAINING','QA','CALLBACK_PENDING']:
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
        cancel=asyncio.Event()
        self.cancels[sid]=cancel
        # Existing work receives cancel; its finally blocks may finish without blocking this request.
        task=asyncio.create_task(self.generate(owner,sid,text,mid,assistant_id,turn,generation,cancel,start_revision is not None,source))
        self.tasks[sid]=task
        self.all_tasks.add(task)
        def done(t):
            self.all_tasks.discard(t)
            if self.tasks.get(sid) is t:
                self.tasks.pop(sid,None)
                self.cancels.pop(sid,None)
            if not t.cancelled(): t.exception()
        task.add_done_callback(done)
        return result

    async def generate(self,owner,sid,text,mid,assistant_id,turn,generation,cancel,opening,source='text'):
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
                if source=='asr':
                    for digit,han in [('1','一'),('2','二'),('3','三')]:aliases.add(company.replace(digit,han))
                identities={phrase for alias in aliases for phrase in [f'我是{alias}',f'我是{alias}的联系人',f'对我是{alias}的联系人']}
                if company and not opening and normalized in identities:
                    row.data={**row.data,'verified':True,'identity_evidence':mid}
                verified=row.data.get('verified',False)
                context=SessionContext(session_id=sid,turn_id=turn,generation_id=generation,generation_epoch=row.data['epoch'],
                    application_id=row.application_id,case_revision=row.data['case_revision'],state_revision=row.revision,
                    strategy_version=row.data['strategy_version'],rule_version=row.data['rule_version'],knowledge_version=row.data['knowledge_version'],model_version=settings.deepseek_model if settings.model_provider=='deepseek' else 'fake-v1')
                history=tuple(messages(db,sid))
            entries=self.services.knowledge.lookup(case.scenario_id,text,['synthetic'],context.knowledge_version)
            entries=available_entries([e for e in entries if e.channel == row.data['route']], case)
            if not company:
                decision=AgentDecision(reply_text='审核任务缺少核验主体，已转人工。',proposed_action='none',evidence_message_ids=[],knowledge_entry_ids=[],next_state='MANUAL_REVIEW')
            elif opening:
                decision=AgentDecision(reply_text=f'您好，这里是模拟商户服务AI演示。请确认您是否是{company}的联系人。',proposed_action='none',evidence_message_ids=[],knowledge_entry_ids=[],next_state='VERIFYING')
            else:
                decision=await asyncio.wait_for(self.services.agent.handle_turn(TurnInput(message_id=mid,text=text,source=source),context,history,case,tuple(entries),verified,cancel),8)
            # Pre-identity and missing-source output cannot bypass the coordinator via a replacement Agent.
            if company and not verified and decision.next_state != 'CLOSING':
                decision=AgentDecision(reply_text=f'这里是模拟商户服务AI。请确认身份：输入“我是{company}”。',proposed_action='none',evidence_message_ids=[],knowledge_entry_ids=[],next_state='VERIFYING')
            if verified and not entries:
                decision=AgentDecision(reply_text='缺少已审核依据，已转人工核实。',proposed_action='none',evidence_message_ids=[],knowledge_entry_ids=[],next_state='MANUAL_REVIEW')
            if source=='asr' and decision.proposed_action!='none' and (not settings.voice_writes_enabled or not row.data.get('voice_explanation_verified')):
                decision=AgentDecision(reply_text='本次语音链路演示已完成，业务写入尚未通过语音告知验收。后续由人工核实处理。',proposed_action='none',evidence_message_ids=[],knowledge_entry_ids=[],next_state='MANUAL_REVIEW')
            if decision.next_state not in ['VERIFYING','QA','CLOSING','MANUAL_REVIEW','ACTION_READY']:
                raise DomainError('INVALID_TRANSITION','Agent提议了不允许的状态')
            if decision.next_state == 'ACTION_READY' and decision.proposed_action == 'none':
                raise DomainError('INVALID_TRANSITION','业务准备状态必须有操作提议')
            # Fixed identity, rectification and action messages must retain exact reviewed wording.
            rephrase=(verified and decision.next_state=='QA' and decision.proposed_action=='none'
                and entries and entries[0].approved_text not in decision.reply_text)
            prior=[ModelMessage(role=m.role,content=m.text) for m in history if m.status=='completed' and m.text and m.generation_id!=UUID(generation)]
            model_request=ModelRequest(messages=[ModelMessage(role='system',content='模拟商户服务。只改写以下已校验答复，不增加任何业务事实、价格、期限或通过承诺：'+decision.reply_text),
                *prior[-12:],ModelMessage(role='user',content=text)],fixture_response=decision.reply_text,allow_rephrase=bool(rephrase),session_id=sid,turn_id=turn,generation_id=generation)
            from .adapters import FakeModel
            output_model=FakeModel() if getattr(self.services.model,'name','fake')=='deepseek' and not model_request.allow_rephrase else self.services.model
            import time
            model_started=time.monotonic()
            first_content=True
            async def output_stream():
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
                msg=db.get(Message,assistant_id)
                msg.data={**msg.data,'view':{**msg.data['view'],'status':'completed'}}
                row.data={**row.data,'busy':False}
                if verified and entries and entries[0].approved_text in decision.reply_text:
                    row.data={**row.data,'explained':True,'reason':case.slots['reason'],'remedy':case.slots['remedy'],'explanation_message_id':assistant_id}
                transition(db,row,decision.next_state)
                if verified and entries: emit(db,row,KnowledgeUsed(type='knowledge.used',entry_ids=[e.entry_id for e in entries],source_refs=entries[0].source_refs),turn=turn,generation=generation)
                if decision.proposed_action != 'none':
                    self.enqueue(db,row,case,decision,entries)
                if decision.next_state in ['CLOSING','MANUAL_REVIEW']:
                    row.data={**row.data,'ended':True}
                    transition(db,row,'MANUAL_REVIEW')
                    contact=('wrong_person' if '不是' in text or '打错' in text else
                        'refused' if any(w in text for w in ['拒绝','不需要','不要联系']) else
                        'verified' if verified else 'disconnected')
                    self.save_non_action_record(db,row,case,contact)
                    emit(db,row,SessionEnded(type='session.ended',reason='manual_review'))
                emit(db,row,GenerationFinished(type='generation.finished',status='completed',message_id=assistant_id),turn=turn,generation=generation)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning('generation_failed kind=%s session=%s',type(exc).__name__,sid)
            with self.repo.transaction() as db:
                row=owned(db,sid,owner,True)
                if self.current(row,generation):
                    emit(db,row,MetricRecorded(type='metric.recorded',name='model_ttft',outcome='timeout' if getattr(exc,'code','')=='MODEL_TIMEOUT' or isinstance(exc,TimeoutError) else 'missing',
                        clock_domain='server.monotonic',measurement_method='failed_generation_no_latency_claim'),turn=turn,generation=generation)
                    self._invalidate(db,row,'failed')

    def application_id(self,owner,sid):
        with self.repo.read() as db: return owned(db,sid,owner).application_id

    def current(self,row,generation):
        return row.data.get('generation_id')==generation and row.data.get('busy') and not row.data.get('ended')

    def enqueue(self,db,row,case,decision,entries):
        key=f'{case.application_id}:{case.case_revision}:reject_application'
        command=ActionCommand(session_id=row.id,application_id=case.application_id,case_revision=case.case_revision,
            decision_id=new_id(),operation_id=new_id(),kind=decision.proposed_action,idempotency_key=key,
            evidence_refs=[row.data.get('identity_evidence'),row.data.get('explanation_message_id')],payload=decision.action_payload)
        # Lock application so sessions sharing an application cannot race the unique business key.
        from .db import Application
        db.scalar(select(Application).where(Application.id==row.application_id).with_for_update())
        fresh=self.services.cases.get_case(row.owner,row.application_id)
        self.services.guard.authorize(fresh,row.data,command,entries)
        existing=db.scalar(select(Operation).where(Operation.key==key))
        if existing:
            row.data={**row.data,'linked_operation_id':existing.id}
            emit(db,row,OperationUpdated(type='operation.updated',result=OperationResult.model_validate(existing.data['result'])),operation=existing.id)
            transition(db,row,'MANUAL_REVIEW')
            row.data={**row.data,'ended':True}
            return
        result=OperationResult(operation_id=command.operation_id,status='pending',updated_at=now())
        db.add(Operation(id=str(command.operation_id),session_id=row.id,key=key,status='pending',
            data={'command':command.model_dump(mode='json'),'case_snapshot':case.model_dump(mode='json'),'result':result.model_dump(mode='json'),'attempts':0,'next_at':0,'writeback_done':False}))
        transition(db,row,'EXECUTING')
        emit(db,row,OperationUpdated(type='operation.updated',result=result),operation=command.operation_id)

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
                transition(db,row,'CLOSING')
                emit(db,row,SessionEnded(type='session.ended',reason='user_ended'))
                if not db.scalar(select(Operation).where(Operation.session_id==row.id)):
                    case=self.services.cases.get_case(owner,row.application_id)
                    self.save_non_action_record(db,row,case,'disconnected')
            db.flush()
            return snapshot(db,row)

    def make_record(self,row,case,operation_ids,contact='verified'):
        entries=self.services.knowledge.lookup(case.scenario_id,'',['synthetic'],row.data['knowledge_version']) if row.data.get('explained') else []
        return CallRecord(record_id=new_id(),record_revision=1,session_id=row.id,application_id=row.application_id,
            case_revision=row.data['case_revision'],contact_status=contact,
            explanation_status='completed' if row.data.get('explained') else 'not_started',reason_codes=[case.scenario_id] if row.data.get('verified') else [],
            rectification_items=[case.slots['remedy']] if row.data.get('explained') else [],source_refs=[ref for e in entries for ref in e.source_refs],operation_refs=operation_ids,
            strategy_version=row.data['strategy_version'],rule_version=row.data['rule_version'],knowledge_version=row.data['knowledge_version'])

    def save_non_action_record(self,db,row,case,contact):
        if not db.scalar(select(Record).where(Record.session_id==row.id)):
            record=self.make_record(row,case,[],contact)
            db.add(Record(id=str(record.record_id),session_id=row.id,data=record.model_dump(mode='json')))

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
            op=db.get(Operation,oid)
            command=ActionCommand.model_validate(op.data['command'])
            prior=op.status
            # Persist dispatch intent BEFORE external I/O. A restart queries instead of resubmitting.
            if prior=='pending':
                op.status='unknown'
                op.data={**op.data,'result':OperationResult(operation_id=oid,status='unknown',updated_at=now()).model_dump(mode='json')}
        if prior=='pending':
            try: result=await asyncio.wait_for(self.services.business.execute(command),3)
            except (TimeoutError,DomainError): result=OperationResult(operation_id=oid,status='unknown',updated_at=now())
        elif prior in ['unknown','not_found_yet']:
            try: result=await asyncio.wait_for(self.services.business.query_operation(command),3)
            except (TimeoutError,DomainError): result=OperationResult(operation_id=oid,status='unknown',updated_at=now())
        else:
            with self.repo.read() as db: result=OperationResult.model_validate(db.get(Operation,oid).data['result'])
        with self.repo.transaction() as db:
            op=db.get(Operation,oid)
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
            case = (ReviewCase.model_validate(op.data['case_snapshot']) if 'case_snapshot' in op.data
                    else self.services.cases.get_case(row.owner, row.application_id))
            record=self.make_record(row,case,[oid])
            transition(db,row,'WRITEBACK_PENDING')
        try: await self.services.business.writeback(record)
        except DomainError: return
        with self.repo.transaction() as db:
            op=db.get(Operation,oid)
            row=db.scalar(select(Conversation).where(Conversation.id==op.session_id).with_for_update())
            op.data={**op.data,'writeback_done':True}
            transition(db,row,'CLOSED' if result.status=='succeeded' else 'MANUAL_REVIEW')
            row.data={**row.data,'ended':True}
            emit(db,row,SessionEnded(type='session.ended',reason='business_completed'))
