import {routeNames,type Application,type View} from '../lib/workbench';

export default function TaskContext({application,view}:{application?:Application;view:View}) {
  return <>
    <div className="titleline"><div><div className="eyebrow">当前审核任务</div><h2>{view.reason??application?.label??'资质审核任务'}</h2></div><span className={`tag ${view.route==='jingmai'?'doc':view.route==='manual'?'warn':''}`}>{routeNames[view.route]}</span></div>
    <dl className="facts"><div><dt>申请商家</dt><dd>{application?.company_hint??'后端未提供'}</dd></div>
      <div><dt>申请品牌</dt><dd className="muted">工作人员接口待提供</dd></div>
      <div><dt>业务分类</dt><dd className="muted">后端未提供</dd></div>
      <div><dt>同场景错误提交次数</dt><dd className="muted">后端未提供</dd></div>
      <div><dt>申请单编号</dt><dd className="identifier">{view.application_id}</dd></div><div><dt>审核版本</dt><dd>第 {view.case_revision} 版</dd></div>
    </dl>
    <section className="route-decision" aria-label="系统渠道判定"><strong>系统渠道判定 · {routeNames[view.route]}</strong>
      <p>{view.route==='phone'?'电话告知与书面执行分别留证，通话结束不等于业务关单。':view.route==='jingmai'?'当前服务端判定为京麦书面渠道，无需强制电话核验。':'当前任务需人工核实，不自动派发驳回。'}</p>
      <small>渠道来自当前会话快照；完整决策依据及规则版本的工作人员投影接口尚未开放。</small>
    </section>
    <section className="review-facts" aria-label="审核事实与整改建议"><h3>审核事实 <span>工作人员视图</span></h3>
      <dl className="facts"><div><dt>既定驳回原因</dt><dd>{view.reason??'当前接口尚未提供工作人员审核事实'}</dd></div>
        <div><dt>整改建议</dt><dd>{view.remedy??'等待已授权的审核事实接口'}</dd></div></dl>
      {!view.reason&&<p className="mini">现有接口在身份核验前隐藏原因、品牌和整改详情；本页保留这一边界，不从演示原型补造内容。</p>}
    </section>
  </>;
}
