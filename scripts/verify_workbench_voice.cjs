const path=require('node:path'),fs=require('node:fs');
const root=path.resolve(__dirname,'..');
const audioRoot=process.env.WORKBENCH_AUDIO_DIR;if(!audioRoot)throw new Error('Set WORKBENCH_AUDIO_DIR to the fixed synthetic WAV directory');
const output=path.join(root,'.cache/workbench');fs.mkdirSync(output,{recursive:true});
const {chromium,expect}=require(path.join(root,'frontend/node_modules/@playwright/test'));
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--autoplay-policy=no-user-gesture-required','--disable-background-timer-throttling']});
 const page=await browser.newPage();const types=[],errors=[];
 page.on('pageerror',e=>errors.push(e.name));
 page.on('websocket',socket=>socket.on('framereceived',event=>{try{types.push(JSON.parse(event.payload).type);}catch{}}));
 const wait=async predicate=>{const end=Date.now()+30000;while(!predicate()&&Date.now()<end)await page.waitForTimeout(100);if(!predicate())throw new Error('Control deadline: '+types.join(','));};
 try{
  await page.addInitScript(()=>{
   navigator.mediaDevices.getUserMedia=async()=>{
    const ctx=new AudioContext({sampleRate:48000});await ctx.resume();const dest=ctx.createMediaStreamDestination();
    const source=ctx.createConstantSource(),zero=ctx.createGain();zero.gain.value=0;source.connect(zero);zero.connect(dest);source.start();
    window.smoke={ctx,dest};return dest.stream;
   };
  });
  await page.goto(process.env.E2E_URL??'http://localhost:3000');
  await page.getByLabel('工作人员密码').fill(process.env.STAFF_PASSWORD??'staff-local-only');
  await page.getByRole('button',{name:'登录',exact:true}).click();
  await page.getByRole('button',{name:'进入电话任务',exact:true}).click();
  await expect(page.getByRole('button',{name:'开始通话',exact:true})).toBeEnabled();

  await page.getByRole('button',{name:'开始通话',exact:true}).click();
  await expect(page.getByText('麦克风已连接 · 当前安静',{exact:true})).toBeVisible({timeout:30000});
  await expect(page.getByRole('log')).toContainText('联系人',{timeout:20000});
  await wait(()=>types.filter(t=>t==='playback.drained').length>=1);
  await page.waitForTimeout(600);
  async function play(name){
   const encoded=fs.readFileSync(path.join(audioRoot,name)).toString('base64');
   await page.evaluate(async encoded=>{
    const data=Uint8Array.from(atob(encoded),c=>c.charCodeAt(0)),{ctx,dest}=window.smoke;
    const buffer=await ctx.decodeAudioData(data.buffer),source=ctx.createBufferSource();source.buffer=buffer;source.connect(dest);
    await new Promise(resolve=>{source.onended=resolve;source.start(ctx.currentTime+.05);});
   },encoded);
  }
  const levelPromise=page.waitForFunction(()=>Number(document.querySelector('meter')?.getAttribute('value'))>.01,null,{timeout:15000}); await play('merchant-1.trimmed.wav'); await levelPromise;
  await expect(page.getByRole('log').locator('article.user')).toContainText(/我是演示商家[一1]/,{timeout:20000});
  await expect(page.getByRole('log')).toContainText('授权书缺少',{timeout:20000});
  await wait(()=>types.filter(t=>t==='playback.drained').length>=2);
  await page.waitForTimeout(600);
  await play('merchant-2.trimmed.wav');
  await expect(page.getByRole('log')).toContainText('不能保证',{timeout:20000});
  await page.screenshot({path:path.join(output,'workbench-voice-desktop.png'),fullPage:true});
  await page.getByRole('button',{name:'打断播报',exact:true}).click();
  await expect(page.getByRole('log')).toContainText('授权书缺少');
  await page.getByRole('button',{name:'结束通话',exact:true}).click();
  await page.evaluate(()=>window.smoke.ctx.close());
  await page.reload();
  await expect(page.getByRole('log')).toContainText(/我是演示商家[一1]/);
  await expect(page.getByRole('log')).toContainText('不能保证');
  await page.setViewportSize({width:390,height:844});
  if(!await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth))throw new Error('Mobile horizontal overflow');
  await page.screenshot({path:path.join(output,'workbench-voice-mobile.png'),fullPage:true});
  await expect(page.getByTestId('written-execute')).toBeDisabled();
  if(errors.length)throw new Error('Page errors: '+errors.join(','));
  fs.writeFileSync(path.join(output,'workbench-smoke.json'),JSON.stringify({frontend_branch:'codex/workbench-business-backend',login:true,real_rtc:true,
   real_native_understanding:true,real_tts:true,opening_subtitle:true,user_final_transcript:true,reviewed_reply_subtitle:true,
   guarantee_answer:true,interrupt_keeps_history:true,reload_restores_history:true,mobile_no_overflow:true,page_errors:errors,
   microphone_rms_detected:true,synthetic_audio:true,real_voice_business_closed:false,latency_p50_measured:false,
   interim_asr_supported:false,voice_keyboard_input_supported:false,types},null,2));
  console.log(JSON.stringify({passed:true,opening_subtitle:true,user_transcript:true,reviewed_subtitles:true,reload:true,mobile:true,errors}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
