'use client';
import {useEffect, useRef, useState} from 'react';
import type {components} from './api.generated';

type View = components['schemas']['SessionView'];
type Application = components['schemas']['ApplicationView'];
type Accepted = components['schemas']['AcceptedTurn'];
type Event = components['schemas']['Event'];
type SourceRef = components['schemas']['SourceRef'];
type Metric = components['schemas']['MetricRecorded'];
const metricLabels: Record<Metric['name'], string> = {
  model_ttft: '模型首字延迟',
  speech_end_to_text: '语音结束至文本',
  speech_end_to_playback: '语音结束至播放',
};
const metricOutcomes: Record<Metric['outcome'], string> = {
  success: '已采集', timeout: '超时', cancelled: '已取消', missing: '缺测',
};
const eventTypes: Event['payload']['type'][] = [
  'turn.started', 'text.delta', 'state.changed', 'generation.finished',
  'turn.interrupted', 'operation.updated', 'session.ended', 'knowledge.used', 'metric.recorded',
];
const stateLabels: Record<string,string> = {ROUTING:'判断关单渠道',EXPLAINING:'说明审核结果',CALLBACK_PENDING:'等待预约处理',CASE_READY:'待开始',OPENING:'开场',VERIFYING:'核验身份',QA:'整改问答',ACTION_READY:'准备处理',EXECUTING:'处理中',RECONCILING:'结果待查证',WRITEBACK_PENDING:'等待回写',CLOSED:'已完成',CLOSING:'结束中',MANUAL_REVIEW:'需人工处理'};
const contactLabels: Record<components['schemas']['CallRecord']['contact_status'], string> = {
  verified: '已核实接听方', wrong_person: '接听方不符', refused: '拒绝沟通',
  no_answer: '未接通', disconnected: '通话断开', not_required: '无需电话核验',
};
const routeLabels = {phone: '电话说明', jingmai: '京麦书面告知', manual: '人工处理'};
const operationLabels: Record<string,string> = {pending:'处理中',unknown:'结果待确认',not_found_yet:'结果待确认',succeeded:'模拟处理成功',failed:'处理失败'};

async function api<T>(path: string, body?: unknown, key?: string): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {method: body === undefined ? 'GET' : 'POST',
    headers: {'Content-Type':'application/json','X-Requested-With':'zhiyu',...(key ? {'Idempotency-Key':key} : {})},
    body: body === undefined ? undefined : JSON.stringify(body), cache:'no-store'});
  const data = await response.json();
  if (!response.ok) throw new Error(data.message ?? `请求失败 ${response.status}`);
  return data as T;
}

