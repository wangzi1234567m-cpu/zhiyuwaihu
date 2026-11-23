'use client';
import {useEffect,useRef,useState} from 'react';
import type {IAgoraRTCClient, ILocalAudioTrack, IRemoteAudioTrack} from 'agora-rtc-sdk-ng';
import type {AgoraVoiceAI, TranscriptHelperItem, UserTranscription, AgentTranscription} from 'agora-agent-client-toolkit';
import type {RTMClient} from 'agora-rtm';
import type {components} from './api.generated';
import {Hand, Mic, PhoneOff} from 'lucide-react';
import {Badge, Button, Card} from './components/ui';
import {EmptyConversation, Message} from './components/message';

type View=components['schemas']['SessionView'];
type Credentials=components['schemas']['MediaCredentials'];
type Transcript=TranscriptHelperItem<Partial<UserTranscription|AgentTranscription>>[];

async function command<T>(sid:string,suffix:string,body:unknown={}):Promise<T>{
  const response=await fetch(`/api/v1/sessions/${sid}/${suffix}`,{method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'zhiyu'},body:JSON.stringify(body)});
  const data=await response.json();
  if(!response.ok)throw new Error(data.message??'语音请求失败');
  return data;
}

export default function VoicePanel({view,onError}:{view:View;onError:(message:string)=>void}){
  const [status,setStatus]=useState('尚未接入麦克风');
  const [active,setActive]=useState(false);
  const [working,setWorking]=useState(false);
  const [transcript,setTranscript]=useState<Transcript>([]);
  const resources=useRef<{rtc?:IAgoraRTCClient;rtm?:RTMClient;channel?:string;ai?:AgoraVoiceAI;mic?:ILocalAudioTrack;remote?:IRemoteAudioTrack}>({});
  const alive=useRef(true);
  const blockedAudio=useRef(false);
  const engaged=useRef(false);
  const turn=useRef<number|undefined>(undefined);
  const sid=view.session_id;
  const uid=useRef<number>(102);

  async function release(){
    const r=resources.current;resources.current={};
    r.remote?.stop();r.mic?.stop();r.mic?.close();
    r.ai?.destroy();
    if(r.rtm&&r.channel)await r.rtm.unsubscribe(r.channel).catch(()=>{});
    await Promise.allSettled([r.rtc?.leave(),r.rtm?.logout()]);
  }

  async function stop(){
    setWorking(true);
    try{await command(sid,'end',{reason:'user_ended'});}
    catch(e){onError(e instanceof Error?e.message:String(e));}
    finally{await release();setActive(false);setWorking(false);setStatus('已结束；后台会继续清理未确认资源');}
  }

  useEffect(()=>{
    alive.current=true;
    const abandon=()=>{
      if(!engaged.current)return;
      fetch(`/api/v1/sessions/${sid}/end`,{method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'zhiyu'},
        body:JSON.stringify({reason:'page_closed'}),keepalive:true}).catch(()=>{});
    };
    window.addEventListener('pagehide',abandon);
    return()=>{alive.current=false;window.removeEventListener('pagehide',abandon);abandon();void release();};
  },[sid]);

  useEffect(()=>{
    if(!active)return;
    const heartbeat=setInterval(()=>{command(sid,'voice/heartbeat',{client_event_id:crypto.randomUUID()}).catch(()=>{
      setStatus('后端连接中断，请结束并重建通话');
      resources.current.remote?.stop();
    });},10000);
    const deadline=setTimeout(()=>void stop(),180000);
    return()=>{clearInterval(heartbeat);clearTimeout(deadline);};
  },[active,sid]);

  useEffect(()=>{if(view.ended&&active){void release();setActive(false);setStatus('业务会话已结束');}},[view.ended,active]);

  async function start(){
    setWorking(true);setStatus('正在准备麦克风与媒体连接');
    try{
      if(!navigator.mediaDevices?.getUserMedia)throw new Error('麦克风需要HTTPS或localhost安全页面');
      const [{default:AgoraRTC},{RTMClient:RTM},{AgoraVoiceAI,AgoraVoiceAIEvents,TranscriptHelperMode}]=await Promise.all([
        import('agora-rtc-sdk-ng'),import('agora-rtm'),import('agora-agent-client-toolkit')]);
      if(!alive.current)return;
      engaged.current=true;
      const credentials=await command<Credentials>(sid,'credentials');
      uid.current=credentials.agent_uid;
      AgoraRTC.setLogLevel(4);
      const rtc=AgoraRTC.createClient({mode:'rtc',codec:'vp8'});
      resources.current.rtc=rtc;
      const rtm=new RTM(credentials.app_id,String(credentials.rtc_uid),{logLevel:'none',logUpload:false});
      resources.current.rtm=rtm;
      rtc.on('user-published',async(user,mediaType)=>{
        if(mediaType!=='audio'||Number(user.uid)!==credentials.agent_uid)return;
        await rtc.subscribe(user,'audio');
        if(!alive.current)return;
        resources.current.remote=user.audioTrack;
        if(!blockedAudio.current)user.audioTrack?.play();
      });
      rtc.on('connection-state-change',(state)=>{
        if(alive.current&&state==='DISCONNECTED'){
          setStatus('媒体连接已断开，请重新建立通话');
          void command(sid,'end',{reason:'media_disconnected'}).catch(()=>{});
          void release();setActive(false);
        }
      });
      await rtm.login({token:credentials.rtm_token});
      await rtm.subscribe(credentials.channel,{withMessage:true,withPresence:true});
      resources.current.channel=credentials.channel;
      const ai=await AgoraVoiceAI.init({rtcEngine:rtc,rtmEngine:rtm,renderMode:TranscriptHelperMode.TEXT,enableLog:false,enableAgoraMetrics:false});
      resources.current.ai=ai;
      ai.on(AgoraVoiceAIEvents.TRANSCRIPT_UPDATED,items=>{
        if(!alive.current)return;
        setTranscript(items); // Toolkit delivers the entire history; never append it.
        const latest=items.at(-1);
        if(latest)turn.current=latest.turn_id;
      });
      ai.on(AgoraVoiceAIEvents.AGENT_THINKING_CHANGED,(_,thinking)=>{
        if(thinking){blockedAudio.current=false;resources.current.remote?.play();}
      });
      ai.on(AgoraVoiceAIEvents.AGENT_INTERRUPTED,(_,event)=>{
        resources.current.remote?.stop();blockedAudio.current=true;
        void command(sid,'voice/telemetry',{client_event_id:crypto.randomUUID(),provider_turn_id:event.turnID,kind:'interrupted',
          clock_domain:'browser.performance',monotonic_ms:performance.now(),measurement_method:'agora_toolkit_interrupted_event'}).catch(()=>{});
      });
      ai.on(AgoraVoiceAIEvents.AGENT_METRICS,(_,metric)=>{
        void command(sid,'voice/telemetry',{client_event_id:crypto.randomUUID(),kind:'sdk_metric',
          clock_domain:'browser.performance',monotonic_ms:performance.now(),measurement_method:'agora_sdk_report_at_browser_receipt',
          provider_metric_name:metric.name,provider_metric_value:metric.value}).catch(()=>{});
      });
      ai.on(AgoraVoiceAIEvents.AGENT_ERROR,()=>{
        if(alive.current){setStatus('语音引擎报错，请检查服务权限、供应商配置或额度');onError('语音引擎报错，已停止通话以避免持续消耗额度');void stop();}
      });
      await rtc.join(credentials.app_id,credentials.channel,credentials.rtc_token,credentials.rtc_uid);
      const mic=await AgoraRTC.createMicrophoneAudioTrack();resources.current.mic=mic;
      await rtc.publish([mic]);
      ai.subscribeMessage(credentials.channel);
      if(!alive.current){await release();return;}
      await command(sid,'voice/start',{expected_revision:view.state_revision});
      if(!alive.current){await command(sid,'end',{reason:'page_closed'}).catch(()=>{});await release();return;}
      setActive(true);setStatus('语音已连接，AI将主动开场');
    }catch(e){
      await command(sid,'end',{reason:'voice_start_failed'}).catch(()=>{});
      await release();
      if(alive.current){setStatus('语音启动失败，请新建会话');onError(e instanceof Error?e.message:String(e));}
    }finally{if(alive.current)setWorking(false);}
  }

  async function interrupt(){
    resources.current.remote?.stop();blockedAudio.current=true;
    try{await command(sid,'voice/interrupt',{client_event_id:crypto.randomUUID(),provider_turn_id:turn.current});setStatus('已请求打断，您可以继续说话');}
    catch(e){onError(e instanceof Error?e.message:String(e));}
  }

  return <Card className="flex min-h-[560px] flex-col overflow-hidden">
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-3">
      <div className="flex min-w-0 items-center gap-2.5">
        <h2 className="text-sm font-semibold text-ink">实时语音联调</h2>
        <Badge tone={active ? 'success' : working ? 'brand' : 'neutral'} dot pulse={active || working}>{active ? '通话中' : working ? '处理中' : '未通话'}</Badge>
      </div>
      <div className="flex items-center gap-2">
        <Button size="sm" disabled={active||working||view.ended||view.state!=='CASE_READY'} loading={working && !active} onClick={()=>void start()}>
          {!(working && !active) && <Mic aria-hidden className="size-3.5"/>}开始语音
        </Button>
        <Button size="sm" variant="secondary" disabled={!active||working} onClick={()=>void interrupt()}><Hand aria-hidden className="size-3.5"/>语音打断</Button>
        <Button size="sm" variant="danger" disabled={!active||working} onClick={()=>void stop()}><PhoneOff aria-hidden className="size-3.5"/>结束语音</Button>
      </div>
    </div>
    <div className="border-b border-line bg-subtle/60 px-5 py-2.5">
      <p className="text-sm text-ink">{status}</p>
      <p className="mt-0.5 text-xs text-ink-faint">使用麦克风和耳机。单次最多三分钟；本轮默认关闭语音业务写入。</p>
    </div>
    <div className="h-[400px] flex-1 space-y-5 overflow-y-auto px-4 py-6 sm:px-6" aria-live="polite">
      {transcript.length === 0
        ? <EmptyConversation icon={<Mic className="size-5"/>}>开始语音后，双方字幕会实时显示在这里。</EmptyConversation>
        : transcript.map((item,index)=>{
          const ai = Number(item.uid)===uid.current;
          return <Message key={`${item.turn_id}:${item.stream_id}:${index}`} role={ai?'assistant':'user'} label={ai?'AI':'商户'}>{item.text}</Message>;
        })}
    </div>
    <p className="border-t border-line px-5 py-2.5 text-[11px] text-ink-faint">字幕来自声网消息；播放状态与SDK事件不等于端到端音频时延测量。</p>
  </Card>;
}
