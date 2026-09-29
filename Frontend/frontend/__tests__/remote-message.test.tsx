/**
 * Phone messages (remote control, docs/remote-control.md step 4). The backend
 * hands a phone's `send_message` to the renderer as a `remote_message` frame
 * on `/wake-stream-all`; the renderer sends it like a typed message: queued
 * while that chat runs, otherwise a user turn addressed by conversation id.
 * Every renderer stream gets the frame, so it must be sent exactly once.
 *
 * Harness: the real `useChat` with hand-fed SSE streams, as in
 * `message-queue.test.tsx`.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, renderHook, act } from '@testing-library/react'

vi.mock('axios', () => {
  const post = vi.fn()
  const get = vi.fn()
  const del = vi.fn()
  const put = vi.fn()
  return { default: { post, get, delete: del, put }, post, get }
})

import axios from 'axios'
import { useChat } from '../renderer/hooks/home/useChat'
import { ChatPanel } from '../renderer/components/home/ChatPanel'
import {
  claimRemoteMessage, parseRemoteMessage, resetRemoteClaimsForTests,
} from '../renderer/lib/remoteControl'

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

type Turn = { convId: number; message: string; origin: string; effort?: string; stream: ReturnType<typeof makeStream> }
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
      turns.push({ convId: body.conversation_id, message: body.message, origin: body.origin, effort: body.effort_level, stream })
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
const defaults = (result: any) => act(() => {
  result.current.setWakeDefaults({
    lang: 'tr', genMode: 'balanced', thinkingLevel: 'medium',
    setPendingGenFiles: vi.fn(), setPendingDelete: vi.fn(),
  })
})
const send = (result: any, text: string) => {
  act(() => {
    void result.current.sendMessage(text, '', 'tr', 'auto', 'medium', vi.fn(), vi.fn(),
      undefined, false, undefined, 'user', undefined)
  })
}
const finish = async (i: number) => {
  turns[i].stream.push({ type: 'done', stop_reason: 'complete' })
  turns[i].stream.close()
  await flush()
}
const open = async (result: any, id: number) => {
  await act(async () => { await result.current.selectConversation({ id } as any) })
}

let seq = 0
const frame = (conversationId: number, text: string, extra: Record<string, unknown> = {}) => ({
  type: 'remote_message', request_id: `req${++seq}`, conversation_id: conversationId, text,
  source: 'phone', device_id: 'dev_1', device_name: 'iPhone', at: Date.now(), ...extra,
})
const pushAll = (f: object) => wakeStreams.forEach(push => push(f))

beforeEach(() => {
  installFetch()
  resetRemoteClaimsForTests()
  try { localStorage.clear() } catch { /* none */ }
  mockedAxios.post.mockReset().mockResolvedValue({ data: {} })
  mockedAxios.get.mockReset().mockImplementation(async (url: string) => {
    if (String(url).includes('/context-usage')) return { data: { percent: 1, should_compact: false, message_count: 0 } }
    return { data: [] }
  })
})

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('phone message · idle chat', () => {
  it('goes out as a user turn to its own chat; the screen and composer stay as they are', async () => {
    const { result } = hook()
    defaults(result)
    await open(result, 1)
    act(() => { result.current.setChatInput('half-typed draft') })
    await flush()

    pushAll(frame(5, 'phone says hi'))
    await flush()

    expect(turns.map(t => [t.convId, t.message, t.origin])).toEqual([[5, 'phone says hi', 'user']])
    expect(result.current.activeConvId).toBe(1)
    expect(result.current.chatInput).toBe('half-typed draft')
    // A chat never opened here is read first, like a wake does.
    expect(mockedAxios.get.mock.calls.some(c => String(c[0]).endsWith('/conversations/5/messages'))).toBe(true)
  })

  it('the user bubble carries the phone marker, and it survives the turn ending off screen', async () => {
    const { result } = hook()
    defaults(result)
    await open(result, 1)
    await flush()
    pushAll(frame(5, 'from the phone'))
    await flush()
    await finish(0)
    await open(result, 5)
    const bubble = result.current.messages.find((m: any) => m.role === 'user')
    expect(bubble).toMatchObject({ content: 'from the phone', source: 'phone', sourceDevice: 'iPhone' })
  })
})

describe('phone message · running chat', () => {
  it('is queued, does not stop the turn, and goes out when the turn ends', async () => {
    const { result } = hook()
    defaults(result)
    await open(result, 1)
    send(result, 'typed first')
    await flush()

    pushAll(frame(1, 'phone second'))
    await flush()
    expect(turns.length).toBe(1)
    expect(stops).toEqual([])
    expect(result.current.queue.map((q: any) => [q.text, q.remote])).toEqual([['phone second', { device: 'iPhone' }]])

    await finish(0)
    expect(turns.map(t => t.message)).toEqual(['typed first', 'phone second'])
    expect(turns[1].convId).toBe(1)
    const bubble = result.current.messages.filter((m: any) => m.role === 'user')[1]
    expect(bubble).toMatchObject({ source: 'phone', sourceDevice: 'iPhone' })
  })
})

