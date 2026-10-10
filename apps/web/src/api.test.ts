import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, protectedMutation } from './api';

afterEach(() => { vi.unstubAllGlobals(); });

describe('持久化请求标识', () => {
  it('传输失败重试保留同一标识，下一次用户操作使用新标识', async () => {
    const fetcher = vi.fn()
      .mockRejectedValueOnce(new TypeError('固定断线验证'))
      .mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ data: { id: 'owned' } }), { status: 200 })));
    vi.stubGlobal('fetch', fetcher);
    await expect(api('/imports', { path: 'owned-source' })).resolves.toEqual({ id: 'owned' });
    expect(fetcher).toHaveBeenCalledTimes(2);
    const first = fetcher.mock.calls[0][1] as RequestInit;
    const retry = fetcher.mock.calls[1][1] as RequestInit;
    expect(first).toBe(retry);
    expect((first.headers as Record<string, string>)['Idempotency-Key']).toBeTruthy();
    await api('/imports', { path: 'owned-source' });
    const next = fetcher.mock.calls[2][1] as RequestInit;
    expect((next.headers as Record<string, string>)['Idempotency-Key']).not.toBe((first.headers as Record<string, string>)['Idempotency-Key']);
  });

  it('服务明确拒绝时不会自动重复请求', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: 'REQUEST_INDETERMINATE', message: '请核对任务' } }), { status: 503 }));
    vi.stubGlobal('fetch', fetcher);
    await expect(api('/tasks/owned/start', {})).rejects.toMatchObject({ code: 'REQUEST_INDETERMINATE', status: 503 });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('学习并发写入和凭据保存沿用原有规则，不进行请求回放', async () => {
    const fetcher = vi.fn().mockRejectedValue(new TypeError('固定断线验证'));
    vi.stubGlobal('fetch', fetcher);
    await expect(api('/projects/owned/learning/entity', { expected_revision: 1 }, 'PUT')).rejects.toThrow('固定断线验证');
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect((fetcher.mock.calls[0][1].headers as Record<string, string>)['Idempotency-Key']).toBeUndefined();
    expect(protectedMutation('/settings/ai', 'PUT')).toBe(false);
    expect(protectedMutation('/projects/owned', 'DELETE')).toBe(true);
    expect(protectedMutation('/tasks/order', 'PUT')).toBe(true);
  });
});
