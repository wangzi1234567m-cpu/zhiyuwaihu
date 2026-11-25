/* Final real workbench recording. Synthetic microphone -> real cloud/RTC; no voice write bypass. */
const fs=require('node:fs'),path=require('node:path'),{execFileSync,spawnSync}=require('node:child_process');
const root=path.resolve(__dirname,'..'),workspace=process.env.LATENCY_WORKSPACE||root;
const {chromium,expect}=require('../frontend/node_modules/@playwright/test');
if(!process.argv.includes('--run-real-demo')||process.env.LATENCY_PLAN_AUTHORIZED!=='20261125-26-130-200')throw new Error('Explicit shared-budget real demo authorization required');
const base=process.env.E2E_URL||'http://127.0.0.1:18765',parent=path.join(root,'.cache/ddl-plan');
let attempt=1;while(fs.existsSync(path.join(parent,`demo-attempt-${String(attempt).padStart(2,'0')}`)))attempt++;
const out=path.join(parent,`demo-attempt-${String(attempt).padStart(2,'0')}`);fs.mkdirSync(out,{recursive:true});
const report={attempt,commit:execFileSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8'}).trim(),passed:false,synthetic_microphone:true,real_cloud_audio:true,voice_auto_close:false,voice_operations:0,notices:[],page_errors:[]};
const save=()=>{fs.writeFileSync(path.join(out,'report.json'),JSON.stringify(report,null,2));fs.writeFileSync(path.join(parent,'final-demo.json'),JSON.stringify({...report,attempt_report:path.relative(root,path.join(out,'report.json')).replaceAll('\\','/')},null,2));};
let browser,page,videoFile,videoStart;
(async()=>{
 browser=await chromium.launch({headless:true,args:['--autoplay-policy=no-user-gesture-required','--disable-background-timer-throttling']});
 videoStart=Date.now();page=await browser.newPage({viewport:{width:1440,height:1000},recordVideo:{dir:out,size:{width:1440,height:1000}}});
 videoFile=await page.video().path();
 page.on('pageerror',e=>report.page_errors.push(e.message));
 page.on('websocket',ws=>ws.on('framereceived',event=>{try{report.notices.push({...JSON.parse(event.payload),observed_at:Date.now()});}catch{}}));
 await page.addInitScript(()=>{
   window.recording={remote:[],muted:true,parts:[]};const r=window.recording;
   function mix(){if(!r.mix){r.mix=new AudioContext();r.destination=r.mix.createMediaStreamDestination();r.recorder=new MediaRecorder(r.destination.stream,{mimeType:'audio/webm;codecs=opus'});r.recorder.ondataavailable=e=>{if(e.data.size)r.parts.push(e.data);};r.started=Date.now();r.recorder.start(1000);void r.mix.resume();}return r;}
   const descriptor=Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype,'muted');
   Object.defineProperty(HTMLMediaElement.prototype,'muted',{...descriptor,set(value){descriptor.set.call(this,value);r.muted=Boolean(value);if(r.remoteGain)r.remoteGain.gain.value=r.muted?0:1;}});
   const Native=window.RTCPeerConnection;
   window.RTCPeerConnection=class extends Native{constructor(...args){super(...args);this.addEventListener('track',async e=>{
     const r=mix(),source=r.mix.createMediaStreamSource(new MediaStream([e.track]));r.remoteGain=r.mix.createGain();r.remoteGain.gain.value=r.muted?0:1;source.connect(r.remoteGain);r.remoteGain.connect(r.destination);
     const ctx=new AudioContext();await ctx.resume();await ctx.audioWorklet.addModule('/realtime-meter.js');const raw=ctx.createMediaStreamSource(new MediaStream([e.track])),meter=new AudioWorkletNode(ctx,'realtime-meter'),zero=ctx.createGain();zero.gain.value=0;
     meter.port.onmessage=e=>{if(e.data.rms>.004)r.remote.push(performance.now());};raw.connect(meter);meter.connect(zero);zero.connect(ctx.destination);r.remoteContext=ctx;
   });}};
   navigator.mediaDevices.getUserMedia=async()=>{const r=mix(),ctx=new AudioContext({sampleRate:48000});await ctx.resume();const dest=ctx.createMediaStreamDestination(),keep=ctx.createConstantSource(),zero=ctx.createGain();zero.gain.value=0;keep.connect(zero);zero.connect(dest);keep.start();r.mix.createMediaStreamSource(dest.stream).connect(r.destination);Object.assign(r,{ctx,dest});return dest.stream;};
 });
 await page.goto(base);await page.getByLabel('工作人员密码').fill('staff-local-only');await page.getByRole('button',{name:'登录',exact:true}).click();
 await expect(page.getByTestId('task-00000000-0000-0000-0000-000000000001')).toBeEnabled();
 await page.evaluate(()=>{const note=document.createElement('div');note.textContent='真实云端 RTC 录制｜固定合成麦克风｜业务为 Mock｜语音自动关单未开放';Object.assign(note.style,{position:'fixed',bottom:'0',left:'0',right:'0',background:'#153c2e',color:'white',padding:'9px',zIndex:'99999',fontSize:'14px',textAlign:'center'});document.body.append(note);});
 await page.getByTestId('task-00000000-0000-0000-0000-000000000001').click();await page.locator('.developer-settings>summary').click();await page.getByLabel('交互方式').selectOption('native_webrtc');await page.getByRole('button',{name:'新建联调会话',exact:true}).click();await page.locator('.developer-settings>summary').click();
 report.session_id=await page.evaluate(()=>localStorage.getItem('zhiyu.session'));save();
 await page.getByRole('button',{name:'开始通话',exact:true}).click();
 const waitDrain=async count=>expect.poll(()=>report.notices.filter(n=>n.type==='playback.drained').length,{timeout:60000}).toBeGreaterThanOrEqual(count);
 await waitDrain(1);await page.waitForTimeout(600);
 async function play(name){const encoded=fs.readFileSync(path.join(workspace,'.cache/round1-audio',name)).toString('base64');await page.evaluate(async encoded=>{const {ctx,dest}=recording,raw=Uint8Array.from(atob(encoded),c=>c.charCodeAt(0)),buffer=await ctx.decodeAudioData(raw.buffer),source=ctx.createBufferSource();source.buffer=buffer;source.connect(dest);await new Promise(resolve=>{source.onended=resolve;source.start(ctx.currentTime+.05);});},encoded);}
 await play('merchant-1.trimmed.wav');await expect(page.getByRole('log')).toContainText('被授权方盖章',{timeout:25000});await waitDrain(2);await page.waitForTimeout(600);
 const previous=await page.evaluate(()=>recording.remote.length);await play('merchant-source.trimmed.wav');await expect(page.getByRole('log')).toContainText('已审核依据',{timeout:25000});await expect.poll(()=>page.evaluate(()=>recording.remote.length),{timeout:15000}).toBeGreaterThan(previous+5);
 const stops=report.notices.filter(n=>n.type==='playback.stop').length;await play('merchant-2.trimmed.wav');await expect(page.getByRole('log')).toContainText('不能保证',{timeout:25000});await expect.poll(()=>report.notices.filter(n=>n.type==='playback.stop').length).toBeGreaterThan(stops);
 await waitDrain(3);await page.waitForTimeout(800);report.spoken_barge_in=true;
 report.voice_snapshot=await(await page.request.get(base+'/api/v1/staff/sessions/'+report.session_id)).json();expect(report.voice_snapshot.verified).toBe(true);expect(report.voice_snapshot.operations).toHaveLength(0);
 await page.screenshot({path:path.join(out,'real-voice.png'),fullPage:true});
 await page.getByRole('button',{name:'结束通话',exact:true}).click();await expect(page.getByTestId('written-execute')).toBeDisabled();
 await page.getByTestId('task-00000000-0000-0000-0000-000000000004').click();await page.getByRole('button',{name:'加载书面预览',exact:true}).click();await expect(page.getByTestId('written-execute')).toBeEnabled();await page.getByTestId('written-execute').click();await expect(page.getByTestId('writeback')).toBeVisible({timeout:15000});
 report.jingmai_snapshot=await(await page.request.get(base+'/api/v1/staff/sessions/'+await page.evaluate(()=>localStorage.getItem('zhiyu.session')))).json();expect(report.jingmai_snapshot.state).toBe('CLOSED');report.mock_jingmai_closed=true;
 await page.screenshot({path:path.join(out,'jingmai-closed.png'),fullPage:true});await page.waitForTimeout(2000);
 const sound=await page.evaluate(async()=>{const r=recording;await new Promise(resolve=>{r.recorder.onstop=resolve;r.recorder.stop();});const data=await new Promise(resolve=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.readAsDataURL(new Blob(r.parts,{type:'audio/webm'}));});return {data,started:r.started};});
 fs.writeFileSync(path.join(out,'actual-audio.webm'),Buffer.from(sound.data.split(',')[1],'base64'));report.audio_offset_ms=sound.started-videoStart;
 expect(report.page_errors).toHaveLength(0);report.passed=true;save();
})().catch(error=>{report.failure=String(error.message).slice(0,500);save();process.exitCode=1;})
.finally(async()=>{
 await browser?.close();if(videoFile)report.screen_video=path.relative(root,videoFile).replaceAll('\\','/');
 if(report.passed){const ffmpeg=path.join(workspace,'.cache/pw-browsers/ffmpeg-1013/ffmpeg-win64.exe'),movie=path.join(out,'zhiyu-final-demo.webm');const result=spawnSync(ffmpeg,['-y','-itsoffset',String(report.audio_offset_ms/1000),'-i',path.join(out,'actual-audio.webm'),'-i',videoFile,'-map','1:v:0','-map','0:a:0','-c','copy','-shortest',movie],{encoding:'utf8'});fs.writeFileSync(path.join(out,'mux.log'),result.stderr||'');report.mux_exit=result.status;if(result.status===0)report.video=path.relative(root,movie).replaceAll('\\','/');else report.passed=false;}
 if(!report.passed)process.exitCode=1;save();console.log(JSON.stringify({passed:report.passed,video:report.video,failure:report.failure,mux_exit:report.mux_exit}));
});