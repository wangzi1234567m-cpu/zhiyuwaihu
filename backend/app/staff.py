"""Staff-only read projections and guarded synthetic business commands.

No model or media calls. All writes use the existing Coordinator/operation ledger.
"""
import os
from uuid import UUID
from sqlalchemy import select, text
from .adapters import DomainError
from .contracts import ActionCommand, AgentDecision, ContactRecorded, ReviewCase, SessionView, KnowledgeEntry
from .coordinator import owned, snapshot, receipt, transition, emit
from .db import Application, Conversation, Knowledge, Operation, RequestReceipt, new_id
from .guard import RULE_VERSION, GuardContext
from .knowledge_templates import task_slot, render, check_template
from .staff_contracts import StaffApplication, WrittenPreview, SupportCatalog, SceneSupport
from .written import WrittenResponseRenderer, digest


class StaffService:
    def __init__(self,coordinator):
        self.coordinator=coordinator

    def latest(self,db,application_id,owner,case_revision):
        # Conversation IDs are random; the first persisted event provides admission time.
        from .db import EventRow
        return db.scalar(select(Conversation).join(EventRow,EventRow.session_id==Conversation.id)
                         .where(Conversation.application_id==str(application_id),Conversation.owner==owner,Conversation.data['case_revision'].as_integer()==case_revision,EventRow.seq==1)
                         .order_by(EventRow.created_at.desc(),Conversation.id.desc()).limit(1))

    def project(self,db,application,owner):
        case=ReviewCase.model_validate(application.data)
        row=self.latest(db,application.id,owner,case.case_revision)
        version=row.data['knowledge_version'] if row else os.getenv('ZHIYU_KNOWLEDGE_VERSION','demo-k1')
        decision=self.coordinator.decide(case,channel=case.default_route,
            contact_outcome=case.contact_outcome,outcome_source='domain_event' if case.contact_event_id else 'none')
        return StaffApplication(application_id=case.application_id,label=f'申请单 {str(case.application_id)[-6:]}',
            company_hint=task_slot(case.slots,'company_name') or '待核实主体',brand_name=task_slot(case.slots,'brand_name') or None,
            scene_name=case.scene_name or case.scenario_id,business_category=case.business_category or '未分类',
            scenario_id=case.scenario_id,default_route=case.default_route,effective_route=decision.route,
            state=row.state if row else 'CASE_READY',case_revision=case.case_revision,submission_attempt=case.submission_attempt,
            call_attempt=case.call_attempt,contact_outcome=case.contact_outcome,contact_event_id=case.contact_event_id,
            reason=task_slot(case.slots,'reason') or None,remedy=task_slot(case.slots,'remedy') or None,
            routing_decision=decision,knowledge_version=version,rule_version=RULE_VERSION,source_refs=case.source_refs,
            snapshot=snapshot(db,row,staff=True) if row else None,
            available_actions=['create_session'] if not row or row.data.get('ended') else ['written_preview'])

    def applications(self,owner):
        with self.coordinator.repo.read() as db:
            return [self.project(db,a,owner) for a in db.scalars(select(Application).where(Application.owner==owner).order_by(Application.id))]

    def detail(self,owner,aid):
        with self.coordinator.repo.read() as db:
            row=db.get(Application,str(aid))
            if row is None or row.owner!=owner:raise DomainError('NOT_FOUND','申请单不存在',404)
            return self.project(db,row,owner)

    def catalog(self,owner):
        version=os.getenv('ZHIYU_KNOWLEDGE_VERSION','demo-k1')
        with self.coordinator.repo.read() as db:
            entries=[KnowledgeEntry.model_validate(k.data)
                     for k in db.scalars(select(Knowledge).where(Knowledge.version==version))]
            cases=[ReviewCase.model_validate(a.data) for a in db.scalars(select(Application).where(Application.owner==owner))]
        scenes=[]
        for scenario in sorted({e.scenario_id for e in entries}|{c.scenario_id for c in cases}):
            candidates=[e for e in entries if e.scenario_id==scenario and e.status=='approved' and not e.issue_codes and 'synthetic' in e.scope]
            # Capacity means reviewed templates exist, not that every task has filled facts.
            def ready(channel):
                selected=[e for e in candidates if e.channel==channel and e.kind in ('scenario','script') and e.approved_text]
                try:
                    for entry in selected:check_template(entry)
                except ValueError:return False
                return bool(selected) and len({e.approved_text for e in selected})==1
            phone=ready('phone');written=ready('jingmai')
            scenes.append(SceneSupport(scenario_id=scenario,published=bool(candidates),phone=phone,written=written,
                unavailable_reasons=([] if phone else ['缺少适用已审电话知识或存在模板冲突'])+([] if written else ['缺少适用已审书面知识或存在模板冲突'])))
        renderable=0
        for case in cases:
            try:WrittenResponseRenderer().render(case,entries,version);renderable+=1
            except DomainError:pass
        return SupportCatalog(knowledge_version=version,published_scenarios=sum(s.published for s in scenes),
            approved_entry_count=sum(e.status=='approved' and not e.issue_codes for e in entries),
            quarantined_entry_count=sum(e.status!='approved' or bool(e.issue_codes) for e in entries),renderable_tasks=renderable,
            phone_supported=sum(s.phone for s in scenes),written_supported=sum(s.written for s in scenes),scenes=scenes)

    def preview(self,owner,sid):
        with self.coordinator.repo.read() as db:
            return self._preview(db,owned(db,sid,owner),owner)

    def _preview(self,db,row,owner):
        application=db.get(Application,row.application_id)
        if application is None or application.owner!=owner:raise DomainError('NOT_FOUND','申请单不存在',404)
        case=ReviewCase.model_validate(application.data)
        data={**row.data,'execution_origin':'staff'}
        decision=self.coordinator.decide(case,channel=case.default_route,
            contact_outcome=data.get('contact_outcome','unknown'),outcome_source=data.get('contact_outcome_source','none'))
        data.update(route=decision.route,routing_decision=decision.model_dump(mode='json'))
        if 'no_answer_after_followup' in decision.reason_codes:data['trusted_followup']=self.coordinator.has_trusted_followup(case)
        result=WrittenPreview(session_id=row.id,case_revision=case.case_revision,state_revision=row.revision,
            knowledge_version=data['knowledge_version'],rule_version=RULE_VERSION)
        entries=self.coordinator.services.knowledge.lookup(case.scenario_id,'',['synthetic'],data['knowledge_version'])
        try:
            entry=WrittenResponseRenderer().render(case,entries,data['knowledge_version'])
        except DomainError as exc:
            result.unavailable_reasons=[exc.code+': '+exc.message]
            result.missing_slots=sorted({name for e in entries if e.channel=='jingmai' for name in e.required_slots if not task_slot(case.slots,name)})
            return result
        result.written_text=entry.approved_text;result.knowledge_entry_id=entry.entry_id
        result.source_refs=entry.source_refs;result.template_version=entry.knowledge_version
        result.previewable=True
        import hashlib
        result.body_sha256=hashlib.sha256(entry.approved_text.encode()).hexdigest()
        # Bind all trusted facts and the stored version, not just visible body text.
        result.preview_id=digest({'case':case.model_dump(mode='json'),'entry':entry.model_dump(mode='json'),
            'body':result.body_sha256,'revision':row.revision,'session':row.id,'knowledge_version':data['knowledge_version'],
            'stored_rule':data['rule_version'],'rule':RULE_VERSION,'route':decision.route,'codes':decision.reason_codes,
            'notifications':data.get('notifications',[]),'contact':data.get('contact_outcome'),'ended':data.get('ended')})
        try:
            if data['knowledge_version']!=os.getenv('ZHIYU_KNOWLEDGE_VERSION','demo-k1'):
                raise DomainError('STALE_KNOWLEDGE','当前会话知识版本已失效，请创建新会话',409)
            if case.contact_event_id and data.get('contact_event_id')!=str(case.contact_event_id):
                raise DomainError('STALE_CONTACT_EVIDENCE','联系事实已更新，请创建新会话',409)
            if row.state not in ('CASE_READY','ACTION_READY','EXPLAINING','QA','CLOSING') or data.get('busy'):
                raise DomainError('BUSINESS_BUSY','当前业务阶段不允许再次执行',409)
            payload=WrittenResponseRenderer().payload(case,[entry],data['knowledge_version'])
            command=ActionCommand(session_id=row.id,application_id=case.application_id,case_revision=case.case_revision,
                decision_id=decision.decision_id,operation_id=new_id(),kind='reject_application',idempotency_key='preview',evidence_refs=[],payload=payload)
            self.coordinator._guard().authorize(case,self.coordinator.guard_view(data),command,[entry],GuardContext())
            result.executable=True
        except DomainError as exc:
            result.unavailable_reasons.append(exc.code+': '+exc.message)
        return result

    def reject(self,owner,sid,request,key):
        with self.coordinator.repo.transaction() as db:
            row=owned(db,sid,owner,True)
            application=db.scalar(select(Application).where(Application.id==row.application_id).with_for_update())
            db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':owner+':staff:'+key})
            rkey=f'{owner}:staff-reject:{key}'
            payload={'session_id':sid,**request.model_dump(mode='json')}
            hashed,old=receipt(db,rkey,payload)
            if old:return snapshot(db,row,staff=True)
            # Lock the release against concurrent edits until the guarded command is durable.
            list(db.scalars(select(Knowledge).where(Knowledge.version==row.data['knowledge_version']).with_for_update()))
            preview=self._preview(db,row,owner)
            if (request.case_revision!=preview.case_revision or request.expected_revision!=preview.state_revision
                    or request.knowledge_version!=preview.knowledge_version or request.rule_version!=preview.rule_version
                    or request.body_sha256!=preview.body_sha256 or request.preview_id!=preview.preview_id):
                raise DomainError('STALE_PREVIEW','预览已变化，请重新核对正文和版本',409)
            if not preview.executable:raise DomainError('WRITTEN_FORBIDDEN','；'.join(preview.unavailable_reasons),403)
            case=ReviewCase.model_validate(application.data)
            if row.data['knowledge_version']!=os.getenv('ZHIYU_KNOWLEDGE_VERSION','demo-k1'):
                raise DomainError('STALE_KNOWLEDGE','知识版本已更新，请创建新业务会话',409)
            entries=self.coordinator.written_entries(case,row.data)
            self.coordinator._refresh_route(db,row,case,entries)
            row.data={**row.data,'execution_origin':'staff'}
            if row.state=='CASE_READY':transition(db,row,'ROUTING')
            transition(db,row,'ACTION_READY')
            proposal=AgentDecision(reply_text='',proposed_action='reject_application',evidence_message_ids=[],knowledge_entry_ids=[preview.knowledge_entry_id],next_state='ACTION_READY')
            self.coordinator.enqueue(db,row,case,proposal,entries)
            db.add(RequestReceipt(key=rkey,payload_hash=hashed,data={'session_id':sid}))
            db.flush()
            return snapshot(db,row,staff=True)

    def contact(self,owner,sid,request):
        from .config import settings
        if settings.environment not in ('development','test'):
            raise DomainError('SYNTHETIC_DISABLED','合成联系事件仅供本地开发和测试',404)
        with self.coordinator.repo.transaction() as db:
            row=owned(db,sid,owner,True)
            application=db.scalar(select(Application).where(Application.id==row.application_id).with_for_update())
            rkey=f'{owner}:contact:{request.event_id}'
            hashed,old=receipt(db,rkey,{'session_id':sid,**request.model_dump(mode='json')})
            if old:return snapshot(db,row,staff=True)
            case=ReviewCase.model_validate(application.data)
            if row.data.get('mode')!='text' or not case.synthetic:
                raise DomainError('SYNTHETIC_CONTACT_ONLY','合成事件不能授权语音业务',403)
            if row.state!='CASE_READY' or row.data.get('busy') or row.data.get('ended'):
                raise DomainError('CONTACT_PHASE','请为新的联系尝试创建待处理会话',409)
            if request.case_revision!=case.case_revision or row.data['case_revision']!=case.case_revision:
                raise DomainError('STALE_REVISION','审核版本已变化',409)
            if request.expected_call_attempt!=case.call_attempt:
                raise DomainError('STALE_CONTACT','联系次数已变化，请刷新任务',409)
            case=case.model_copy(update={'call_attempt':case.call_attempt+1,'contact_outcome':request.outcome,'contact_event_id':request.event_id})
            application.data=case.model_dump(mode='json')
            row.data={**row.data,'call_attempt':case.call_attempt,'contact_outcome':request.outcome,'contact_outcome_source':'domain_event','contact_event_id':str(request.event_id)}
            decision=self.coordinator._refresh_route(db,row,case,[])
            emit(db,row,ContactRecorded(event_id=request.event_id,outcome=request.outcome,call_attempt=case.call_attempt))
            if request.outcome in ('wrong_person','refused','disconnected') or decision.route=='manual':
                transition(db,row,'MANUAL_REVIEW');row.data={**row.data,'ended':True}
                self.coordinator.save_non_action_record(db,row,case,request.outcome)
            elif 'no_answer_second_submission_followup' in decision.reason_codes:
                transition(db,row,'ROUTING');transition(db,row,'ACTION_READY')
                from .contracts import FollowupPayload
                proposal=AgentDecision(reply_text='',proposed_action='create_followup',action_payload=FollowupPayload(type='create_followup',reason_code='no_answer_second_submission'),evidence_message_ids=[],knowledge_entry_ids=[],next_state='ACTION_READY')
                self.coordinator.enqueue(db,row,case,proposal,[])
            db.add(RequestReceipt(key=rkey,payload_hash=hashed,data={'session_id':sid}))
            db.flush()
            return snapshot(db,row,staff=True)
