'use client';
import {useEffect,useRef,useState} from 'react';
import type {components} from './api.generated';
import {RealtimeVoice} from '../lib/realtime';

export default function RealtimeVoicePanel({view,onError}:{view:components['schemas']['SessionView'];onError:(text:string)=>void}) {
  const [status,setStatus]=useState('准备实时语音');
  const [active,setActive]=useState(false);
  const [working,setWorking]=useState(false);
  const resource=useRef<RealtimeVoice|undefined>(undefined);
  useEffect(()=>()=>{void resource.current?.close();},[view.session_id]);
  useEffect(()=>{if(view.ended){void resource.current?.close();setActive(false);}},[view.ended]);
  async function start() {
    setWorking(true);setStatus('正在连接麦克风');
    const client=new RealtimeVoice(view.session_id,{status:text=>{setStatus(text);if(text.includes('关闭'))setActive(false);},error:onError});
    resource.current=client;
    try{await client.start(view.state_revision);setActive(true);}
    catch(error){onError(error instanceof Error?error.message:String(error));setStatus('连接失败，可重试');}
    finally{setWorking(false);}
  }
  async function end() {
    setWorking(true);
    try {
      await resource.current?.close(true);
      const response=await fetch(`/api/v1/sessions/${view.session_id}/end`,{method:'POST',
        headers:{'Content-Type':'application/json','X-Requested-With':'zhiyu'},body:JSON.stringify({reason:'user_ended'})});
      if(!response.ok)throw new Error('结束状态未确认，请刷新查询');
      setStatus('已结束');
    } catch(error){onError(String(error));}
    finally{setActive(false);setWorking(false);}
  }
  return <section className="voice-panel"><h2>实时语音</h2><p role="status">{status}</p>
    <p className="hint">请使用耳机。当前为合成任务联调，单次最多三分钟。</p>
    <div className="actions"><button disabled={active||working||view.ended} onClick={()=>void start()}>连接语音</button>
      <button disabled={!active||working} onClick={()=>resource.current?.interrupt()}>打断</button>
      <button disabled={!active||working} onClick={()=>void end()}>结束</button></div>
  </section>;
}
