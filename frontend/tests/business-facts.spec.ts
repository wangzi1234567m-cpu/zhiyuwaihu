import {test,expect} from '@playwright/test';
import {mockStaff} from './staff-fixtures';

test('unknown and manual-review backend states remain incomplete',async({page})=>{
  const id='00000000-0000-0000-0000-000000000001';
  await page.addInitScript(sid=>localStorage.setItem('zhiyu.session',sid),id);
  await page.route('**/api/v1/capabilities',r=>r.fulfill({json:{voice:false,realtime_webrtc:false}}));
  await page.route('**/events?*',r=>r.fulfill({contentType:'text/event-stream',body:': ready\n\n'}));
  await mockStaff(page,()=>[{session_id:id,application_id:id,case_revision:1,state:'MANUAL_REVIEW',state_revision:3,last_event_seq:10,route:'manual',mode:'text',verified:false,ended:false,busy:false,operations:[{operation_id:id,kind:'create_followup',status:'unknown'}],messages:[]}]);
  await page.goto('/');
  await expect(page.getByLabel('任务详情与交互')).toContainText('需要人工核实');
  await expect(page.getByTestId('operation-status')).toContainText('结果待查证');
  await expect(page.getByTestId('completed-count')).toHaveText('0');
  await expect(page.getByRole('log')).toHaveCount(0);
  await expect(page.getByTestId('writeback')).toHaveCount(0);
});
