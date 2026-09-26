/**
 * AUTO-WAKE — the chat continuing ON ITS OWN once a background job finishes.
 *
 * Two things are being measured here, and both can break silently:
 *
 *  1. The RENDERING of the `system` role. ChatPanel's message loop used to be
 *     a binary choice ("is it assistant, or not") — meaning a `system` role
 *     was drawn as a BLUE USER BUBBLE. A sentence the user never wrote
 *     appearing as theirs isn't a visual glitch, it's a false claim. The test
 *     therefore checks not just that the label exists, but that the bubble is
 *     ABSENT.
 *
 *  2. The `origin: 'wake'` contract actually going OVER THE WIRE. If the field
 *     is dropped, the backend assumes the turn is `user`: the message gets
 *     stored with the user role, the consecutive-wake counter never runs, and
 *     the safety valve silently switches off. That's why the test checks the
 *     request body (not just the URL).
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

import axios from 'axios'
import { ChatPanel } from '../renderer/components/home/ChatPanel'
import { useChat } from '../renderer/hooks/home/useChat'
import { cevir } from '../renderer/lib/i18n'

const mockedAxios = axios as unknown as { post: ReturnType<typeof vi.fn>; get: ReturnType<typeof vi.fn> }

const API = 'http://127.0.0.1:8000'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

// ── 1. rendering the system row ─────────────────────────────────────────────

const renderPanel = (messages: any[]) => {
  const props: any = {
    messages,
    activeConvId: 1,
    user: { id: 1, name: 'b', sessionToken: 'tok' },
    loading: false,
    clearHistory: vi.fn(),
    lang: 'tr',
    effectiveProvider: 'claude',
    thinkingLevel: 'auto',
    workspacePath: '/ws',
    handleExportToUnity: vi.fn(),
    pendingGenFiles: null,
    setPendingGenFiles: vi.fn(),
    pendingFix: null,
    setPendingFix: vi.fn(),
    openedFilePath: null,
    setCode: vi.fn(),
    refreshFileTree: vi.fn(),
    analyzeProject: vi.fn(),
    openFile: vi.fn(),
    sendMessage: vi.fn(),
    messagesEndRef: React.createRef<HTMLDivElement>(),
    ipc: { invoke: vi.fn().mockResolvedValue({ success: true }) },
    showToast: vi.fn(),
    diffFile: null,
    setDiffFile: vi.fn(),
    pendingDelete: null,
    setPendingDelete: vi.fn(),
    pendingCommand: null,
    setPendingCommand: vi.fn(),
    onApproveCommand: vi.fn().mockResolvedValue(null),
    pendingQuestion: null,
    setPendingQuestion: vi.fn(),
    onAnswerQuestion: vi.fn(),
    deleteFile: vi.fn(),
    setIsTerminalOpen: vi.fn(),
    apiBase: API,
    mcpGate: null,
    mcpWorkspaceMismatch: false,
    mcpOpenWorkspacePath: null,
    onMcpResolved: vi.fn(),
    activity: null,
  }
  return render(<ChatPanel {...props} />)
}

const SISTEM_MESAJI = {
  id: 9,
  role: 'system' as const,
  content: 'Arka plan görevleri tamamlandı: derleme',
  smells: [],
  timestamp: '2026-09-05T00:00:00Z',
}

describe('AUTO-WAKE row', () => {
  it('system role renders as a separate event row', () => {
    renderPanel([SISTEM_MESAJI])
    expect(screen.getByText(cevir('chat.wakeRow'))).toBeTruthy()
    expect(screen.getByText(/derleme/)).toBeTruthy()
  })

  it('system role does NOT render as a USER BUBBLE', () => {
    // Not the rendering itself, its CLAIM is what's being measured: the blue
    // bubble says "the user wrote this". The class name is the only
    // distinguishing signal in production.
    const { container } = renderPanel([SISTEM_MESAJI])
    expect(container.querySelector('.bg-blue-500\\/10')).toBeNull()
  })

  it('localizes every reason code in a joined system message', () => {
    const { container } = renderPanel([{
      ...SISTEM_MESAJI,
      id: 11,
      content: 'tasks_done|build · tasks_done_saved|saved',
    }])
    expect(container.textContent).not.toContain('tasks_done')
    expect(container.textContent).not.toContain('tasks_done_saved')
  })

  it('a real user message is still in a bubble', () => {
    const { container } = renderPanel([{ ...SISTEM_MESAJI, id: 10, role: 'user', content: 'selam' }])
    expect(container.querySelector('.bg-blue-500\\/10')).not.toBeNull()
  })
})

// ── 2. useChat: the origin=wake contract ─────────────────────────────────────

/** A fake `fetch` response that returns a single SSE frame. */
const sseYanit = (cerceve: object) => ({
  ok: true,
  body: {
    getReader: () => {
      let verildi = false
      return {
        read: async () => {
          if (verildi) return { done: true, value: undefined }
          verildi = true
          return {
            done: false,
            value: new TextEncoder().encode(`data: ${JSON.stringify(cerceve)}\n\n`),
          }
        },
      }
    },
  },
})

const hook = () => renderHook(() => useChat(
  API,
  { id: 1, name: 'b', sessionToken: 'tok' } as any,
  { provider_type: 'subscription', model_name: 'claude-opus-5' } as any,
  '/ws',
  vi.fn(),
  vi.fn(),
  (n: string) => n,
))