describe('phone message · exactly once', () => {
  it('the same request id delivered twice (or to two windows) is sent once', async () => {
    const a = hook()
    const b = hook()
    defaults(a.result)
    defaults(b.result)
    await flush()
    expect(wakeStreams.length).toBe(2)

    const f = frame(9, 'only once')
    pushAll(f)
    pushAll(f)
    await flush()
    expect(turns.map(t => t.message)).toEqual(['only once'])
  })

  it('a claim is also kept across windows (shared storage), not only in this one', async () => {
    expect(await claimRemoteMessage('abc')).toBe(true)
    expect(await claimRemoteMessage('abc')).toBe(false)
    resetRemoteClaimsForTests() // another window: its own in-memory set, same storage
    expect(await claimRemoteMessage('abc')).toBe(false)
    expect(await claimRemoteMessage('abd')).toBe(true)
  })

  it('a frame that arrives before the page chose its send options waits for them', async () => {
    const { result } = hook()
    await flush()
    pushAll(frame(3, 'early'))
    await flush()
    expect(turns).toEqual([])
    defaults(result)
    await flush()
    expect(turns.map(t => [t.convId, t.message])).toEqual([[3, 'early']])
  })

  it('malformed frames are ignored', () => {
    expect(parseRemoteMessage({ ...frame(1, 'x'), source: 'desktop' })).toBeNull()
    expect(parseRemoteMessage({ ...frame(0, 'x') })).toBeNull()
    expect(parseRemoteMessage({ ...frame(1, '   ') })).toBeNull()
    expect(parseRemoteMessage({ ...frame(1, 'x'), request_id: '../x' })).toBeNull()
    expect(parseRemoteMessage(frame(2, 'ok'))).toMatchObject({ conversationId: 2, text: 'ok', deviceName: 'iPhone' })
    // Owner decision, 28 Sep 2026: commands are ordinary phone text now.
    for (const text of ['/usage', '  /compact', '\u200b/model x', '/skill arg', 'yol: /tmp/x']) {
      expect(parseRemoteMessage(frame(1, text)), text).toMatchObject({ text })
    }
  })
})

