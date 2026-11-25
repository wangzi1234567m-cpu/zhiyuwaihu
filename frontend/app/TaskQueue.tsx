import {useState} from 'react';
import {routeNames,stateNames,sessionStatus,type Application,type TaskViews} from '../lib/workbench';

export default function TaskQueue({applications,views,selected,loading,locked,errors,onSelect}:{
  applications:Application[];views:TaskViews;selected:string;loading:boolean;locked:boolean;errors:Record<string,string>;onSelect:(id:string)=>void;
}) {
  const [filter,setFilter]=useState<'all'|'phone'|'jingmai'>('all');
  const items=applications.filter(task=>filter==='all'||(views[task.application_id]?.route??task.effective_route)===filter);
  return <aside className="panel task-queue" aria-label="审核任务队列">
    <div className="panelhead"><h2>审核任务队列</h2><span className="tag">{items.length} 项</span></div>
    <div className="pad"><div className="filters" role="group" aria-label="按业务渠道筛选">
      {([['all','全部'],['phone','电话'],['jingmai','京麦']] as const).map(([key,label])=><button key={key} className={filter===key?'active':''} aria-pressed={filter===key} onClick={()=>setFilter(key)}>{label}</button>)}
    </div><div className="case-list">
      {loading&&!applications.length&&<p className="empty">正在读取审核任务…</p>}
      {!loading&&!items.length&&<p className="empty">暂无此渠道的已加载任务</p>}
      {items.map(task=>{const view=views[task.application_id];return <button type="button" key={task.application_id}
        className={`task-card ${task.application_id===selected?'selected':''}`} aria-pressed={task.application_id===selected}
        disabled={locked} onClick={()=>onSelect(task.application_id)} data-testid={`task-${task.application_id}`}>
        <div className="task-meta"><small>{task.label}</small><span className={`tag ${view?.route==='jingmai'?'doc':view?.route==='manual'?'warn':''}`}>{routeNames[view?.route??task.effective_route]}</span></div>
        <strong>{task.company_hint}</strong><span className="task-scenario">{task.scene_name}</span>
        <div className="task-bottom"><code title={task.application_id}>{task.application_id.slice(0,8)}…{task.application_id.slice(-4)}</code><span>{errors[task.application_id]?'加载失败':view?sessionStatus(view):stateNames[task.state]}</span></div>
      </button>;})}
    </div><p className="note">渠道取自服务端 RoutingPolicy，不能在前端切换。原始场景数不代表已发布支持范围。</p>
    {locked&&<p className="mini">请先结束当前语音通话，再切换任务。</p>}</div>
  </aside>;
}
