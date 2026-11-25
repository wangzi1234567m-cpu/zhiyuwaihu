/* Real merged browser/RTC -> native understanding -> business -> Qwen TTS.
   Requires explicit --run-real-latency; raw evidence stays in ignored .cache. */
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {execFileSync}=require('node:child_process');
const {chromium}=require('../frontend/node_modules/@playwright/test');
const ts=require('../frontend/node_modules/typescript');
const root=path.resolve(__dirname,'..');
const workspace=process.env.LATENCY_WORKSPACE||((path.basename(path.dirname(root))==='tmp')?path.resolve(root,'../..'):root);
const base='http://127.0.0.1:18765',token=process.env.LATENCY_CONTROL_TOKEN,run=process.env.LATENCY_RUN_ID;
if(!process.argv.includes('--run-real-latency')||!token||!run)throw new Error('Explicit real evaluation and control configuration required');
const output=path.join(root,'.cache',run+'.json');
fs.mkdirSync(path.dirname(output),{recursive:true});
const calls=Number(process.env.LATENCY_CALLS||15),target=30;
const specs=[['identity','merchant-1.trimmed.wav'],['guarantee','merchant-2.trimmed.wav'],['source','merchant-source.trimmed.wav']];
const audio=specs.map(([id,name])=>{
  const bytes=fs.readFileSync(path.join(workspace,'.cache/round1-audio',name));
  let rate,channels,bits,data;
  for(let p=12;p+8<=bytes.length;){const kind=bytes.toString('ascii',p,p+4),size=bytes.readUInt32LE(p+4);
    if(kind==='fmt '){channels=bytes.readUInt16LE(p+10);rate=bytes.readUInt32LE(p+12);bits=bytes.readUInt16LE(p+22);}
    if(kind==='data')data=bytes.subarray(p+8,p+8+size);p+=8+size+(size%2);}
  if(channels!==1||bits!==16||!data)throw new Error('Expected mono PCM16 fixture');
  const block=Math.round(rate*.01);let voicedEnd=0;
  for(let i=0;i<data.length/2;i+=block){let sq=0,n=Math.min(block,data.length/2-i);
    for(let j=0;j<n;j++)sq+=(data.readInt16LE((i+j)*2)/32768)**2;
    if(Math.sqrt(sq/n)>.004)voicedEnd=(i+n)/rate*1000;}
  return {id,name,sha256:crypto.createHash('sha256').update(bytes).digest('hex'),
    duration_ms:data.length/2/rate*1000,speech_end_ms:voicedEnd,encoded:bytes.toString('base64')};
});
const hash=crypto.createHash('sha256').update(JSON.stringify(audio.map(({encoded,...rest})=>rest))).digest('hex');
const report=fs.existsSync(output)?JSON.parse(fs.readFileSync(output,'utf8')):{
  metadata:{run_id:run,commit:execFileSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8'}).trim(),knowledge_version:process.env.ZHIYU_KNOWLEDGE_VERSION||'demo-k1',case_set_sha256:hash,
    code_variant:process.env.LATENCY_VARIANT||'unmodified-main',
    provider_source_sha256:crypto.createHash('sha256').update(fs.readFileSync(path.join(root,'backend/app/realtime/provider.py'))).digest('hex'),
    network_profile:'same Windows host/browser and aiortc loopback; real Beijing provider WAN; no TURN',
    client:'headless Chromium, shipped RealtimeVoice and AudioWorklet',measurement_method:'controlled_input_end_to_remote_pcm',
    input_end_definition:'last 10ms RMS>0.004 block in fixed synthetic WAV; AudioContext scheduled playback mapped to performance clock',
    business_writes:false,physical_speaker_measurement:false,audio:audio.map(({encoded,...rest})=>rest)},
  calls:[],samples:[]};
