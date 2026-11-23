'use client';
import {useEffect, useRef, useState} from 'react';
import type {components} from './api.generated';
import BusinessFacts from './BusinessFacts';
import RoutingPreview from './routing-preview';
import RsiPanel from './rsi-panel';
import {api} from '../lib/api';
import VoicePanel from './VoicePanel';
import {AlertCircle, X} from 'lucide-react';
import TopBar from './components/top-bar';
import LoginCard from './components/login-card';
import TaskPanel from './components/task-panel';
import SessionProgress from './components/session-progress';
import TextConversation from './components/text-conversation';

type View = components['schemas']['SessionView'];
type Application = components['schemas']['ApplicationView'];
type Accepted = components['schemas']['AcceptedTurn'];
type Event = components['schemas']['Event'];


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
  const [pending,setPending] = useState('');
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

  async function run(fn: ()=>Promise<void>, name='') {
    setError(''); setSubmitting(true); setPending(name);
    try {await fn();} catch(e) {setError(e instanceof Error?e.message:String(e));} finally {setSubmitting(false); setPending('');}
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
  async function interrupt() {
    await api(`/sessions/${sid}/interruptions`,{generation_id:view!.generation_id,client_event_id:crypto.randomUUID()});
    setView(await api<View>(`/sessions/${sid}`)); setLive('');
  }
  async function end() {
    setView(await api<View>(`/sessions/${sid}/end`,{reason:'user_ended'})); setLive('');
  }
  return <div className="min-h-dvh bg-canvas">
    <TopBar capabilities={capabilities}/>
    <main className="mx-auto max-w-7xl space-y-6 px-4 py-8 sm:px-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-ink text-balance">商户资质告知助手</h1>
          <p className="mt-1 text-sm text-ink-muted">合成申请单 · 业务联调环境</p>
        </div>
      </div>

      {error && <div role="alert" className="flex animate-fade-in items-start gap-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
        <AlertCircle aria-hidden className="mt-0.5 size-4 shrink-0"/>
        <p className="min-w-0 flex-1 break-words">{error}</p>
        <button type="button" aria-label="关闭提示" onClick={()=>setError('')} className="rounded p-0.5 text-rose-600 hover:bg-rose-100"><X aria-hidden className="size-4"/></button>
      </div>}

      {!logged
        ? <LoginCard password={password} onPasswordChange={setPassword} submitting={submitting}
            onSubmit={()=>run(async()=>{await api('/auth/login',{username:'demo',password});await initialize();})}/>
        : <div className="grid items-start gap-6 lg:grid-cols-[320px_minmax(0,1fr)]">
          <aside className="space-y-6 lg:sticky lg:top-20">
            <TaskPanel mode={mode} onModeChange={setMode} capabilities={capabilities} applications={applications}
              selected={selected} onSelectedChange={setSelected}
              createDisabled={submitting||!selected||!!view?.busy} creating={pending==='create'} onCreate={()=>run(create,'create')}/>
            <BusinessFacts view={view}/>
          </aside>
          <section className="min-w-0 space-y-6" aria-label="会话">
            {view && <SessionProgress view={view}/>}
            {view?.mode==='voice'
              ? <VoicePanel key={view.session_id} view={view} onError={setError}/>
              : <TextConversation view={view} sid={sid} connecting={connecting} submitting={submitting} pending={pending}
                  live={live} generationId={generation.current} input={input} onInputChange={setInput}
                  onStart={()=>run(start,'start')} onInterrupt={()=>run(interrupt,'interrupt')} onEnd={()=>run(end,'end')} onSend={()=>run(send,'send')}/>}
          </section>
        </div>}

      {logged && capabilities?.routing_preview && <RoutingPreview/>}
      {logged && <RsiPanel/>}
      <footer className="pt-2 pb-6 text-center text-xs text-ink-faint">当前使用语义 Agent、规则理解和模拟业务。文字响应不代表语音性能。</footer>
    </main>
  </div>;
}
