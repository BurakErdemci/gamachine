import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook } from '@testing-library/react'

vi.mock('axios', () => ({ default: {
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(),
  defaults: { headers: { common: {} } },
} }))
vi.mock('../renderer/components/ui/ConfirmDialog', () => ({ confirmDialog: vi.fn().mockResolvedValue(true) }))

import axios from 'axios'
import { useChat } from '../renderer/hooks/home/useChat'
import { useSideChat } from '../renderer/hooks/home/useSideChat'
import { aktifDilAyarla } from '../renderer/lib/i18n'

const API = 'http://127.0.0.1:8000'
const user = { id: 1, name: 'b', sessionToken: 'tok' } as any
const config = { provider_type: 'subscription', model_name: 'claude-opus-5' } as any
let fetchMock: ReturnType<typeof vi.fn>

const completedStream = () => {
  let sent = false
  return { ok: true, status: 200, body: { getReader: () => ({ read: async () => {
    if (sent) return { done: true }
    sent = true
    return { done: false, value: new TextEncoder().encode('data: {"type":"done"}\n\n') }
  } }) } }
}

beforeEach(() => {
  vi.mocked(axios.get).mockResolvedValue({ data: [] })
  vi.mocked(axios.post).mockResolvedValue({ data: {} })
  fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (String(url).endsWith('/wake-stream-all')) {
      return Promise.resolve({ ok: true, body: { getReader: () => ({
        read: () => new Promise((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
        }),
      }) } })
    }
    if (String(url).endsWith('/side') && init?.method === 'POST') {
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ side_id: 50, side_of: 1 }) })
    }
    if (String(url).endsWith('/chat-stream') || String(url).endsWith('/side-stream')) {
      return Promise.resolve(completedStream())
    }
    return Promise.resolve({ ok: true, status: 200, json: async () => ({}) })
  })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  cleanup()
  aktifDilAyarla(null)
  localStorage.removeItem('app-lang')
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

describe.each(['en', 'tr'] as const)('SSE language %s', lang => {
  it('sends the chosen language on chat, wake and side fetches', async () => {
    aktifDilAyarla(lang)
    const { result } = renderHook(() => ({
      chat: useChat(API, user, config, '', vi.fn(), vi.fn(), name => name),
      side: useSideChat(API, user),
    }))
    await act(async () => {
      await result.current.chat.selectConversation({ id: 1, title: 'Main', parent_id: null, hidden: false } as any)
      await result.current.side.open(1)
    })
    await act(async () => {
      await result.current.chat.sendMessage('Hello', '', lang, 'auto', 'medium', vi.fn(), vi.fn())
      await result.current.side.ask('Why?', { lang })
    })
    for (const endpoint of ['/chat-stream', '/wake-stream-all', '/side-stream']) {
      const requests = fetchMock.mock.calls.filter(([url]) => String(url).endsWith(endpoint))
      expect(requests.length, endpoint).toBeGreaterThan(0)
      for (const [, init] of requests) {
        expect(new Headers(init.headers).get('X-UI-Lang'), endpoint).toBe(lang)
        expect(new Headers(init.headers).get('X-Session-Token'), endpoint).toBe('tok')
        if (endpoint !== '/wake-stream-all') expect(JSON.parse(init.body).language).toBe(lang)
      }
    }
  })
})
