/**
 * MESSAGE QUEUE. Owner: "a message typed while the agent runs must not stop
 * the turn; it waits in a queue and is sent when the turn ends."
 *
 * The hook tests drive the real `useChat` with hand-fed SSE streams, one per
 * `/chat-stream` request, so a turn ends exactly when the test says so. What
 * is asserted is what went over the wire (which request, in which order, to
 * which chat) and what the hook hands the screen.
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

describe('message queue · enqueue', () => {
  it('a send while the chat runs is queued, not sent, and does not stop the turn', async () => {
    const { result } = hook()
    await open(result, 1)
    send(result, 'first')
    await flush()
    act(() => { result.current.setChatInput('second') })
    send(result, 'second (files inlined)', 'second')
    await flush()

    expect(turns.length).toBe(1)
    expect(stops).toEqual([])
    expect(result.current.loading).toBe(true)
    expect(result.current.queue.map((q: any) => q.draft)).toEqual(['second'])
    expect(result.current.queue[0].text).toBe('second (files inlined)')
    expect(result.current.chatInput).toBe('')
  })
})

describe('message queue · drain', () => {
  it('after a clean finish the queue goes out in order, one message per turn', async () => {
    const { result } = hook()
    await open(result, 1)
    send(result, 'first')
    await flush()
    send(result, 'second')
    send(result, 'third')
    await flush()
    expect(userTurns()).toEqual(['first'])

    await finish(0)
    expect(userTurns()).toEqual(['first', 'second'])
    expect(turns[1].convId).toBe(1)
    expect(result.current.queue.map((q: any) => q.draft)).toEqual(['third'])
    expect(result.current.loading).toBe(true)

    await finish(1)
    expect(userTurns()).toEqual(['first', 'second', 'third'])
    expect(result.current.queue).toEqual([])

    await finish(2)
    expect(turns.length).toBe(3)
    expect(result.current.loading).toBe(false)
  })

  it('a chat in the background drains without touching the composer or the screen', async () => {
    const { result } = hook()
    await open(result, 1)
    send(result, 'A first')
    await flush()
    send(result, 'A queued')
    await flush()

    await open(result, 2)
    act(() => { result.current.setChatInput('draft typed in B') })
    await finish(0)

    expect(userTurns()).toEqual(['A first', 'A queued'])
    expect(turns[1].convId).toBe(1)
    expect(result.current.activeConvId).toBe(2)
    expect(result.current.chatInput).toBe('draft typed in B')
    // The screen shows B, whose composer and loading flag are untouched.
    expect(result.current.loading).toBe(false)
  })
})

describe('message queue · pause', () => {
  // Claude Code's Stop: "If you have messages queued, Claude Code sends them next".
  it('Stop sends the queued messages next, once the backend has stopped the turn', async () => {
    const { result } = hook()
    await open(result, 1)
    send(result, 'first')
    await flush()
    send(result, 'second')
    await flush()

    await act(async () => { await result.current.stopMessage() })
    await flush()
    expect(stops).toEqual([1])
    const stopAt = fetchMock.mock.calls.findIndex(c => String(c[0]).endsWith('/chat-stop/1'))
    const secondAt = fetchMock.mock.calls.findIndex(
      c => String(c[0]).endsWith('/chat-stream') && JSON.parse(c[1].body).message === 'second')
    expect(secondAt).toBeGreaterThan(stopAt)
    expect(userTurns()).toEqual(['first', 'second'])
    expect(result.current.queuePaused).toBe(false)
    expect(result.current.queue).toEqual([])
  })

  it('an error ending the turn pauses the queue instead of cascading', async () => {
    const { result } = hook()
    await open(result, 1)
    send(result, 'first')
    await flush()
    send(result, 'second')
    await flush()

    turns[0].stream.push({ type: 'error', message: 'boom' })
    turns[0].stream.close()
    await flush()

    expect(userTurns()).toEqual(['first'])
    expect(result.current.queuePaused).toBe(true)
    expect(result.current.queue.length).toBe(1)

    act(() => { result.current.resumeQueue() })
    await flush()
    expect(userTurns()).toEqual(['first', 'second'])
  })

  it('a stream that ends without done/response also pauses', async () => {
    const { result } = hook()
    await open(result, 1)
    send(result, 'first')
    await flush()
    send(result, 'second')
    await flush()
    turns[0].stream.close()
    await flush()
    expect(userTurns()).toEqual(['first'])
    expect(result.current.queuePaused).toBe(true)
  })
})

describe('message queue · edit, delete, send now', () => {
  it('edit hands the message back and removes it; delete removes it', async () => {
    const { result } = hook()
    await open(result, 1)
    send(result, 'first')
    await flush()
    send(result, 'second')
    send(result, 'third')
    await flush()
    const [second, third] = result.current.queue

    let taken: any
    act(() => { taken = result.current.editQueued(second.id) })
    expect(taken.draft).toBe('second')
    expect(result.current.queue.map((q: any) => q.id)).toEqual([third.id])

    act(() => { result.current.deleteQueued(third.id) })
    expect(result.current.queue).toEqual([])

    await finish(0)
    expect(userTurns()).toEqual(['first'])
  })

  it('removing the last message of a paused queue clears the pause', async () => {
    const { result } = hook()
    await open(result, 1)
    send(result, 'first')
    await flush()
    send(result, 'second')
    await flush()
    turns[0].stream.push({ type: 'error', message: 'boom' })
    turns[0].stream.close()
    await flush()
    expect(result.current.queuePaused).toBe(true)
    act(() => { result.current.deleteQueued(result.current.queue[0].id) })
    expect(result.current.queuePaused).toBe(false)
  })

  it('"send now" on a Codex chat stops the running turn, then sends that message first', async () => {
    const { result } = hook(CODEX)
    await open(result, 1)
    send(result, 'first')
    await flush()
    send(result, 'second')
    send(result, 'third')
    await flush()
    const third = result.current.queue[1]

    await act(async () => { await result.current.sendQueuedNow(third.id) })
    await flush()

    expect(stops).toEqual([1])
    // Stop was requested before the new turn went out.
    const stopAt = fetchMock.mock.calls.findIndex(c => String(c[0]).endsWith('/chat-stop/1'))
    const thirdAt = fetchMock.mock.calls.findIndex(
      c => String(c[0]).endsWith('/chat-stream') && JSON.parse(c[1].body).message === 'third')
    expect(stopAt).toBeGreaterThan(-1)
    expect(thirdAt).toBeGreaterThan(stopAt)
    expect(userTurns()).toEqual(['first', 'third'])
    expect(result.current.queuePaused).toBe(false)
    expect(result.current.queue.map((q: any) => q.draft)).toEqual(['second'])

    // The rest of the queue carries on normally.
    await finish(1)
    expect(userTurns()).toEqual(['first', 'third', 'second'])
  })

  /** Holds every `/chat-stop` answer until the returned release is called. */
  const holdStops = () => {
    const original = fetchMock.getMockImplementation() as (url: string, init?: any) => unknown
    const releases: Array<(value: any) => void> = []
    fetchMock.mockImplementation((url: string, init?: any) =>
      String(url).includes('/chat-stop/')
        ? (stops.push(Number(String(url).split('/').pop())),
          new Promise(resolve => { releases.push(resolve) }))
        : original(url, init))
    return () => releases.splice(0).forEach(release => release({ ok: true }))
  }

  // Codex queueaudit, 28 Sep 2026: the next queued message went out unasked.
  it('"send now" on a Codex chat whose message is deleted while Stop is pending sends nothing', async () => {
    const { result } = hook(CODEX)
    await open(result, 1)
    send(result, 'first')
    await flush()
    send(result, 'remove me')
    send(result, 'keep queued')
    await flush()
    const selected = result.current.queue[0]
    const releaseStops = holdStops()

    let sending: Promise<boolean>
    act(() => { sending = result.current.sendQueuedNow(selected.id) })
    act(() => { result.current.deleteQueued(selected.id) })
    let sent: boolean | undefined
    await act(async () => { releaseStops(); sent = await sending! })
    await flush()

    expect(sent).toBe(false)
    expect(userTurns()).toEqual(['first'])
    expect(result.current.queue.map((q: any) => q.draft)).toEqual(['keep queued'])
    expect(result.current.queuePaused).toBe(true)
  })

  it('"send now" on a Codex chat whose message is taken back for editing while Stop is pending sends nothing', async () => {
    const { result } = hook(CODEX)
    await open(result, 1)
    send(result, 'first')
    await flush()
    send(result, 'keep queued')
    send(result, 'edit me')
    await flush()
    const selected = result.current.queue[1]
    const releaseStops = holdStops()

    let sending: Promise<boolean>
    act(() => { sending = result.current.sendQueuedNow(selected.id) })
    act(() => { result.current.editQueued(selected.id) })
    await act(async () => { releaseStops(); await sending! })
    await flush()

    expect(userTurns()).toEqual(['first'])
    expect(result.current.queue.map((q: any) => q.draft)).toEqual(['keep queued'])
    expect(result.current.queuePaused).toBe(true)
  })

  it('a second "send now" on a Codex chat click on the same message does not send it before Stop is answered', async () => {
    const { result } = hook(CODEX)
    await open(result, 1)
    send(result, 'first')
    await flush()
    send(result, 'second')
    send(result, 'third')
    await flush()
    const third = result.current.queue[1]
    const releaseStops = holdStops()

    let first: Promise<boolean>
    let second: Promise<boolean>
    act(() => { first = result.current.sendQueuedNow(third.id) })
    act(() => { second = result.current.sendQueuedNow(third.id) })
    await flush()
    expect(userTurns()).toEqual(['first'])

    let sent: boolean[] = []
    await act(async () => { releaseStops(); sent = await Promise.all([first!, second!]) })
    await flush()

    expect(sent).toEqual([true, false])
    expect(stops).toEqual([1])
    expect(userTurns()).toEqual(['first', 'third'])
    expect(result.current.queue.map((q: any) => q.draft)).toEqual(['second'])
  })
})

