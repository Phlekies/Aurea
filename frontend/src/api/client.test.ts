import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, getHealth } from './client';

afterEach(() => vi.unstubAllGlobals());

describe('API client', () => {
  it('returns the validated health response', async () => {
    const payload = { status: 'ok', service: 'aurea', version: '0.1.0' };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(payload))));
    await expect(getHealth()).resolves.toEqual(payload);
    expect(fetch).toHaveBeenCalledWith('/health', expect.objectContaining({ signal: expect.any(AbortSignal) }));
  });

  it('reports HTTP failure without accepting its response body', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('unavailable', { status: 503 })));
    await expect(getHealth()).rejects.toMatchObject({ status: 503, name: 'ApiError' });
  });

  it.each([null, {}, { status: 'ok', service: 'other', version: '0.1.0' }, { status: 'ok', service: 'aurea', version: 1 }])('rejects an invalid response %j', async (payload) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(payload))));
    await expect(getHealth()).rejects.toBeInstanceOf(ApiError);
  });

  it('passes caller cancellation through to fetch', async () => {
    const controller = new AbortController();
    vi.stubGlobal('fetch', vi.fn().mockImplementation((_url: string, options: RequestInit) => {
      expect(options.signal?.aborted).toBe(true);
      return Promise.reject(new DOMException('Aborted', 'AbortError'));
    }));
    controller.abort();
    await expect(getHealth(controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
  });
});
