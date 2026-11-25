import BusinessFacts from './BusinessFacts';
import {eventLabel,stateNames,type BusinessEvent,type View} from '../lib/workbench';

export default function BusinessWorkflow({view,events,connecting,onRefresh}:{view:View|null;events:BusinessEvent[];connecting:boolean;onRefresh:()=>void}) {
  const op=view?.operations.find(item=>item.kind==='reject_application');
  const explanation=view?.record?.explanation_status==='completed';
  const closed=view?.state==='CLOSED'&&!!view.record&&view.operations.length>0&&view.operations.every(item=>item.status==='succeeded');
  const steps=view?.route==='jingmai'?[
    {name:'路由判定',detail:'服务端判定无需强制外呼',done:!!view,now:false},
    {name:'核对书面意见',detail:'已审正文预览接口待提供',done:false,now:!op},
    {name:'京麦书面驳回',detail:op?'以持久操作结果为准':'受控执行入口待提供',done:op?.status==='succeeded',now:!!op&&op.status!=='succeeded'},
    {name:'结果回写',detail:'操作成功与回写分别确认',done:closed,now:view.state==='WRITEBACK_PENDING'},
  ]:[
    {name:'路由判定',detail:view?'从申请单与规则得出渠道':'等待业务上下文',done:!!view,now:!view},
    {name:'电话联系',detail:view?.mode==='text'?'文字联调，不产生通话凭据':'接通、未接通分别留证',done:view?.mode==='voice'&&view.record?.contact_status==='verified',now:!!view&&['CASE_READY','OPENING','VERIFYING'].includes(view.state)},
    {name:'身份核验',detail:view?.verified?'服务端已核对':'核对公司或店铺',done:!!view?.verified,now:view?.state==='VERIFYING'},
    {name:'原因与整改告知',detail:explanation?'服务端记录完整告知':'生成或挂断不能代替告知证据',done:explanation,now:!!view&&['EXPLAINING','QA','CALLBACK_PENDING'].includes(view.state)},
    {name:'京麦书面驳回',detail:op?'实际业务操作状态':'等待合法业务操作',done:op?.status==='succeeded',now:!!view&&['ACTION_READY','EXECUTING','RECONCILING'].includes(view.state)},
    {name:'结果回写',detail:closed?'业务闭环已确认':'持久回写完成后才关单',done:closed,now:view?.state==='WRITEBACK_PENDING'},
  ];
  return <aside className="panel workflow" aria-label="业务状态与验收"><div className="panelhead"><h2>业务状态与验收</h2><span className="muted">服务端事实</span></div>
    <div className="pad"><h3 className="section-label">业务处理路径</h3><ol className="steps">
      {steps.map((step,index)=><li key={step.name} className={`step ${step.done?'done':step.now?'now':'pending'}`} aria-current={step.now?'step':undefined}>
        <span className="step-dot">{step.done?'✓':index+1}</span><div><strong>{step.name}</strong><small>{step.detail}</small></div>
      </li>)}
    </ol>
    {view?.state==='MANUAL_REVIEW'&&<div className="notice warning" role="status">需要人工处理。没有声称已建立外部人工工单。</div>}
    {view&&['RECONCILING','WRITEBACK_PENDING','CLOSING'].includes(view.state)&&<div className="notice warning">{stateNames[view.state]}，请继续查证；不要重复执行。</div>}
    <div className="divider"/><h3 className="section-label">执行事实</h3>{view?<BusinessFacts view={view}/>:<p className="muted">选中任务后读取业务状态。</p>}
    <div className="events-heading"><h3 className="section-label">业务事件</h3><span className="muted">{connecting?'补读连接中':'持久事件'}</span></div>
    <ol className="business-events">{events.filter(e=>e.payload.type!=='metric.recorded').slice(-7).map(event=><li key={event.event_id}><span>#{event.seq}</span>{eventLabel(event)}</li>)}</ol>
    {!events.length&&<p className="muted">尚未读取到持久业务事件</p>}
    <button type="button" className="refresh-button" onClick={onRefresh} disabled={!view}>重新查证状态</button>
    <p className="note">只展示后端响应与持久事件；等待、打断、挂断和断线均不直接计为已告知或已完成。</p></div>
  </aside>;
}