describe('message queue · turn ownership', () => {
  // Codex queueaudit, 28 Sep 2026: the ownership token was the assistant
  // message id (`Date.now() + 1`), so the stopped turn's cleanup cleared the
  // replacement turn started in the same millisecond.
  it('a stopped turn cannot clear a replacement turn created in the same millisecond', async () => {
    const clock = vi.spyOn(Date, 'now').mockReturnValue(123456)
    try {
      const { result } = hook()
      await open(result, 1)
      send(result, 'first')
      await flush()
      send(result, 'queued')
      await flush()

      act(() => {
        void result.current.stopMessage()
        result.current.resumeQueue()
      })
      expect(userTurns()).toEqual(['first', 'queued'])
      await flush()
      expect(result.current.loading).toBe(true)

      await finish(1)
      expect(result.current.loading).toBe(false)
    } finally {
      clock.mockRestore()
    }
  })

  // Codex queueaudit, 28 Sep 2026: both turns got id `Date.now() + 1`, so the
  // replacement's text was written into the stopped turn's bubble as well.
  it('two turns started in one millisecond get distinct ids and keep their own text', async () => {
    const clock = vi.spyOn(Date, 'now').mockReturnValue(654321)
    try {
      const { result } = hook()
      await open(result, 1)
      send(result, 'first')
      await flush()
      turns[0].stream.push({ type: 'text', content: 'old answer' })
      await flush()
      send(result, 'queued')
      await flush()

      act(() => {
        void result.current.stopMessage()
        result.current.resumeQueue()
      })
      await flush()
      turns[1].stream.push({ type: 'text', content: 'new answer' })
      await flush()

      const ids = result.current.messages.map((m: any) => m.id)
      expect(new Set(ids).size).toBe(ids.length)
      expect(result.current.messages.filter((m: any) => m.role === 'assistant')
        .map((m: any) => m.content)).toEqual(['old answer', 'new answer'])
    } finally {
      clock.mockRestore()
    }
  })
})

