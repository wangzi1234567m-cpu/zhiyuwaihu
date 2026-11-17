import {expect, test} from '@playwright/test';
import type {components} from '../app/api.generated';

type SessionView = components['schemas']['SessionView'];
type SessionEvent = components['schemas']['Event'];

test('renders a synthetic session, unresolved operation and measured latency', async ({page}) => {
  const sessionId = '00000000-0000-4000-8000-000000000004';
  const applicationId = '00000000-0000-4000-8000-000000000001';
  const timestamp = '2026-01-01T00:00:00Z';
  const view: SessionView = {
    session_id: sessionId, application_id: applicationId, case_revision: 1,
    route: 'manual', state: 'MANUAL_REVIEW', state_revision: 2, last_event_seq: 2,
    verified: false, busy: false, ended: true, reason: 'PRIVATE_REASON', remedy: 'PRIVATE_REMEDY',
    operations: [{
      operation_id: '00000000-0000-4000-8000-000000000010',
      status: 'not_found_yet',
      updated_at: timestamp,
    }],
  };
  const event: SessionEvent = {
    schema_version: '0.3.0', turn_id: null, generation_id: null, operation_id: null,
    event_id: '00000000-0000-4000-8000-000000000014',
    session_id: sessionId, seq: 3, state_revision: 2, occurred_at: timestamp,
    strategy_version: 'demo-p1', knowledge_version: 'demo-k1',
    payload: {
      type: 'metric.recorded', name: 'model_ttft', duration_ms: 125, outcome: 'success',
      clock_domain: 'fixture', measurement_method: 'fixture timeline',
    },
  };
  await page.addInitScript(id => localStorage.setItem('zhiyu.session', id), sessionId);
  await page.route('**/api/v1/applications', route => route.fulfill({
    json: [{application_id: applicationId, label: '合成申请单 1', company_hint: '演示商家1'}],
  }));
  await page.route(`**/api/v1/sessions/${sessionId}`, route => route.fulfill({json: view}));
  await page.route(`**/api/v1/sessions/${sessionId}/events?*`, route => route.fulfill({
    contentType: 'text/event-stream',
    body: `id: ${event.seq}\nevent: ${event.payload.type}\ndata: ${JSON.stringify(event)}\n\n`,
  }));
  await page.goto('/');
  await expect(page.getByText('关单渠道：人工处理')).toBeVisible();
  await expect(page.getByTestId('operation-status')).toHaveText('结果待确认');
  await expect(page.getByText('PRIVATE_REASON')).toHaveCount(0);
  await expect(page.getByText('PRIVATE_REMEDY')).toHaveCount(0);
  await expect(page.getByRole('region', {name: '本次会话指标'}).getByText('125 ms')).toBeVisible();
});


const routingFixtures = [
  {
    name: '京麦-only直接书面关单',
    contact: 'not_required',
    contactLabel: '无需电话核验',
    route: 'jingmai',
    state: 'CLOSED',
    ended: true,
    status: 'succeeded',
    channel: '京麦书面告知',
    stateLabel: '已完成',
    operationLabel: '模拟处理成功',
  },
  {
    name: '首次提交未接通后京麦关单',
    contact: 'no_answer',
    contactLabel: '未接通',
    route: 'jingmai',
    state: 'CLOSED',
    ended: true,
    status: 'succeeded',
    channel: '京麦书面告知',
    stateLabel: '已完成',
    operationLabel: '模拟处理成功',
  },
  {
    name: '同场景再次提交且本次未接通，等待二次触达跟进',
    contact: null,
    contactLabel: null,
    route: 'phone',
    state: 'CALLBACK_PENDING',
    ended: false,
    status: 'unknown',
    channel: '电话说明',
    stateLabel: '等待预约处理',
    operationLabel: '结果待确认',
  },
  {
    name: '二次触达仍未接通后京麦关单',
    contact: 'no_answer',
    contactLabel: '未接通',
    route: 'jingmai',
    state: 'CLOSED',
    ended: true,
    status: 'succeeded',
    channel: '京麦书面告知',
    stateLabel: '已完成',
    operationLabel: '模拟处理成功',
  },
] as const;

for (const fixture of routingFixtures) {
  test(fixture.name, async ({page}) => {
    const sessionId = '00000000-0000-4000-8000-000000000004';
    const applicationId = '00000000-0000-4000-8000-000000000001';
    const operationId = '00000000-0000-4000-8000-000000000010';
    const view: SessionView = {
      session_id: sessionId, application_id: applicationId, case_revision: 1,
      route: fixture.route, state: fixture.state, state_revision: 2, last_event_seq: 2,
      verified: false, busy: false, ended: fixture.ended,
      operations: [{
        operation_id: operationId,
        status: fixture.status,
        updated_at: '2026-01-01T00:00:00Z',
      }],
      record: fixture.contact ? {
        record_id: '00000000-0000-4000-8000-000000000011', record_revision: 1,
        session_id: sessionId, application_id: applicationId, case_revision: 1,
        contact_status: fixture.contact,
        explanation_status: fixture.contact === 'no_answer' ? 'not_started' : 'not_required',
        reason_codes: [], rectification_items: [], source_refs: [], operation_refs: [operationId],
        strategy_version: 'demo-p1', rule_version: 'demo-r1', knowledge_version: 'demo-k1',
      } : null,
    };
    await page.addInitScript(id => localStorage.setItem('zhiyu.session', id), sessionId);
    await page.route('**/api/v1/applications', route => route.fulfill({
      json: [{application_id: applicationId, label: '合成申请单 1', company_hint: '演示商家1'}],
    }));
    await page.route(`**/api/v1/sessions/${sessionId}`, route => route.fulfill({json: view}));
    await page.route(`**/api/v1/sessions/${sessionId}/events?*`, route => route.fulfill({
      contentType: 'text/event-stream', body: ': fixture\n\n',
    }));
    await page.goto('/');
    await expect(page.getByText(`关单渠道：${fixture.channel}`)).toBeVisible();
    await expect(page.getByText(`状态：${fixture.stateLabel}`, {exact: true})).toBeVisible();
    await expect(page.getByTestId('operation-status')).toHaveText(fixture.operationLabel);
    await expect(page.getByRole('button', {name: '开始', exact: true})).toBeDisabled();
    await expect(page.getByText('身份：尚未核对')).toBeVisible();
    if (fixture.contactLabel) {
      await expect(page.getByText(`联系结果：${fixture.contactLabel}`)).toBeVisible();
      await expect(page.getByTestId('writeback')).toBeVisible();
    } else {
      await expect(page.getByTestId('writeback')).toHaveCount(0);
    }
  });
}