describe('phone message - effort', () => {
  it('a level chosen on the phone replaces the page default for that message only', async () => {
    const { result } = hook()
    defaults(result)
    await open(result, 1)
    pushAll(frame(5, 'think hard', { effort: 'xhigh' }))
    await flush()
    await finish(0)
    pushAll(frame(5, 'no choice'))
    await flush()
    expect(turns.map(t => [t.message, t.effort])).toEqual([['think hard', 'xhigh'], ['no choice', 'medium']])
  })

  it('a queued phone message keeps its level until its turn starts', async () => {
    const { result } = hook()
    defaults(result)
    await open(result, 1)
    send(result, 'typed first')
    await flush()
    pushAll(frame(1, 'phone second', { effort: 'max' }))
    await flush()
    expect(result.current.queue.map((q: any) => [q.text, q.thinkingLevel])).toEqual([['phone second', 'max']])
    await finish(0)
    expect(turns.map(t => [t.message, t.effort])).toEqual([['typed first', 'medium'], ['phone second', 'max']])
  })

  it('every level the registry can return goes through, auto, off and none included', () => {
    for (const level of ['auto', 'off', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']) {
      expect(parseRemoteMessage(frame(1, 'x', { effort: level })), level).toMatchObject({ effort: level })
    }
  })

  it('a level the page does not know makes the frame invalid; null or absent means none', () => {
    for (const bad of ['turbo', 'HIGH', ' high', '', 'None', 3, true, ['high'], {}]) {
      expect(parseRemoteMessage(frame(1, 'x', { effort: bad })), JSON.stringify(bad)).toBeNull()
    }
    expect(parseRemoteMessage(frame(1, 'x', { effort: null }))).not.toHaveProperty('effort')
    expect(parseRemoteMessage(frame(1, 'x'))).not.toHaveProperty('effort')
  })
})

describe('phone message \u00b7 slash commands', () => {
  const compactCalls = () => mockedAxios.post.mock.calls.map(c => String(c[0])).filter(u => u.endsWith('/compact'))

  beforeEach(() => {
    mockedAxios.post.mockImplementation(async (url: string) =>
      String(url).endsWith('/compact') ? { data: { status: 'success', summary: 'short' } } : { data: {} })
  })

  it('/compact compacts the addressed chat, not the one on screen, and starts no turn', async () => {
    const { result } = hook()
    defaults(result)
    await open(result, 1)
    await flush()

    pushAll(frame(5, '/compact'))
    await flush()

    expect(compactCalls()).toEqual([`${API}/conversations/5/compact`])
    expect(mockedAxios.get.mock.calls.some(c => String(c[0]).endsWith('/conversations/5/messages'))).toBe(true)
    expect(turns).toEqual([])
    expect(result.current.activeConvId).toBe(1)
    // Chat 5 is not on screen: the composer button of chat 1 must not spin.
    expect(result.current.isCompacting).toBe(false)
  })

  it('surrounding blanks do not matter; "/compact now" is not the command', async () => {
    const { result } = hook()
    defaults(result)
    await open(result, 1)
    pushAll(frame(5, '  /compact \n'))
    await flush()
    expect(compactCalls()).toEqual([`${API}/conversations/5/compact`])
    expect(turns).toEqual([])

    pushAll(frame(6, '/compact now'))
    await flush()
    expect(compactCalls().length).toBe(1)
    expect(turns.map(t => [t.convId, t.message])).toEqual([[6, '/compact now']])
  })

  it('a /compact for the chat on screen shows the button as busy until it ends', async () => {
    const { result } = hook()
    defaults(result)
    await open(result, 1)
    let finishCompact: (v: unknown) => void = () => {}
    mockedAxios.post.mockImplementation((url: string) => String(url).endsWith('/compact')
      ? new Promise(res => { finishCompact = res })
      : Promise.resolve({ data: {} }))
    pushAll(frame(1, '/compact'))
    await flush()
    expect(result.current.isCompacting).toBe(true)
    await act(async () => { finishCompact({ data: { status: 'success', summary: '' } }) })
    await flush()
    expect(result.current.isCompacting).toBe(false)
  })

  it('/compact still waits for nothing: no send options are needed', async () => {
    const { result } = hook()
    await flush()
    pushAll(frame(3, '/compact'))
    await flush()
    expect(compactCalls()).toEqual([`${API}/conversations/3/compact`])
    void result
  })

  it('/compact delivered to two windows runs once', async () => {
    const a = hook()
    const b = hook()
    defaults(a.result)
    defaults(b.result)
    await flush()
    const f = frame(4, '/compact')
    pushAll(f)
    pushAll(f)
    await flush()
    expect(compactCalls()).toEqual([`${API}/conversations/4/compact`])
  })

  it('/usage from the phone goes out as a user turn and its answer is a usage card like a typed one', async () => {
    const { result } = hook()
    defaults(result)
    await open(result, 5)
    pushAll(frame(5, '/usage'))
    await flush()

    expect(turns.map(t => [t.convId, t.message, t.origin])).toEqual([[5, '/usage', 'user']])
    const [bubble, answer] = result.current.messages
    expect(bubble).toMatchObject({ role: 'user', content: '/usage', source: 'phone', sourceDevice: 'iPhone' })
    expect(answer).toMatchObject({ role: 'assistant', slashCommand: 'usage' })
  })

  it('/model and other CLI commands go out as they are', async () => {
    const { result } = hook()
    defaults(result)
    await open(result, 1)
    pushAll(frame(1, '/model sonnet'))
    await flush()
    expect(turns.map(t => [t.convId, t.message])).toEqual([[1, '/model sonnet']])
    expect(compactCalls()).toEqual([])
  })
})

describe('phone marker · chat bubble', () => {
  const panel = (messages: any[]) => render(<ChatPanel {...({
    messages, activeConvId: 7, user: USER, loading: false, clearHistory: vi.fn(), lang: 'tr',
    thinkingLevel: 'auto', workspacePath: '/ws', handleExportToUnity: vi.fn(),
    pendingGenFiles: null, setPendingGenFiles: vi.fn(), pendingFix: null, setPendingFix: vi.fn(),
    openedFilePath: null, setCode: vi.fn(), refreshFileTree: vi.fn(), analyzeProject: vi.fn(),
    openFile: vi.fn(), sendMessage: vi.fn(), messagesEndRef: React.createRef<HTMLDivElement>(),
    ipc: { invoke: vi.fn() }, showToast: vi.fn(), diffFile: null, setDiffFile: vi.fn(),
    pendingDelete: null, setPendingDelete: vi.fn(), pendingCommand: null, setPendingCommand: vi.fn(),
    onApproveCommand: vi.fn(), pendingQuestion: null, setPendingQuestion: vi.fn(),
    onAnswerQuestion: vi.fn(), deleteFile: vi.fn(), setIsTerminalOpen: vi.fn(), apiBase: API,
    mcpGate: null, mcpWorkspaceMismatch: false, mcpOpenWorkspacePath: null, onMcpResolved: vi.fn(),
    activity: null,
  } as any)} />)

  it('shows the device on a phone message and nothing on a typed one', () => {
    panel([
      { id: 1, role: 'user', content: 'typed', smells: [], timestamp: '' },
      { id: 2, role: 'user', content: 'from phone', smells: [], timestamp: '', source: 'phone', sourceDevice: 'iPhone 17' },
    ])
    const markers = screen.getAllByTestId('phone-marker')
    expect(markers.length).toBe(1)
    expect(markers[0].textContent).toContain('iPhone 17')
    expect(markers[0].textContent).toContain('📱')
  })
})
