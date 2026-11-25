import {test,expect} from '@playwright/test';
const id=(n:number)=>`00000000-0000-0000-0000-${n.toString(16).padStart(12,'0')}`;
test('reviewed source to guarded written execution, contact routing and durable workbench',async({page})=>{
  test.setTimeout(120000);
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  let creates=0;page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/api/v1/sessions'))creates++;});
  await page.goto('/');await page.getByLabel('工作人员密码').fill(process.env.STAFF_PASSWORD??'staff-local-only');
  await page.getByRole('button',{name:'登录',exact:true}).click();
  await expect(page.getByTestId(`task-${id(11)}`)).toBeEnabled();
  expect(creates).toBe(0);
  await page.getByTestId(`task-${id(2)}`).click();expect(creates).toBe(0);
  async function idle(endpoint:string,click:()=>Promise<void>){
    const pending=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith(endpoint));
    await click();const response=await pending;expect(response.status()).toBe(202);
    const accepted=await response.json(),sid=await page.evaluate(()=>localStorage.getItem('zhiyu.session'));
    await expect.poll(async()=>{const s=await(await page.request.get(`/api/v1/sessions/${sid}`)).json();return s.messages?.find((m:{generation_id:string;role:string})=>m.generation_id===accepted.generation_id&&m.role==='assistant')?.status;},{timeout:10000}).toBe('completed');
  }
  async function execute(){
    await expect(page.getByTestId('written-execute')).toBeEnabled();
    await page.getByTestId('written-execute').dblclick();
    await expect(page.getByTestId('writeback')).toBeVisible();
  }
  for(const n of [1,2,3]){
    await page.getByTestId(`task-${id(n)}`).click();
    await expect(page.getByLabel('审核事实与整改建议')).not.toContainText('接口待提供');
    await page.getByRole('button',{name:'进入电话任务',exact:true}).click();
    await idle('/start',()=>page.getByRole('button',{name:'开始文字联调',exact:true}).click());
    const log=page.getByRole('log');await expect(log).not.toContainText('合成品牌');await expect(log).not.toContainText('输入“');
    for(const text of [`我是演示商家${n}`,'补齐材料一定必过吗']){
      await page.getByLabel('商户回复').fill(text);await idle('/turns',()=>page.getByRole('button',{name:'发送',exact:true}).click());
    }
    await expect(log).toContainText('不能保证');
    if(n===1&&process.env.WORKBENCH_SOURCE_ACCEPTANCE==='1'){for(const text of ['联系方式在哪里上传','品牌方使用固定模板，无法改动']){await page.getByLabel('商户回复').fill(text);await idle('/turns',()=>page.getByRole('button',{name:'发送',exact:true}).click());}await expect(log).toContainText('特殊报备');await expect(log).toContainText('授权书位置上传');}
    if(n===3){await page.getByRole('button',{name:'结束通话',exact:true}).click();await expect(page.getByTestId('completed-count')).toHaveText('2');await execute();}
    else{await page.getByLabel('商户回复').fill('明白了');await idle('/turns',()=>page.getByRole('button',{name:'发送',exact:true}).click());await expect(page.getByTestId('writeback')).toBeVisible();}
    await expect(page.getByTestId('completed-count')).toHaveText(String(n));
    await page.reload();await expect(log).toContainText(`我是演示商家${n}`);
  }
  await page.setViewportSize({width:1440,height:1000});await page.screenshot({path:test.info().outputPath('phone-desktop.png'),fullPage:true});
  for(const n of [4,8,9,10]){
    await page.getByTestId(`task-${id(n)}`).click();await page.getByRole('button',{name:'加载书面预览',exact:true}).click();
    await expect(page.getByLabel('电话告知交互')).toHaveCount(0);await expect(page.getByTestId('written-body')).toContainText('模拟');
    if(n===4)await page.screenshot({path:test.info().outputPath('written-preview.png'),fullPage:true});
    await execute();
  }
  await expect(page.getByTestId('completed-count')).toHaveText('7');
  async function noAnswer(){
    const settings=page.locator('.developer-settings');if(await settings.getAttribute('open')===null)await settings.locator('>summary').click();
    await page.getByRole('button',{name:'记录合成未接通',exact:true}).click();await settings.locator('>summary').click();
  }
  await page.getByTestId(`task-${id(5)}`).click();await page.getByRole('button',{name:'进入电话任务',exact:true}).click();await noAnswer();await execute();
  await page.getByTestId(`task-${id(11)}`).click();await page.getByRole('button',{name:'进入电话任务',exact:true}).click();await noAnswer();
  await expect(page.getByTestId('operation-status')).toContainText('人工跟进');
  await expect(page.getByTestId('writeback')).toBeVisible();
  await expect(page.getByTestId('completed-count')).toHaveText('8');
  await page.locator('.developer-settings>summary').click();await page.getByRole('button',{name:'新建联调会话',exact:true}).click();await page.locator('.developer-settings>summary').click();
  await noAnswer();await execute();await expect(page.getByTestId('completed-count')).toHaveText('9');
  expect(creates).toBe(10);
  await page.reload();await expect(page.getByTestId('completed-count')).toHaveText('9');
  await page.screenshot({path:test.info().outputPath('contact-closed-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  await page.screenshot({path:test.info().outputPath('contact-closed-mobile.png'),fullPage:true});
  expect(errors).toEqual([]);
});
