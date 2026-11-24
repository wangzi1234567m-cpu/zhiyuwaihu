import {test,expect} from '@playwright/test';
test.afterEach(async({page},info)=>{
  if(info.status===info.expectedStatus)return;
  const sid=await page.evaluate(()=>localStorage.getItem('zhiyu.session')).catch(()=>null);
  if(sid){
    const response=await page.request.get(`/api/v1/sessions/${sid}`);
    await info.attach('backend-session-snapshot',{body:await response.body(),contentType:'application/json'});
  }
});
test('three text rounds, writeback and reload',async({page})=>{
  async function completeTurn(endpoint:string,click:()=>Promise<void>){
    const pending=page.waitForResponse(r=>r.request().method()==='POST' && r.url().endsWith(endpoint));
    await click();
    const response=await pending;
    expect(response.status()).toBe(202);
    const accepted=await response.json();
    const sid=await page.evaluate(()=>localStorage.getItem('zhiyu.session'));
    await expect.poll(async()=>{
      const snapshot=await (await page.request.get(`/api/v1/sessions/${sid}`)).json();
      const reply=snapshot.messages?.find((m:{generation_id:string;role:string})=>m.generation_id===accepted.generation_id && m.role==='assistant');
      return reply?.status;
    },{message:'Wait for the accepted generation to complete',timeout:10000}).toBe('completed');
  }
  await page.goto('/');
  await page.getByLabel('演示密码').fill(process.env.DEMO_PASSWORD ?? 'demo-local-only');
  await page.getByRole('button',{name:'登录',exact:true}).click();
  await page.getByRole('button',{name:'创建会话'}).click();
  await completeTurn('/start',()=>page.getByRole('button',{name:'开始通话',exact:true}).click());
  for(const text of ['我是演示商家1','补齐材料一定必过吗','明白了']){
    await page.getByLabel('商户回复').fill(text);
    await completeTurn('/turns',()=>page.getByRole('button',{name:'发送',exact:true}).click());
    if(text==='我是演示商家1')await expect(page.getByRole('log')).toContainText(text);
    if(text.includes('必过'))await expect(page.getByText('补齐材料后仍需审核，不能保证通过。')).toBeVisible();
  }
  const sid=await page.evaluate(()=>localStorage.getItem('zhiyu.session'));
  await expect.poll(async()=>{const snapshot=await (await page.request.get(`/api/v1/sessions/${sid}`)).json();return snapshot.state;}).toBe('CLOSED');
  await page.reload();
  await expect(page.getByRole('log')).toContainText('明白了');
  await expect(page.locator('header').getByText('语音未配置')).toBeVisible();
  await page.screenshot({path:test.info().outputPath('completed-flow.png'),fullPage:true});
});
