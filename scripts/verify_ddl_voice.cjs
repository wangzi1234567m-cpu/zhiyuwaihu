/* Actual workbench -> WebRTC -> native understanding -> v3 review -> streamed TTS.
   Fixed synthetic microphone, decoded remote PCM, spoken barge-in and reconnect.
   No text turns, no injected subtitles, no business write permission. */
const fs=require('node:fs'),path=require('node:path'),{execFileSync}=require('node:child_process');
const root=path.resolve(__dirname,'..'),{chromium,expect}=require(path.join(root,'frontend/node_modules/@playwright/test'));
const base=process.env.E2E_URL||'http://localhost:13000',fixtures=process.env.WORKBENCH_AUDIO_DIR;
if(!fixtures||!process.argv.includes('--run-real-voice'))throw new Error('Explicit real voice flag and WORKBENCH_AUDIO_DIR required');
const out=path.join(root,'.cache/ddl');fs.mkdirSync(out,{recursive:true});
const report={commit:execFileSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8'}).trim(),knowledge_version:'workbench-reviewed-20261125-v3',
  phase:'init',passed:false,network:[],page_errors:[],observations:[],physical_microphone_tested:false,voice_auto_close_accepted:false};
const save=()=>fs.writeFileSync(path.join(out,'v3-real-voice.json'),JSON.stringify(report,null,2)+'\n');
let browser,page;
(async()=>{
 browser=await chromium.launch({headless:true,args:['--autoplay-policy=no-user-gesture-required','--disable-background-timer-throttling']});
 page=await browser.newPage({viewport:{width:1440,height:1000}});
 page.on('pageerror',e=>report.page_errors.push(e.message));
 page.on('response',r=>{const u=new URL(r.url());if(/realtime\/(ice|offer)/.test(u.pathname)||u.pathname.endsWith('/sessions'))report.network.push({path:u.pathname,status:r.status()});});
 await page.addInitScript(()=>{
   window.smoke={notices:[],remote:[],metrics:[]};
   window.addEventListener('zhiyu-realtime-metric',e=>smoke.metrics.push({...e.detail,at:performance.now()}));
   const Native=window.RTCPeerConnection;
   window.RTCPeerConnection=class extends Native{constructor(...args){super(...args);this.addEventListener('track',async e=>{
     const ctx=new AudioContext();await ctx.resume();await ctx.audioWorklet.addModule('/realtime-meter.js');
     const source=ctx.createMediaStreamSource(new MediaStream([e.track])),node=new AudioWorkletNode(ctx,'realtime-meter'),zero=ctx.createGain();zero.gain.value=0;
     node.port.onmessage=e=>{if(Number(e.data.rms)>.004)smoke.remote.push(performance.now());};source.connect(node);node.connect(zero);zero.connect(ctx.destination);smoke.remoteContext=ctx;
   });}};
   navigator.mediaDevices.getUserMedia=async()=>{
     const ctx=new AudioContext({sampleRate:48000});await ctx.resume();const dest=ctx.createMediaStreamDestination(),keep=ctx.createConstantSource(),zero=ctx.createGain();zero.gain.value=0;keep.connect(zero);zero.connect(dest);keep.start();Object.assign(smoke,{ctx,dest});return dest.stream;
   };
 });
 const notices=[];
 page.on('websocket',ws=>ws.on('framereceived',frame=>{try{notices.push({...JSON.parse(frame.payload),at:Date.now()});}catch{}}));
 async function stage(name){report.phase=name;save();}
 async function waitNotice(type,count){await expect.poll(()=>notices.filter(n=>n.type===type).length,{timeout:45000}).toBeGreaterThanOrEqual(count);}
 async function play(file){
   const bytes=fs.readFileSync(file).toString('base64');
   await page.evaluate(async encoded=>{const {ctx,dest}=smoke,raw=Uint8Array.from(atob(encoded),c=>c.charCodeAt(0)),buffer=await ctx.decodeAudioData(raw.buffer),source=ctx.createBufferSource();source.buffer=buffer;source.connect(dest);
     await new Promise(resolve=>{source.onended=resolve;source.start(ctx.currentTime+.05);});},bytes);
 }
 await stage('staff_login');await page.goto(base);await page.getByLabel('工作人员密码').fill(process.env.STAFF_PASSWORD||'staff-local-only');await page.getByRole('button',{name:'登录',exact:true}).click();
 await expect(page.getByTestId('task-00000000-0000-0000-0000-000000000001')).toBeEnabled();
 const caps=await(await page.request.get(base+'/api/v1/capabilities')).json();report.capabilities=caps;expect(caps.realtime_webrtc).toBe(true);
 await page.getByTestId('task-00000000-0000-0000-0000-000000000001').click();
 await page.locator('.developer-settings>summary').click();await page.getByLabel('交互方式').selectOption('native_webrtc');
 // Explicitly create a new compatible session; retain old text/voice history in DB.
 await page.getByRole('button',{name:'新建联调会话',exact:true}).click();await page.locator('.developer-settings>summary').click();
 const sid=await page.evaluate(()=>localStorage.getItem('zhiyu.session'));report.session_id=sid;
 const snapshot=await(await page.request.get(base+'/api/v1/staff/sessions/'+sid)).json();expect(snapshot.mode).toBe('voice');expect(snapshot.voice_transport).toBe('native_webrtc');
 const task=await(await page.request.get(base+'/api/v1/staff/applications/'+snapshot.application_id)).json();expect(task.knowledge_version).toBe(report.knowledge_version);
 await stage('rtc_start_and_opening');await page.getByRole('button',{name:'开始通话',exact:true}).click();
 await expect(page.getByText('麦克风已连接 · 当前安静',{exact:true})).toBeVisible({timeout:30000});await expect(page.getByRole('log')).toContainText('模拟商户服务AI',{timeout:30000});await waitNotice('playback.drained',1);
 await expect.poll(()=>page.evaluate(()=>smoke.remote.length),{timeout:10000}).toBeGreaterThan(0);report.opening_remote_pcm=true;
 await stage('natural_identity');const level=page.waitForFunction(()=>Number(document.querySelector('meter')?.getAttribute('value'))>.01,null,{timeout:20000});await play(path.join(fixtures,'merchant-1.trimmed.wav'));await level;
 await expect(page.getByRole('log').locator('article.user')).toContainText('演示商家',{timeout:25000});await expect(page.getByRole('log')).toContainText('被授权方盖章',{timeout:25000});await waitNotice('playback.drained',2);
 await stage('reviewed_qa_and_spoken_barge_in');const stopBefore=notices.filter(n=>n.type==='playback.stop').length;const remoteBefore=await page.evaluate(()=>smoke.remote.length);
 await play(path.join(out,'audio/merchant-contact-upload.wav'));await expect(page.getByRole('log')).toContainText('授权书位置上传',{timeout:25000});
 await expect.poll(()=>page.evaluate(()=>smoke.remote.length),{timeout:15000}).toBeGreaterThan(remoteBefore+4);
 await play(path.join(fixtures,'merchant-2.trimmed.wav'));await expect.poll(()=>notices.filter(n=>n.type==='playback.stop').length,{timeout:15000}).toBeGreaterThan(stopBefore);
 await expect(page.getByRole('log')).toContainText('不能保证',{timeout:25000});report.spoken_barge_in=true;report.reviewed_qa=true;
 const drainedBefore=notices.filter(n=>n.type==='playback.drained').length;await waitNotice('playback.drained',drainedBefore+1);
 await page.screenshot({path:path.join(out,'v3-real-voice.png'),fullPage:true});
 await stage('active_session_reconnect');await page.reload();await expect(page.getByRole('log')).toContainText('不能保证');await expect(page.getByRole('button',{name:'开始通话',exact:true})).toBeEnabled();
 await page.waitForTimeout(1200);await page.getByRole('button',{name:'开始通话',exact:true}).click();await expect(page.getByText('麦克风已连接 · 当前安静',{exact:true})).toBeVisible({timeout:30000});
 await play(path.join(fixtures,'merchant-source.trimmed.wav'));await expect(page.getByRole('log')).toContainText('已审核依据',{timeout:25000});
 await expect.poll(()=>page.evaluate(()=>smoke.remote.length),{timeout:20000}).toBeGreaterThan(0);report.reconnect_remote_pcm=true;
 await page.getByRole('button',{name:'结束通话',exact:true}).click();await page.reload();await expect(page.getByRole('log')).toContainText('不能保证');await expect(page.getByTestId('written-execute')).toBeDisabled();
 report.final_snapshot=await(await page.request.get(base+'/api/v1/staff/sessions/'+sid)).json();report.observations=notices.map(n=>({type:n.type,generation_id:n.generation_id,code:n.code}));
 expect(report.final_snapshot.operations).toHaveLength(0);expect(report.page_errors).toHaveLength(0);report.phase='complete';report.passed=true;save();
 console.log(JSON.stringify({passed:true,session_id:sid,knowledge:report.knowledge_version,spoken_barge_in:true,reconnect:true,voice_operations:0}));
})().catch(e=>{report.failure=e.message;save();console.error(JSON.stringify({phase:report.phase,failure:e.message}));process.exitCode=1;})
 .finally(async()=>{save();if(report.session_id&&page)try{await page.request.post(base+'/api/v1/sessions/'+report.session_id+'/end',{headers:{'X-Requested-With':'zhiyu'},data:{reason:'user_ended'}});}catch{}await browser?.close();});
