/**
 * Following a turn this renderer did not start (docs/remote-control-web.md):
 * the web remote's turn runs on the desktop backend and streams to the
 * browser only; every other renderer of the chat gets `turn_live` frames on
 * `/wake-stream-all` and reads the turn from the ring
 * (`GET /conversations/{id}/turn-events`). A renderer running the turn itself
 * ignores the frames.
 *
 * Harness: the real `useChat` with hand-fed SSE streams, as in
 * `remote-message.test.tsx`.
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

const mockedAxios = axios as unknown as {
  post: ReturnType<typeof vi.fn>; get: ReturnType<typeof vi.fn>
}

const API = 'http://127.0.0.1:8000'
const USER = { id: 1, name: 'b', sessionToken: 'tok' } as any
const CONFIG = { provider_type: 'subscription', model_name: 'claude-opus-5' } as any

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

type Turn = { convId: number; message: string; stream: ReturnType<typeof makeStream> }
let turns: Turn[]
let stops: number[]
/** One wake stream per hook instance (= per window); each gets every frame. */
let wakeStreams: Array<(frame: object) => void>

const installFetch = () => {
  turns = []
  stops = []
  wakeStreams = []
  vi.stubGlobal('fetch', vi.fn((url: string, init?: any) => {
    const u = String(url)
    if (u.endsWith('/chat-stream')) {
      const body = JSON.parse(init.body)
      const stream = makeStream()
      turns.push({ convId: body.conversation_id, message: body.message, stream })
      init.signal?.addEventListener('abort', () => {
        const e = new Error('aborted'); (e as any).name = 'AbortError'; stream.fail(e)
      })
      return Promise.resolve(stream.response)
    }
    if (u.includes('/wake-stream-all')) {
      let waiter: ((v: any) => void) | null = null
      const frames: Uint8Array[] = []
      wakeStreams.push((frame: object) => {
        const bytes = enc(frame)
        if (waiter) { const w = waiter; waiter = null; w({ done: false, value: bytes }) } else frames.push(bytes)
      })
      return Promise.resolve({
        ok: true,
        body: {
          getReader: () => ({
            read: () => frames.length
              ? Promise.resolve({ done: false, value: frames.shift() })
              : new Promise(res => { waiter = res }),
          }),
        },
      })
    }
    const stop = u.match(/\/chat-stop\/(\d+)$/)
    if (stop) stops.push(Number(stop[1]))
    return Promise.resolve({ ok: true, status: 200, json: async () => ({ status: 'ok' }) })
  }))
}

const hook = () => renderHook(() => useChat(API, USER, CONFIG, '/ws', vi.fn(), vi.fn(), (n: string) => n))
const flush = async () => { await act(async () => { for (let i = 0; i < 20; i++) await Promise.resolve() }) }
const open = async (result: any, id: number) => {
  await act(async () => { await result.current.selectConversation({ id } as any) })
}
const send = (result: any, text: string) => {
  act(() => {
    void result.current.sendMessage(text, '', 'tr', 'auto', 'medium', vi.fn(), vi.fn(),
      undefined, false, undefined, 'user', undefined)
  })
}

// The backend's side: stored messages and the turn-event ring per chat.
let stored: Record<number, any[]>
let ring: Record<number, any[]>
const turnEventReads = () => mockedAxios.get.mock.calls.filter(c => String(c[0]).includes('/turn-events'))
/** Records a ring event and sends its id-only frame to every renderer stream. */
const emit = async (convId: number, kind: string, data: Record<string, unknown> = {}) => {
  const events = (ring[convId] ??= [])
  const seq = events.length + 1
  events.push({ ...data, seq, kind, ts: 0 })
  wakeStreams.forEach(push => push({ type: 'turn_live', conversation_id: convId, kind, seq }))
  await flush()
}

beforeEach(() => {
  installFetch()
  stored = {}
  ring = {}
  try { localStorage.clear() } catch { /* none */ }
  mockedAxios.post.mockReset().mockResolvedValue({ data: {} })
  mockedAxios.get.mockReset().mockImplementation(async (url: string) => {
    const u = String(url)
    if (u.includes('/context-usage')) return { data: { percent: 1, should_compact: false, message_count: 0 } }
    const events = u.match(/\/conversations\/(\d+)\/turn-events\?since=(\d+)$/)
    if (events) {
      const all = ring[Number(events[1])] ?? []
      const since = Number(events[2])
      return {
        data: {
          conversation_id: Number(events[1]), epoch: 'e1', gap: false,
          events: all.filter(e => e.seq > since), last_seq: all.length,
        },
      }
    }
    const messages = u.match(/\/conversations\/(\d+)\/messages$/)
    if (messages) return { data: stored[Number(messages[1])] ?? [] }
    return { data: [] }
  })
})

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

const USER_ROW = { id: 11, role: 'user', content: 'from the browser', smells: [], timestamp: 't1' }
const REPLY_ROW = { id: 12, role: 'assistant', content: 'Hello world', smells: [], timestamp: 't2' }

