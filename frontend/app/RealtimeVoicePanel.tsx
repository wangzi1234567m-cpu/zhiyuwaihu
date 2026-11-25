'use client';
import {useEffect,useRef,useState} from 'react';
import type {components} from './api.generated';
import {RealtimeVoice} from '../lib/realtime';

import CallStatus from './CallStatus';

export default function RealtimeVoicePanel({view,onError,onStatus}:{onStatus?:(text:string)=>void;view:components['schemas']['SessionView'];onError:(text:string)=>void}) {
  const [status,setStatus]=useState('等待开始');
  useEffect(()=>{onStatus?.(status);},[status,onStatus]);
  const [active,setActive]=useState(false);
  const [working,setWorking]=useState(false);
  const [inputLevel,setInputLevel]=useState(0);
  const resource=useRef<RealtimeVoice|undefined>(undefined);
  useEffect(()=>()=>{void resource.current?.close();},[view.session_id]);
  useEffect(()=>{if(view.ended){void resource.current?.close();setActive(false);}},[view.ended]);
  async function start() {
    setWorking(true);setStatus('正在连接麦克风');
    const client=new RealtimeVoice(view.session_id,{status:text=>{setStatus(text);if(text.includes('关闭')){setActive(false);setInputLevel(0);}if(text.includes('已连接'))setActive(true);},inputLevel:setInputLevel,error:text=>{setActive(false);setInputLevel(0);setStatus('连接失败');onError(text);}});
    resource.current=client;
    try{await client.start(view.state_revision);}
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
  return <section className="voice-controls"><CallStatus active={active} working={working} status={view.ended?'通话已结束':status}/>
    <div className="microphone-status"><meter aria-label="麦克风输入音量" min={0} max={1} value={active?Math.min(1,inputLevel*6):0}/><span>{!active?'麦克风未连接':inputLevel>.008?'检测到麦克风声音':'麦克风已连接 · 当前安静'}</span></div>
    <p className="hint">此语音方式暂不提供临时识别字幕；已提交的对话通过会话记录同步。</p>
    <div className="actions"><button disabled={active||working||view.ended} onClick={()=>void start()}>开始通话</button>
      <button disabled={!active||working} onClick={()=>resource.current?.interrupt()}>打断播报</button>
      <button disabled={!active||working} onClick={()=>void end()}>结束通话</button></div>
  </section>;
}
