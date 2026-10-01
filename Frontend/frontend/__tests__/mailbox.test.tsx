/**
 * Chat mailbox: the AI of one chat leaves a note for another chat.
 *
 * Measured here:
 *  1. A note wakes a chat that is NOT on screen: its turn runs in that chat's
 *     own runtime, the screen stays where it is and the user's draft in the
 *     message box is untouched (a wake turn used to clear it).
 *  2. The step-mode card for a note is its own kind and shows who writes to
 *     whom and what.
 *  3. The stored note renders as a note bubble: not a user bubble and not the
 *     muted wake row.
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
  const post = vi.fn(); const get = vi.fn(); const del = vi.fn(); const put = vi.fn()
  return { default: { post, get, delete: del, put }, post, get }
})

import axios from 'axios'
import { useChat } from '../renderer/hooks/home/useChat'
import { useMCPApproval, mailOzeti, MCP_MSG_ID } from '../renderer/hooks/home/useMCPApproval'
import { CommandApproval } from '../renderer/components/home/CommandApproval'
import { ChatPanel, MAIL_AUTO_TAG, UNDELIVERED_MARKER } from '../renderer/components/home/ChatPanel'
import { cevir } from '../renderer/lib/i18n'

const mockedAxios = axios as unknown as { post: ReturnType<typeof vi.fn>; get: ReturnType<typeof vi.fn> }
const API = 'http://127.0.0.1:8000'
const USER = { id: 1, name: 'b', sessionToken: 'tok' } as any
const CONFIG = { provider_type: 'subscription', model_name: 'claude-opus-5' } as any
const NOTE = '📨 #3 "Derleme": build temiz'

const enc = (ev: object) => new TextEncoder().encode(`data: ${JSON.stringify(ev)}\n\n`)

/** An SSE response whose frames the test pushes by hand. */
const makeStream = () => {
  const queue: any[] = []
  let waiter: ((v: any) => void) | null = null
  const deliver = (item: any) => { if (waiter) { const w = waiter; waiter = null; w(item) } else queue.push(item) }
  return {
    push: (ev: object) => deliver({ done: false, value: enc(ev) }),
    close: () => deliver({ done: true }),
    response: {
      ok: true,
      body: { getReader: () => ({ read: () => (queue.length ? Promise.resolve(queue.shift()) : new Promise(r => { waiter = r })) }) },
    },
  }
}

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

const flush = async () => { await act(async () => { for (let i = 0; i < 20; i++) await Promise.resolve() }) }

