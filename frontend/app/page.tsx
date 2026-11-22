'use client';
import {useEffect, useRef, useState} from 'react';
import type {components} from './api.generated';
import BusinessFacts from './BusinessFacts';
import RoutingPreview from './routing-preview';
import RsiPanel from './rsi-panel';
import {api} from '../lib/api';
import VoicePanel from './VoicePanel';

type View = components['schemas']['SessionView'];
type Application = components['schemas']['ApplicationView'];
type Accepted = components['schemas']['AcceptedTurn'];
type Event = components['schemas']['Event'];
const stateLabels: Record<string,string> = {CASE_READY:'待开始',OPENING:'开场',VERIFYING:'核验身份',QA:'整改问答',ACTION_READY:'准备处理',EXECUTING:'处理中',RECONCILING:'结果待查证',WRITEBACK_PENDING:'等待回写',CLOSED:'已完成',CLOSING:'结束中',MANUAL_REVIEW:'需人工处理',ROUTING:'判断渠道',EXPLAINING:'说明整改',CALLBACK_PENDING:'等待预约'};


export default function Home() {
  const [logged,setLogged] = useState(false);
  const [capabilities,setCapabilities]=useState<components['schemas']['Capabilities']|null>(null);
  const [mode,setMode]=useState<'text'|'voice'>('text');
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
  const generation = useRef<string|null>(null);
  const cursor = useRef(0);
  const activeSid = useRef('');

  async function initialize() {
    const items = await api<Application[]>('/applications');
    setCapabilities(await api<components['schemas']['Capabilities']>('/capabilities'));
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
    const refresh = async () => {
      const next = await api<View>(`/sessions/${sid}`);
      if(closed || activeSid.current!==sid) return;
      if(next.last_event_seq<cursor.current) return;
      setView(next); generation.current=next.generation_id ?? null;
      cursor.current=Math.max(cursor.current,next.last_event_seq);
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
            if(event.payload.invalidated_generation_id===generation.current) setLive('');
          }
          if(['generation.finished','operation.updated','session.ended'].includes(event.payload.type)) {
            refresh().catch(e=>setError(String(e)));
          }
        };
        ['turn.started','text.delta','state.changed','generation.finished','turn.interrupted','operation.updated','session.ended','knowledge.used'].forEach(t=>source!.addEventListener(t,receive as EventListener));
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
    const next=await api<View>('/sessions',{application_id:selected,mode},crypto.randomUUID());
    localStorage.setItem('zhiyu.session',next.session_id); setSid(next.session_id);
  }
  async function start() {
    if(!view) return;
    const turn=await api<Accepted>(`/sessions/${sid}/start`,{expected_revision:view.state_revision},crypto.randomUUID());
    generation.current=turn.generation_id;
    setView({...view,busy:true,generation_id:turn.generation_id});
  }
  async function send() {
    if(!input.trim())return;
    const turn=await api<Accepted>(`/sessions/${sid}/turns`,{text:input,client_event_id:crypto.randomUUID()});
    generation.current=turn.generation_id; setInput(''); setLive('');
    const next=await api<View>(`/sessions/${sid}`);
    if(activeSid.current===sid && next.last_event_seq>=cursor.current) {
      cursor.current=next.last_event_seq;
      setView(next);
      setLive('');
    }
  }
  return <main>
    <header><div><p className="eyebrow">智语外呼 / 开发联调</p><h1>商户资质告知助手</h1><p>合成申请单 · 业务联调环境</p></div><span className="badge">{capabilities?.voice?'语音可联调':'语音未配置'}</span></header>
    {error && <div role="alert" className="error">{error}</div>}
    {!logged ? <section className="login"><h2>进入演示环境</h2><p>使用本地配置的演示密码。默认值见启动说明。</p><form onSubmit={e=>{e.preventDefault();run(async()=>{await api('/auth/login',{username:'demo',password});await initialize();});}}><label>演示密码<input aria-label="演示密码" type="password" value={password} onChange={e=>setPassword(e.target.value)}/></label><button disabled={submitting}>登录</button></form></section> : <div className="grid">
      <aside><h2>合成任务</h2><label>交互模式<select value={mode} onChange={e=>setMode(e.target.value as 'text'|'voice')}><option value="text">文本联调</option><option value="voice" disabled={!capabilities?.voice}>实时语音</option></select></label>{capabilities?.voice_unavailable_reason&&<p className="hint">{capabilities.voice_unavailable_reason}</p>}<label>选择申请单<select value={selected} onChange={e=>setSelected(e.target.value)}>{applications.map(a=><option key={a.application_id} value={a.application_id}>{a.label} · {a.company_hint}</option>)}</select></label><button disabled={submitting||!selected||!!view?.busy} onClick={()=>run(create)}>创建会话</button><p className="hint">身份输入：我是演示商家1（按所选商家编号调整）</p><p className="hint">之后可问“补齐材料一定必过吗”，再说“明白了”。</p><hr/><h2>业务事实</h2><p>状态：{view ? stateLabels[view.state] ?? view.state : '尚未创建'}</p><p>身份：{view?.verified?'已核对':'尚未核对'}</p>{view && <BusinessFacts view={view}/>}</aside>
      <section className="conversation">{view?.mode==='voice'?<VoicePanel key={view.session_id} view={view} onError={setError}/>:<><div className="toolbar"><h2>文本会话</h2><span>{sid?(connecting?'事件连接中':'已连接'):'待连接'}</span></div><div className="actions"><button disabled={!view||view.state!=='CASE_READY'||submitting} onClick={()=>run(start)}>开始</button><button disabled={!view?.busy||!view.generation_id||submitting} onClick={()=>run(async()=>{await api(`/sessions/${sid}/interruptions`,{generation_id:view!.generation_id,client_event_id:crypto.randomUUID()});setView(await api<View>(`/sessions/${sid}`));setLive('');})}>打断</button><button disabled={!view||view.ended||submitting} onClick={()=>run(async()=>{setView(await api<View>(`/sessions/${sid}/end`,{reason:'user_ended'}));setLive('');})}>结束</button></div>
      <div className="messages" aria-live="polite">{!view?.messages?.length && <p className="empty">创建会话后点击开始，AI 会主动开场。</p>}{view?.messages?.filter(m=>!(live && m.status==='generating' && m.generation_id===generation.current)).map(m=><article key={m.message_id} className={m.role}><strong>{m.role==='user'?'商户':'AI'}</strong><p>{m.text || '…'}</p>{m.status==='interrupted'&&<small>已打断</small>}{m.status==='failed'&&<small>生成失败，可重试</small>}</article>)}{live && <article className="assistant"><strong>AI · 正在回复</strong><p>{live}</p></article>}</div>
      <form className="composer" onSubmit={e=>{e.preventDefault();run(send);}}><input aria-label="商户回复" placeholder="输入商户回复；新输入会打断旧生成" value={input} onChange={e=>setInput(e.target.value)} disabled={!view||view.ended||view.state==='CASE_READY'}/><button disabled={!view||view.ended||view.state==='CASE_READY'||submitting||!input.trim()}>发送</button></form></>}</section>
    </div>}
    {logged && capabilities?.routing_preview && <RoutingPreview/>}
    {logged && <RsiPanel/>}
    <footer>当前为确定性 DemoAgent 和模拟业务。文字响应不代表语音性能。</footer>
  </main>;
}
