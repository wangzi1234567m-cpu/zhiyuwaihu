'use client';
import {useCallback,useEffect,useRef,useState} from 'react';
import {api,ApiRequestError} from './api';
import type {Application,BusinessEvent,Capabilities,TaskViews,Transport,View,SupportCatalog,WrittenPreview} from './workbench';
import type {components} from '../app/api.generated';
type Accepted=components['schemas']['AcceptedTurn'];
const STORE='zhiyu.workbench.sessions.v1';
function savedSessions():Record<string,string> {
  try { const parsed=JSON.parse(localStorage.getItem(STORE)||'{}');
    return Object.fromEntries(Object.entries(parsed).filter((entry):entry is [string,string]=>/^[a-f0-9-]{36}$/i.test(entry[0])&&typeof entry[1]==='string'&&/^[a-f0-9-]{36}$/i.test(entry[1])));
  } catch{return {};}
}

export function useWorkbench() {
  const [logged,setLogged]=useState(false),[loading,setLoading]=useState(false),[pending,setPending]=useState(false);
  const [error,setError]=useState(''),[applications,setApplications]=useState<Application[]>([]);
  const [catalog,setCatalog]=useState<SupportCatalog|null>(null);
  const [caps,setCaps]=useState<Capabilities|null>(null),[views,setViews]=useState<TaskViews>({});
  const [selected,setSelected]=useState(''),[sid,setSid]=useState(''),[live,setLive]=useState('');
  const [transport,setTransportState]=useState<Transport>('text'),[connecting,setConnecting]=useState(false);
  const [events,setEvents]=useState<Record<string,BusinessEvent[]>>({}),[taskErrors,setTaskErrors]=useState<Record<string,string>>({});
  const generation=useRef<string|null>(null),activeSid=useRef(''),cursor=useRef(0),actionLock=useRef(false);
  const sessionIds=useRef<Record<string,string>>({}),initTask=useRef<Promise<void>|null>(null);
  const startKeys=useRef<Record<string,string>>({}),turnReceipt=useRef<{sid:string;text:string;key:string}|null>(null);
  const view=views[selected]??null;

  const remember=useCallback((next:View)=>{
    sessionIds.current[next.application_id]=next.session_id;
    localStorage.setItem(STORE,JSON.stringify(sessionIds.current));
    setViews(previous=>{
      const old=previous[next.application_id];
      if(old?.session_id===next.session_id&&old.last_event_seq>next.last_event_seq)return previous;
      return {...previous,[next.application_id]:next};
    });
  },[]);
  const activate=useCallback((next:View)=>{
    remember(next);activeSid.current=next.session_id;cursor.current=next.last_event_seq;
    setSelected(next.application_id);setSid(next.session_id);setLive('');
    localStorage.setItem('zhiyu.session',next.session_id);
  },[remember]);
  const create=useCallback(async(applicationId:string,mode:Transport,key?:string)=>{
    return api<View>('/sessions',{application_id:applicationId,mode:mode==='native_webrtc'?'voice':mode,
      voice_transport:mode==='native_webrtc'?'native_webrtc':'agora'},key??crypto.randomUUID());
  },[]);
  const initialize=useCallback(async()=>{
    if(initTask.current)return initTask.current;
    const work=async()=>{
      setLoading(true);setError('');
      try {
        const [items,capabilities,support]=await Promise.all([api<Application[]>('/staff/applications'),api<Capabilities>('/capabilities'),api<SupportCatalog>('/staff/support-catalog')]);
        setApplications(items);setCaps(capabilities);setCatalog(support);setLogged(true);setTaskErrors({});
        const storedMode=localStorage.getItem('zhiyu.workbench.transport');
        const mode:Transport=storedMode==='text'?'text':storedMode==='native_webrtc'&&capabilities.realtime_webrtc?'native_webrtc':
          storedMode==='voice'&&capabilities.voice?'voice':capabilities.realtime_webrtc?'native_webrtc':capabilities.voice?'voice':'text';
        setTransportState(mode);sessionIds.current=savedSessions();
        const restored:TaskViews={};
        for(const task of items)if(task.snapshot){restored[task.application_id]=task.snapshot;remember(task.snapshot);}
        setViews(restored);
        const legacy=localStorage.getItem('zhiyu.session');
        let chosen=items.find(task=>task.snapshot?.session_id===legacy)??items[0];
        if(legacy&&!items.some(task=>task.snapshot?.session_id===legacy))try{
          const previous=await api<View>(`/sessions/${legacy}`);
          chosen=items.find(task=>task.application_id===previous.application_id)??chosen;
        }catch{/* Staff projection remains authoritative if a cached session is stale. */}
        if(chosen){setSelected(chosen.application_id);if(chosen.snapshot)activate(chosen.snapshot);else{setSid('');activeSid.current='';setLive('');}}
      }catch(e){if(e instanceof ApiRequestError&&(e.status===401||e.status===403))setLogged(false);else setError(e instanceof Error?e.message:'加载失败，请重试');}
      finally{setLoading(false);}
    };
    initTask.current=work();try{await initTask.current;}finally{initTask.current=null;}
  },[activate,remember]);
  useEffect(()=>{void initialize();},[initialize]);

  useEffect(()=>{
    if(!sid||!logged)return;
    let closed=false;let source:EventSource|undefined;let timer:ReturnType<typeof setTimeout>|undefined;
    activeSid.current=sid;setLive('');setConnecting(true);
    const refresh=async()=>{
      const next=await api<View>(`/staff/sessions/${sid}`);
      if(closed||activeSid.current!==sid||next.last_event_seq<cursor.current)return;
      remember(next);cursor.current=next.last_event_seq;generation.current=next.generation_id??null;setLive('');
    };
    const connect=async()=>{
      try{
        await refresh();if(closed)return;
        source=new EventSource(`/api/v1/sessions/${sid}/events?after_seq=0`);
        source.onopen=()=>{if(!closed)setConnecting(false);};
        const receive=(raw:MessageEvent<string>)=>{
          if(closed||activeSid.current!==sid)return;
          let event:BusinessEvent;try{event=JSON.parse(raw.data);}catch{setError('业务事件格式异常，请重新读取状态');return;}
          if(event.session_id!==sid)return;
          if(event.payload.type!=='text.delta')setEvents(all=>{
            const old=all[sid]??[];if(old.some(e=>e.seq===event.seq))return all;
            return {...all,[sid]:[...old,event].sort((a,b)=>a.seq-b.seq).slice(-80)};
          });
          if(event.seq<=cursor.current)return;
          cursor.current=event.seq;
          if(event.payload.type==='turn.started'){
            generation.current=event.generation_id??null;setLive('');
            setViews(old=>{const entry=Object.entries(old).find(([,v])=>v.session_id===sid);return entry?{...old,[entry[0]]:{...entry[1],busy:true,generation_id:event.generation_id}}:old;});
          }else if(event.payload.type==='text.delta'&&event.generation_id===generation.current){const text=event.payload.text;setLive(old=>old+text);}
          else if(event.payload.type==='turn.interrupted'&&event.payload.invalidated_generation_id===generation.current){generation.current=null;setLive('');}
          else if(event.payload.type==='state.changed'){
            const state=event.payload.state;
            setViews(old=>{const entry=Object.entries(old).find(([,v])=>v.session_id===sid);return entry?{...old,[entry[0]]:{...entry[1],state,state_revision:event.state_revision}}:old;});
          }
          if(['generation.finished','operation.updated','session.ended','knowledge.used','contact.recorded'].includes(event.payload.type))void refresh().catch(e=>{if(!closed)setError(String(e));});
        };
        for(const name of ['turn.started','text.delta','state.changed','generation.finished','turn.interrupted','operation.updated','session.ended','knowledge.used','metric.recorded','contact.recorded'])source.addEventListener(name,receive as EventListener);
        source.onerror=()=>{source?.close();if(!closed){setConnecting(true);timer=setTimeout(connect,1500);}};
      }catch(e){if(!closed){setError(e instanceof Error?e.message:'状态恢复失败');setConnecting(true);timer=setTimeout(connect,2500);}}
    };
    void connect();return()=>{closed=true;source?.close();if(timer)clearTimeout(timer);};
  },[sid,logged,remember]);

  const run=async(fn:()=>Promise<void>)=>{
    if(actionLock.current)return;actionLock.current=true;setPending(true);setError('');
    try{await fn();}catch(e){setError(e instanceof Error?e.message:String(e));}finally{actionLock.current=false;setPending(false);}
  };
  return {logged,loading,pending,error,setError,applications,caps,catalog,views,view,selected,sid,live,transport,connecting,
    generation:generation.current,events:events[sid]??[],taskErrors,
    login:(password:string)=>run(async()=>{await api('/auth/staff/login',{username:'demo',password});await initialize();}),
    selectTask:(id:string)=>run(async()=>{const task=await api<Application>(`/staff/applications/${id}`);setApplications(old=>old.map(a=>a.application_id===id?task:a));if(task.snapshot)activate(task.snapshot);else{setViews(old=>{const next={...old};delete next[id];return next;});setSelected(id);setSid('');activeSid.current='';setLive('');}}),
    reload:()=>run(async()=>{await initialize();}),
    refresh:()=>run(async()=>{if(selected){const task=await api<Application>(`/staff/applications/${selected}`);setApplications(old=>old.map(a=>a.application_id===selected?task:a));if(task.snapshot)activate(task.snapshot);else{setViews(old=>{const next={...old};delete next[selected];return next;});setSid('');activeSid.current='';setLive('');}}}),
    changeTransport:(mode:Transport)=>run(async()=>{
      setTransportState(mode);localStorage.setItem('zhiyu.workbench.transport',mode);
      if(view?.state==='CASE_READY'&&!view.busy){const next=await create(view.application_id,view.route==='phone'?mode:'text');activate(next);}
    }),
    newSession:()=>run(async()=>{const task=applications.find(a=>a.application_id===selected);if(task)activate(await create(selected,task.effective_route==='phone'?transport:'text'));}),
    rejectWritten:(preview:WrittenPreview,key:string)=>run(async()=>{if(!sid)return;const next=await api<View>(`/staff/sessions/${sid}/reject`,{case_revision:preview.case_revision,expected_revision:preview.state_revision,knowledge_version:preview.knowledge_version,rule_version:preview.rule_version,body_sha256:preview.body_sha256,preview_id:preview.preview_id},key);remember(next);}),
    recordNoAnswer:()=>run(async()=>{if(!view)return;const task=await api<Application>(`/staff/applications/${selected}`);const next=await api<View>(`/staff/sessions/${sid}/contact-events`,{event_id:crypto.randomUUID(),case_revision:task.case_revision,expected_call_attempt:task.call_attempt,outcome:'no_answer'});remember(next);const fresh=await api<Application>(`/staff/applications/${selected}`);setApplications(old=>old.map(a=>a.application_id===selected?fresh:a));}),
    start:()=>run(async()=>{if(!view)return;const key=startKeys.current[sid]??(startKeys.current[sid]=crypto.randomUUID());
      const turn=await api<Accepted>(`/sessions/${sid}/start`,{expected_revision:view.state_revision},key);
      generation.current=turn.generation_id;remember({...view,busy:true,generation_id:turn.generation_id});}),
    send:async(text:string)=>{let sent=false;await run(async()=>{if(!text.trim()||!view||view.mode!=='text')return;
      let receipt=turnReceipt.current;if(!receipt||receipt.sid!==sid||receipt.text!==text)receipt=turnReceipt.current={sid,text,key:crypto.randomUUID()};
      const turn=await api<Accepted>(`/sessions/${sid}/turns`,{text,client_event_id:receipt.key});
      turnReceipt.current=null;generation.current=turn.generation_id;setLive('');remember({...view,busy:true,generation_id:turn.generation_id});sent=true;
    });return sent;},
    interrupt:()=>run(async()=>{if(!view?.generation_id)return;await api(`/sessions/${sid}/interruptions`,{generation_id:view.generation_id,client_event_id:crypto.randomUUID()});generation.current=null;setLive('');remember(await api<View>(`/sessions/${sid}`));}),
    end:()=>run(async()=>{if(!sid)return;generation.current=null;setLive('');remember(await api<View>(`/sessions/${sid}/end`,{reason:'user_ended'}));}),
  };
}
