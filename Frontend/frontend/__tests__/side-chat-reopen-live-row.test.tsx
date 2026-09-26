// Codex sideverify (27 Sep 2026): a stale idle-sweep reopen deleted the side
// row a newly opened panel already held.
import { afterEach, expect, it, vi } from 'vitest';
import { act, cleanup, renderHook } from '@testing-library/react';
import { useSideChat } from '../renderer/hooks/home/useSideChat';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('keeps the new panel row when an older reopen resolves with that same row', async () => {
  let resolveOldReopen!: (value: any) => void;
  const oldReopen = new Promise<any>(resolve => { resolveOldReopen = resolve; });
  let creates = 0;
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (url.endsWith('/conversations/1/side') && init?.method === 'POST') {
      creates += 1;
      if (creates === 1) return Promise.resolve({ ok: true, json: async () => ({ side_id: 50 }) });
      if (creates === 2) return oldReopen;
      return Promise.resolve({ ok: true, json: async () => ({ side_id: 51 }) });
    }
    if (url.endsWith('/conversations/50/side-stream')) {
      return Promise.resolve({ ok: false, status: 404 });
    }
    return Promise.resolve({ ok: true, status: 200 });
  });
  vi.stubGlobal('fetch', fetchMock);
  const { result } = renderHook(() => useSideChat('http://127.0.0.1:8000',
    { id: 1, name: 'test', sessionToken: 'test' } as any));
  await act(async () => { await result.current.open(1); });
  act(() => { void result.current.ask('question'); });
  await act(async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); });
  expect(creates).toBe(2);
  act(() => { result.current.close(); });
  await act(async () => { await result.current.open(1); });
  expect(result.current.sideId).toBe(51);
  await act(async () => {
    resolveOldReopen({ ok: true, json: async () => ({ side_id: 51 }) });
    for (let i = 0; i < 8; i++) await Promise.resolve();
  });
  expect(fetchMock.mock.calls.some(([url, init]) =>
    String(url).endsWith('/conversations/51/side') && init?.method === 'DELETE')).toBe(false);
});
