'use client';
import {useEffect, useRef, useState} from 'react';
import type {components} from './api.generated';
import SubtitlePanel from './SubtitlePanel';
import {api} from '../lib/api';
import VoicePanel from './VoicePanel';
import RealtimeVoicePanel from './RealtimeVoicePanel';

type View = components['schemas']['SessionView'];
type Application = components['schemas']['ApplicationView'];
type Accepted = components['schemas']['AcceptedTurn'];
type Event = components['schemas']['Event'];



export default function Home() {
  const [callStatus,setCallStatus]=useState('等待开始');
  const [logged,setLogged] = useState(false);
  const [capabilities,setCapabilities]=useState<components['schemas']['Capabilities']|null>(null);
  const [mode,setMode]=useState<'text'|'voice'|'native_webrtc'>('text');
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
    const caps=await api<components['schemas']['Capabilities']>('/capabilities'); setCapabilities(caps); setMode(caps.voice?'voice':caps.realtime_webrtc?'native_webrtc':'text');
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
        setView(next); setSelected(next.application_id); generation.current=next.generation_id ?? null; cursor.current=next.last_event_seq;
        if(next.ended){setConnecting(false);return;}
        source=new EventSource(`/api/v1/sessions/${sid}/events?after_seq=${cursor.current}`);
        source.onopen=()=>setConnecting(false);
        const receive = (raw: MessageEvent<string>) => {
          const event=JSON.parse(raw.data) as Event;
          if(closed || event.seq<=cursor.current) return;
          cursor.current=event.seq;
          if(event.payload.type==='session.ended'){source?.close();setLive('');generation.current=null;}
          if(event.payload.type==='turn.started') {
            generation.current=event.generation_id ?? null; setLive('');
            setView(previous=>previous ? {...previous,busy:true,generation_id:event.generation_id} : previous);
          } else if(event.payload.type==='text.delta') {
            const delta=event.payload.text;
            if(event.generation_id===generation.current) setLive(x=>x+delta);
          } else if(event.payload.type==='turn.interrupted') {
            if(event.payload.invalidated_generation_id===generation.current) {generation.current=null; setLive('');}
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
    const next=await api<View>('/sessions',{application_id:selected,mode:mode==='native_webrtc'?'voice':mode,
      voice_transport:mode==='native_webrtc'?'native_webrtc':'agora'},crypto.randomUUID());
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
    <header><div className="brand"><span className="logo"><img src="/zhiyu-logo.jpg" alt="智语外呼" width="64" height="64"/></span><div><p className="eyebrow">智语外呼</p><h1>商户资质告知助手</h1></div></div><span className="badge">{view?.ended?'通话已结束':view?.mode==='voice'?callStatus:connecting?'正在连接':(capabilities?.voice||capabilities?.realtime_webrtc)?'等待开始':'语音未配置'}</span></header>
    {error && <div role="alert" className="error">{error}</div>}
    {!logged ? <section className="login"><h2>欢迎使用</h2><form onSubmit={e=>{e.preventDefault();run(async()=>{await api('/auth/login',{username:'demo',password});await initialize();});}}><label>演示密码<input autoComplete="current-password" aria-label="演示密码" type="password" value={password} onChange={e=>setPassword(e.target.value)}/></label><button disabled={submitting}>登录</button></form></section> : <>
      <div className="selection"><label>申请单<select aria-label="选择申请单" value={selected} disabled={!!view&&!view.ended&&(view.busy||view.state!=='CASE_READY')} onChange={e=>setSelected(e.target.value)}>{applications.map(a=><option key={a.application_id} value={a.application_id}>{a.label} · {a.company_hint}</option>)}</select></label><label>交互方式<select value={mode} disabled={!!view&&!view.ended&&view.state!=='CASE_READY'} onChange={e=>setMode(e.target.value as typeof mode)}><option value="voice" disabled={!capabilities?.voice}>实时语音</option><option value="native_webrtc" disabled={!capabilities?.realtime_webrtc}>实时语音升级</option><option value="text">文字对话</option></select></label><button className="secondary" disabled={submitting||!selected||!!view?.busy||!!view&&!view.ended&&view.state!=='CASE_READY'} onClick={()=>run(create)}>创建会话</button></div>
      <div className="service-note"><p className="service-motto">匠心兼程，服务立行</p></div>
      <section className="conversation">
        {view?.mode==='voice' ? (view.voice_transport==='native_webrtc'?<><SubtitlePanel view={view} live={live} generation={generation.current}/><RealtimeVoicePanel key={view.session_id} view={view} onError={setError} onStatus={setCallStatus}/></>:<VoicePanel key={view.session_id} view={view} onError={setError} onStatus={setCallStatus}/>) : <>
          <SubtitlePanel view={view} live={live} generation={generation.current}/>
          <div className="call-status" role="status">{view?.ended?'通话已结束':view?.busy?'AI 正在思考':view&&view.state!=='CASE_READY'?'等待回复':'等待开始'}</div>
          <div className="actions"><button disabled={!view||view.state!=='CASE_READY'||submitting} onClick={()=>run(start)}>开始通话</button><button className="secondary" disabled={!view?.busy||!view.generation_id||submitting} onClick={()=>run(async()=>{generation.current=null;setLive('');await api(`/sessions/${sid}/interruptions`,{generation_id:view!.generation_id,client_event_id:crypto.randomUUID()});setView(await api<View>(`/sessions/${sid}`));})}>打断播报</button><button className="secondary" disabled={!view||view.ended||submitting} onClick={()=>run(async()=>{generation.current=null;setLive('');setView(await api<View>(`/sessions/${sid}/end`,{reason:'user_ended'}));})}>结束通话</button></div>
          <form className="composer" onSubmit={e=>{e.preventDefault();run(send);}}><input aria-label="商户回复" placeholder="输入商户回复…" value={input} onChange={e=>setInput(e.target.value)} disabled={!view||view.ended||view.state==='CASE_READY'}/><button disabled={!view||view.ended||view.state==='CASE_READY'||submitting||!input.trim()}>发送</button></form>
        </>}
      </section>
    </>}
  </main>;
}