describe('message queue · wake turns and busy chats', () => {
  it('a wake frame at turn end does not overtake a queued user message', async () => {
    const { result } = hook()
    await open(result, 1)
    send(result, 'first')
    await flush()
    send(result, 'second')
    await flush()

    await finish(0)
    act(() => { pushWake({ type: 'wake', conversation_id: 1, count: 1, notices: ['n'], text: 'wake text' }) })
    await flush()

    expect(turns.map(t => [t.origin, t.message])).toEqual([['user', 'first'], ['user', 'second']])
  })

  it('a wake frame while a queue waits paused does not start a turn', async () => {
    const { result } = hook()
    await open(result, 1)
    send(result, 'first')
    await flush()
    send(result, 'second')
    await flush()
    turns[0].stream.push({ type: 'error', message: 'boom' })
    turns[0].stream.close()
    await flush()

    act(() => { pushWake({ type: 'wake', conversation_id: 1, count: 1, notices: ['n'], text: 'wake text' }) })
    await flush()

    expect(turns.map(t => t.origin)).toEqual(['user'])
    expect(result.current.queue.length).toBe(1)
  })

  it('a wake still runs when the queue is empty', async () => {
    const { result } = hook()
    await open(result, 1)
    send(result, 'first')
    await flush()
    await finish(0)
    act(() => { pushWake({ type: 'wake', conversation_id: 1, count: 1, notices: ['n'], text: 'wake text' }) })
    await flush()
    expect(turns.map(t => [t.origin, t.message])).toEqual([['user', 'first'], ['wake', 'wake text']])
  })

  it('"send next" on a busy chat keeps the message and sends it at the next turn end', async () => {
    const { result } = hook()
    await open(result, 1)
    send(result, 'first')
    await flush()
    send(result, 'queued')
    await flush()
    turns[0].stream.push({ type: 'error', message: 'boom' })
    turns[0].stream.close()
    await flush()

    // The paused chat is idle, so a new message goes straight out.
    send(result, 'direct')
    await flush()
    expect(userTurns()).toEqual(['first', 'direct'])

    let sent: boolean | undefined
    act(() => { sent = result.current.resumeQueue() })
    await flush()
    expect(sent).toBe(false)
    expect(result.current.queue.map((q: any) => q.draft)).toEqual(['queued'])
    expect(result.current.queuePaused).toBe(false)

    await finish(1)
    expect(userTurns()).toEqual(['first', 'direct', 'queued'])
  })
})

