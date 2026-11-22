import type {components} from './api.generated';

type View = components['schemas']['SessionView'];
const routes = {phone: '电话告知', jingmai: '京麦书面', manual: '人工核实'};
const actions = {reject_application: '模拟驳回', create_callback: '创建预约', create_followup: '创建人工跟进', writeback: '记录回写'};
const results = {pending: '处理中', succeeded: '模拟处理成功', unknown: '结果待确认', not_found_yet: '尚未查到结果', failed: '处理失败'};
const measurements = {model_ttft: '模型首段响应', speech_end_to_text: '说完至文本', speech_end_to_playback: '说完至播放'};
const outcomes = {success: '已测量', timeout: '超时', cancelled: '已取消', missing: '缺少测量'};

export default function BusinessFacts({view}: {view: View}) {
  return <>
    <p>关单渠道：{routes[view.route]}</p>
    {view.verified && <>
      {view.reason && <p>原因：{view.reason}</p>}
      {view.remedy && <p>整改：{view.remedy}</p>}
      {!!view.source_refs?.length && <details><summary>查看整改依据</summary>
        <ul>{view.source_refs.map(ref => <li key={[ref.file_sha256,ref.sheet,ref.row,ref.columns].join('/')}>
          {ref.sheet} · 第 {ref.row} 行 · {ref.columns.join('、')}
        </li>)}</ul>
      </details>}
    </>}
    {view.operations.map(op => <p key={op.operation_id} data-testid="operation-status">
      {op.kind ? actions[op.kind] : '业务操作'} · {results[op.status]}
      {op.error && <small>{op.error.message}</small>}
      {op.resource_id && <small>{op.resource_id}</small>}
    </p>)}
    {view.record && <p data-testid="writeback">通话记录已回写</p>}
    {!!view.metrics?.length && <details><summary>查看本次响应指标</summary>
      <ul>{view.metrics.map(m => <li key={m.name}>{measurements[m.name]} · {m.duration_ms == null ? outcomes[m.outcome] : m.duration_ms.toFixed(0)+' ms'}
        <small>{m.measurement_method}</small></li>)}</ul>
    </details>}
  </>;
}
