/**
 * agy turns are serialized machine-wide: a second agy chat waits for another
 * chat's whole turn and looked frozen. The backend now sends
 * `status {code: 'agy_queued'}` while it waits and `status {code: 'agy_started'}`
 * when the turn starts; the chat's activity line shows the queued text and
 * drops it on start. The sidebar keeps its ordinary `running` status.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, renderHook, act } from '@testing-library/react'

vi.mock('axios', () => {
  const post = vi.fn()
  const get = vi.fn()
  const del = vi.fn()
  const put = vi.fn()
  return { default: { post, get, delete: del, put }, post, get }
})

import axios from 'axios'
import { useChat } from '../renderer/hooks/home/useChat'
import { cevir } from '../renderer/lib/i18n'

const mockedAxios = axios as unknown as {
  post: ReturnType<typeof vi.fn>; get: ReturnType<typeof vi.fn>
}

const API = 'http://127.0.0.1:8000'
const USER = { id: 1, name: 'b', sessionToken: 'tok' } as any
const CONFIG = { provider_type: 'subscription', model_name: 'gemini-3.8-flash' } as any

const enc = (ev: object) => new TextEncoder().encode(`data: ${JSON.stringify(ev)}\n\n`)

const makeStream = () => {
  const queue: Array<{ done: boolean; value?: Uint8Array }> = []
  let waiter: ((v: any) => void) | null = null
  let failer: ((e: any) => void) | null = null
  const deliver = (item: { done: boolean; value?: Uint8Array }) => {
    if (waiter) { const w = waiter; waiter = null; failer = null; w(item) } else queue.push(item)
  }
  return {
    push: (ev: object) => deliver({ done: false, value: enc(ev) }),
    close: () => deliver({ done: true }),
    fail: (err: Error) => { if (failer) { const f = failer; waiter = null; failer = null; f(err) } },
    response: {
      ok: true,
      body: {
        getReader: () => ({
          read: () => queue.length
            ? Promise.resolve(queue.shift())
            : new Promise((res, rej) => { waiter = res; failer = rej }),
        }),
      },
    },
  }
}

let streams: Record<number, ReturnType<typeof makeStream>[]>
let fetchMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  streams = {}
  fetchMock = vi.fn((url: string, init?: any) => {
    const u = String(url)
    if (u.endsWith('/chat-stream')) {
      const convId = JSON.parse(init.body).conversation_id
      const s = makeStream()
      ;(streams[convId] ||= []).push(s)
      init.signal?.addEventListener('abort', () => {
        const e = new Error('aborted'); (e as any).name = 'AbortError'; s.fail(e)
      })
      return Promise.resolve(s.response)
    }
    if (u.includes('/wake-stream')) return new Promise(() => {})
    return Promise.resolve({ ok: true, status: 200, json: async () => ({ status: 'ok' }) })
  })
  vi.stubGlobal('fetch', fetchMock)
  mockedAxios.post.mockReset().mockImplementation(async () => ({ data: {} }))
  mockedAxios.get.mockReset().mockImplementation(async (url: string) => {
    if (String(url).includes('/context-usage')) return { data: { percent: 1, should_compact: false, message_count: 0 } }
    return { data: [] }
  })
})

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

const hook = () => renderHook(() => useChat(API, USER, CONFIG, '/ws', vi.fn(), vi.fn(), (n: string) => n))
const flush = async () => { await act(async () => { for (let i = 0; i < 10; i++) await Promise.resolve() }) }
const send = (result: any, text: string) => {
  act(() => { void result.current.sendMessage(text, '', 'tr', 'auto', 'medium', vi.fn(), vi.fn()) })
}

describe('agy queued indicator', () => {
  it('shows the queued line while waiting and replaces it when the turn starts', async () => {
    const { result } = hook()
    await act(async () => { await result.current.selectConversation({ id: 1 } as any) })
    send(result, 'second agy chat')
    await flush()
    const s = streams[1][0]

    s.push({ type: 'status', code: 'agy_queued', detail: 'Sırada — backend fallback' })
    await flush()
    expect(result.current.activity?.detail).toBe(cevir('activity.agyQueued'))
    expect(cevir('activity.agyQueued')).not.toBe('activity.agyQueued')
    // No new sidebar status: a queued chat is still a running chat.
    expect(result.current.convStatus[1]).toBe('running')

    s.push({ type: 'status', code: 'agy_started', detail: 'Çalışıyor…' })
    await flush()
    expect(result.current.activity?.detail).toBe(cevir('activity.working'))
    expect(result.current.activity?.detail).not.toBe(cevir('activity.agyQueued'))

    s.push({ type: 'text', content: 'OK' })
    s.push({ type: 'response', content: 'OK' })
    s.push({ type: 'done', iterations: 1, stop_reason: 'complete' })
    s.close()
    await flush()
    expect(result.current.activity).toBeNull()
    expect(result.current.loading).toBe(false)
  })

  it('Stop while queued clears the queued line', async () => {
    const { result } = hook()
    await act(async () => { await result.current.selectConversation({ id: 1 } as any) })
    send(result, 'queued turn')
    await flush()
    streams[1][0].push({ type: 'status', code: 'agy_queued' })
    await flush()
    expect(result.current.activity?.detail).toBe(cevir('activity.agyQueued'))

    act(() => { result.current.stopMessage() })
    await flush()
    expect(result.current.activity).toBeNull()
    expect(result.current.loading).toBe(false)
    expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith('/chat-stop/1'))).toBe(true)
  })
})