describe('message queue · mid-turn send on a Claude chat', () => {
  it('Enter while the turn runs goes to /chat-inject as "sent" and never stops the turn', async () => {
    injectStatus = 'injected'
    const { result } = hook()
    await open(result, 1)
    send(result, 'first')
    await flush()
    send(result, 'also check the lights')
    await flush()

    expect(injects.map(i => [i.convId, i.message])).toEqual([[1, 'also check the lights']])
    expect(stops).toEqual([])
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/chat-stop/'))).toBe(false)
    expect(result.current.queue.map((q: any) => [q.draft, q.state])).toEqual([['also check the lights', 'sent']])
    expect(result.current.loading).toBe(true)

    // A 'sent' message never goes out again at the turn end, and cannot be taken back.
    let taken: any
    act(() => { taken = result.current.editQueued(result.current.queue[0].id) })
    expect(taken).toBeNull()
    await finish(0)
    expect(userTurns()).toEqual(['first'])
  })

  it('user_injected moves the message into the thread; the answer after it is a new bubble', async () => {
    injectStatus = 'injected'
    const { result } = hook()
    await open(result, 1)
    send(result, 'build the scene')
    await flush()
    turns[0].stream.push({ type: 'text', content: 'placing the floor' })
    await flush()
    send(result, 'use the red cube')
    await flush()
    const clientId = injects[0].client_id
    expect(clientId).toBeTruthy()

    turns[0].stream.push({ type: 'user_injected', client_id: clientId, text: 'use the red cube' })
    turns[0].stream.push({ type: 'text', content: 'red cube placed' })
    await flush()

    expect(result.current.queue).toEqual([])
    expect(result.current.messages.map((m: any) => [m.role, m.content])).toEqual([
      ['user', 'build the scene'],
      ['assistant', 'placing the floor'],
      ['user', 'use the red cube'],
      ['assistant', 'red cube placed'],
    ])
    await finish(0)
    expect(userTurns()).toEqual(['build the scene'])
  })

  it('user_injected_dropped puts the message back in the queue as unsent', async () => {
    injectStatus = 'injected'
    const { result } = hook()
    await open(result, 1)
    send(result, 'first')
    await flush()
    send(result, 'second')
    await flush()
    turns[0].stream.push({ type: 'user_injected_dropped', client_ids: [injects[0].client_id] })
    await flush()
    expect(result.current.queue.map((q: any) => [q.draft, q.state])).toEqual([['second', undefined]])
    await finish(0)
    expect(userTurns()).toEqual(['first', 'second'])
  })

  it('a user_injected frame on the wake channel takes the message out of the queue', async () => {
    injectStatus = 'injected'
    const { result } = hook()
    await open(result, 1)
    send(result, 'first')
    await flush()
    send(result, 'after the stop')
    await flush()
    act(() => { pushWake({ type: 'user_injected', conversation_id: 1, client_id: injects[0].client_id, text: 'after the stop' }) })
    await flush()
    expect(result.current.queue).toEqual([])
  })

  it('"send now" on a Claude chat injects the message and does not call /chat-stop', async () => {
    const { result } = hook()
    await open(result, 1)
    send(result, 'first')
    await flush()
    send(result, 'second')  // answered not_running: an ordinary queued message
    await flush()
    expect(result.current.queue.map((q: any) => q.state)).toEqual([undefined])

    injectStatus = 'injected'
    let sent: boolean | undefined
    await act(async () => { sent = await result.current.sendQueuedNow(result.current.queue[0].id) })
    await flush()

    expect(sent).toBe(true)
    expect(stops).toEqual([])
    expect(injects.map(i => i.message)).toEqual(['second', 'second'])
    expect(userTurns()).toEqual(['first'])
    expect(result.current.loading).toBe(true)
    expect(result.current.queue.map((q: any) => q.state)).toEqual(['sent'])
  })

  it('not_running keeps the message an ordinary queued one, sent at the turn end', async () => {
    const { result } = hook()
    await open(result, 1)
    send(result, 'first')
    await flush()
    send(result, 'second')
    await flush()
    expect(injects.map(i => i.message)).toEqual(['second'])
    expect(result.current.queue.map((q: any) => [q.draft, q.state])).toEqual([['second', undefined]])
    await finish(0)
    expect(userTurns()).toEqual(['first', 'second'])
  })

  it('a message with a picture keeps the plain queue', async () => {
    injectStatus = 'injected'
    const { result } = hook()
    await open(result, 1)
    send(result, 'first')
    await flush()
    act(() => {
      void result.current.sendMessage('see this', '', 'tr', 'auto', 'medium', vi.fn(), vi.fn(),
        ['data:image/png;base64,AA'], false, undefined, 'user')
    })
    await flush()
    expect(injects).toEqual([])
    expect(result.current.queue.map((q: any) => q.state)).toEqual([undefined])
  })

  it('a chat on Codex keeps the plain queue', async () => {
    injectStatus = 'injected'
    const { result } = hook(CODEX)
    await open(result, 1)
    send(result, 'first')
    await flush()
    send(result, 'second')
    await flush()
    expect(injects).toEqual([])
  })
})