if(report.metadata.case_set_sha256!==hash||report.metadata.run_id!==run)throw new Error('Frozen input mismatch');
const save=()=>fs.writeFileSync(output,JSON.stringify(report,null,2)+'\n');
save();
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let browser;
const watchdog=setTimeout(()=>{report.deadline=true;save();void browser?.close();},22*60*1000);
async function evaluate(){
  browser=await chromium.launch({headless:true,args:['--autoplay-policy=no-user-gesture-required','--disable-background-timer-throttling']});
  for(let n=0;n<calls&&report.calls.length<15&&report.samples.filter(s=>s.status==='success').length<target;n++){
    const call={index:report.calls.length+1,started_at:new Date().toISOString(),samples:[],harness_revision:'continuous-silent-source-v2'};
    report.calls.push(call);save();
    const context=await browser.newContext(),page=await context.newPage();let sid;
    const faults=[];page.on('pageerror',e=>faults.push(e.name));
    try{
      await page.goto(base);
      await page.evaluate(()=>{
        window.evalState={metrics:[],notices:[],failures:[],lastRemote:0,activity:[],input:null};
        const Original=window.RTCPeerConnection;
        window.RTCPeerConnection=class extends Original{constructor(...args){super(...args);this.addEventListener('track',e=>{
          const ctx=new AudioContext(),stream=new MediaStream([e.track]),source=ctx.createMediaStreamSource(stream);
          const node=ctx.createScriptProcessor(256,1,1),silent=ctx.createGain();silent.gain.value=0;
          source.connect(node);node.connect(silent);silent.connect(ctx.destination);void ctx.resume();
          window.evalState.remoteContext=ctx;
          node.onaudioprocess=event=>{let sq=0;const data=event.inputBuffer.getChannelData(0);for(const x of data)sq+=x*x;
            if(Math.sqrt(sq/data.length)>.004){const at=performance.now();window.evalState.lastRemote=at;window.evalState.activity.push(at);}};
        });}};
        navigator.mediaDevices.getUserMedia=async()=>{
          const ctx=new AudioContext({sampleRate:48000});await ctx.resume();const dest=ctx.createMediaStreamDestination();
          // Keep the synthetic microphone rendering silent frames between WAVs.
          const keep=ctx.createConstantSource(),zero=ctx.createGain();zero.gain.value=0;keep.connect(zero);zero.connect(dest);keep.start();
          window.evalState.input={ctx,dest};return dest.stream;};
        window.addEventListener('zhiyu-realtime-metric',event=>window.evalState.metrics.push({...event.detail,at:performance.now()}));
      });
      const source=fs.readFileSync(path.join(root,'frontend/lib/realtime.ts'),'utf8').replace('export class RealtimeVoice','window.RealtimeVoice = class RealtimeVoice');
      const js=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2020,module:ts.ModuleKind.ES2022}}).outputText.replace(/export\s*\{\s*\};?/g,'');
      await page.addScriptTag({content:js});
      async function request(url,body,privateRoute=false){return page.evaluate(async({url,body,privateRoute,token})=>{
        const r=await fetch(url,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json','X-Requested-With':'zhiyu',...(privateRoute?{'X-Latency-Token':token}:{})},body:body===undefined?undefined:JSON.stringify(body)});
        const data=await r.json();if(!r.ok)throw new Error('HTTP_'+r.status+'_'+(data.code||'ERROR'));return data;
      },{url,body,privateRoute,token});}
      await request('/api/v1/dev/login',{username:'demo',password:'local-latency-only'});
      report.metadata.provider=await request('/eval/config',undefined,true);save();
      const view=await page.evaluate(async()=>{
        const r=await fetch('/api/v1/sessions',{method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'zhiyu','Idempotency-Key':crypto.randomUUID()},
          body:JSON.stringify({application_id:'00000000-0000-0000-0000-000000000001',mode:'voice',voice_transport:'native_webrtc'})});
        const data=await r.json();if(!r.ok)throw new Error(data.code);return data;});
      sid=view.session_id;call.session_id=sid;save();
      await page.evaluate(async({sid,revision})=>{
        window.client=new window.RealtimeVoice(sid,{status:t=>evalState.notices.push(t),error:t=>evalState.failures.push(t)});
        const orig=window.client.notice.bind(window.client);
        window.client.notice=async data=>{evalState.notices.push({...data,at:performance.now()});return orig(data);};
        await window.client.start(revision);
      },{sid,revision:view.state_revision});
      await page.waitForFunction(()=>evalState.notices.some(t=>typeof t==='string'&&t.includes('已连接'))||evalState.failures.length,{},{timeout:25000});
      // Opening is explicitly excluded; wait for its reviewed output to drain.
      await page.waitForFunction(()=>evalState.notices.some(t=>t.type==='playback.drained')||evalState.failures.length,{},{timeout:25000});
      if(await page.evaluate(()=>evalState.failures.length))throw new Error('OPENING_FAILED');
      await page.waitForFunction(()=>performance.now()-evalState.lastRemote>500,{},{timeout:15000});
      call.connected=true;
      for(const fixture of audio){
        if(report.samples.filter(s=>s.status==='success').length>=target)break;
        const sample={call:call.index,case:fixture.id,status:'pending',session_id:sid,input_sha256:fixture.sha256};
        report.samples.push(sample);call.samples.push(report.samples.length-1);save();
        try{
          const sync=[];
          for(let i=0;i<7;i++)sync.push(await page.evaluate(async token=>{const start=performance.now();
            const r=await fetch('/eval/clock',{headers:{'X-Latency-Token':token}}),data=await r.json(),end=performance.now();
            return {rtt_ms:end-start,offset_ms:data.server_ms-(start+end)/2};},token));
          sample.clock_sync=sync.sort((a,b)=>a.rtt_ms-b.rtt_ms)[0];
          const before=await page.evaluate(()=>({metrics:evalState.metrics.length,notices:evalState.notices.length,activity:evalState.activity.length}));
          sample.input=await page.evaluate(async fixture=>{
            const {ctx,dest}=evalState.input,bytes=Uint8Array.from(atob(fixture.encoded),c=>c.charCodeAt(0));
            const buffer=await ctx.decodeAudioData(bytes.buffer),source=ctx.createBufferSource();source.buffer=buffer;source.connect(dest);
            const scheduled=ctx.currentTime+.05,reference=performance.now(),ctxReference=ctx.currentTime;
            const start=reference+(scheduled-ctxReference)*1000,speech_end=start+fixture.speech_end_ms;
            return new Promise(resolve=>{source.onended=()=>{window.dispatchEvent(new CustomEvent('zhiyu-realtime-input-ended'));
              resolve({start_ms:start,speech_end_ms:speech_end,scheduled_file_end_ms:start+buffer.duration*1000,
                ended_handler_ms:performance.now(),duration_ms:buffer.duration*1000});};source.start(scheduled);});
          },fixture);save();
          await page.waitForFunction(before=>evalState.metrics.slice(before.metrics).some(m=>m.name==='first_answer_audio')||evalState.failures.length,{...before},{timeout:16000});
          const observed=await page.evaluate(before=>({metrics:evalState.metrics.slice(before.metrics),notices:evalState.notices.slice(before.notices),failures:evalState.failures}),before);
          sample.browser=observed;
          const answer=observed.metrics.find(m=>m.name==='first_answer_audio');
          if(!answer)throw new Error('NO_EFFECTIVE_AUDIO');
          sample.generation_id=answer.generation_id;sample.effective_answer_ms=answer.at-sample.input.speech_end_ms;
          sample.file_end_to_answer_ms=answer.at-sample.input.scheduled_file_end_ms;
          await page.waitForFunction(before=>evalState.notices.slice(before.notices).some(n=>n.type==='playback.drained')||evalState.failures.length,before,{timeout:23000});
          await page.waitForFunction(()=>performance.now()-evalState.lastRemote>500,{},{timeout:12000});
          const state=await request('/eval/state/'+sid,undefined,true);sample.server=state;
          const segment=state.traces.find(t=>t.kind==='reviewed_segment_received'&&t.generation_id===answer.generation_id);
          const after=state.traces.filter(t=>t.at_ms>=sample.input.speech_end_ms+sample.clock_sync.offset_ms-200);
          const final=after.find(t=>t.kind==='input_final');
          sample.recognized=final?{text:final.text,candidate:final.candidate}:null;
          sample.answer_text=segment?.text;
          const intents=final?.candidate?.intents||[];
          sample.business_valid=fixture.id==='identity'?state.verified&&intents.includes('confirm_identity'):
            fixture.id==='guarantee'?intents.includes('ask_guarantee')&&/不能保证|不能承诺|无法保证/.test(segment?.text||''):
            intents.includes('ask_source')&&/依据|来源/.test(segment?.text||'');
          if(state.effects!==0)throw new Error('UNEXPECTED_BUSINESS_EFFECT');
          sample.status=sample.business_valid?'success':'invalid_business_answer';
        }catch(error){sample.status='failed';sample.failure=String(error.message).slice(0,150);
          try{sample.server=await request('/eval/state/'+sid,undefined,true);}catch{}
        }
        save();console.log(JSON.stringify({call:call.index,case:sample.case,status:sample.status,ms:sample.effective_answer_ms,
          failures:sample.server?.traces.filter(t=>t.kind==='session_metric'&&t.name==='failure').map(t=>t.code)}));
        if(sample.status==='failed')break;
      }
      call.final=await request('/eval/state/'+sid,undefined,true);
    }catch(error){call.failure=String(error.message).slice(0,150);}
    finally{
      call.page_faults=faults;
      try{await page.evaluate(async()=>{await window.client?.close(true);await evalState.input?.ctx.close();await evalState.remoteContext?.close();});}catch{}
      if(sid){try{await page.evaluate(async sid=>fetch('/api/v1/sessions/'+sid+'/end',{method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'zhiyu'},body:JSON.stringify({reason:'user_ended'})}),sid);}catch{}}
      call.finished_at=new Date().toISOString();save();await context.close();
    }
    if(report.calls.slice(-2).length===2&&report.calls.slice(-2).every(c=>c.failure||!c.samples.some(i=>report.samples[i].status==='success'))){
      report.stopped_reason='two_consecutive_calls_without_valid_answers';save();break;}
    await delay(500);
  }
}
evaluate().catch(error=>{report.harness_failure=String(error.message).slice(0,150);save();process.exitCode=1;})
  .finally(async()=>{clearTimeout(watchdog);await browser?.close();console.log(JSON.stringify({saved:output,valid:report.samples.filter(s=>s.status==='success').length,total:report.samples.length,calls:report.calls.length}));});
