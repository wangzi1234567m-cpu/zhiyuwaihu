import {AlertTriangle} from 'lucide-react';
import {cx} from '../../lib/cx';
import {stageIndex, stages, stateLabels, stateTone, type View} from '../../lib/session';
import {Badge, Card} from './ui';

export default function SessionProgress({view}: {view: View}) {
  const current = stageIndex(view.state);
  const done = view.state === 'CLOSED';
  return <Card className="px-5 py-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="flex items-center gap-2.5">
        <h2 className="text-sm font-semibold text-ink">会话进度</h2>
        <Badge tone={stateTone(view.state)} dot pulse={view.busy}>{stateLabels[view.state] ?? view.state}</Badge>
        {view.mode === 'voice' && <Badge>实时语音</Badge>}
      </div>
      <span className="font-mono text-xs text-ink-faint" title={view.session_id}>会话 {view.session_id.slice(0, 8)}</span>
    </div>
    {view.state === 'MANUAL_REVIEW' && <p className="mt-3 flex items-center gap-2 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">
      <AlertTriangle aria-hidden className="size-3.5"/>当前会话需人工处理
    </p>}
    <ol className="mt-4 grid grid-cols-7 gap-1.5" aria-label="流程阶段">
      {stages.map((stage, i) => {
        const complete = done || i < current;
        const active = !done && i === current;
        return <li key={stage.label} className="min-w-0" aria-current={active ? 'step' : undefined}>
          <div className={cx('h-1 rounded-full transition-colors duration-300',
            complete ? 'bg-brand' : active ? 'bg-brand/50' : 'bg-line')}/>
          <span className={cx('mt-2 hidden truncate text-[11px] sm:block',
            active ? 'font-medium text-ink' : complete ? 'text-ink-muted' : 'text-ink-faint')}>{stage.label}</span>
        </li>;
      })}
    </ol>
  </Card>;
}