describe('a note wakes a chat in the background', () => {
  let wake: ReturnType<typeof makeStream>
  let turns: Record<number, ReturnType<typeof makeStream>>
  let bodies: any[]

  beforeEach(() => {
    wake = makeStream()
    turns = {}
    bodies = []
    mockedAxios.post.mockReset().mockResolvedValue({ data: {} })
    mockedAxios.get.mockReset().mockImplementation((url: string) => {
      if (url.endsWith('/conversations/7/messages')) {
        return Promise.resolve({ data: [{ id: 70, role: 'user', content: 'eski soru', smells: [], timestamp: 't' }] })
      }
      return Promise.resolve({ data: [] })
    })
    vi.stubGlobal('fetch', vi.fn((url: string, init?: any) => {
      const u = String(url)
      if (u.endsWith('/wake-stream-all')) return Promise.resolve(wake.response)
      if (u.endsWith('/chat-stream')) {
        const body = JSON.parse(init.body)
        bodies.push(body)
        const s = makeStream()
        turns[body.conversation_id] = s
        return Promise.resolve(s.response)
      }
      return new Promise(() => {})
    }))
  })

  it('runs the turn in that chat without switching the screen or clearing the draft', async () => {
    const setGen = vi.fn(); const setDel = vi.fn()
    const { result } = renderHook(() => useChat(API, USER, CONFIG, '/ws', vi.fn(), vi.fn(), (n: string) => n))
    await flush()
    act(() => {
      result.current.setWakeDefaults({ lang: 'tr', genMode: 'step', thinkingLevel: 'auto',
        setPendingGenFiles: setGen, setPendingDelete: setDel })
      result.current.setActiveConvId(1)
      result.current.setChatInput('yarım taslak')
    })

    await act(async () => { wake.push({ type: 'wake', conversation_id: 7, count: 1, notices: ['mail|#3'], text: 'mail|#3' }) })
    await flush()

    expect(bodies).toHaveLength(1)
    expect(bodies[0]).toMatchObject({ conversation_id: 7, origin: 'wake', thinking_level: 'medium', language: 'tr' })
    expect(result.current.activeConvId).toBe(1)
    expect(result.current.chatInput).toBe('yarım taslak')
    expect(result.current.convStatus[7]).toBe('running')
    // The chat on screen got nothing of the other chat's turn.
    expect(result.current.messages).toEqual([])

    await act(async () => {
      turns[7].push({ type: 'wake_message', content: NOTE })
      turns[7].push({ type: 'response', content: 'not alındı' })
      turns[7].push({ type: 'done', stop_reason: 'complete' })
      turns[7].close()
    })
    await flush()
    expect(result.current.activeConvId).toBe(1)
    expect(result.current.chatInput).toBe('yarım taslak')
    expect(result.current.convStatus[7]).toBe('unread')

    // Opening it: the history fetched before the turn, the stored note (not
    // the notice text the row was first drawn with), then the answer.
    mockedAxios.get.mockImplementation((url: string) => Promise.resolve({
      data: url.endsWith('/conversations/7/messages')
        ? [{ id: 70, role: 'user', content: 'eski soru', smells: [], timestamp: 't' },
          { id: 71, role: 'system', content: NOTE, smells: [], timestamp: 't' },
          { id: 72, role: 'assistant', content: 'not alındı', smells: [], timestamp: 't' }]
        : [] }))
    await act(async () => { await result.current.selectConversation({ id: 7 } as any) })
    await flush()
    const shown = result.current.messages.map((m: any) => [m.role, m.content])
    expect(shown).toEqual([['user', 'eski soru'], ['system', NOTE], ['assistant', 'not alındı']])
  })

  it('a live copy shows the stored note once the server names it', async () => {
    const { result } = renderHook(() => useChat(API, USER, CONFIG, '/ws', vi.fn(), vi.fn(), (n: string) => n))
    await flush()
    act(() => {
      result.current.setWakeDefaults({ lang: 'tr', genMode: 'step', thinkingLevel: 'auto',
        setPendingGenFiles: vi.fn(), setPendingDelete: vi.fn() })
    })
    await act(async () => { wake.push({ type: 'wake', conversation_id: 7, count: 1, notices: ['mail|#3'], text: 'mail|#3' }) })
    await flush()
    // Watching it live: open it while its turn runs.
    await act(async () => { await result.current.selectConversation({ id: 7 } as any) })
    await act(async () => { turns[7].push({ type: 'wake_message', content: NOTE }) })
    await flush()
    const system = result.current.messages.filter((m: any) => m.role === 'system')
    expect(system.map((m: any) => m.content)).toEqual([NOTE])
    expect(result.current.messages[0]).toMatchObject({ role: 'user', content: 'eski soru' })
  })

  it('without any turn arguments the wake is dropped, not guessed', async () => {
    renderHook(() => useChat(API, USER, CONFIG, '/ws', vi.fn(), vi.fn(), (n: string) => n))
    await flush()
    await act(async () => { wake.push({ type: 'wake', conversation_id: 7, count: 1, notices: ['mail|#3'], text: 'mail|#3' }) })
    await flush()
    expect(bodies).toHaveLength(0)
  })
})

describe('the mail card', () => {
  const params = { from_id: 3, from_title: 'Derleme', to_id: 7, to_title: 'Arayüz', body: 'build temiz\nikinci satır' }

  it('is routed as its own kind from /mcp-pending', async () => {
    mockedAxios.get.mockReset().mockResolvedValue({ data: { pending: {
      g1: { tool: 'send_chat_message', kind: 'mail', params, workspace_path: '', conversation_id: 3 },
    } } })
    const setPendingCommand = vi.fn()
    const { result } = renderHook(() => useMCPApproval({
      API, enabled: false, workspacePath: '/ws',
      setPendingGenFiles: vi.fn(), setPendingDelete: vi.fn(), setPendingCommand, setPendingFix: vi.fn(),
      screenConvId: 3,
    }))
    await act(async () => { await result.current.poll() })
    expect(setPendingCommand).toHaveBeenCalledWith({
      command: mailOzeti(params), gateId: 'g1', messageId: MCP_MSG_ID, kind: 'mail',
    })
  })

  it('renders from, to and the body', () => {
    render(<CommandApproval command={mailOzeti(params)} kind="mail" onConfirm={vi.fn()} onCancel={vi.fn()} />)
    expect(screen.getByText(cevir('mailApproval.title'))).toBeTruthy()
    expect(screen.getByTestId('mail-route').textContent).toBe('#3 "Derleme" → #7 "Arayüz"')
    expect(screen.getByTestId('mail-body').textContent).toBe('build temiz\nikinci satır')
    expect(screen.getByText(cevir('mailApproval.run'))).toBeTruthy()
    // Not drawn as a shell command.
    expect(screen.queryByText('$')).toBeNull()
  })
})

