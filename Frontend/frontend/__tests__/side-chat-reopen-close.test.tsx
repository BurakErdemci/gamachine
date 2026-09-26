// Codex sideaudit (27 Sep 2026): closing the panel while an idle-swept side
// chat was being reopened left the new side row behind.
import { afterEach, expect, it, vi } from 'vitest';
import { act, cleanup, renderHook } from '@testing-library/react';
import { useSideChat } from '../renderer/hooks/home/useSideChat';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('deletes a side row created after the panel closes during idle-sweep retry', async () => {
  let resolveReopen!: (value: any) => void;
  const reopened = new Promise<any>(resolve => { resolveReopen = resolve; });
  let opens = 0;
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (url.endsWith('/conversations/1/side') && init?.method === 'POST') {
      opens += 1;
      return opens === 1
        ? Promise.resolve({ ok: true, json: async () => ({ side_id: 50 }) })
        : reopened;
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
  expect(opens).toBe(2);
  act(() => { result.current.close(); });
  await act(async () => {
    resolveReopen({ ok: true, json: async () => ({ side_id: 51 }) });
    for (let i = 0; i < 8; i++) await Promise.resolve();
  });
  expect(fetchMock.mock.calls.some(([url, init]) =>
    String(url).endsWith('/conversations/51/side') && init?.method === 'DELETE')).toBe(true);
});