describe('useChat · origin=wake', () => {
  beforeEach(() => {
    mockedAxios.post.mockReset().mockResolvedValue({ data: { id: 5 } })
    mockedAxios.get.mockReset().mockResolvedValue({ data: [] })
  })

  it('origin: "wake" goes in the body and the message enters the list with SYSTEM role', async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (String(url).includes('/wake-stream')) return new Promise(() => {})  // never resolves
      return Promise.resolve(sseYanit({ type: 'done', stop_reason: 'complete' }))
    })
    vi.stubGlobal('fetch', fetchMock)

    const { result } = hook()
    await act(async () => {
      await result.current.sendMessage(
        'Arka plan görevleri tamamlandı: derleme', '', 'tr', 'auto', 'medium',
        vi.fn(), vi.fn(), undefined, false, undefined, 'wake',
      )
    })

    const cagri = fetchMock.mock.calls.find(c => String(c[0]).endsWith('/chat-stream'))
    expect(cagri).toBeTruthy()
    expect(JSON.parse(cagri![1].body).origin).toBe('wake')
    expect(result.current.messages.some((m: any) => m.role === 'system')).toBe(true)
    // A user bubble must NOT be produced: the wake text does not belong to the user.
    expect(result.current.messages.some((m: any) => m.role === 'user')).toBe(false)
  })

  it('a default send is still origin: "user"', async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (String(url).includes('/wake-stream')) return new Promise(() => {})
      return Promise.resolve(sseYanit({ type: 'done', stop_reason: 'complete' }))
    })
    vi.stubGlobal('fetch', fetchMock)

    const { result } = hook()
    await act(async () => {
      await result.current.sendMessage('selam', '', 'tr', 'auto', 'medium', vi.fn(), vi.fn())
    })

    const cagri = fetchMock.mock.calls.find(c => String(c[0]).endsWith('/chat-stream'))
    expect(JSON.parse(cagri![1].body).origin).toBe('user')
    expect(result.current.messages.some((m: any) => m.role === 'user')).toBe(true)
  })

  it('the chain safety valve is reported to the user with a SEPARATE text', async () => {
    // Falling into `stoppedOther` would say "the run stopped midway"; but here
    // the run never started, and the reason is a limit, not a fault. This test
    // guards that distinction.
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (String(url).includes('/wake-stream')) return new Promise(() => {})
      return Promise.resolve(sseYanit({ type: 'done', stop_reason: 'wake_chain_exhausted' }))
    })
    vi.stubGlobal('fetch', fetchMock)

    const { result } = hook()
    await act(async () => {
      await result.current.sendMessage(
        'devam', '', 'tr', 'auto', 'medium', vi.fn(), vi.fn(),
        undefined, false, undefined, 'wake',
      )
    })

    const notices = result.current.messages.flatMap((m: any) => m.notices || [])
    expect(notices.length).toBe(1)
    expect(notices[0].message).toBe(cevir('notice.wakeChainExhausted'))
    expect(notices[0].message).not.toBe(cevir('notice.stoppedOther'))
  })
})

// ── 3. the wake-stream channel ────────────────────────────────────────────────

describe('useChat · wake channel', () => {
  beforeEach(() => {
    mockedAxios.post.mockReset().mockResolvedValue({ data: { id: 5 } })
    mockedAxios.get.mockReset().mockResolvedValue({ data: [] })
  })

  it('the channel does NOT open when there is no active chat', async () => {
    const fetchMock = vi.fn().mockReturnValue(new Promise(() => {}))
    vi.stubGlobal('fetch', fetchMock)
    hook()
    await act(async () => { await Promise.resolve() })
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/wake-stream'))).toBe(false)
  })

  it('a turn starts BY ITSELF when a wake frame arrives (origin=wake)', async () => {
    // The channel REOPENS once the turn ends (by design). The fake endpoint
    // therefore gives the wake frame ONCE: in reality the loop stops there
    // because the queue gets drained and the backend cuts consecutive wakes
    // off at 3 — the client has NO safety valve of its own, and leaving it
    // that way on purpose is only correct as long as the server-side valve is
    // measured.
    let wakeVerildi = false
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (String(url).includes('/wake-stream')) {
        if (wakeVerildi) return new Promise(() => {})
        wakeVerildi = true
        return Promise.resolve(sseYanit({
          type: 'wake', conversation_id: 5, count: 2,
          notices: ['a', 'b'], text: 'a · b',
        }))
      }
      return Promise.resolve(sseYanit({ type: 'done', stop_reason: 'complete' }))
    })
    vi.stubGlobal('fetch', fetchMock)

    const { result } = hook()
    // The channel only makes sense AFTER a real send: a wake turn borrows its
    // language/mode arguments from the last user send.
    await act(async () => {
      await result.current.sendMessage('selam', '', 'tr', 'auto', 'medium', vi.fn(), vi.fn())
    })
    await act(async () => { await new Promise(r => setTimeout(r, 30)) })

    const wakeTuru = fetchMock.mock.calls.filter(
      c => String(c[0]).endsWith('/chat-stream') && JSON.parse(c[1].body).origin === 'wake',
    )
    expect(wakeTuru.length).toBe(1)
    expect(JSON.parse(wakeTuru[0][1].body).message).toBe('a · b')
  })
})
