import {test,expect} from '@playwright/test';

test('three supported authorization cases close through persisted backend facts',async({page})=>{
  test.setTimeout(90000);
  await page.goto('/');
  await page.getByLabel('演示密码').fill(process.env.DEMO_PASSWORD??'demo-local-only');
  await page.getByRole('button',{name:'登录',exact:true}).click();
  await expect(page.getByTestId('task-00000000-0000-0000-0000-000000000003')).toBeEnabled();
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  async function completeTurn(endpoint:string,click:()=>Promise<void>){
    const pending=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith(endpoint));
    await click();const response=await pending;expect(response.status()).toBe(202);
    const accepted=await response.json();
    const sid=await page.evaluate(()=>localStorage.getItem('zhiyu.session'));
    await expect.poll(async()=>{
      const snapshot=await(await page.request.get(`/api/v1/sessions/${sid}`)).json();
      return snapshot.messages?.find((m:{generation_id:string;role:string})=>m.generation_id===accepted.generation_id&&m.role==='assistant')?.status;
    },{timeout:10000}).toBe('completed');
  }
  for(const n of [1,2,3]){
    await page.getByTestId(`task-00000000-0000-0000-0000-${String(n).padStart(12,'0')}`).click();
    await completeTurn('/start',()=>page.getByRole('button',{name:'开始文字联调',exact:true}).click());
    const log=page.getByRole('log');
    await expect(log).not.toContainText('合成品牌');
    await expect(log).not.toContainText('模拟授权');
    for(const text of [`我是演示商家${n}`,'补齐材料一定必过吗','明白了']){
      await page.getByLabel('商户回复').fill(text);
      await completeTurn('/turns',()=>page.getByRole('button',{name:'发送',exact:true}).click());
    }
    await expect(page.getByTestId('writeback')).toBeVisible();
    await expect(page.getByTestId('completed-count')).toHaveText(String(n));
    await page.reload();await expect(log).toContainText('明白了');
  }
  await page.setViewportSize({width:1440,height:1000});
  await page.screenshot({path:test.info().outputPath('workbench-phone-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  await page.screenshot({path:test.info().outputPath('workbench-phone-mobile.png'),fullPage:true});
  await page.getByRole('button',{name:'京麦',exact:true}).click();
  await page.getByTestId('task-00000000-0000-0000-0000-000000000004').click();
  await expect(page.getByLabel('系统渠道判定')).toContainText('京麦');
  await expect(page.getByLabel('电话告知交互')).toHaveCount(0);
  await expect(page.getByTestId('written-execute')).toBeDisabled();
  await page.setViewportSize({width:1440,height:1000});
  await page.screenshot({path:test.info().outputPath('workbench-jingmai-desktop.png'),fullPage:true});
  await page.getByText('开发设置',{exact:true}).click();
  await page.getByText('未发布场景规则试算',{exact:true}).click();
  const preview=page.locator('.routing');
  await preview.getByLabel('审核场景').selectOption('17');
  await preview.getByLabel('本次提交的联系记录').selectOption('1');
  await expect(preview.locator('.decision>strong')).toHaveText('京麦书面告知');
  await preview.getByLabel('同场景错误提交次数').selectOption('2');
  await preview.getByLabel('本次提交的联系记录').selectOption('1');
  await expect(preview.locator('.decision>strong')).toHaveText('创建二次外呼跟进事件');
  await preview.getByLabel('本次提交的联系记录').selectOption('2');
  await expect(preview.locator('.decision>strong')).toHaveText('京麦书面告知');
  await expect(page.getByTestId('completed-count')).toHaveText('3');
  expect(errors).toEqual([]);
});
