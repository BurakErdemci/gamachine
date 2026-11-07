/**
 * A turn the agent runs by itself (woken by finished background work) is
 * followed like any `turn_live` turn, and shows like a normal one while it
 * runs: provider · model header, the activity row (step, tokens, timer) and
 * the tool cards. Harness as in `turn-live-follow.test.tsx`; the bubble is
 * drawn by the real ChatPanel from the hook's state.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { cleanup, renderHook, act, render } from '@testing-library/react'

vi.mock('@monaco-editor/react', () => ({
  __esModule: true,
  default: () => null,
  DiffEditor: () => null,
  Editor: () => null,
  loader: { config: () => {}, init: () => Promise.resolve({}) },
}))

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

const USER_ROW = { id: 11, role: 'user', content: 'wake', smells: [], timestamp: 't1' }

const drawn = (result: any) => render(<ChatPanel {...({
  messages: result.current.messages, activeConvId: 5, user: USER, loading: result.current.loading, lang: 'en',
  thinkingLevel: 'auto', workspacePath: '/ws', handleExportToUnity: vi.fn(),
  pendingGenFiles: null, setPendingGenFiles: vi.fn(), pendingFix: null, setPendingFix: vi.fn(),
  openedFilePath: null, setCode: vi.fn(), refreshFileTree: vi.fn(),
  openFile: vi.fn(), sendMessage: vi.fn(), messagesEndRef: React.createRef<HTMLDivElement>(),
  ipc: { invoke: vi.fn() }, showToast: vi.fn(), diffFile: null, setDiffFile: vi.fn(),
  pendingDelete: null, setPendingDelete: vi.fn(), pendingCommand: null, setPendingCommand: vi.fn(),
  onApproveCommand: vi.fn(), pendingQuestion: null, setPendingQuestion: vi.fn(),
  onAnswerQuestion: vi.fn(), deleteFile: vi.fn(), setIsTerminalOpen: vi.fn(), apiBase: API,
  mcpGate: null, mcpWorkspaceMismatch: false, mcpOpenWorkspacePath: null, onMcpResolved: vi.fn(),
  activity: result.current.activity,
} as any)} />)

describe('turn_live · a turn the agent runs by itself', () => {
  it('shows the agent label, the running tool, tokens and the tool card, then clears at turn_end', async () => {
    const { result } = hook()
    await open(result, 5)
    await flush()
    stored[5] = [USER_ROW]
    await emit(5, 'turn_start', { provider: 'claude', model: 'claude-opus-5-5', origin: 'agent' })
    expect(result.current.messages[1]).toMatchObject({ provider: 'claude', model: 'claude-opus-5-5' })

    await emit(5, 'tool_call', { name: 'Read', summary: 'a.ts' })
    await emit(5, 'status', { tokens: 1234, detail: 'working' })
    expect(result.current.activity).toMatchObject({ tokens: 1234 })
    await emit(5, 'tool_call', { name: 'Grep', summary: 'foo' })
    expect(result.current.activity?.detail).toContain('Grep')
    await emit(5, 'tool_result', { name: 'Read', success: true, summary: 'read 10 lines' })
    expect(result.current.messages[1].tool_calls).toMatchObject([
      { tool: 'Read', success: true, summary: 'read 10 lines' },
      { tool: 'Grep' },
    ])
    expect(result.current.loading).toBe(true)

    const view = drawn(result)
    expect(view.container.textContent).toMatch(/Claude Code/i)
    expect(view.container.textContent).toMatch(/opus/i)
    expect(view.container.textContent).toContain('Grep')
    expect(view.container.querySelector('.working-meta')).not.toBeNull()
    expect(view.container.textContent).toContain('1.2k')
    view.unmount()

    stored[5] = [USER_ROW, { id: 12, role: 'assistant', content: 'done', smells: [], timestamp: 't2' }]
    await emit(5, 'turn_end', { status: 'done' })
    expect(result.current.loading).toBe(false)
    expect(result.current.activity).toBeNull()
  })

  it('a first read that already holds the whole turn so far shows the same', async () => {
    const { result } = hook()
    await open(result, 5)
    await flush()
    stored[5] = [USER_ROW]
    ring[5] = [
      { seq: 1, kind: 'turn_start', provider: 'claude', model: 'claude-opus-5-5', origin: 'agent', ts: 0 },
      { seq: 2, kind: 'tool_call', name: 'Bash', summary: 'ls', ts: 0 },
      { seq: 3, kind: 'tool_result', name: 'Bash', success: false, summary: 'nope', ts: 0 },
      { seq: 4, kind: 'status', tokens: 50, detail: 'working', ts: 0 },
    ]
    wakeStreams.forEach(push => push({ type: 'turn_live', conversation_id: 5, kind: 'tool_call', seq: 4 }))
    await flush()
    expect(result.current.messages[1]).toMatchObject({ provider: 'claude', model: 'claude-opus-5-5' })
    expect(result.current.messages[1].tool_calls).toMatchObject([{ tool: 'Bash', success: false, summary: 'nope' }])
    expect(result.current.activity).toMatchObject({ tokens: 50 })
  })
})
