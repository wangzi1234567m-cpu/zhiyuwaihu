'use client';
import {useEffect, useState} from 'react';
import {Loader2} from 'lucide-react';
import {api} from '../lib/api';
import type {components} from './api.generated';
import {Card, CardHeader, Field, Select, Skeleton} from './components/ui';

type Catalog = components['schemas']['Catalog'];
type Outcome = components['schemas']['Outcome'];
const actions = {phone:'电话说明', jingmai:'京麦书面告知', followup:'创建二次外呼跟进事件', manual:'转人工跟进'};

function Check({checked, onChange, children}: {checked: boolean; onChange: (v: boolean) => void; children: React.ReactNode}) {
  return <label className="flex cursor-pointer items-center gap-2.5 rounded-lg border border-line px-3 py-2.5 text-sm text-ink transition-colors hover:bg-subtle has-[:checked]:border-brand/40 has-[:checked]:bg-brand-soft/60">
    <input type="checkbox" className="size-4 accent-brand" checked={checked} onChange={e=>onChange(e.target.checked)}/>{children}
  </label>;
}

export default function RoutingPreview() {
  const [catalog,setCatalog] = useState<Catalog>();
  const [row,setRow] = useState(0);
  const [submission,setSubmission] = useState(1);
  const [missed,setMissed] = useState(0);
  const [callback,setCallback] = useState(false);
  const [risk,setRisk] = useState(false);
  const [result,setResult] = useState<Outcome>();
  const [error,setError] = useState('');
  useEffect(()=>{
    let active=true;
    api<Catalog>('/dev/routing').then(data=>{
      if(active){setCatalog(data); setRow(data.scenarios[0]?.row ?? 0);}
    }).catch(e=>{if(active)setError(e.message);});
    return ()=>{active=false;};
  },[]);
  useEffect(()=>{
    if(!row)return;
    let active=true;
    setResult(undefined); setError('');
    api<Outcome>('/dev/routing',{row,submission,missed,callback,risk})
      .then(next=>{if(active)setResult(next);}).catch(e=>{if(active)setError(e.message);});
    return ()=>{active=false;};
  },[row,submission,missed,callback,risk]);
  const scenario=catalog?.scenarios.find(s=>s.row===row);
  return <Card>
    <CardHeader title="关单规则试算" description="选择场景和联系记录，查看建议的下一步。试算不会创建工单。"/>
    <div className="p-5">
      {error && <p role="alert" className="mb-4 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</p>}
      {catalog ? <div className="grid gap-5 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="space-y-4">
          <Field label="审核场景"><Select value={row} onChange={e=>setRow(Number(e.target.value))}>
            {catalog.scenarios.map(s=><option value={s.row} key={s.row}>{s.label}</option>)}
          </Select></Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="同场景错误提交次数"><Select value={submission} onChange={e=>{setSubmission(Number(e.target.value));setMissed(0);}}>
              <option value={1}>第一次</option><option value={2}>第二次</option><option value={3}>第三次及以上</option>
            </Select></Field>
            <Field label="本次提交的联系记录"><Select value={missed} onChange={e=>setMissed(Number(e.target.value))}>
              <option value={0}>尚未拨打</option><option value={1}>首次未接通</option>
              {submission===2 && <option value={2}>二次外呼仍未接通</option>}
            </Select></Field>
          </div>
          <div className="space-y-2">
            <Check checked={callback} onChange={setCallback}>商家要求驳回并回电告知</Check>
            <Check checked={risk} onChange={setRisk}>存在投诉升级或曝光风险</Check>
          </div>
        </div>
        <section className="flex flex-col rounded-xl border border-line bg-subtle/60 p-5" aria-live="polite">
          <h3 className="text-xs font-medium text-ink-muted">建议下一步</h3>
          <strong className="mt-2 flex items-center gap-2 text-lg font-semibold tracking-tight text-ink">
            {!result && <Loader2 aria-hidden className="size-4 animate-spin text-ink-faint"/>}
            {result ? (result.action ? actions[result.action] : '基础渠道预览') : '正在试算…'}
          </strong>
          {result?.reason && <p className="mt-2 text-sm leading-6 text-ink-muted">{result.reason}</p>}
          {scenario?.missing_template && <p className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">该场景缺少书面模板，发送前需人工复核。</p>}
          {scenario && <small className="mt-auto pt-4 text-[11px] text-ink-faint">{catalog.source==='workbook'?'清洗主表':'合成示例'} · 场景汇总第 {row} 行 · 基础渠道为{scenario.channel==='phone'?'电话':'京麦'}</small>}
        </section>
      </div> : !error && <div className="space-y-3" aria-label="正在加载场景…"><Skeleton className="h-9"/><Skeleton className="h-9"/><Skeleton className="h-24"/></div>}
    </div>
  </Card>;
}
