import {routeNames,type Application,type View} from '../lib/workbench';
export default function TaskContext({application,view}:{application:Application;view:View|null}) {
  const route=view?.route??application.effective_route;
  return <>
    <div className="titleline"><div><div className="eyebrow">当前审核任务</div><h2>{application.scene_name}</h2></div><span className={`tag ${route==='jingmai'?'doc':route==='manual'?'warn':''}`}>{routeNames[route]}</span></div>
    <dl className="facts"><div><dt>申请商家</dt><dd>{application.company_hint}</dd></div><div><dt>申请品牌</dt><dd>{application.brand_name??'不适用或未提供'}</dd></div>
      <div><dt>业务分类</dt><dd>{application.business_category}</dd></div><div><dt>同场景错误提交次数</dt><dd>{application.submission_attempt} 次</dd></div>
      <div><dt>申请单编号</dt><dd className="identifier">{application.application_id}</dd></div><div><dt>审核版本</dt><dd>第 {application.case_revision} 版</dd></div></dl>
    <section className="route-decision" aria-label="系统渠道判定"><strong>系统渠道判定 · {routeNames[route]}</strong>
      <p>主表默认：{routeNames[application.default_route]}；当前判定：{routeNames[route]}。只读预判不构成业务执行授权。</p>
      <details><summary>查看渠道判定依据</summary><ul>{application.routing_decision.evidence?.map(e=><li key={e.code}>{e.detail}</li>)}</ul></details>
      <small>规则 {application.rule_version} · 知识 {application.knowledge_version} · 电话结束不等于业务关单</small></section>
    <section className="review-facts" aria-label="审核事实与整改建议"><h3>审核事实 <span>工作人员授权视图</span></h3>
      <dl className="facts"><div><dt>既定驳回原因</dt><dd>{application.reason??'任务缺少审核事实，不能自动执行'}</dd></div><div><dt>整改建议</dt><dd>{application.remedy??'需人工核实'}</dd></div></dl>
      <p className="mini">工作人员事实独立授权；商家在身份核验前不会从字幕和语音中收到具体品牌或驳回原因。</p></section>
  </>;
}
