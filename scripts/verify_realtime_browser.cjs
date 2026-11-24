/* Offline browser/aiortc adapter check. No reusable business Mock or cloud evaluation. */
const fs=require('node:fs');
const path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium}=require('../frontend/node_modules/@playwright/test');
const ts=require('../frontend/node_modules/typescript');
const root=path.resolve(__dirname,'..');
const python=process.env.REALTIME_TEST_PYTHON||'python';
const base='http://127.0.0.1:18764';
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let server,browser;
const watchdog=setTimeout(()=>{console.error('Browser adapter test deadline');process.exitCode=1;void browser?.close();server?.kill();},180000);

(async()=>{
  server=spawn(python,['-m','uvicorn','realtime_browser_app:app','--host','127.0.0.1','--port','18764','--no-access-log','--lifespan','off'],{
    cwd:root,windowsHide:true,env:{...process.env,PYTHONPATH:[path.join(root,'backend'),path.join(root,'backend/tests')].join(path.delimiter),
      CLOUD_CALLS_ENABLED:'false',VOICE_ENABLED:'false',VOICE_WRITES_ENABLED:'false',MODEL_PROVIDER:'fake'},stdio:['ignore','pipe','pipe']});
  let logs='';server.stderr.on('data',chunk=>{logs+=String(chunk);});
  let state;
  for(let i=0;i<100;i++){
    if(server.exitCode!==null)throw new Error(`Loopback server exited: ${logs}`);
    try{state=await (await fetch(base+'/test/state')).json();break;}catch{await sleep(100);}
  }
  if(!state)throw new Error('Loopback server not ready');
  const source=fs.readFileSync(path.join(root,'frontend/lib/realtime.ts'),'utf8').replace('export class RealtimeVoice','window.RealtimeVoice = class RealtimeVoice');
  const js=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2020,module:ts.ModuleKind.ES2022}}).outputText.replace(/export\s*\{\s*\};?/g,'');
  const worklet=fs.readFileSync(path.join(root,'frontend/public/realtime-meter.js'),'utf8');
  browser=await chromium.launch({headless:true,args:['--autoplay-policy=no-user-gesture-required']});
  const context=await browser.newContext();
  const page=await context.newPage();
  await page.route('**/realtime-meter.js',route=>route.fulfill({contentType:'text/javascript',body:worklet}));
  const errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto(base);
  await page.addScriptTag({content:js});
  await page.evaluate(()=>{
    window.statuses=[];window.metrics=[];window.failures=[];
    window.addEventListener('zhiyu-realtime-metric',event=>window.metrics.push(event.detail));
    // Synthetic browser microphone; real RTP is still negotiated and decoded.
    navigator.mediaDevices.getUserMedia=async()=>{
      const ctx=new AudioContext();await ctx.resume();window.inputContext=ctx;
      const source=ctx.createOscillator(),gain=ctx.createGain(),dest=ctx.createMediaStreamDestination();
      gain.gain.value=0;source.connect(gain);gain.connect(dest);source.start();
      window.inputGain=gain;
      return dest.stream;
    };
  });
  for(let i=0;i<20;i++){
    await page.evaluate(async sid=>{
      window.statuses=[];
      window.client=new window.RealtimeVoice(sid,{status:t=>window.statuses.push(t),error:t=>window.failures.push(t)});
      await window.client.start(1);
    },state.sid);
    await page.waitForFunction(()=>window.statuses.some(t=>t.includes('已连接')),{},{timeout:10000});
    if(i===0){
      await page.evaluate(()=>window.dispatchEvent(new CustomEvent('zhiyu-realtime-input-ended')));
      await fetch(base+'/test/reply',{method:'POST'});
      await page.waitForFunction(()=>window.metrics.some(m=>m.name==='first_answer_audio'),{},{timeout:10000});
      await page.evaluate(()=>window.client.interrupt());
      await page.waitForFunction(()=>window.metrics.some(m=>m.name==='interrupt_stopped'),{},{timeout:5000});
      await page.evaluate(()=>window.dispatchEvent(new CustomEvent('zhiyu-realtime-input-ended')));
      await fetch(base+'/test/reply',{method:'POST'});
      await page.waitForFunction(()=>window.metrics.filter(m=>m.name==='first_answer_audio').length===2,{},{timeout:10000});
      // Local worklet speech activity automatically mutes output before server ack.
      await page.evaluate(()=>{window.inputGain.gain.value=.15;});
      await page.waitForFunction(()=>window.metrics.filter(m=>m.name==='interrupt_stopped').length===2,{},{timeout:5000});
    }
    await page.evaluate(async()=>{
      await window.client.close(true);await window.client.close(true);
      if(window.client.pc.connectionState!=='closed'||window.client.nodes.length||
        window.client.stream.getTracks().some(t=>t.readyState!=='ended')||window.client.context.state!=='closed')throw new Error('browser resource leak');
      await window.inputContext.close();
    });
    for(let j=0;j<40;j++){
      state=await(await fetch(base+'/test/state')).json();
      if(state.sessions===0)break;
      await sleep(50);
    }
    if(state.sessions)throw new Error('server registry leak');
  }
  const observations=await page.evaluate(()=>({metrics:window.metrics,failures:window.failures}));
  if(errors.length||observations.failures.length)throw new Error(JSON.stringify({errors,...observations}));
  if(!state.input_frames)throw new Error('no inbound audio frames');
  if(state.evidence_refs.some(Boolean))throw new Error('browser adapter test must not create trusted proof');
  const report={test:'loopback_browser_adapter',cycles:20,cloud_calls:0,passed:true,
    input_frames:state.input_frames,observations:observations.metrics,teammate_mock_verified:false,
    effective_answer_p50_ms:null,under_2_seconds_accepted:false,
    note:'Synthetic adapter observations only; no real-model or business latency claim.'};
  const output=path.join(root,'.cache','realtime-browser.json');
  fs.mkdirSync(path.dirname(output),{recursive:true});fs.writeFileSync(output,JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify(report));
})().catch(error=>{console.error(error.message);process.exitCode=1;}).finally(async()=>{
  clearTimeout(watchdog);
  await browser?.close();server?.kill();
});
