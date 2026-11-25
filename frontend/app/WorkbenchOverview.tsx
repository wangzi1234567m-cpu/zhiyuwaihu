import {taskComplete,type TaskViews,type SupportCatalog} from '../lib/workbench';

export default function WorkbenchOverview({views,total,catalog}:{views:TaskViews;total:number;catalog:SupportCatalog|null}) {
  const complete=Object.values(views).filter(taskComplete).length;
  return <section className="stats" aria-label="业务概览">
    <article className="stat-card"><small>原始主表场景</small><div><strong>{catalog?.original_total??'—'}</strong><span>原始数据统计</span></div></article>
    <article className="stat-card"><small>原表默认电话渠道</small><div><strong>{catalog?.original_phone??'—'}</strong><span>非当前任务分流数</span></div></article>
    <article className="stat-card"><small>原表默认京麦渠道</small><div><strong>{catalog?.original_jingmai??'—'}</strong><span>非已发布支持数</span></div></article>
    <article className="stat-card"><small>本次工作台已完成</small><div><strong data-testid="completed-count">{complete}</strong><span>/ {total} 个加载任务</span></div><small>仅计服务端确认的闭环</small></article>
  </section>;
}
