'use client';
import {useState} from 'react';
import {useWorkbench} from '../lib/use-workbench';
import {sourceStats,stateNames,type Transport,type View} from '../lib/workbench';
import WorkbenchOverview from './WorkbenchOverview';
import TaskQueue from './TaskQueue';
import TaskContext from './TaskContext';
import BusinessWorkflow from './BusinessWorkflow';
import SubtitlePanel from './SubtitlePanel';
import RealtimeVoicePanel from './RealtimeVoicePanel';
import VoicePanel from './VoicePanel';
import RoutingPreview from './routing-preview';

function WrittenProcess({view}:{view:View}) {
  const operation=view.operations.find(op=>op.kind==='reject_application');
  return <section className="written-process" aria-label="书面驳回与结果回写">
    <h3>{view.route==='jingmai'?'京麦书面驳回':'后续书面驳回与结果回写'}</h3>
    <p className="muted">{view.route==='jingmai'?'无需强制发起语音。正文核对、业务执行与回写分别确认。':'电话结束后仍须确认告知证据、书面操作与回写结果，挂断不计关单。'}</p>
    <div className="written-sheet"><span className="eyebrow">书面驳回意见</span><p>已审书面正文预览接口尚未开放。</p><small>不将AI口头回答或原型模板冒充实际京麦提交内容。</small>
      {!!view.record?.rectification_items.length&&<><h4>已登记的整改条目</h4><ul>{view.record.rectification_items.map((text,index)=><li key={index}>{text}</li>)}</ul></>}
    </div>
    <div className="written-actions"><button disabled title="当前API未提供受控书面执行入口" data-testid="written-execute">执行京麦驳回</button><span className="muted">{operation?'已有业务操作，请在右侧查证其实际结果。':'执行接口待接入，当前不派发操作。'}</span></div>
    {view.ended&&view.state!=='CLOSED'&&<p className="notice warning">通话已结束；业务状态为“{stateNames[view.state]}”，尚未形成完整关单。</p>}
  </section>;
}