export default function Home() {
  const [logged,setLogged] = useState(false);
  const [password,setPassword] = useState('');
  const [applications,setApplications] = useState<Application[]>([]);
  const [selected,setSelected] = useState('');
  const [view,setView] = useState<View|null>(null);
  const [sid,setSid] = useState('');
  const [input,setInput] = useState('');
  const [error,setError] = useState('');
  const [connecting,setConnecting] = useState(false);
  const [submitting,setSubmitting] = useState(false);
  const [live,setLive] = useState('');
  const [observations, setObservations] = useState<{
    sid: string;
    sources: SourceRef[];
    metrics: Partial<Record<Metric['name'], Metric>>;
  }>({sid: '', sources: [], metrics: {}});
  const currentObservations = observations.sid === sid ? observations : undefined;
  const sources = view?.record?.source_refs.length ? view.record.source_refs : currentObservations?.sources ?? [];
  const metrics = Object.values(currentObservations?.metrics ?? {});
  const generation = useRef<string|null>(null);
  const cursor = useRef(0);
  const activeSid = useRef('');

  async function initialize() {
    const items = await api<Application[]>('/applications');
    setApplications(items); setSelected(items[0]?.application_id ?? ''); setLogged(true);
    const saved = localStorage.getItem('zhiyu.session');
    if(saved) setSid(saved);
  }
  useEffect(()=>{initialize().catch(()=>{});},[]);
  useEffect(()=>{
    if(!sid || !logged) return;
    let closed = false;
    let source: EventSource|undefined;
    let retry: ReturnType<typeof setTimeout>|undefined;
    activeSid.current = sid;
    setLive(''); setView(null); setConnecting(true);
    setObservations({sid, sources: [], metrics: {}});
    const refresh = async () => {
      const next = await api<View>(`/sessions/${sid}`);
      if(closed || activeSid.current!==sid) return;
      if(next.last_event_seq<cursor.current) return;
      setView(next); generation.current=next.generation_id ?? null;
      setLive('');
    };
    const connect = async () => {
      try {
        const next = await api<View>(`/sessions/${sid}`);
        if(closed) return;
        setView(next); generation.current=next.generation_id ?? null; cursor.current=next.last_event_seq;
        source=new EventSource(`/api/v1/sessions/${sid}/events?after_seq=${cursor.current}`);
        source.onopen=()=>setConnecting(false);
        const receive = (raw: MessageEvent<string>) => {
          const event=JSON.parse(raw.data) as Event;
          if(closed || event.seq<=cursor.current) return;
          cursor.current=event.seq;
          if(event.payload.type==='turn.started') {
            generation.current=event.generation_id ?? null; setLive('');
            setView(previous=>previous ? {...previous,busy:true,generation_id:event.generation_id} : previous);
          } else if(event.payload.type==='text.delta') {
            const delta=event.payload.text;
            if(event.generation_id===generation.current) setLive(x=>x+delta);
          } else if(event.payload.type==='turn.interrupted') {
            if(event.payload.invalidated_generation_id===generation.current) {
              generation.current = null;
              setLive('');
            }
          }
          const payload = event.payload;
          if (payload.type === 'knowledge.used') {
            setObservations(previous => previous.sid === sid ? {...previous, sources: payload.source_refs} : previous);
          } else if (payload.type === 'metric.recorded') {
            setObservations(previous => previous.sid === sid
              ? {...previous, metrics: {...previous.metrics, [payload.name]: payload}}
              : previous);
          }
          if (payload.type === 'state.changed') {
            setView(previous => previous && event.state_revision >= previous.state_revision
              ? {...previous, state: payload.state, state_revision: event.state_revision}
              : previous);
          }
          if(['generation.finished','turn.interrupted','operation.updated','session.ended'].includes(payload.type)) {
            refresh().catch(e=>setError(String(e)));
          }
        };
        eventTypes.forEach(type => source!.addEventListener(type, receive as EventListener));
        source.onerror=()=>{
          source?.close(); setConnecting(true);
          if(!closed) retry=setTimeout(connect,1500);
        };
      } catch(e) { if(!closed) {setError(String(e)); setConnecting(false);} }
    };
    connect();
    return ()=>{closed=true; source?.close(); if(retry)clearTimeout(retry);};
  },[sid,logged]);

  async function run(fn: ()=>Promise<void>) {
    setError(''); setSubmitting(true);
    try {await fn();} catch(e) {setError(e instanceof Error?e.message:String(e));} finally {setSubmitting(false);}
  }
  async function create() {
    const next=await api<View>('/sessions',{application_id:selected,mode:'text'},crypto.randomUUID());
    localStorage.setItem('zhiyu.session',next.session_id); setSid(next.session_id);
  }
  async function start() {
    if(!view) return;
    const turn=await api<Accepted>(`/sessions/${sid}/start`,{expected_revision:view.state_revision},crypto.randomUUID());
    generation.current=turn.generation_id;
    const next=await api<View>(`/sessions/${sid}`);
    setView(previous => previous && previous.state_revision > next.state_revision ? previous : next);
  }
  async function send() {
    if(!input.trim())return;
    const turn=await api<Accepted>(`/sessions/${sid}/turns`,{text:input,client_event_id:crypto.randomUUID()});
    generation.current=turn.generation_id; setInput(''); setLive('');
    const next=await api<View>(`/sessions/${sid}`);
    if(activeSid.current===sid && next.last_event_seq>=cursor.current) setView(next);
  }
  return <main>
    <header><div><p className="eyebrow">智语外呼 / 开发联调</p><h1>商户资质告知助手</h1><p>合成数据 · 文本模式 · 不执行真实外呼</p></div><span className="badge">语音未配置</span></header>
    {error && <div role="alert" className="error">{error}</div>}
    {!logged ? <section className="login"><h2>进入演示环境</h2><p>使用本地配置的演示密码。默认值见启动说明。</p><form onSubmit={e=>{e.preventDefault();run(async()=>{await api('/dev/login',{username:'demo',password});await initialize();});}}><label>演示密码<input aria-label="演示密码" type="password" value={password} onChange={e=>setPassword(e.target.value)}/></label><button disabled={submitting}>登录</button></form></section> : <div className="grid">
      <aside><h2>合成任务</h2><label>选择申请单<select value={selected} onChange={e=>setSelected(e.target.value)}>{applications.map(a=><option key={a.application_id} value={a.application_id}>{a.label} · {a.company_hint}</option>)}</select></label><button disabled={submitting||!selected||!!view?.busy} onClick={()=>run(create)}>创建会话</button><p className="hint">身份输入：我是演示商家1（按所选商家编号调整）</p><p className="hint">之后可问“补齐材料一定必过吗”，再说“明白了”。</p><hr/><h2>业务事实</h2><p>状态：{view ? stateLabels[view.state] ?? view.state : '尚未创建'}</p><p>身份：{view?.verified?'已核对':'尚未核对'}</p>{view && <p>关单渠道：{routeLabels[view.route]}</p>}
      {view?.verified && view.reason && <p>原因：{view.reason}</p>}
      {view?.verified && view.remedy && <p>整改：{view.remedy}</p>}
      {view?.verified && sources.length > 0 && <section aria-label="审核依据">
        <h3>审核依据</h3>
        <ul>{sources.map((ref, index) => <li key={index}>
          {ref.sheet} · 第 {ref.row} 行 <small title={ref.file_sha256}>{ref.file_sha256.slice(0, 12)}</small>
        </li>)}</ul>
      </section>}{view?.operations.map(o=><p key={o.operation_id} data-testid="operation-status">{operationLabels[o.status]}{o.resource_id && <small>{o.resource_id}</small>}</p>)}{view?.record && <>
        <p>联系结果：{contactLabels[view.record.contact_status]}</p>
        <p data-testid="writeback">通话记录已回写</p>
      </>}
      {view && <section aria-label="本次会话指标">
        <hr/><h3>本次会话指标</h3>
        {metrics.length === 0 ? <p className="hint">等待指标事件</p> : <dl>
          {metrics.map(metric => <div key={metric.name}>
            <dt>{metricLabels[metric.name]}</dt>
            <dd>{metric.outcome === 'success' && metric.duration_ms != null
              ? `${metric.duration_ms} ms`
              : metric.outcome === 'success' ? '缺测' : metricOutcomes[metric.outcome]}</dd>
            <small>{metric.measurement_method} · {metric.clock_domain}</small>
          </div>)}
        </dl>}
      </section>}</aside>
      <section className="conversation"><div className="toolbar"><h2>文本会话</h2><span>{sid?(connecting?'事件连接中':'已连接'):'待连接'}</span></div><div className="actions"><button disabled={!view||view.state!=='CASE_READY'||submitting} onClick={()=>run(start)}>开始</button><button disabled={!view?.busy||!view.generation_id||submitting} onClick={()=>run(async()=>{await api(`/sessions/${sid}/interruptions`,{generation_id:view!.generation_id,client_event_id:crypto.randomUUID()});setView(await api<View>(`/sessions/${sid}`));setLive('');})}>打断</button><button disabled={!view||view.ended||submitting} onClick={()=>run(async()=>{setView(await api<View>(`/sessions/${sid}/end`,{reason:'user_ended'}));setLive('');})}>结束</button></div>
      <div className="messages" aria-live="polite">{!view?.messages?.length && <p className="empty">创建会话后点击开始，AI 会主动开场。</p>}{view?.messages?.filter(m => !(live && m.status === 'generating' && m.generation_id === generation.current)).map(m=><article key={m.message_id} className={m.role}><strong>{m.role==='user'?'商户':'AI'}</strong><p>{m.text || '…'}</p>{m.status==='interrupted'&&<small>已打断</small>}{m.status==='failed'&&<small>生成失败，可重试</small>}</article>)}{live && <article className="assistant"><strong>AI · 正在回复</strong><p>{live}</p></article>}</div>
      <form className="composer" onSubmit={e=>{e.preventDefault();run(send);}}><input aria-label="商户回复" placeholder="输入商户回复；新输入会打断旧生成" value={input} onChange={e=>setInput(e.target.value)} disabled={!view||view.ended||view.state==='CASE_READY'}/><button disabled={!view||view.ended||view.state==='CASE_READY'||submitting||!input.trim()}>发送</button></form></section>
    </div>}
    <footer>当前为确定性 DemoAgent 和模拟业务。文字响应不代表语音性能。</footer>
  </main>;
}
