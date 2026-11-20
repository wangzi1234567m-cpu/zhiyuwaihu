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
  await completeTurn('/start',()=>page.getByRole('button',{name:'开始',exact:true}).click());
  await expect(page.getByText('关单渠道：电话说明')).toBeVisible();
  await expect(page.getByText('原因：模拟授权书缺项')).toHaveCount(0);
  for(const text of ['我是演示商家1','补齐材料一定必过吗','明白了']){
    await page.getByLabel('商户回复').fill(text);
    await completeTurn('/turns',()=>page.getByRole('button',{name:'发送',exact:true}).click());
    if (text === '我是演示商家1') {
      await expect(page.getByText('身份：已核对')).toBeVisible();
      await expect(page.getByText('原因：模拟授权书缺项')).toBeVisible();
    }
    if(text.includes('必过'))await expect(page.getByText(/补齐材料后仍需审核，不能保证通过/)).toBeVisible();
  }
  await expect(page.getByTestId('writeback')).toBeVisible();
  await expect(page.getByTestId('operation-status')).toContainText('模拟处理成功');
  await page.reload();
  await expect(page.getByTestId('writeback')).toBeVisible();
  await expect(page.getByRole('region',{name:'审核依据'})).toBeVisible();
  await expect(page.getByText('语音未配置')).toBeVisible();
  await page.screenshot({path:test.info().outputPath('completed-flow.png'),fullPage:true});
});

test('mock routing previews and written closure', async ({page})=>{
  await page.goto('/');
  await page.getByLabel('演示密码').fill(process.env.DEMO_PASSWORD ?? 'demo-local-only');
  await page.getByRole('button',{name:'登录',exact:true}).click();
  await page.getByRole('button',{name:'关单规则试算'}).click();
  await page.getByLabel('审核场景').selectOption('17');
  await page.getByLabel('同场景错误提交次数').selectOption('2');
  await page.getByLabel('本次提交的联系记录').selectOption('1');
  await expect(page.getByText('创建二次外呼跟进事件',{exact:true})).toBeVisible();
  await page.getByLabel('本次提交的联系记录').selectOption('2');
  await expect(page.getByText('京麦书面告知',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'会话工作台'}).click();
  await page.getByLabel('选择申请单').selectOption('00000000-0000-0000-0000-000000000004');
  await page.getByRole('button',{name:'创建会话'}).click();
  await expect(page.getByText('联系结果：无需电话核验')).toBeVisible();
  await expect(page.getByTestId('writeback')).toBeVisible();
});

test('displays separate baseline and candidate results from an RSI report', async ({page})=>{
  await page.goto('/');
  await page.getByLabel('演示密码').fill(process.env.DEMO_PASSWORD ?? 'demo-local-only');
  await page.getByRole('button',{name:'登录',exact:true}).click();
  await page.getByRole('button',{name:'RSI 评测'}).click();
  await page.getByLabel('评测报告（JSON）').setInputFiles({
    name:'dev.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify({
      arm_summary:{baseline:{runs:4,passed:3},candidate:{runs:4,passed:4}},
    })),
  });
  await expect(page.getByRole('row',{name:/开发集 · 基线/})).toContainText('75.0%');
  await expect(page.getByRole('row',{name:/开发集 · 候选/})).toContainText('100.0%');
});
