/**
 * A message handed to a running Claude turn ('sent') never gets stuck: when
 * its turn ends without the agent taking it in, it can be deleted, a
 * `user_injected_dropped` makes it an ordinary queued message, and after
 * 120 s with no turn running it reverts by itself. It is never sent twice
 * (audit FE-1, 7 Nov 2026). The harness below is message-queue.test.tsx's.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, fireEvent, cleanup, renderHook, act } from '@testing-library/react'

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
import { AnimatedChatInput } from '../renderer/components/ui/animated-ai-chat'
import { cevir } from '../renderer/lib/i18n'

const mockedAxios = axios as unknown as {
  post: ReturnType<typeof vi.fn>; get: ReturnType<typeof vi.fn>
}

const API = 'http://127.0.0.1:8000'
const USER = { id: 1, name: 'b', sessionToken: 'tok' } as any
// `subscription` keeps the `done` branch away from `parseGeneratedFiles`.
const CONFIG = { provider_type: 'subscription', model_name: 'claude-opus-5' } as any
// A chat on Codex: a message typed mid-turn keeps the plain queue (no inject).
const CODEX = { provider_type: 'subscription', model_name: 'gpt-6-luna' } as any

const enc = (ev: object) => new TextEncoder().encode(`data: ${JSON.stringify(ev)}\n\n`)

/** One SSE stream whose frames the test pushes by hand. */
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

type Turn = { convId: number; message: string; origin: string; stream: ReturnType<typeof makeStream> }
let turns: Turn[]
let stops: number[]
let fetchMock: ReturnType<typeof vi.fn>
let pushWake: (frame: object) => void
/** Bodies of /chat-inject requests, and what the backend answers them. */
let injects: Array<{ convId: number; message: string; client_id: string }>
let injectStatus: string

