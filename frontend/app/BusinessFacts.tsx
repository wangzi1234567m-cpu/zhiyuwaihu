import type {View} from '../lib/workbench';
const actions={reject_application:'京麦驳回',create_callback:'预约',create_followup:'人工跟进',writeback:'回写'};
const results={pending:'处理中',succeeded:'模拟处理成功',unknown:'结果待查证',not_found_yet:'尚未查到',failed:'处理失败'};
const explanations={not_started:'未开始',partial:'部分完成',completed:'已确认完成',unknown:'待确认',not_required:'无需电话告知'};
export default function BusinessFacts({view}:{view:View}) {
  const rejection=view.operations.find(op=>op.kind==='reject_application');
  const written=view.state==='CLOSED'&&!!view.record;
  return <>
    <dl className="execution-facts"><div><dt>通话次数</dt><dd>后端未提供</dd></div>
      <div><dt>身份核验</dt><dd>{view.verified?'已核对':view.route==='jingmai'?'不强制电话核验':'未核对'}</dd></div>
      <div><dt>告知完成</dt><dd>{view.record?explanations[view.record.explanation_status]:'待后端证据确认'}</dd></div>
      <div><dt>京麦操作</dt><dd>{rejection?results[rejection.status]:'尚未确认执行'}</dd></div>
      <div><dt>回写结果</dt><dd data-testid={written?'writeback':undefined}>{written?'已确认回写':view.state==='WRITEBACK_PENDING'?'等待回写':view.record?'记录已登记，闭环待确认':'尚未确认回写'}</dd></div>
    </dl>
    {view.operations.map(op=><div className={`operation-result ${op.status==='unknown'?'unknown':''}`} key={op.operation_id} data-testid="operation-status">
      <strong>{op.kind?actions[op.kind]:'业务操作'} · {results[op.status]}</strong>{op.error&&<p>{op.error.message}</p>}
      <small className="identifier">{op.operation_id}</small>
    </div>)}
    {!!view.source_refs?.length&&<details className="source-details"><summary>查看已授权来源依据</summary><ul>{view.source_refs.map(ref=><li key={`${ref.file_sha256}:${ref.sheet}:${ref.row}:${ref.columns.join(',')}`}>
      {ref.sheet} · 第{ref.row}行 · {ref.columns.join('、')}</li>)}</ul></details>}
    {!!view.metrics?.length&&<details className="source-details"><summary>响应测量</summary><ul>{view.metrics.map(metric=><li key={metric.name}>{metric.name}：{metric.duration_ms==null?'尚无有效测量':`${Math.round(metric.duration_ms)}ms`}</li>)}</ul></details>}
  </>;
}
