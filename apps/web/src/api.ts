let csrf = '';
export const pageClientId = crypto.randomUUID();

export class ApiError extends Error {
  constructor(message: string, public code: string, public status: number) { super(message); }
}

export function protectedMutation(path: string, method: string) {
  if (method === 'DELETE') return /^\/projects\/[^/]+$/.test(path);
  if (method === 'PUT') return path === '/tasks/order';
  return method === 'POST' && /^\/(?:imports(?:\/preview)?|settings\/ai\/test|projects\/[^/]+\/(?:analyze|index|overview|reanalysis(?:\/preview)?)|tasks\/[^/]+\/(?:start|pause|cancel))$/.test(path);
}

export async function api<T>(path: string, body?: unknown, method?: string): Promise<T> {
  const actualMethod = method || (body === undefined ? 'GET' : 'POST');
  const key = protectedMutation(path, actualMethod) ? crypto.randomUUID() : '';
  const options: RequestInit = {
    method: actualMethod,
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf, 'X-Client-Id': pageClientId,
      ...(key ? { 'Idempotency-Key': key } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: 'same-origin',
  };
  let response: Response;
  try { response = await fetch(`/api/v1${path}`, options); }
  catch (error) {
    // Retry transport failure only; the same receipt prevents a second domain operation.
    if (!key || !(error instanceof TypeError)) throw error;
    response = await fetch(`/api/v1${path}`, options);
  }
  const result = await response.json();
  if (!response.ok) throw new ApiError(result.error?.message || result.detail?.[0]?.msg || `请求失败 (${response.status})`, result.error?.code || '', response.status);
  return result.data as T;
}

export async function bootstrap() {
  const nonce = new URLSearchParams(location.hash.slice(1)).get('nonce');
  if (nonce) {
    history.replaceState(null, '', location.pathname);
    const result = await api<{ csrf_token: string }>('/session/exchange', { nonce });
    csrf = result.csrf_token;
  } else {
    const result = await api<{ csrf_token: string }>('/session');
    csrf = result.csrf_token;
  }
}

export function keepalive(path: string, body: unknown, method = 'POST') {
  return fetch(`/api/v1${path}`, { method, keepalive: true, credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body) });
}
