import {CheckCircle2} from 'lucide-react';
import type {components} from './api.generated';
import {stateLabels, stateTone} from '../lib/session';
import type {Tone} from '../lib/cx';
import {cx} from '../lib/cx';
import {Badge, Card, CardHeader, Disclosure, dotTones} from './components/ui';

type View = components['schemas']['SessionView'];
type Operation = View['operations'][number];
const routes = {phone: '电话告知', jingmai: '京麦书面', manual: '人工核实'};
const actions = {reject_application: '模拟驳回', create_callback: '创建预约', create_followup: '创建人工跟进', writeback: '记录回写'};
const results = {pending: '处理中', succeeded: '模拟处理成功', unknown: '结果待确认', not_found_yet: '尚未查到结果', failed: '处理失败'};
const resultTones: Record<Operation['status'], Tone> = {pending: 'brand', succeeded: 'success', unknown: 'warning', not_found_yet: 'warning', failed: 'danger'};
const measurements = {model_ttft: '模型首段响应', speech_end_to_text: '说完至文本', speech_end_to_playback: '说完至播放'};
const outcomes = {success: '已测量', timeout: '超时', cancelled: '已取消', missing: '缺少测量'};

function Row({label, children}: {label: string; children: React.ReactNode}) {
  return <div className="flex items-center justify-between gap-3 py-2.5 text-sm">
    <dt className="text-ink-muted">{label}</dt>
    <dd className="text-right font-medium text-ink">{children}</dd>
  </div>;
}

function Block({label, children}: {label: string; children: React.ReactNode}) {
  return <div className="py-2.5 text-sm leading-6">
    <dt className="text-ink-muted">{label}</dt>
    <dd className="mt-0.5 text-ink">{children}</dd>
  </div>;
}

export default function BusinessFacts({view}: {view: View | null}) {
  return <Card>
    <CardHeader title="业务事实" description={view ? '随会话事件刷新' : '创建会话后显示'}/>
    <div className="px-5 pt-1 pb-4">
      <dl className="divide-y divide-line">
        <Row label="状态：">{view
          ? <Badge tone={stateTone(view.state)}>{stateLabels[view.state] ?? view.state}</Badge>
          : <span className="font-normal text-ink-faint">尚未创建</span>}</Row>
        <Row label="身份：">{view?.verified
          ? <span className="inline-flex items-center gap-1 text-emerald-700"><CheckCircle2 aria-hidden className="size-3.5"/>已核对</span>
          : <span className="font-normal text-ink-faint">尚未核对</span>}</Row>
        {view && <Row label="关单渠道：">{routes[view.route]}</Row>}
        {view?.verified && <>
          {view.reason && <Block label="原因：">{view.reason}</Block>}
          {view.remedy && <Block label="整改：">{view.remedy}</Block>}
        </>}
      </dl>

      {view && <div className="mt-2 space-y-3">
        {view.verified && !!view.source_refs?.length && <Disclosure summary="查看整改依据">
          <ul className="space-y-1.5 text-xs text-ink-muted">{view.source_refs.map(ref => <li key={[ref.file_sha256,ref.sheet,ref.row,ref.columns].join('/')}>
            {ref.sheet} · 第 {ref.row} 行 · {ref.columns.join('、')}
          </li>)}</ul>
        </Disclosure>}

        {view.operations.length > 0 && <div>
          <h3 className="mb-2 text-xs font-medium text-ink-muted">业务操作</h3>
          <ul className="space-y-1.5">{view.operations.map(op => <li key={op.operation_id} className="rounded-lg border border-line px-3 py-2">
            <p data-testid="operation-status" className="flex items-center gap-2 text-sm text-ink">
              <span aria-hidden className={cx('size-1.5 shrink-0 rounded-full', dotTones[resultTones[op.status]], op.status === 'pending' && 'animate-pulse')}/>
              {op.kind ? actions[op.kind] : '业务操作'} · {results[op.status]}
            </p>
            {op.error && <small className="mt-1 block text-xs text-rose-700">{op.error.message}</small>}
            {op.resource_id && <small className="mt-1 block truncate font-mono text-[11px] text-ink-faint">{op.resource_id}</small>}
          </li>)}</ul>
        </div>}

        {view.record && <p data-testid="writeback" className="flex items-center gap-2 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
          <CheckCircle2 aria-hidden className="size-4"/>通话记录已回写
        </p>}

        {!!view.metrics?.length && <Disclosure summary="查看本次响应指标">
          <ul className="space-y-2">{view.metrics.map(m => <li key={m.name} className="text-xs">
            <span className="block text-ink">{measurements[m.name]} · <span className="font-mono">{m.duration_ms == null ? outcomes[m.outcome] : m.duration_ms.toFixed(0)+' ms'}</span></span>
            <small className="block font-mono text-[11px] text-ink-faint">{m.measurement_method}</small>
          </li>)}</ul>
        </Disclosure>}
      </div>}
    </div>
  </Card>;
}
