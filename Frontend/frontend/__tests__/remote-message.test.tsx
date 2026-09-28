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

type Turn = { convId: number; message: string; origin: string; stream: ReturnType<typeof makeStream> }
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
      turns.push({ convId: body.conversation_id, message: body.message, origin: body.origin, stream })
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