describe('message queue · composer', () => {
  const mount = (isLoading: boolean, queue?: any) => {
    const onSendMessage = vi.fn()
    const onStop = vi.fn()
    render(
      <AnimatedChatInput
        value="" setValue={vi.fn()} onSendMessage={onSendMessage} onStop={onStop}
        isLoading={isLoading} api="http://127.0.0.1:1" queue={queue}
      />,
    )
    const textarea = screen.getByRole('textbox') as HTMLTextAreaElement
    return { onSendMessage, onStop, textarea }
  }

  it('Enter while the chat runs hands the message on and keeps Stop available', () => {
    const { onSendMessage, textarea } = mount(true)
    expect(screen.getByText(cevir('composer.stop'))).toBeTruthy()
    expect(document.querySelector('[data-send-button]')).toBeNull()

    fireEvent.change(textarea, { target: { value: 'later please' } })
    expect(screen.getByText(cevir('composer.stop'))).toBeTruthy()
    expect(document.querySelector('[data-send-button]')).not.toBeNull()

    fireEvent.keyDown(textarea, { key: 'Enter' })
    expect(onSendMessage).toHaveBeenCalledWith('later please', [], [])
    expect(textarea.value).toBe('')
  })

  it('draws the queue with edit, delete and send-now; paused shows "send next"', () => {
    const item = { id: 7, draft: 'queued text', images: ['data:image/png;base64,AA'] }
    const queue = {
      items: [item], paused: true,
      onEdit: vi.fn(() => item), onDelete: vi.fn(), onSendNow: vi.fn(), onResume: vi.fn(),
    }
    const { textarea } = mount(true, queue)
    expect(screen.getByText(cevir('queue.paused'), { exact: false })).toBeTruthy()
    expect(screen.getByText('queued text', { exact: false })).toBeTruthy()

    fireEvent.click(document.querySelector('[data-queue-resume]')!)
    expect(queue.onResume).toHaveBeenCalled()
    fireEvent.click(document.querySelector('[data-queue-send-now]')!)
    expect(queue.onSendNow).toHaveBeenCalledWith(7)
    fireEvent.click(document.querySelector('[data-queue-delete]')!)
    expect(queue.onDelete).toHaveBeenCalledWith(7)

    fireEvent.click(document.querySelector('[data-queue-edit]')!)
    expect(queue.onEdit).toHaveBeenCalledWith(7)
    expect(textarea.value).toBe('queued text')
  })

  it('Esc while the chat runs is Stop', () => {
    const { onStop, textarea } = mount(true)
    fireEvent.keyDown(textarea, { key: 'Escape' })
    expect(onStop).toHaveBeenCalledTimes(1)
  })

  it('a held Esc (repeated keydown) is one Stop', () => {
    const { onStop, textarea } = mount(true)
    fireEvent.keyDown(textarea, { key: 'Escape' })
    fireEvent.keyDown(textarea, { key: 'Escape', repeat: true })
    fireEvent.keyDown(textarea, { key: 'Escape', repeat: true })
    expect(onStop).toHaveBeenCalledTimes(1)
  })

  it('Esc with nothing running stops nothing', () => {
    const { onStop, textarea } = mount(false)
    fireEvent.keyDown(textarea, { key: 'Escape' })
    expect(onStop).not.toHaveBeenCalled()
  })

  it('a sent message shows its label and no edit, send-now or delete', () => {
    const queue = {
      items: [{ id: 8, draft: 'read it next', state: 'sent' }], paused: false,
      onEdit: vi.fn(), onDelete: vi.fn(), onSendNow: vi.fn(), onResume: vi.fn(),
    }
    mount(true, queue)
    expect(screen.getByText(cevir('queue.sent'), { exact: false })).toBeTruthy()
    expect(document.querySelector('[data-queue-edit]')).toBeNull()
    expect(document.querySelector('[data-queue-send-now]')).toBeNull()
    expect(document.querySelector('[data-queue-delete]')).toBeNull()
  })

  it('draws nothing for an empty queue', () => {
    mount(true, { items: [], paused: false, onEdit: vi.fn(), onDelete: vi.fn(), onSendNow: vi.fn(), onResume: vi.fn() })
    expect(document.querySelector('[data-message-queue]')).toBeNull()
  })
})
