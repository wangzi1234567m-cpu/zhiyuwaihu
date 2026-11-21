export async function api<T>(path: string, body?: unknown, key?: string): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {method: body === undefined ? 'GET' : 'POST',
    headers: {'Content-Type':'application/json','X-Requested-With':'zhiyu',...(key ? {'Idempotency-Key':key} : {})},
    body: body === undefined ? undefined : JSON.stringify(body), cache:'no-store'});
  const data = await response.json();
  if (!response.ok) throw new Error(data.message ?? `请求失败 ${response.status}`);
  return data as T;
}