export default function WorkbenchPage() {
  const work=useWorkbench();
  const [password,setPassword]=useState(''),[input,setInput]=useState(''),[callStatus,setCallStatus]=useState('等待发起外呼');
  const view=work.view,application=work.applications.find(item=>item.application_id===work.selected);
  const mediaLocked=!!view&&view.mode==='voice'&&!view.ended&&view.state!=='CASE_READY';
  const queueLocked=work.pending||work.loading||mediaLocked;
  const canTalk=!!view&&!view.ended&&view.state!=='CASE_READY'&&view.mode==='text'&&view.route==='phone';
  return <>
    <header className="app-header"><div className="brand"><span className="brand-mark" aria-hidden="true">智</span><div><div className="eyebrow">ZHIYU · OUTBOUND AI</div><h1>智语外呼 · 商户资质审核</h1></div></div>
      <div className="header-meta"><span className="tag">资审工作台</span><span className="muted">{work.logged?'合成任务联调环境':'工作人员登录'}</span></div>
    </header>
    <main>
      <div className="intro"><div><div className="eyebrow">QUALIFICATION OPERATIONS</div><h2>资审关单工作台</h2><p>依据审核任务分流，完成商家告知、整改指导与结果回写。</p></div>
        {work.logged&&<details className="developer-settings"><summary>开发设置</summary><div className="settings-popover">
          <h3>交互传输设置</h3><p className="muted">只影响联调方式，不改变服务端业务渠道。</p>
          <label>交互方式<select value={work.transport} disabled={work.pending||!!view&&view.state!=='CASE_READY'&&!view.ended} onChange={e=>void work.changeTransport(e.target.value as Transport)}>
            <option value="native_webrtc" disabled={!work.caps?.realtime_webrtc}>WebRTC 实时语音</option><option value="voice" disabled={!work.caps?.voice}>旧语音</option><option value="text">文字对话</option>
          </select></label>
          {work.transport==='text'&&<p className="notice">文字联调用于验证业务，不产生真实通话和播放凭据。</p>}
          {!work.caps?.realtime_webrtc&&<p className="mini">{work.caps?.realtime_unavailable_reason??'新语音模式未配置'}</p>}
          <button className="secondary" disabled={work.pending||!view||view.busy||mediaLocked} onClick={()=>void work.newSession()}>新建联调会话</button>
          <button className="secondary" disabled={work.pending||work.loading} onClick={()=>void work.reload()}>重新读取任务队列</button>
          {work.caps?.routing_preview&&<details className="rule-settings"><summary>未发布场景规则试算</summary><p className="notice warning">以下仅供后端规则试算，不改变选中任务，不构成已发布支持或业务执行。</p><RoutingPreview/></details>}
        </div></details>}
      </div>
      {work.error&&<div className="notice error" role="alert">{work.error}<button onClick={()=>work.setError('')} aria-label="关闭错误提示">×</button></div>}
      {!work.logged?<section className="login panel"><div className="panelhead"><h2>进入审核工作台</h2></div><form className="pad" onSubmit={e=>{e.preventDefault();void work.login(password);}}>
        <p className="muted">使用当前环境的工作人员演示账号。</p><label>演示密码<input type="password" autoComplete="current-password" value={password} onChange={e=>setPassword(e.target.value)} aria-label="演示密码"/></label>
        <button disabled={work.pending||work.loading}>登录</button></form></section>:<>
        <WorkbenchOverview views={work.views} total={work.applications.length}/>
        <div className="support-line"><span>原始统计已核对赛题ZIP与主表来源；实际发布支持数未提供。</span><span>{work.loading?'正在加载任务与服务端渠道…':'已加载 '+Object.keys(work.views).length+' 个任务会话'}</span></div>
        <div className="workbench-layout">
          <TaskQueue applications={work.applications} views={work.views} selected={work.selected} loading={work.loading} locked={queueLocked} errors={work.taskErrors} onSelect={id=>void work.selectTask(id)}/>
          <section className="panel task-detail" aria-label="任务详情与交互"><div className="panelhead"><h2>任务详情与交互</h2><span className={'tag '+(view?.state==='MANUAL_REVIEW'?'warn':'')}>{view?stateNames[view.state]:'读取任务中'}</span></div>
            <div className="pad">{!view?<div className="empty"><h3>{work.loading?'正在读取审核任务':'暂无可用任务上下文'}</h3><p>任务数据来自服务端，不使用原型中的模拟记录。</p><button disabled={work.loading} onClick={()=>void work.reload()}>重新加载</button></div>:<>
              <TaskContext application={application} view={view}/>
              {view.route==='phone'?<>
                <section className="phone-interaction" aria-label="电话告知交互"><div className="section-heading"><h3>电话联系与整改告知</h3><span className="muted">{work.connecting?'业务事件重连中':view.mode==='text'?'文字联调':callStatus}</span></div>
                  {view.mode==='voice'&&<p className="voice-guidance">身份核验时请对着麦克风说：“我是{application?.company_hint??'该商家'}的联系人。”识别完成后显示在下方。</p>}
                  {view.mode==='voice'?(view.voice_transport==='native_webrtc'?<><SubtitlePanel view={view} live={work.live} generation={work.generation}/><RealtimeVoicePanel key={view.session_id} view={view} onError={work.setError} onStatus={setCallStatus}/></>:
                    <VoicePanel key={view.session_id} view={view} onError={work.setError} onStatus={setCallStatus}/>):<>
                    <SubtitlePanel view={view} live={work.live} generation={work.generation}/>
                    <div className="call-status" role="status">{view.ended?'通话已结束':view.busy?'AI 正在处理':view.state==='CASE_READY'?'待开始文字联调':'等待商户回复'}</div>
                    <div className="actions"><button disabled={view.state!=='CASE_READY'||work.pending||view.ended} onClick={()=>void work.start()}>开始文字联调</button>
                      <button className="secondary" disabled={!view.busy||work.pending||!view.generation_id} onClick={()=>void work.interrupt()}>打断播报</button>
                      <button className="danger" disabled={view.ended||work.pending} onClick={()=>void work.end()}>结束通话</button></div>
                    <form className="composer" onSubmit={e=>{e.preventDefault();void work.send(input).then(sent=>{if(sent)setInput('');});}}><input aria-label="商户回复" placeholder="输入商户回复，提交后由服务端校验…" value={input} onChange={e=>setInput(e.target.value)} disabled={!canTalk||work.pending}/><button disabled={!canTalk||work.pending||!input.trim()}>发送</button></form>
                    <p className="mini">当前是开发设置中的文字联调。消息与业务结果均由后端返回，不计作语音告知证据。</p>
                  </>}
                </section><WrittenProcess view={view}/>
              </>:view.route==='jingmai'?<WrittenProcess view={view}/>:<section className="notice warning"><h3>需要人工核实</h3><p>服务端判定当前任务不能自动处理。请查阅右侧业务事件；没有创建外部人工工单。</p></section>}
            </>}</div>
          </section>
          <BusinessWorkflow view={view} events={work.events} connecting={work.connecting} onRefresh={()=>void work.refresh()}/>
        </div>
      </>}
      <footer className="page-foot">原始数据依据：赛题主表“场景汇总”，电话{sourceStats.phone} / 京麦{sourceStats.jingmai}。{sourceStats.total}条原始场景不代表系统全部支持自动回答。<br/>
        当前接口未提供的工作人员事实、发布范围及书面执行入口已明确标注；不调用浏览器朗读，不向真实号码外呼，不连接真实京麦写接口。</footer>
    </main>
  </>;
}
