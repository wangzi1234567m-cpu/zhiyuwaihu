export class ApiRequestError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}

export async function api<T>(path: string, body?: unknown, key?: string): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {method: body === undefined ? 'GET' : 'POST',
    headers: {'Content-Type':'application/json','X-Requested-With':'zhiyu',...(key ? {'Idempotency-Key':key} : {})},
    body: body === undefined ? undefined : JSON.stringify(body), cache:'no-store'});
  const data = await response.json().catch(()=>{throw new ApiRequestError(response.status,'INVALID_RESPONSE','后端未就绪或代理响应异常，请稍后重新登录或重试');});
  if (!response.ok) throw new ApiRequestError(response.status, data.code ?? 'REQUEST_FAILED', data.message ?? `请求失败 ${response.status}`);
  return data as T;
}
