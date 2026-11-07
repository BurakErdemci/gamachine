/**
 * MID-TURN SEND ON A CODEX CHAT. Owner: sending a message or "Send now" must
 * never stop the turn or kill its background work. A Codex chat hands the
 * message to the running turn (/chat-inject -> turn/steer) exactly as a Claude
 * chat does, and never POSTs /chat-stop; `not_running` makes it an ordinary
 * queued message again.
 *
 * Drives the real `useChat` with hand-fed SSE streams (same harness shape as
 * message-queue.test.tsx); only fetch/axios (the backend) are faked.
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

vi.mock('../renderer/hooks/home/useVoiceInput', () => ({
  useVoiceInput: () => ({
    state: 'idle', elapsedMs: 0, error: null, partialText: '',
    start: vi.fn(), stop: vi.fn(), cancel: vi.fn(), clearError: vi.fn(),
  }),
  formatElapsed: () => '00:00',
}))

import axios from 'axios'
import { useChat } from '../renderer/hooks/home/useChat'

const mockedAxios = axios as unknown as {
  post: ReturnType<typeof vi.fn>; get: ReturnType<typeof vi.fn>
}

const API = 'http://127.0.0.1:8000'
const USER = { id: 1, name: 'b', sessionToken: 'tok' } as any
const CODEX = { provider_type: 'subscription', model_name: 'gpt-6-luna' } as any

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

type Turn = { message: string; origin: string; stream: ReturnType<typeof makeStream> }
let turns: Turn[]
let stops: number[]
let injects: Array<{ convId: number; message: string; client_id: string }>
let injectStatus: string

beforeEach(() => {
  turns = []
  stops = []
  injects = []
  injectStatus = 'not_running'
  vi.stubGlobal('fetch', vi.fn((url: string, init?: any) => {
    const u = String(url)
    if (u.endsWith('/chat-stream')) {
      const body = JSON.parse(init.body)
      const stream = makeStream()
      turns.push({ message: body.message, origin: body.origin, stream })
      init.signal?.addEventListener('abort', () => {
        const e = new Error('aborted'); (e as any).name = 'AbortError'; stream.fail(e)
      })
      return Promise.resolve(stream.response)
    }
    if (u.includes('/wake-stream-all')) {
      return Promise.resolve({
        ok: true, body: { getReader: () => ({ read: () => new Promise(() => {}) }) },
      })
    }
    const inject = u.match(/\/chat-inject\/(\d+)$/)
    if (inject) {
      injects.push({ convId: Number(inject[1]), ...JSON.parse(init.body) })
      const status = injectStatus
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ status }) })
    }
    const stop = u.match(/\/chat-stop\/(\d+)$/)
    if (stop) stops.push(Number(stop[1]))
    return Promise.resolve({ ok: true, status: 200, json: async () => ({ status: 'ok' }) })
  }))
  mockedAxios.post.mockReset().mockResolvedValue({ data: {} })
  mockedAxios.get.mockReset().mockImplementation(async (url: string) => {
    if (String(url).includes('/context-usage')) return { data: { percent: 1, should_compact: false, message_count: 0 } }
    return { data: [] }
  })
})

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

const hook = () => renderHook(() => useChat(API, USER, CODEX, '/ws', vi.fn(), vi.fn(), (n: string) => n))
const flush = async () => { await act(async () => { for (let i = 0; i < 15; i++) await Promise.resolve() }) }
const send = (result: any, text: string) => {
  act(() => {
    void result.current.sendMessage(text, '', 'tr', 'auto', 'medium', vi.fn(), vi.fn(),
      undefined, false, undefined, 'user', undefined, undefined)
  })
}
const open = async (result: any, id: number) => {
  await act(async () => { await result.current.selectConversation({ id } as any) })
}
const finish = async (i: number) => {
  turns[i].stream.push({ type: 'done', stop_reason: 'complete' })
  turns[i].stream.close()
  await flush()
}
const userTurns = () => turns.filter(t => t.origin === 'user').map(t => t.message)

describe('mid-turn send on a Codex chat', () => {
  it('"send now" posts /chat-inject and never /chat-stop', async () => {
    const { result } = hook()
    await open(result, 1)
    send(result, 'first')
    await flush()
    send(result, 'second')  // answered not_running: an ordinary queued message
    await flush()

    injectStatus = 'injected'
    let sent: boolean | undefined
    await act(async () => { sent = await result.current.sendQueuedNow(result.current.queue[0].id) })
    await flush()

    expect(sent).toBe(true)
    expect(injects.map(i => [i.convId, i.message])).toEqual([[1, 'second'], [1, 'second']])
    expect(stops).toEqual([])
    expect(userTurns()).toEqual(['first'])
    expect(result.current.loading).toBe(true)
    expect(result.current.queue.map((q: any) => q.state)).toEqual(['sent'])
  })

  it('Enter while the turn runs goes to /chat-inject and never stops the turn', async () => {
    injectStatus = 'injected'
    const { result } = hook()
    await open(result, 1)
    send(result, 'first')
    await flush()
    send(result, 'also check the lights')
    await flush()

    expect(injects.map(i => i.message)).toEqual(['also check the lights'])
    expect(stops).toEqual([])
    expect(result.current.queue.map((q: any) => [q.draft, q.state])).toEqual([['also check the lights', 'sent']])
    await finish(0)
    expect(userTurns()).toEqual(['first'])
  })

  it('not_running falls back to the plain queue: no Stop, sent at the turn end', async () => {
    const { result } = hook()
    await open(result, 1)
    send(result, 'first')
    await flush()
    send(result, 'second')
    await flush()

    let sent: boolean | undefined
    await act(async () => { sent = await result.current.sendQueuedNow(result.current.queue[0].id) })
    await flush()

    expect(sent).toBe(false)
    expect(injects.map(i => i.message)).toEqual(['second', 'second'])
    expect(stops).toEqual([])
    expect(result.current.queue.map((q: any) => [q.draft, q.state])).toEqual([['second', undefined]])
    expect(userTurns()).toEqual(['first'])
    await finish(0)
    expect(userTurns()).toEqual(['first', 'second'])
  })
})
