let csrf = '';

export async function api<T>(path: string, body?: unknown, method?: string): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    method: method || (body === undefined ? 'GET' : 'POST'),
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: 'same-origin',
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error?.message || result.detail?.[0]?.msg || `请求失败 (${response.status})`);
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
