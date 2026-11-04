/**
 * BACKGROUND TASKS ROW. Owner (4 Nov 2026): when the model has answered but
 * background tasks still run, the chat looks idle — composer free, a new
 * message goes out at once — and a quiet line lists the running tasks; when
 * they finish the chat continues by itself (a `wake` frame, as before).
 *
 * The `bg_tasks` frame on /wake-stream-all is data, not a turn: it never sets
 * `loading`, so the composer shows send, not stop.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, renderHook, act } from '@testing-library/react'

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
vi.mock('../renderer/hooks/home/useVoiceInput', () => ({
  useVoiceInput: () => ({
    state: 'idle', elapsedMs: 0, error: null, partialText: '',
    start: vi.fn(), stop: vi.fn(), cancel: vi.fn(), clearError: vi.fn(),
  }),
  formatElapsed: () => '00:00',
}))

import axios from 'axios'
import { useChat, setBgTasks } from '../renderer/hooks/home/useChat'
import { ChatPanel } from '../renderer/components/home/ChatPanel'
import { AnimatedChatInput } from '../renderer/components/ui/animated-ai-chat'
import { cevir } from '../renderer/lib/i18n'

const mockedAxios = axios as unknown as {
  post: ReturnType<typeof vi.fn>; get: ReturnType<typeof vi.fn>
}

const API = 'http://127.0.0.1:8000'
const USER = { id: 1, name: 'b', sessionToken: 'tok' } as any
const CONFIG = { provider_type: 'subscription', model_name: 'claude-opus-5' } as any

const enc = (ev: object) => new TextEncoder().encode(`data: ${JSON.stringify(ev)}\n\n`)

type Turn = { convId: number; message: string; origin: string }
let turns: Turn[]
let pushWake: (frame: object) => void

const installFetch = () => {
  turns = []
  let wakeWaiter: ((v: any) => void) | null = null
  const wakeFrames: Uint8Array[] = []
  pushWake = (frame: object) => {
    const bytes = enc(frame)
    if (wakeWaiter) { const w = wakeWaiter; wakeWaiter = null; w({ done: false, value: bytes }) }
    else wakeFrames.push(bytes)
  }
  vi.stubGlobal('fetch', vi.fn((url: string, init?: any) => {
    const u = String(url)
    if (u.endsWith('/chat-stream')) {
      const body = JSON.parse(init.body)
      turns.push({ convId: body.conversation_id, message: body.message, origin: body.origin })
      return Promise.resolve({ ok: true, body: { getReader: () => ({ read: () => new Promise(() => {}) }) } })
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
    return Promise.resolve({ ok: true, status: 200, json: async () => ({ status: 'ok' }) })
  }))
}

const hook = () => renderHook(() => useChat(API, USER, CONFIG, '/ws', vi.fn(), vi.fn(), (n: string) => n))
const flush = async () => { await act(async () => { for (let i = 0; i < 15; i++) await Promise.resolve() }) }
const open = async (result: any, id: number) => {
  await act(async () => { await result.current.selectConversation({ id } as any) })
}

const TASKS = [
  { task_id: 'a', desc: 'audit the scene', kind: 'agent', started_at: Date.now() / 1000 - 65 },
  { task_id: 'b', desc: 'npm run dev', kind: 'bash', started_at: Date.now() / 1000 - 5 },
]

const panel = (loading = false) => render(
  <ChatPanel {...({
    messages: [{ id: 1, role: 'assistant', content: 'Started both in the background.' }],
    activeConvId: 1, user: USER, loading, lang: 'tr', effectiveProvider: 'claude',
    thinkingLevel: 'auto', workspacePath: '/ws', handleExportToUnity: vi.fn(),
    pendingGenFiles: null, setPendingGenFiles: vi.fn(), pendingFix: null, setPendingFix: vi.fn(),
    openedFilePath: null, setCode: vi.fn(), refreshFileTree: vi.fn(), openFile: vi.fn(),
    sendMessage: vi.fn(), messagesEndRef: React.createRef<HTMLDivElement>(),
    ipc: { invoke: vi.fn().mockResolvedValue({ success: true }) }, showToast: vi.fn(),
    diffFile: null, setDiffFile: vi.fn(), pendingDelete: null, setPendingDelete: vi.fn(),
    pendingCommand: null, setPendingCommand: vi.fn(), onApproveCommand: vi.fn().mockResolvedValue(null),
    pendingQuestion: null, setPendingQuestion: vi.fn(), onAnswerQuestion: vi.fn(),
    deleteFile: vi.fn(), setIsTerminalOpen: vi.fn(), apiBase: API, mcpGate: null,
    mcpWorkspaceMismatch: false, mcpOpenWorkspacePath: null, onMcpResolved: vi.fn(),
    activity: loading ? { detail: 'Çalışıyor', tokens: 0 } : null,
  } as any)} />,
)

beforeEach(() => {
  installFetch()
  mockedAxios.post.mockReset().mockResolvedValue({ data: {} })
  mockedAxios.get.mockReset().mockImplementation(async (url: string) => {
    if (String(url).includes('/context-usage')) return { data: { percent: 1, should_compact: false, message_count: 0 } }
    return { data: [] }
  })
})

afterEach(() => {
  act(() => { setBgTasks(1, []) })
  cleanup()
  vi.unstubAllGlobals()
})

describe('background tasks · row', () => {
  it('shows the count, the names and the oldest task\'s time, with no spinner', () => {
    act(() => { setBgTasks(1, TASKS) })
    panel()
    const row = screen.getByTestId('bg-tasks-row')
    expect(row.textContent).toContain(cevir('chat.bgTasks.running', { n: '2', names: 'audit the scene, npm run dev' }))
    expect(row.textContent).toMatch(/1:0[5-9]/)
    expect(row.querySelector('.dots')).toBeNull()
  })

  it('lists three names, then "+k"', () => {
    act(() => {
      setBgTasks(1, ['a', 'b', 'c', 'd', 'e'].map(id => ({ task_id: id, desc: `task ${id}`, kind: 'other', started_at: null })))
    })
    panel()
    expect(screen.getByTestId('bg-tasks-row').textContent).toBe(
      cevir('chat.bgTasks.running', { n: '5', names: `task a, task b, task c ${cevir('chat.bgTasks.more', { k: '2' })}` }))
  })

  it('clears on an empty list and stays away while a turn runs', () => {
    act(() => { setBgTasks(1, TASKS) })
    const { unmount } = panel()
    expect(screen.queryByTestId('bg-tasks-row')).not.toBeNull()
    act(() => { setBgTasks(1, []) })
    expect(screen.queryByTestId('bg-tasks-row')).toBeNull()
    unmount()
    act(() => { setBgTasks(1, TASKS) })
    panel(true)
    expect(screen.queryByTestId('bg-tasks-row')).toBeNull()
  })

  it('belongs to its own chat only', () => {
    act(() => { setBgTasks(2, TASKS) })
    panel()
    expect(screen.queryByTestId('bg-tasks-row')).toBeNull()
    act(() => { setBgTasks(2, []) })
  })
})

describe('background tasks · the bg_tasks frame', () => {
  it('keeps the chat idle: composer has send, not stop; a new message goes out at once', async () => {
    const { result } = hook()
    await open(result, 1)
    await flush()
    act(() => { pushWake({ type: 'bg_tasks', conversation_id: 1, tasks: TASKS }) })
    await flush()
    expect(result.current.bgTasks.map((t: any) => t.desc)).toEqual(['audit the scene', 'npm run dev'])
    expect(result.current.loading).toBe(false)

    render(
      <AnimatedChatInput
        value="" setValue={vi.fn()} onSendMessage={vi.fn()} onStop={vi.fn()}
        isLoading={result.current.loading} api="http://127.0.0.1:1"
      />,
    )
    expect(document.querySelector('[data-send-button]')).not.toBeNull()
    expect(document.querySelector('[data-stop-button]')).toBeNull()

    act(() => {
      void result.current.sendMessage('meanwhile', '', 'tr', 'auto', 'medium', vi.fn(), vi.fn())
    })
    await flush()
    expect(turns.map(t => [t.origin, t.message])).toEqual([['user', 'meanwhile']])
    expect(result.current.queue.length).toBe(0)
  })

  it('an empty list clears it, and the wake still starts the continuation turn', async () => {
    const { result } = hook()
    await open(result, 1)
    await flush()
    act(() => { pushWake({ type: 'bg_tasks', conversation_id: 1, tasks: TASKS }) })
    await flush()
    expect(result.current.bgTasks.length).toBe(2)
    act(() => {
      result.current.setWakeDefaults({ lang: 'tr', genMode: 'auto', thinkingLevel: 'medium', setPendingGenFiles: vi.fn(), setPendingDelete: vi.fn() } as any)
    })
    act(() => { pushWake({ type: 'bg_tasks', conversation_id: 1, tasks: [] }) })
    act(() => { pushWake({ type: 'wake', conversation_id: 1, count: 1, notices: ['tasks_done|audit the scene'], text: 'tasks_done|audit the scene' }) })
    await flush()
    await flush()
    expect(result.current.bgTasks).toEqual([])
    expect(turns.map(t => t.origin)).toEqual(['wake'])
  })
})
