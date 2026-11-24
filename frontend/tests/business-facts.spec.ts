import {test, expect} from '@playwright/test';

test('keeps internal business facts and evaluation tools off the merchant page', async ({page}) => {
  const id = '00000000-0000-0000-0000-000000000001';
  let verified = false;
  await page.addInitScript(sid => localStorage.setItem('zhiyu.session', sid), id);
  await page.route('**/api/v1/applications', r => r.fulfill({json: [{application_id:id,label:'合成任务',company_hint:'演示商家'}]}));
  await page.route('**/api/v1/capabilities', r => r.fulfill({json: {voice:false, model:'fake'}}));
  await page.route('**/events?*', r => r.fulfill({contentType:'text/event-stream', body:': ready\n\n'}));
  await page.route('**/api/v1/sessions/'+id, r => r.fulfill({json: {
    session_id:id, application_id:id, case_revision:1, state:'RECONCILING', state_revision:3,
    last_event_seq:10, route:'jingmai', mode:'text', verified, ended:false,
    reason:'合成原因', remedy:'合成整改', source_refs:[{sheet:'模拟规则',row:27,columns:['B'],file_sha256:'0'.repeat(64)}],
    operations:[{operation_id:id,kind:'create_followup',status:'unknown'}], messages:[],
    metrics:[{name:'model_ttft',outcome:'success',duration_ms:123,measurement_method:'模拟首段'}]
  }}));
  await page.goto('/');
  await expect(page.getByRole('log')).toBeVisible();
  await expect(page.getByTestId('operation-status')).toHaveCount(0);
  await expect(page.getByText('原因：合成原因')).toHaveCount(0);
  await expect(page.getByText('查看整改依据')).toHaveCount(0);
  await expect(page.getByLabel('评测报告（JSON）')).toHaveCount(0);
  verified=true;
  await page.reload();
  await expect(page.getByText('原因：合成原因')).toHaveCount(0);
});