const installFetch = () => {
  turns = []
  stops = []
  injects = []
  injectStatus = 'not_running'
  let wakeWaiter: ((v: any) => void) | null = null
  const wakeFrames: Uint8Array[] = []
  pushWake = (frame: object) => {
    const bytes = enc(frame)
    if (wakeWaiter) { const w = wakeWaiter; wakeWaiter = null; w({ done: false, value: bytes }) }
    else wakeFrames.push(bytes)
  }
  fetchMock = vi.fn((url: string, init?: any) => {
    const u = String(url)
    if (u.endsWith('/chat-stream')) {
      const body = JSON.parse(init.body)
      const stream = makeStream()
      turns.push({ convId: body.conversation_id, message: body.message, origin: body.origin, stream })
      init.signal?.addEventListener('abort', () => {
        const e = new Error('aborted'); (e as any).name = 'AbortError'; stream.fail(e)
      })
      return Promise.resolve(stream.response)
    }
    if (u.includes('/wake-stream-all')) {
      return Promise.resolve({
        ok: true,
        body: {
          getReader: () => ({
            read: () => wakeFrames.length
              ? Promise.resolve({ done: false, value: wakeFrames.shift() })
              : new Promise(res => { wakeWaiter = res }),
          }),
        },
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
  })
  vi.stubGlobal('fetch', fetchMock)
}

const hook = (config: any = CONFIG) => renderHook(() => useChat(API, USER, config, '/ws', vi.fn(), vi.fn(), (n: string) => n))

/** Lets pending promise callbacks (fetch resolution, reader reads) run. */
const flush = async () => { await act(async () => { for (let i = 0; i < 15; i++) await Promise.resolve() }) }

const send = (result: any, text: string, draft?: string) => {
  act(() => {
    void result.current.sendMessage(text, '', 'tr', 'auto', 'medium', vi.fn(), vi.fn(),
      undefined, false, undefined, 'user', undefined, draft)
  })
}

/** Ends turn `i` cleanly. */
const finish = async (i: number) => {
  turns[i].stream.push({ type: 'done', stop_reason: 'complete' })
  turns[i].stream.close()
  await flush()
}

const userTurns = () => turns.filter(t => t.origin === 'user').map(t => t.message)

const open = async (result: any, id: number) => {
  await act(async () => { await result.current.selectConversation({ id } as any) })
}

beforeEach(() => {
  installFetch()
  mockedAxios.post.mockReset().mockResolvedValue({ data: {} })
  mockedAxios.get.mockReset().mockImplementation(async (url: string) => {
    if (String(url).includes('/context-usage')) return { data: { percent: 1, should_compact: false, message_count: 0 } }
    return { data: [] }
  })
})

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

const queueOf = (result: any) => result.current.queue.map((q: any) => [q.draft, q.state ?? null, !!q.turnEnded])

describe('a sent message whose turn ends without the agent taking it in', () => {
  it('stays sent but becomes deletable, and is never sent again by itself', async () => {
    injectStatus = 'injected'
    const { result } = hook()
    await open(result, 1)
    send(result, 'first')
    await flush()
    send(result, 'second')
    await flush()
    await finish(0)

    expect(queueOf(result)).toEqual([['second', 'sent', true]])
    expect(userTurns()).toEqual(['first'])
    // Edit would hand it back for a second send; only delete is offered.
    let edited: any
    act(() => { edited = result.current.editQueued(result.current.queue[0].id) })
    expect(edited).toBeNull()
    act(() => { result.current.deleteQueued(result.current.queue[0].id) })
    expect(result.current.queue).toEqual([])
    expect(userTurns()).toEqual(['first'])
  })

  it('a drop frame after the turn makes it an ordinary queued message', async () => {
    injectStatus = 'injected'
    const { result } = hook()
    await open(result, 1)
    send(result, 'first')
    await flush()
    send(result, 'second')
    await flush()
    await finish(0)
    act(() => { pushWake({ type: 'user_injected_dropped', conversation_id: 1, client_ids: [injects[0].client_id] }) })
    await flush()
    // An ordinary queued message, sent once by the drain.
    expect(userTurns()).toEqual(['first', 'second'])
    expect(result.current.queue).toEqual([])
  })

  it('reverts to queued after 120 s with no turn running, without sending', async () => {
    injectStatus = 'injected'
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
    try {
      const { result } = hook()
      await open(result, 1)
      send(result, 'first')
      await flush()
      send(result, 'second')
      await flush()
      await finish(0)
      act(() => { vi.advanceTimersByTime(119_000) })
      expect(queueOf(result)).toEqual([['second', 'sent', true]])
      act(() => { vi.advanceTimersByTime(2_000) })
      expect(queueOf(result)).toEqual([['second', null, false]])
      expect(userTurns()).toEqual(['first'])
    } finally {
      vi.useRealTimers()
    }
  })

  it('after Stop the sent message is deletable too', async () => {
    injectStatus = 'injected'
    const { result } = hook()
    await open(result, 1)
    send(result, 'first')
    await flush()
    send(result, 'second')
    await flush()
    await act(async () => { await result.current.stopMessage() })
    await flush()
    expect(queueOf(result)).toEqual([['second', 'sent', true]])
    expect(userTurns()).toEqual(['first'])
  })
})

describe('the queue strip for a sent message whose turn ended', () => {
  it('offers delete only', () => {
    const onDelete = vi.fn()
    render(
      <AnimatedChatInput
        value="" setValue={vi.fn()} onSendMessage={vi.fn()} onStop={vi.fn()}
        isLoading={false} api="http://127.0.0.1:1"
        queue={{ items: [{ id: 3, draft: 'unread', state: 'sent', turnEnded: true }], paused: false,
          onEdit: vi.fn(), onDelete, onSendNow: vi.fn(), onResume: vi.fn() }}
      />,
    )
    expect(document.querySelector('[data-queue-edit]')).toBeNull()
    expect(document.querySelector('[data-queue-send-now]')).toBeNull()
    fireEvent.click(document.querySelector('[data-queue-delete]')!)
    expect(onDelete).toHaveBeenCalledWith(3)
  })
})
