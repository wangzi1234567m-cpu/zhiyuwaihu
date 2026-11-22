import {test, expect} from '@playwright/test';

test('shows only verified facts, distinguishes unknown writes and restores metrics', async ({page}) => {
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
  await expect(page.getByTestId('operation-status')).toHaveText('创建人工跟进 · 结果待确认');
  await expect(page.getByText('关单渠道：京麦书面')).toBeVisible();
  await expect(page.getByText('原因：合成原因')).toHaveCount(0);
  await expect(page.getByText('查看整改依据')).toHaveCount(0);
  verified = true;
  await page.reload();
  await expect(page.getByText('原因：合成原因')).toBeVisible();
  await page.getByText('查看整改依据').click();
  await expect(page.getByText('模拟规则 · 第 27 行 · B')).toBeVisible();
  await page.getByText('查看本次响应指标').click();
  await expect(page.getByText('模型首段响应 · 123 ms')).toBeVisible();
  await page.getByLabel('评测报告（JSON）').setInputFiles({name:'test.json',mimeType:'application/json',
    buffer:Buffer.from(JSON.stringify({arm_summary:{baseline:{runs:2,passed:1},candidate:{runs:2,passed:2}}}))});
  await expect(page.getByRole('cell',{name:'50.0%'})).toBeVisible();
  await expect(page.getByRole('cell',{name:'100.0%'})).toBeVisible();
});
