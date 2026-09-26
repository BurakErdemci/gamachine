/**
 * "Awaiting approval" must be visible wherever the user looks while another
 * chat waits: its sidebar row (rolled up to the family root), its tab, the
 * closed-branches button when the branch is hidden, the Chats tab while the
 * sidebar shows Files, and the sidebar toggle while the sidebar is collapsed.
 *
 * Owner report (26 Sep 2026): in another chat, nothing marked the chat that
 * asked for approval. The DOM paths below were already right for rows and
 * tabs; the dot had no colour (see tailwind-status-colors.test.ts). The
 * collapsed sidebar, the Files tab and hidden branches had no marker at all.
 *
 * Harness wires the real useChat + useMCPApproval + Sidebar + ChatTabs as
 * home.tsx does (home.tsx itself cannot be mounted here).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, act, fireEvent } from '@testing-library/react'

vi.mock('axios', () => {
  const post = vi.fn(); const get = vi.fn(); const del = vi.fn(); const put = vi.fn()
  return { default: { post, get, delete: del, put }, post, get }
})

import axios from 'axios'
import { useChat } from '../renderer/hooks/home/useChat'
import { useMCPApproval } from '../renderer/hooks/home/useMCPApproval'
import { Sidebar } from '../renderer/components/home/Sidebar'
import { ChatTabs } from '../renderer/components/home/ChatTabs'
import { SidebarToggle } from '../renderer/components/home/AwaitingBadge'
import { awaitingElsewhere } from '../renderer/lib/convFamily'
import { cevir, translations } from '../renderer/lib/i18n'

const mocked = axios as unknown as Record<'post' | 'get' | 'delete' | 'put', ReturnType<typeof vi.fn>>

const API = 'http://127.0.0.1:8000'
const USER = { id: 1, name: 'b', sessionToken: 'tok' } as any
const CONFIG = { provider_type: 'subscription', model_name: 'claude-opus-5' } as any
const AWAITING = cevir('sidebar.statusAwaiting')

const conv = (id: number, extra: object = {}) => ({
  id, title: `chat-${id}`, created_at: `2026-09-26T00:00:0${id}Z`, updated_at: '2026-09-26T00:00:00Z',
  parent_id: null, hidden: false, ...extra,
})
// Root 1 with a visible branch 5 and a hidden branch 6; root 2 alone.
const LIST = () => [conv(1), conv(2), conv(5, { parent_id: 1 }), conv(6, { parent_id: 1, hidden: true })]

const unityReq = (owner: number) => ({
  tool: 'manage_gameobject', params: { action: 'delete', target: 'x' }, workspace_path: '/ws', conversation_id: owner,
})

const enc = (ev: object) => new TextEncoder().encode(`data: ${JSON.stringify(ev)}\n\n`)
const makeStream = () => {
  let waiter: ((v: any) => void) | null = null
  const queue: any[] = []
  return {
    push: (ev: object) => { const item = { done: false, value: enc(ev) }; if (waiter) { const w = waiter; waiter = null; w(item) } else queue.push(item) },
    response: { ok: true, body: { getReader: () => ({
      read: () => queue.length ? Promise.resolve(queue.shift()) : new Promise(res => { waiter = res }),
    }) } },
  }
}

let pending: Record<string, any>
let streams: Record<number, ReturnType<typeof makeStream>>
let chat: ReturnType<typeof useChat>
let setOpen: (v: boolean) => void
let setTab: (v: 'chats' | 'files') => void

const showToast = vi.fn()
const refreshFileTree = vi.fn()
const suggest = (n: string) => n
const noop = () => {}

const Harness: React.FC = () => {
  const [open, _setOpen] = React.useState(true)
  const [tab, _setTab] = React.useState<'chats' | 'files'>('chats')
  const [, setGen] = React.useState<any>(null)
  const [, setDel] = React.useState<any>(null)
  setOpen = _setOpen; setTab = _setTab
  const c = useChat(API, USER, CONFIG, '/ws', showToast, refreshFileTree, suggest)
  useMCPApproval({
    API, enabled: true, workspacePath: '/ws',
    setPendingGenFiles: setGen, setPendingDelete: setDel,
    setPendingCommand: c.setPendingCommand, setPendingFix: c.setPendingFix,
    showToast, screenConvId: c.activeConvId, onOwnersChange: c.setBridgeGates,
  })
  chat = c
  return (
    <>
      <SidebarToggle open={open} onToggle={() => _setOpen(!open)} awaiting={awaitingElsewhere(c.convStatus, c.activeConvId, c.conversations)} />
      <Sidebar
        {...({} as any)}
        isSidebarOpen={open} sidebarTab={tab} setSidebarTab={_setTab}
        conversations={c.conversations} activeConvId={c.activeConvId} convStatus={c.convStatus}
        selectConversation={c.selectConversation} createNewConversation={noop} deleteConversation={noop}
        editingId={null} setEditingId={noop} tempTitle="" setTempTitle={noop} saveRename={noop}
        fileTree={[]} treeContextMenu={null} setTreeContextMenu={noop}
        user={USER} setShowSettings={noop} handleLogout={noop}
      />
      <ChatTabs
        conversations={c.conversations} activeConvId={c.activeConvId} convStatus={c.convStatus}
        branchBlocked={false} onSelect={c.selectConversation} onBranch={async () => null} onClose={c.closeBranch}
      />
    </>
  )
}

const flush = async () => { await act(async () => { for (let i = 0; i < 10; i++) await Promise.resolve() }) }
const mount = async () => {
  render(<Harness />)
  await flush()
  await act(async () => { await chat.fetchConversations(1) })
  await flush()
}
const open = async (id: number) => {
  await act(async () => { await chat.selectConversation(chat.conversations.find(c => c.id === id) ?? ({ id } as any)) })
  await flush()
}
const poll = async () => {
  // The hook polls on its own interval; one explicit tick through the fake list.
  await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
  await flush()
}
const title = (testId: string) => screen.queryByTestId(testId)?.getAttribute('title') ?? null

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
  pending = {}
  streams = {}
  showToast.mockReset()
  mocked.get.mockReset().mockImplementation(async (url: string) => {
    const u = String(url)
    if (u.endsWith('/mcp-pending')) return { data: { pending } }
    if (u.endsWith('/conversations/1')) return { data: LIST() }
    if (u.includes('/context-usage')) return { data: { percent: 0, message_count: 0 } }
    return { data: [] }
  })
  mocked.post.mockReset().mockResolvedValue({ data: {} })
  mocked.put.mockReset().mockResolvedValue({ data: {} })
  vi.stubGlobal('fetch', vi.fn((url: string, init?: any) => {
    const u = String(url)
    if (u.endsWith('/chat-stream')) {
      const s = makeStream()
      streams[JSON.parse(init.body).conversation_id] = s
      return Promise.resolve(s.response)
    }
    return new Promise(() => {})
  }))
  vi.spyOn(console, 'warn').mockImplementation(() => {})
})

afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

const sendIn = async (id: number) => {
  await open(id)
  act(() => { chat.sendMessage('q', '', 'tr', 'step', 'medium', vi.fn(), vi.fn()) })
  await flush()
}

describe('awaiting · each path marks the waiting chat', () => {
  it('(a) a Unity bridge card owned by a background root marks its row', async () => {
    await mount()
    await open(1)
    pending = { g2: unityReq(2) }
    await poll()
    expect(title('conv-status-2')).toBe(AWAITING)
    expect(title('conv-status-1')).toBeNull()
  })

  it('(a+c+e) a bridge card owned by a background branch marks its tab and its root row', async () => {
    await mount()
    await open(1)
    pending = { g5: unityReq(5) }
    await poll()
    expect(title('tab-status-5')).toBe(AWAITING)
    expect(title('tab-status-1')).toBeNull()
    expect(title('conv-status-1')).toBe(AWAITING)
  })

  it('(b+c) an own-stream command card in a background branch marks its tab', async () => {
    await mount()
    await sendIn(5)
    await open(1)
    act(() => { streams[5].push({ type: 'command_approval_needed', command: 'ls', gate_id: 'c5' }) })
    await flush()
    expect(title('tab-status-5')).toBe(AWAITING)
    expect(title('conv-status-1')).toBe(AWAITING)
  })

  it('(b) own-stream question and delete cards in a background chat mark its row', async () => {
    await mount()
    await sendIn(2)
    await open(1)
    act(() => { streams[2].push({ type: 'question_needed', questions: [{ question: 'x' }], gate_id: 'q2' }) })
    await flush()
    expect(title('conv-status-2')).toBe(AWAITING)

    await sendIn(5)
    await open(1)
    act(() => { streams[5].push({ type: 'pending_delete', path: 'a.cs' }) })
    await flush()
    expect(title('tab-status-5')).toBe(AWAITING)
  })

  it('(e) a waiting branch rolls up to its root row while another family is on screen', async () => {
    await mount()
    await open(2)
    pending = { g5: unityReq(5) }
    await poll()
    expect(title('conv-status-1')).toBe(AWAITING)
    // Tabs belong to the family on screen, which has no branches.
    expect(screen.queryByTestId('chat-tab-5')).toBeNull()
  })
})

describe('awaiting · places that had no marker', () => {
  it('a hidden branch that waits marks the closed-branches button', async () => {
    await mount()
    await open(1)
    expect(screen.queryByTestId('closed-branches-status')).toBeNull()
    pending = { g6: unityReq(6) }
    await poll()
    expect(title('closed-branches-status')).toBe(AWAITING)
  })

  it('a collapsed sidebar puts an amber count on its toggle; open, the rows carry it', async () => {
    await mount()
    await open(1)
    act(() => setOpen(false))
    expect(screen.queryByTestId('sidebar-toggle-awaiting')).toBeNull()

    pending = { g2: unityReq(2), g5: unityReq(5) }
    await poll()
    const badge = screen.getByTestId('sidebar-toggle-awaiting')
    expect(badge.textContent).toBe('2')
    expect(badge.getAttribute('title')).toBe(cevir('sidebar.awaitingCount', { sayi: 2 }))
    expect(badge.className).toContain('bg-amber-400')
    expect(badge.className).not.toMatch(/animate-/)

    act(() => setOpen(true))
    expect(screen.queryByTestId('sidebar-toggle-awaiting')).toBeNull()

    act(() => setOpen(false))
    pending = {}
    await poll()
    expect(screen.queryByTestId('sidebar-toggle-awaiting')).toBeNull()
  })

  it('the chat on screen is not counted: its card is already in front of the user', async () => {
    await mount()
    await open(2)
    act(() => setOpen(false))
    pending = { g2: unityReq(2) }
    await poll()
    expect(screen.queryByTestId('sidebar-toggle-awaiting')).toBeNull()
    await open(1)
    expect(screen.getByTestId('sidebar-toggle-awaiting').textContent).toBe('1')
  })

  it('the Chats tab carries the count while the sidebar shows Files', async () => {
    await mount()
    await open(1)
    pending = { g2: unityReq(2) }
    await poll()
    expect(screen.queryByTestId('chats-tab-awaiting')).toBeNull()
    act(() => setTab('files'))
    expect(screen.getByTestId('chats-tab-awaiting').textContent).toBe('1')
    fireEvent.click(screen.getByText(cevir('sidebar.chats')))
    expect(screen.queryByTestId('chats-tab-awaiting')).toBeNull()
    expect(title('conv-status-2')).toBe(AWAITING)
  })

  it('the count string exists in both languages', () => {
    expect(translations.tr['sidebar.awaitingCount']).toContain('{sayi}')
    expect(translations.en['sidebar.awaitingCount']).toContain('{sayi}')
  })
})
