// 前端唯一的 HTTP/SSE 客户端（t3）。只消费后端契约，不合成业务结果。
import type {components} from '../app/api.generated';

export type SessionView = components['schemas']['SessionView'];
export type ApplicationView = components['schemas']['ApplicationView'];
export type AcceptedTurn = components['schemas']['AcceptedTurn'];
export type Capabilities = components['schemas']['Capabilities'];
export type OperationResult = components['schemas']['OperationResult'];
export type CallRecord = components['schemas']['CallRecord'];
export type SessionEvent = components['schemas']['Event'];
export type ApiError = components['schemas']['ApiError'];

/** 后端未连上/未迁移时抛出的可展示错误，界面必须显示它而不是假成功。 */
export class BackendError extends Error {
  code: string;
  status: number;
  requestId: string | null;
  retryable: boolean;
  constructor(message: string, code: string, status: number, requestId: string | null, retryable: boolean) {
    super(message);
    this.name = 'BackendError';
    this.code = code;
    this.status = status;
    this.requestId = requestId;
    this.retryable = retryable;
  }
}

function parseError(status: number, data: unknown): BackendError {
  const body = data as Partial<ApiError> | null;
  if (body && typeof body.message === 'string' && typeof body.code === 'string') {
    return new BackendError(body.message, body.code, status, body.request_id ?? null, Boolean(body.retryable));
  }
  return new BackendError(`请求失败（HTTP ${status}），请检查后端与数据库是否已启动`, 'UNEXPECTED_RESPONSE', status, null, status >= 500);
}

export interface RequestOptions {
  body?: unknown;
  idempotencyKey?: string;
  signal?: AbortSignal;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const {body, idempotencyKey, signal} = options;
  const method = body === undefined ? 'GET' : 'POST';
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, {
      method,
      signal,
      headers: {
        'Content-Type': 'application/json',
        // 后端 boundary 中间件要求写请求带此头，否则返回 CSRF_REJECTED。
        'X-Requested-With': 'zhiyu',
        ...(idempotencyKey ? {'Idempotency-Key': idempotencyKey} : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: 'no-store',
      credentials: 'same-origin',
    });
  } catch (error) {
    // 网络层失败：后端未启动 / 反向代理未配置。绝不能当成成功。
    const detail = error instanceof Error ? error.message : String(error);
    throw new BackendError(`无法连接后端服务（${detail}）`, 'NETWORK_ERROR', 0, null, true);
  }
  let data: unknown = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }
  if (!response.ok) throw parseError(response.status, data);
  if (data === null) throw new BackendError('后端返回了无法解析的响应', 'UNEXPECTED_RESPONSE', response.status, null, true);
  return data as T;
}

export const api = {
  login: (username: string, password: string) => request<{owner: string}>('/dev/login', {body: {username, password}}),
  capabilities: () => request<Capabilities>('/capabilities'),
  applications: () => request<ApplicationView[]>('/applications'),
  createSession: (applicationId: string, idempotencyKey: string) =>
    request<SessionView>('/sessions', {body: {application_id: applicationId, mode: 'text'}, idempotencyKey}),
  session: (sid: string) => request<SessionView>(`/sessions/${sid}`),
  start: (sid: string, expectedRevision: number, idempotencyKey: string) =>
    request<AcceptedTurn>(`/sessions/${sid}/start`, {body: {expected_revision: expectedRevision}, idempotencyKey}),
  turn: (sid: string, text: string, clientEventId: string) =>
    request<AcceptedTurn>(`/sessions/${sid}/turns`, {body: {text, client_event_id: clientEventId}}),
  interrupt: (sid: string, generationId: string, clientEventId: string) =>
    request<{applied: boolean}>(`/sessions/${sid}/interruptions`, {body: {generation_id: generationId, client_event_id: clientEventId}}),
  end: (sid: string, reason = 'user_ended') => request<SessionView>(`/sessions/${sid}/end`, {body: {reason}}),
  operation: (oid: string) => request<OperationResult>(`/operations/${oid}`),
  eventStreamUrl: (sid: string, afterSeq: number) => `/api/v1/sessions/${sid}/events?after_seq=${afterSeq}`,
};
