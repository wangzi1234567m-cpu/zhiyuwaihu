// 只读展示映射（t3）：全部字段来自公共契约 backend/app/contracts.py 导出的
// docs/design/contracts/openapi.json，界面不从 LLM 话术、动画或本地计数器推断结果。
// 保留 0.3.0 原 State 字面量与字段，不引入平行状态模型。

export type StatePhase = 'waiting' | 'executing' | 'review' | 'failed' | 'completed' | 'unknown';

/** 状态中文名：一一对应 14 个既有 State 字面量。 */
export const stateLabels: Record<string, string> = {
  CASE_READY: '待开始',
  ROUTING: '路由判定中',
  OPENING: '开场',
  VERIFYING: '核验身份',
  EXPLAINING: '说明原因与整改',
  QA: '整改问答',
  CALLBACK_PENDING: '等待回电预约',
  CLOSING: '结束中',
  ACTION_READY: '准备处理',
  EXECUTING: '处理中',
  RECONCILING: '结果待查证',
  WRITEBACK_PENDING: '等待回写',
  CLOSED: '已完成',
  MANUAL_REVIEW: '需人工处理',
};

/** 状态所属阶段：等待 / 执行 / 审查 / 失败 / 已完成（页面明确区分这五类）。 */
export const statePhases: Record<string, StatePhase> = {
  CASE_READY: 'waiting',
  ROUTING: 'waiting',
  OPENING: 'executing',
  VERIFYING: 'executing',
  EXPLAINING: 'executing',
  QA: 'executing',
  CALLBACK_PENDING: 'waiting',
  CLOSING: 'executing',
  ACTION_READY: 'executing',
  EXECUTING: 'executing',
  RECONCILING: 'review',
  WRITEBACK_PENDING: 'review',
  CLOSED: 'completed',
  MANUAL_REVIEW: 'failed',
};

export const phaseLabels: Record<StatePhase, string> = {
  waiting: '等待',
  executing: '执行中',
  review: '审查/回写',
  failed: '失败/人工',
  completed: '已完成',
  unknown: '未知',
};

/** 业务操作状态：与 OperationResult.status 一一对应。 */
export const operationLabels: Record<string, string> = {
  pending: '处理中',
  unknown: '结果待确认',
  not_found_yet: '尚未查到结果',
  succeeded: '模拟处理成功',
  failed: '处理失败',
};

/** 路由：RoutingDecision.route 仍为 phone|jingmai|manual，不改名。 */
export const routeLabels: Record<string, string> = {
  phone: '电话告知',
  jingmai: '京麦站内信',
  manual: '人工处理',
};

/** CallRecord.contact_status，真实接触结果只由后端回写。 */
export const contactLabels: Record<string, string> = {
  verified: '身份已核对',
  wrong_person: '非本人接听',
  refused: '商户拒绝沟通',
  no_answer: '未接通',
  disconnected: '线路中断',
  not_required: '无需通话',
  unknown: '未确认',
};

/** CallRecord.explanation_status。 */
export const explanationLabels: Record<string, string> = {
  not_started: '尚未告知',
  partial: '部分告知',
  completed: '已告知',
  unknown: '未确认',
  not_required: '无需告知',
};

export function stateLabel(state: string): string {
  return stateLabels[state] ?? `未知状态(${state})`;
}

/** 未识别的状态一律归入 unknown，绝不默认成“已完成”。 */
export function statePhase(state: string): StatePhase {
  return statePhases[state] ?? 'unknown';
}

export function phaseLabel(phase: StatePhase): string {
  return phaseLabels[phase] ?? phaseLabels.unknown;
}

export function operationLabel(status: string): string {
  return operationLabels[status] ?? `未知状态(${status})`;
}

/** 操作状态所属阶段；unknown/not_found_yet 属“待查证”，不当失败也不当成功。 */
export function operationPhase(status: string): StatePhase {
  if (status === 'succeeded') return 'completed';
  if (status === 'failed') return 'failed';
  if (status === 'pending') return 'executing';
  if (status === 'unknown' || status === 'not_found_yet') return 'review';
  return 'unknown';
}

export function routeLabel(route: string): string {
  return routeLabels[route] ?? `未知路由(${route})`;
}

export function contactLabel(value: string): string {
  return contactLabels[value] ?? `未知结论(${value})`;
}

export function explanationLabel(value: string): string {
  return explanationLabels[value] ?? `未知结论(${value})`;
}

/** 真实模型与假模型必须在页面上可区分，避免把测试替身当成真实模型。 */
export function modelLabel(model: string): string {
  if (!model) return '模型未上报';
  return model === 'fake' ? 'fake（确定性测试替身，非真实模型）' : model;
}

/** 页面是否需要展示“失败/人工”警示：来自后端状态或后端回写的操作状态。 */
export function needsFailureBanner(state: string, operationStatuses: string[]): boolean {
  if (statePhase(state) === 'failed') return true;
  return operationStatuses.some((status) => operationPhase(status) === 'failed');
}

/** 事件流连接状态：与“业务是否成功”无关，仅表示能否继续收到后端事件。 */
export type StreamState = 'idle' | 'connecting' | 'open' | 'failed';

/**
 * 连接状态文案。绝不把“已放弃重连”显示成“已连接”：
 * 断流后界面必须显示事实上的未同步状态，业务状态以最后一次快照为准。
 */
export function streamLabel(stream: StreamState): string {
  if (stream === 'open') return '已连接';
  if (stream === 'connecting') return '事件连接中';
  if (stream === 'failed') return '事件连接已断开（状态不再自动同步）';
  return '待连接';
}

/**
 * 回写区块标题：只陈述后端确实发生过的事。
 * - 有终态操作（succeeded/failed）：业务操作结果已回写。
 * - 操作仍未定（pending/unknown/not_found_yet）：记录已生成但回写未确认。
 * - 没有任何操作：这是错人/拒绝/用户结束等非驳回结论，只有记录、没有驳回回写。
 */
export function recordTitle(operationStatuses: string[]): string {
  if (operationStatuses.some((s) => s === 'succeeded' || s === 'failed')) return '业务操作结果已回写（后端记录）';
  if (operationStatuses.length > 0) return '通话记录已生成，业务操作回写尚未确认';
  return '后端已生成通话记录（本次未派发驳回操作）';
}

/** 语音能力文案：后端能力未取到时如实说未知，不假设“未配置”。 */
export function voiceLabel(voice: boolean | null): string {
  if (voice === null) return '语音状态未知（未取到后端能力）';
  return voice ? '语音已配置' : '语音未配置';
}
