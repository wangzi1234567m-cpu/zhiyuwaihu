'use client';
import {useEffect, useState} from 'react';
import {request} from '../lib/api';
import type {components} from './api.generated';

type Catalog = components['schemas']['Catalog'];
type Outcome = components['schemas']['Outcome'];
const actions = {phone:'电话说明', jingmai:'京麦书面告知', followup:'创建二次外呼跟进事件', manual:'转人工跟进'};

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
    request<Catalog>('/dev/routing').then(data=>{
      if(active){setCatalog(data); setRow(data.scenarios[0]?.row ?? 0);}
    }).catch(e=>{if(active)setError(e.message);});
    return ()=>{active=false;};
  },[]);
  useEffect(()=>{
    if(!row)return;
    let active=true;
    setResult(undefined); setError('');
    request<Outcome>('/dev/routing',{body:{row,submission,missed,callback,risk}})
      .then(next=>{if(active)setResult(next);}).catch(e=>{if(active)setError(e.message);});
    return ()=>{active=false;};
  },[row,submission,missed,callback,risk]);
  const scenario=catalog?.scenarios.find(s=>s.row===row);
  return <section className="routing">
    <header><div><h2>关单规则试算</h2><p>选择场景和联系记录，查看建议的下一步。试算不会创建工单。</p></div></header>
    {error && <p role="alert">{error}</p>}
    {catalog ? <div className="route-grid"><div>
      <label>审核场景<select value={row} onChange={e=>setRow(Number(e.target.value))}>
        {catalog.scenarios.map(s=><option value={s.row} key={s.row}>{s.label}</option>)}
      </select></label>
      <label>同场景错误提交次数<select value={submission} onChange={e=>{setSubmission(Number(e.target.value));setMissed(0);}}>
        <option value={1}>第一次</option><option value={2}>第二次</option><option value={3}>第三次及以上</option>
      </select></label>
      <label>本次提交的联系记录<select value={missed} onChange={e=>setMissed(Number(e.target.value))}>
        <option value={0}>尚未拨打</option><option value={1}>首次未接通</option>
        {submission===2 && <option value={2}>二次外呼仍未接通</option>}
      </select></label>
      <label><input type="checkbox" checked={callback} onChange={e=>setCallback(e.target.checked)}/>商家要求驳回并回电告知</label>
      <label><input type="checkbox" checked={risk} onChange={e=>setRisk(e.target.checked)}/>存在投诉升级或曝光风险</label>
    </div><section className="decision" aria-live="polite"><h3>建议下一步</h3>
      <strong>{result ? actions[result.action] : '正在试算…'}</strong><p>{result?.reason}</p>
      {scenario && <small>{catalog.source==='workbook'?'清洗主表':'合成示例'} · 场景汇总第 {row} 行 · 基础渠道为{scenario.channel==='phone'?'电话':'京麦'}</small>}
      {scenario?.missing_template && <p>该场景缺少书面模板，发送前需人工复核。</p>}
    </section></div> : !error && <p>正在加载场景…</p>}
  </section>;
}