describe('the note bubble', () => {
  const panel = (messages: any[]) => render(<ChatPanel {...({
    messages, activeConvId: 7, user: USER, loading: false, clearHistory: vi.fn(), lang: 'tr',
    effectiveProvider: 'claude', thinkingLevel: 'auto', workspacePath: '/ws', handleExportToUnity: vi.fn(),
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

  it('renders a stored note as a note, not as a user bubble or a wake row', () => {
    const { container } = panel([{ id: 1, role: 'system', content: NOTE, smells: [], timestamp: 't' }])
    const note = screen.getByTestId('mail-note')
    expect(note.textContent).toContain(cevir('chat.mailNote'))
    expect(note.textContent).toContain('#3 "Derleme": build temiz')
    expect(container.querySelector('[data-role="user"]')).toBeNull()
    expect(screen.queryByText(cevir('chat.wakeRow'))).toBeNull()
    expect(screen.queryByTestId('mail-note-auto')).toBeNull()
  })

  it('marks a reply the other chat never sent itself (Burak, 27 Sep 2026)', () => {
    // Backend mailbox.format_note: the tag sits between the number and the title.
    panel([{ id: 3, role: 'system', content: `📨 #3 ${MAIL_AUTO_TAG} "Derleme": Turkuaz`,
      smells: [], timestamp: 't' }])
    expect(screen.getByTestId('mail-note-auto').textContent).toBe(cevir('chat.mailNoteAuto'))
    const note = screen.getByTestId('mail-note')
    expect(note.textContent).toContain('#3 "Derleme": Turkuaz')
    expect(note.textContent).not.toContain(MAIL_AUTO_TAG)
  })

  it('a tag inside a note body is not a marker', () => {
    panel([{ id: 4, role: 'system', content: `📨 #3 "Derleme": metinde ${MAIL_AUTO_TAG} geçiyor`,
      smells: [], timestamp: 't' }])
    expect(screen.queryByTestId('mail-note-auto')).toBeNull()
    expect(screen.getByTestId('mail-note').textContent).toContain(MAIL_AUTO_TAG)
  })

  it('a mail notice not yet replaced reads as a localized wake row', () => {
    const { container } = panel([{ id: 2, role: 'system', content: 'mail|#3', smells: [], timestamp: 't' }])
    expect(screen.queryByTestId('mail-note')).toBeNull()
    expect(container.textContent).toContain(cevir('chat.wakeRow.mail'))
    expect(container.textContent).not.toContain('mail|')
  })

  // Owner decision, 28 Sep 2026: a note the startup sweep could not deliver
  // (backend agentic/mailbox.py `format_undelivered_recipient_note`) renders
  // grey, distinct from a live note, and not as the wake row or a user bubble.
  it('an undelivered note renders grey, not as a live mail note or a wake row', () => {
    const { container } = panel([{
      id: 5, role: 'system',
      content: `${UNDELIVERED_MARKER} Uygulama yeniden başladığı için bu not teslim edilmedi:\n`
        + '- #3 "Derleme": build temiz\n'
        + 'İstersen bu sohbete kendin yazarak devam edebilirsin.',
      smells: [], timestamp: 't',
    }])
    const note = screen.getByTestId('mail-note-undelivered')
    expect(note.textContent).toContain(cevir('chat.mailUndelivered'))
    expect(note.textContent).toContain('#3 "Derleme": build temiz')
    expect(screen.queryByTestId('mail-note')).toBeNull()
    expect(screen.queryByText(cevir('chat.wakeRow'))).toBeNull()
    expect(container.querySelector('[data-role="user"]')).toBeNull()
  })
})
