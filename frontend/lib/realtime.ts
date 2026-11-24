import type {components} from '../app/api.generated';
type Control=components['schemas']['RealtimeControl'];
type Notice={type:string;code?:string;epoch?:number;answer?:boolean;requires_ready?:boolean;
  generation_id?:string;playback_instance_id?:string;text?:string};

export class RealtimeVoice {
  private pc?:RTCPeerConnection;
  private ws?:WebSocket;
  private stream?:MediaStream;
  private context?:AudioContext;
  private audio?:HTMLAudioElement;
  private nodes:AudioWorkletNode[]=[];
  private sources:MediaStreamAudioSourceNode[]=[];
  private heartbeat?:ReturnType<typeof setInterval>;
  private timer?:ReturnType<typeof setTimeout>;
  private drain?:ReturnType<typeof setInterval>;
  private abort=new AbortController();
  private closed=false;
  private lastRemote=0;
  private remoteObserved=false;
  private ready=false;
  private reportConnection() {if(this.ready&&this.pc?.connectionState==='connected')this.callbacks.status('实时语音已连接，正在聆听');}
  private stoppedEpoch=0;
  private awaitingStop=false;
  private pendingPermit?:Notice;
  private currentGeneration?:string;
  private answered=new Set<string>();
  private lastVoice=0;
  private lastSpeechEnd=0;
  private speechBlocks=0;
  private speaking=false;
  private outputAllowed=false;
  private interruptedAt=0;
  private controlledEnd=false;
  private markInputEnd=()=>{this.lastSpeechEnd=performance.now();this.controlledEnd=true;};
  private callbacks:{status:(text:string)=>void;error:(text:string)=>void};

