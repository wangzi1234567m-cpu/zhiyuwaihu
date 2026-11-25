'use client';
import {useCallback,useEffect,useRef,useState} from 'react';
import {api,ApiRequestError} from './api';
import type {Application,BusinessEvent,Capabilities,TaskViews,Transport,View} from './workbench';
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
  const prepare=useCallback(async(next:View,mode:Transport)=>{
    if(next.route!=='phone'||next.state!=='CASE_READY'||next.mode==='voice'||mode==='text')return next;
    return create(next.application_id,mode);
  },[create]);
  const initialize=useCallback(async()=>{
    if(initTask.current)return initTask.current;
    const work=async()=>{
      setLoading(true);setError('');
      try {
        const [items,capabilities]=await Promise.all([api<Application[]>('/applications'),api<Capabilities>('/capabilities')]);
        setApplications(items);setCaps(capabilities);setLogged(true);
        const storedMode=localStorage.getItem('zhiyu.workbench.transport');
        const mode:Transport=storedMode==='text'?'text':storedMode==='native_webrtc'&&capabilities.realtime_webrtc?'native_webrtc':
          storedMode==='voice'&&capabilities.voice?'voice':capabilities.realtime_webrtc?'native_webrtc':capabilities.voice?'voice':'text';
        setTransportState(mode);sessionIds.current=savedSessions();
        const restored:TaskViews={};let preferred='';
        const legacy=localStorage.getItem('zhiyu.session');
        if(legacy)try{const previous=await api<View>(`/sessions/${legacy}`);
          if(items.some(a=>a.application_id===previous.application_id)){restored[previous.application_id]=previous;preferred=previous.application_id;remember(previous);}
        }catch{/* Stale legacy IDs never authorize another task. */}
        for(const task of items){
          if(restored[task.application_id])continue;
          try{
            let next:View;
            const saved=sessionIds.current[task.application_id];
            if(saved){try{next=await api<View>(`/sessions/${saved}`);}catch(e){if(!(e instanceof ApiRequestError)||e.status!==404)throw e;next=await create(task.application_id,'text');}}
            else {
              // Persist a request key before admission so StrictMode/network retries
              // cannot create duplicate preview sessions. No start/turn is sent.
              const keyName=`zhiyu.workbench.admission.${task.application_id}`;
              let key=localStorage.getItem(keyName);if(!key){key=crypto.randomUUID();localStorage.setItem(keyName,key);}
              next=await create(task.application_id,'text',key);
            }
            if(next.application_id!==task.application_id)throw new Error('会话与申请单不匹配');
            restored[task.application_id]=next;remember(next);
          }catch(e){setTaskErrors(old=>({...old,[task.application_id]:e instanceof Error?e.message:'任务加载失败'}));}
        }
        const chosen=restored[preferred]??items.map(a=>restored[a.application_id]).find(Boolean);
        if(chosen){try{activate(await prepare(chosen,mode));}catch(e){activate(chosen);setError(e instanceof Error?e.message:'语音会话准备失败，可切换开发设置');}}
      }catch(e){if(e instanceof ApiRequestError&&e.status===401)setLogged(false);else setError(e instanceof Error?e.message:'加载失败，请重试');}
      finally{setLoading(false);}
    };
    initTask.current=work();try{await initTask.current;}finally{initTask.current=null;}
  },[activate,create,prepare,remember]);
  useEffect(()=>{void initialize();},[initialize]);

  useEffect(()=>{
    if(!sid||!logged)return;
    let closed=false;let source:EventSource|undefined;let timer:ReturnType<typeof setTimeout>|undefined;
    activeSid.current=sid;setLive('');setConnecting(true);
    const refresh=async()=>{
      const next=await api<View>(`/sessions/${sid}`);
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
          if(['generation.finished','operation.updated','session.ended','knowledge.used'].includes(event.payload.type))void refresh().catch(e=>{if(!closed)setError(String(e));});
        };
        for(const name of ['turn.started','text.delta','state.changed','generation.finished','turn.interrupted','operation.updated','session.ended','knowledge.used','metric.recorded'])source.addEventListener(name,receive as EventListener);
        source.onerror=()=>{source?.close();if(!closed){setConnecting(true);timer=setTimeout(connect,1500);}};
      }catch(e){if(!closed){setError(e instanceof Error?e.message:'状态恢复失败');setConnecting(true);timer=setTimeout(connect,2500);}}
    };
    void connect();return()=>{closed=true;source?.close();if(timer)clearTimeout(timer);};
  },[sid,logged,remember]);

  const run=async(fn:()=>Promise<void>)=>{
    if(actionLock.current)return;actionLock.current=true;setPending(true);setError('');
    try{await fn();}catch(e){setError(e instanceof Error?e.message:String(e));}finally{actionLock.current=false;setPending(false);}
  };
  return {logged,loading,pending,error,setError,applications,caps,views,view,selected,sid,live,transport,connecting,
    generation:generation.current,events:events[sid]??[],taskErrors,
    login:(password:string)=>run(async()=>{await api('/auth/login',{username:'demo',password});await initialize();}),
    selectTask:(id:string)=>run(async()=>{const next=views[id];if(!next)throw new Error('该任务尚未加载，请重试加载队列');activate(await prepare(next,transport));}),
    reload:()=>run(async()=>{await initialize();}),
    refresh:()=>run(async()=>{if(sid)remember(await api<View>(`/sessions/${sid}`));}),
    changeTransport:(mode:Transport)=>run(async()=>{
      setTransportState(mode);localStorage.setItem('zhiyu.workbench.transport',mode);
      if(view?.state==='CASE_READY'&&!view.busy){const next=await create(view.application_id,view.route==='phone'?mode:'text');activate(next);}
    }),
    newSession:()=>run(async()=>{if(view)activate(await create(view.application_id,view.route==='phone'?transport:'text'));}),
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