describe('turn_live · a renderer that did not start the turn', () => {
  it('shows the user message, the streamed text, then the stored messages', async () => {
    const { result } = hook()
    await open(result, 5)
    await flush()
    expect(result.current.messages).toEqual([])

    // The browser's turn: its user message is stored before turn_start.
    stored[5] = [USER_ROW]
    await emit(5, 'turn_start', { provider: 'claude', model: 'm', origin: 'user' })
    expect(result.current.messages.map((m: any) => [m.role, m.content])).toEqual([
      ['user', 'from the browser'], ['assistant', ''],
    ])
    expect(result.current.loading).toBe(true)

    await emit(5, 'text', { content: 'Hello' })
    await emit(5, 'tool_call', { name: 'Read', summary: 'a.ts' })
    await emit(5, 'text', { content: ' world' })
    expect(result.current.messages[1]).toMatchObject({ role: 'assistant', content: 'Hello world' })
    expect(result.current.loading).toBe(true)

    stored[5] = [USER_ROW, REPLY_ROW]
    await emit(5, 'turn_end', { status: 'done' })
    expect(result.current.messages).toEqual([USER_ROW, REPLY_ROW])
    expect(result.current.loading).toBe(false)
    // Nothing here opened /chat-stream: the turn was never this renderer's.
    expect(turns).toEqual([])
  })

  it('follows a chat in the background; a message typed meanwhile waits for the turn end', async () => {
    const { result } = hook()
    await open(result, 1)
    await flush()

    stored[5] = [USER_ROW]
    await emit(5, 'turn_start')
    await emit(5, 'text', { content: 'Hel' })
    expect(result.current.convStatus[5]).toBe('running')

    await open(result, 5)
    expect(result.current.messages.map((m: any) => m.content)).toEqual(['from the browser', 'Hel'])
    send(result, 'typed here')
    await flush()
    expect(turns).toEqual([])
    expect(result.current.queue.map((q: any) => q.text)).toEqual(['typed here'])

    stored[5] = [USER_ROW, REPLY_ROW]
    await emit(5, 'turn_end', { status: 'done' })
    expect(turns.map(t => [t.convId, t.message])).toEqual([[5, 'typed here']])
  })

  it('Stop on a followed turn goes through /chat-stop and clears the loading flag', async () => {
    const { result } = hook()
    await open(result, 5)
    stored[5] = [USER_ROW]
    await emit(5, 'turn_start')
    expect(result.current.loading).toBe(true)

    await act(async () => { await result.current.stopMessage() })
    expect(stops).toEqual([5])
    expect(result.current.loading).toBe(false)

    stored[5] = [USER_ROW, { ...REPLY_ROW, content: 'Hel' }]
    await emit(5, 'turn_end', { status: 'stopped' })
    expect(result.current.messages).toEqual(stored[5])
    expect(result.current.loading).toBe(false)
  })
})

describe('turn_live · a read or a store that comes late', () => {
  const sleep = async (ms: number) => { await act(async () => { await new Promise(r => setTimeout(r, ms)) }) }

  it('a failed turn-events read is retried and the chat stops loading', async () => {
    const { result } = hook()
    await open(result, 5)
    stored[5] = [USER_ROW]
    await emit(5, 'turn_start')
    expect(result.current.loading).toBe(true)

    const base = mockedAxios.get.getMockImplementation() as any
    let failed = false
    mockedAxios.get.mockImplementation(async (url: string) => {
      if (!failed && String(url).includes('/turn-events')) { failed = true; throw new Error('relay dropped') }
      return base(url)
    })
    await emit(5, 'text', { content: 'Hello world' })
    expect(failed).toBe(true)
    expect(result.current.messages.map((m: any) => m.content)).toEqual(['from the browser', ''])

    await sleep(700)
    expect(result.current.messages.map((m: any) => m.content)).toEqual(['from the browser', 'Hello world'])
    stored[5] = [USER_ROW, REPLY_ROW]
    await emit(5, 'turn_end', { status: 'done' })
    expect(result.current.loading).toBe(false)
    expect(result.current.messages).toEqual([USER_ROW, REPLY_ROW])
  })

  it('a reply stored after turn_end still appears, once', async () => {
    const { result } = hook()
    await open(result, 5)
    stored[5] = [USER_ROW]
    await emit(5, 'turn_start')
    await emit(5, 'text', { content: 'Hello world' })

    // The reads at turn end come back without the reply; it lands 1.5 s later.
    const gets = mockedAxios.get.getMockImplementation() as any
    const landAt = Date.now() + 1500
    mockedAxios.get.mockImplementation(async (url: string) => {
      if (String(url).endsWith('/messages') && Date.now() < landAt) return { data: [USER_ROW] }
      return gets(url)
    })
    stored[5] = [USER_ROW, REPLY_ROW]
    await emit(5, 'turn_end', { status: 'done' })

    await sleep(1250)
    expect(result.current.loading).toBe(false)
    expect(result.current.messages.map((m: any) => m.content)).toEqual(['from the browser', 'Hello world'])

    await sleep(2500)
    expect(result.current.messages).toEqual([USER_ROW, REPLY_ROW])
  })
})

describe('turn_live · the renderer running the turn itself', () => {
  it('ignores the frames of its own turn', async () => {
    const { result } = hook()
    await open(result, 5)
    send(result, 'typed here')
    await flush()
    expect(turns).toHaveLength(1)

    await emit(5, 'turn_start')
    await emit(5, 'text', { content: 'Hi' })
    turns[0].stream.push({ type: 'text', content: 'Hi' })
    await flush()
    await emit(5, 'turn_end', { status: 'done' })
    expect(turnEventReads()).toEqual([])
    expect(result.current.messages.map((m: any) => [m.role, m.content])).toEqual([
      ['user', 'typed here'], ['assistant', 'Hi'],
    ])

    turns[0].stream.push({ type: 'done', stop_reason: 'complete' })
    turns[0].stream.close()
    await flush()
    expect(result.current.loading).toBe(false)
    expect(turnEventReads()).toEqual([])
  })
})