  constructor(private sid:string, callbacks:{status:(text:string)=>void;error:(text:string)=>void}) {
    this.callbacks=callbacks;
    window.addEventListener('zhiyu-realtime-input-ended',this.markInputEnd);
  }
  private async request<T>(suffix:string, body?:unknown):Promise<T> {
    const response=await fetch(`/api/v1/sessions/${this.sid}/realtime/${suffix}`,{
      method:body===undefined?'GET':'POST',signal:this.abort.signal,
      headers:{'Content-Type':'application/json','X-Requested-With':'zhiyu'},
      body:body===undefined?undefined:JSON.stringify(body)});
    const data=await response.json();
    if(!response.ok)throw new Error(data.message??'实时连接请求失败');
    return data as T;
  }
  private send(value:Pick<Control,'type'> & Partial<Omit<Control,'type'|'request_id'>>) {
    if(this.ws?.readyState===WebSocket.OPEN)this.ws.send(JSON.stringify({...value,request_id:crypto.randomUUID()}));
  }
  private metric(name:'first_answer_audio'|'interrupt_stopped',duration:number) {
    this.send({type:'observation',name,duration_ms:duration});
    window.dispatchEvent(new CustomEvent('zhiyu-realtime-metric',{detail:{name,duration_ms:duration,
      generation_id:this.currentGeneration,clock_domain:'browser.performance',
      measurement_method:name==='interrupt_stopped'?'interrupt_request_to_remote_silence':
        (this.controlledEnd?'controlled_input_end_to_remote_pcm':'local_vad_estimate_to_remote_pcm'),evidence:false}}));
  }
  private async meter(stream:MediaStream, remote:boolean) {
    const ctx=this.context!;
    const source=ctx.createMediaStreamSource(stream),node=new AudioWorkletNode(ctx,'realtime-meter');
    const silent=ctx.createGain();silent.gain.value=0;
    source.connect(node);node.connect(silent);silent.connect(ctx.destination);
    this.sources.push(source);this.nodes.push(node);
    node.port.onmessage=event=>{
      if(this.closed)return;
      const now=performance.now(),rms=Number(event.data.rms);
      if(remote) {
        this.remoteObserved=true;
        if(rms>0.004)this.lastRemote=now;
        if(this.interruptedAt&&now-this.lastRemote>=60) {
          this.metric('interrupt_stopped',now-this.interruptedAt);this.interruptedAt=0;
        }
        if(rms>0.004&&this.outputAllowed&&this.pendingPermit?.answer&&this.currentGeneration&&
          !this.answered.has(this.currentGeneration)&&this.lastSpeechEnd>0) {
          this.answered.add(this.currentGeneration);
          this.metric('first_answer_audio',now-this.lastSpeechEnd);
        }
      } else {
        if(rms>0.025) {
          this.lastVoice=now;this.speechBlocks++;
          if(this.speechBlocks>=3&&!this.speaking) {
            this.speaking=true;
            this.controlledEnd=false;
            if(this.outputAllowed||this.pendingPermit)this.interrupt();
          }
        } else {
          this.speechBlocks=0;
          if(this.speaking&&!this.controlledEnd&&now-this.lastVoice>=100) {
            this.lastSpeechEnd=this.lastVoice;
          }
          if(this.speaking&&now-this.lastVoice>=500)this.speaking=false;
        }
      }
    };
  }
  private async notice(data:Notice) {
    if(this.closed)return;
    if(data.type==='error')throw new Error(`实时通话停止：${data.code??'UNKNOWN'}`);
    if(data.type==='ready'){this.ready=true;this.reportConnection();}
    if(data.type==='waiting')this.callbacks.status(data.text??'正在核实，请稍等');
    if(data.type==='playback.stop') {
      this.callbacks.status('已打断');
      this.stoppedEpoch=Math.max(this.stoppedEpoch,data.epoch??0);
      this.awaitingStop=false;this.mute();
    }
    if(data.type==='playback.permit') {
      if(this.awaitingStop||(data.epoch??0)<this.stoppedEpoch)return;
      this.pendingPermit=data;this.currentGeneration=data.generation_id;this.callbacks.status('AI 正在播报');
      if(data.requires_ready) {
        if(this.drain)clearInterval(this.drain);
        const requested=performance.now();
        this.drain=setInterval(()=>{
          if(this.pendingPermit!==data||this.closed){clearInterval(this.drain);return;}
          const now=performance.now();
          // TTS audio is held by the server until this ack. Observe old RTP draining.
          if(this.remoteObserved&&now-Math.max(this.lastRemote,requested)>=100) {
            clearInterval(this.drain);
            if(this.audio)this.audio.muted=false;
            this.outputAllowed=true;
            this.send({type:'playback_ready',playback_instance_id:data.playback_instance_id});
          } else if(now-requested>2500)void this.fail('旧音频未能及时排空，请重新连接');
        },20);
      }
    }
  }
  private mute() {
    if(this.audio)this.audio.muted=true;
    this.outputAllowed=false;this.pendingPermit=undefined;
    if(this.drain)clearInterval(this.drain);
  }
  interrupt() {
    if(this.closed)return;
    this.interruptedAt=performance.now();this.awaitingStop=true;this.mute();
    this.send({type:'interrupt'});
    this.callbacks.status('已打断，可以继续讲话');
  }
  async start(revision:number) {
    try {
      if(!navigator.mediaDevices?.getUserMedia)throw new Error('麦克风需要HTTPS或localhost');
      this.stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true},video:false});
      if(this.closed){this.stream.getTracks().forEach(t=>t.stop());return;}
      this.timer=setTimeout(()=>void this.fail('实时媒体连接超时'),20000);
      this.context=new AudioContext();await this.context.resume();
      await this.context.audioWorklet.addModule('/realtime-meter.js');
      await this.meter(this.stream,false);
      const ice=await this.request<components['schemas']['RealtimeIce']>('ice');
      const pc=new RTCPeerConnection({iceServers:ice.ice_servers.map(s=>({urls:s.urls,
        username:s.username??undefined,credential:s.credential??undefined}))});
      this.pc=pc;
      this.audio=new Audio();this.audio.autoplay=true;this.audio.muted=true;
      pc.ontrack=event=>{
        const remote=new MediaStream([event.track]);
        this.audio!.srcObject=remote;
        void this.audio!.play().catch(()=>void this.fail('浏览器阻止播放，请重新开始'));
        void this.meter(remote,true).catch(()=>void this.fail('无法监测远端音频'));
      };
      pc.onconnectionstatechange=()=>{
        this.reportConnection();
        if(pc.connectionState==='failed'||pc.connectionState==='disconnected')void this.fail('媒体连接断开，请重新连接');
      };
      this.stream.getTracks().forEach(track=>pc.addTrack(track,this.stream!));
      await pc.setLocalDescription(await pc.createOffer());
      if(pc.iceGatheringState!=='complete')await new Promise<void>((resolve,reject)=>{
        const changed=()=>{if(pc.iceGatheringState==='complete'){pc.removeEventListener('icegatheringstatechange',changed);resolve();}};
        pc.addEventListener('icegatheringstatechange',changed);
        this.abort.signal.addEventListener('abort',()=>{pc.removeEventListener('icegatheringstatechange',changed);reject(new Error('连接已取消'));},{once:true});
        changed();
      });
      const answer=await this.request<components['schemas']['RealtimeAnswer']>('offer',{
        type:'offer',sdp:pc.localDescription!.sdp,expected_state_revision:revision});
      await pc.setRemoteDescription({type:'answer',sdp:answer.sdp});
      const ws=new WebSocket(`${location.protocol==='https:'?'wss:':'ws:'}//${location.host}/api/v1/sessions/${this.sid}/realtime/control?binding=${encodeURIComponent(answer.connection_binding)}`);
      this.ws=ws;
      await new Promise<void>((resolve,reject)=>{
        ws.onopen=()=>{this.send({type:'ready'});resolve();};
        ws.onerror=()=>reject(new Error('控制连接失败'));
        ws.onclose=()=>{if(!this.closed)void this.fail('控制连接断开');reject(new Error('连接已关闭'));};
        ws.onmessage=event=>{
          try{void this.notice(JSON.parse(String(event.data)) as Notice).catch(e=>void this.fail(String(e)));}
          catch{void this.fail('控制消息格式不正确');}
        };
      });
      this.heartbeat=setInterval(()=>this.send({type:'heartbeat'}),10000);
      if(this.timer)clearTimeout(this.timer);
      this.timer=setTimeout(()=>void this.close(true),180000);
    } catch(error) {
      await this.close();throw error;
    }
  }
  private async fail(message:string) {
    if(this.closed)return;
    this.callbacks.error(message);await this.close();
    this.callbacks.status('连接已关闭，可重新建立媒体连接');
  }
  async close(explicit=false) {
    if(this.closed)return;
    if(explicit)this.send({type:'end'});
    this.closed=true;this.mute();this.abort.abort();
    window.removeEventListener('zhiyu-realtime-input-ended',this.markInputEnd);
    if(this.timer)clearTimeout(this.timer);
    if(this.heartbeat)clearInterval(this.heartbeat);
    this.nodes.forEach(n=>{n.port.onmessage=null;n.disconnect();});this.nodes=[];
    this.sources.forEach(n=>n.disconnect());this.sources=[];
    this.stream?.getTracks().forEach(t=>t.stop());
    if(this.audio){this.audio.pause();this.audio.srcObject=null;}
    if(this.ws){this.ws.onclose=null;this.ws.onmessage=null;this.ws.close();}
    if(this.pc){this.pc.ontrack=null;this.pc.onconnectionstatechange=null;this.pc.close();}
    await this.context?.close().catch(()=>{});
    if(explicit)this.callbacks.status('语音连接已关闭');
  }
}
