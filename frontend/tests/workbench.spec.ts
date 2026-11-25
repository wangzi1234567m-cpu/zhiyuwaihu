import {test,expect} from '@playwright/test';
import {mockStaff} from './staff-fixtures';

const id=(n:number)=>`00000000-0000-0000-0000-${String(n).padStart(12,'0')}`;
test('staff workbench uses backend channels, distinguishes hangup from completion and restores facts',async({page})=>{
  let state='QA',ended=false;
  const snapshots=()=>[1,2].map(n=>({session_id:id(n),application_id:id(n),case_revision:1,state:n===1?state:'CASE_READY',
    state_revision:3,last_event_seq:10,route:n===1?'phone':'jingmai',mode:'text',verified:false,ended:n===1&&ended,busy:false,
    operations:n===1?[{operation_id:id(11),kind:'reject_application',status:'unknown'}]:[],metrics:[],source_refs:[],
    messages:n===1?[{message_id:'m1',role:'assistant',text:'请确认您是该商家联系人。',status:'completed'}]:[]}));
  await page.addInitScript(ids=>{if(!localStorage.getItem('zhiyu.session'))localStorage.setItem('zhiyu.session',ids[0]);if(!localStorage.getItem('zhiyu.workbench.sessions.v1'))localStorage.setItem('zhiyu.workbench.sessions.v1',JSON.stringify(Object.fromEntries(ids.map(i=>[i,i]))));},[id(1),id(2)]);
  await mockStaff(page,snapshots);
  await page.route('**/api/v1/capabilities',r=>r.fulfill({json:{voice:false,realtime_webrtc:false,routing_preview:false}}));
  await page.route('**/events?*',r=>r.fulfill({contentType:'text/event-stream',body:': ready\n\n'}));
  await page.route('**/api/v1/sessions/*',async r=>{
    const n=r.request().url().endsWith(id(1))?0:1;
    await r.fulfill({json:snapshots()[n]});
  });
  await page.route('**/api/v1/sessions/*/end',async r=>{ended=true;state='CLOSING';await r.fulfill({json:snapshots()[0]});});
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('/');
  await expect(page.getByLabel('审核任务队列').getByRole('button',{name:/合成商家/})).toHaveCount(2);
  await expect(page.getByRole('log')).toContainText('请确认');
  await expect(page.getByTestId('operation-status')).toContainText('结果待查证');
  await expect(page.getByTestId('completed-count')).toHaveText('0');
  await expect(page.getByTestId('writeback')).toHaveCount(0);
  await expect(page.getByLabel('审核事实与整改建议')).toContainText('工作人员可见的原因');
  await page.getByRole('button',{name:'结束通话',exact:true}).click();
  await expect(page.getByLabel('书面驳回与结果回写')).toContainText('尚未形成完整关单');
  await expect(page.getByTestId('completed-count')).toHaveText('0');
  await page.getByRole('button',{name:'京麦',exact:true}).click();
  await expect(page.getByLabel('审核任务队列').getByRole('button',{name:/合成商家/})).toHaveCount(1);
  await page.getByTestId(`task-${id(2)}`).click();
  await expect(page.getByLabel('书面驳回与结果回写')).toBeVisible();
  await expect(page.getByLabel('电话告知交互')).toHaveCount(0);
  await expect(page.getByTestId('written-execute')).toBeDisabled();
  await page.reload();
  await expect(page.getByLabel('系统渠道判定')).toContainText('京麦');
  await page.setViewportSize({width:1440,height:1000});
  await page.screenshot({path:test.info().outputPath('jingmai-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  await page.screenshot({path:test.info().outputPath('jingmai-mobile.png'),fullPage:true});
  expect(errors).toEqual([]);
});

test('failed read projection stays unavailable and never creates sessions',async({page})=>{
  let creates=0;
  await page.route('**/api/v1/staff/applications',r=>r.fulfill({status:503,json:{code:'UNAVAILABLE',message:'读取超时，请稍后重试'}}));
  await page.route('**/api/v1/capabilities',r=>r.fulfill({json:{voice:false,realtime_webrtc:false}}));
  await page.route('**/api/v1/staff/support-catalog',r=>r.fulfill({json:{}}));
  await page.route('**/api/v1/sessions',r=>{creates++;return r.fulfill({status:503,json:{}});});
  await page.goto('/');
  await expect(page.locator('.notice.error[role=alert]')).toContainText('读取超时');
  await expect(page.getByRole('button',{name:'登录',exact:true})).toBeEnabled();
  expect(creates).toBe(0);
});
