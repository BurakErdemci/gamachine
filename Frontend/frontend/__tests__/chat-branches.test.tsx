/**
 * TABBED BRANCHING. A chat family is a root plus flat branches (parent_id =
 * root). The sidebar shows roots only; the chat column shows the family as
 * tabs. Closing a tab hides the branch (PUT /hidden), never deletes it, and a
 * hidden branch comes back when anything opens it. Deleting a root drops every
 * id the backend reports gone.
 *
 * The backend branch API is written in parallel, so it is mocked here to the
 * agreed contract.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, renderHook, act, fireEvent } from '@testing-library/react'

vi.mock('axios', () => {
  const post = vi.fn()
  const get = vi.fn()
  const del = vi.fn()
  const put = vi.fn()
  return { default: { post, get, delete: del, put }, post, get }
})
vi.mock('../renderer/components/ui/ConfirmDialog', () => ({ confirmDialog: vi.fn().mockResolvedValue(true) }))

import axios from 'axios'
import { useChat } from '../renderer/hooks/home/useChat'
import { useChatNotifications } from '../renderer/hooks/home/useChatNotifications'
import { Sidebar } from '../renderer/components/home/Sidebar'
import { ChatTabs, BranchButton } from '../renderer/components/home/ChatTabs'
import { cevir } from '../renderer/lib/i18n'

const mocked = axios as unknown as Record<'post' | 'get' | 'delete' | 'put', ReturnType<typeof vi.fn>>

const API = 'http://127.0.0.1:8000'
const USER = { id: 1, name: 'b', sessionToken: 'tok' } as any
const CONFIG = { provider_type: 'subscription', model_name: 'claude-opus-5' } as any

const conv = (id: number, extra: object = {}) => ({
  id, title: `chat-${id}`, created_at: `2026-09-26T00:00:0${id}Z`, updated_at: '2026-09-26T00:00:00Z',
  parent_id: null, hidden: false, ...extra,
})
// Root 1 with a visible branch 5 and a hidden branch 6; root 2 alone.
const LIST = () => [conv(1), conv(2), conv(5, { parent_id: 1 }), conv(6, { parent_id: 1, hidden: true })]

const enc = (ev: object) => new TextEncoder().encode(`data: ${JSON.stringify(ev)}\n\n`)
const makeStream = () => {
  let waiter: ((v: any) => void) | null = null
  let failer: ((e: any) => void) | null = null
  const queue: any[] = []
  return {
    push: (ev: object) => { const item = { done: false, value: enc(ev) }; if (waiter) { const w = waiter; waiter = null; w(item) } else queue.push(item) },
    close: () => { const item = { done: true }; if (waiter) { const w = waiter; waiter = null; w(item) } else queue.push(item) },
    fail: (err: Error) => { if (failer) { const f = failer; waiter = null; failer = null; f(err) } },
    response: { ok: true, body: { getReader: () => ({
      read: () => queue.length ? Promise.resolve(queue.shift()) : new Promise((res, rej) => { waiter = res; failer = rej }),
    }) } },
  }
}
let streams: Record<number, ReturnType<typeof makeStream>>
let serverList: any[]

const flush = async () => { await act(async () => { for (let i = 0; i < 10; i++) await Promise.resolve() }) }

let showToast: ReturnType<typeof vi.fn<(msg: string, type: any) => void>>
const hook = () => renderHook(() => useChat(API, USER, CONFIG, '/ws', showToast, vi.fn(), (n: string) => n))

const load = async (result: any) => {
  await act(async () => { await result.current.fetchConversations(1) })
}

beforeEach(() => {
  showToast = vi.fn<(msg: string, type: any) => void>()
  streams = {}
  serverList = LIST()
  vi.stubGlobal('fetch', vi.fn((url: string, init?: any) => {
    const u = String(url)
    if (u.endsWith('/chat-stream')) {
      const id = JSON.parse(init.body).conversation_id
      const s = makeStream()
      streams[id] = s
      init.signal?.addEventListener('abort', () => { const e = new Error('aborted'); (e as any).name = 'AbortError'; s.fail(e) })
      return Promise.resolve(s.response)
    }
    if (u.includes('/wake-stream')) return new Promise(() => {})
    return Promise.resolve({ ok: true, status: 200, json: async () => ({}) })
  }))
  mocked.get.mockReset().mockImplementation(async (url: string) => {
    const u = String(url)
    if (u.endsWith('/conversations/1')) return { data: serverList.map(c => ({ ...c })) }
    if (u.includes('/context-usage')) return { data: { percent: 1, should_compact: false, message_count: 0 } }
    return { data: [] }
  })
  mocked.post.mockReset().mockImplementation(async (url: string) => {
    if (String(url).endsWith('/conversations/5/branch')) {
      const created = { id: 7, title: 'chat-7', parent_id: 1, hidden: false, created_at: '2026-09-26T00:00:09Z', updated_at: '2026-09-26T00:00:09Z' }
      serverList.push(created)
      return { data: created }
    }
    return { data: {} }
  })
  // Stored like the server would, since a stored hide is followed by a list read.
  mocked.put.mockReset().mockImplementation(async (url: string, body: any) => {
    const id = Number(String(url).split('/').at(-2))
    serverList = serverList.map(c => (c.id === id ? { ...c, hidden: body.hidden } : c))
    return { data: { id, hidden: body.hidden } }
  })
  mocked.delete.mockReset()
})

afterEach(() => { cleanup(); vi.unstubAllGlobals(); delete (window as any).ipc })

const sidebarProps = {
  isSidebarOpen: true, sidebarTab: 'chats', setSidebarTab: vi.fn(), selectConversation: vi.fn(),
  createNewConversation: vi.fn(), deleteConversation: vi.fn(), editingId: null, setEditingId: vi.fn(),
  tempTitle: '', setTempTitle: vi.fn(), saveRename: vi.fn(), user: USER, setShowSettings: vi.fn(), handleLogout: vi.fn(),
}

describe('branches · sidebar', () => {
  it('lists roots only, highlights the family of an active branch, and shows the family status', () => {
    render(<Sidebar {...({} as any)} {...sidebarProps}
      conversations={LIST()} activeConvId={5}
      convStatus={{ 5: 'running', 6: 'awaiting' }}
    />)
    expect(screen.queryByText('chat-5')).toBeNull()
    expect(screen.queryByText('chat-6')).toBeNull()
    expect(screen.getByText('chat-1')).toBeTruthy()
    expect(screen.getByTestId('conv-row-1').getAttribute('data-active')).toBe('true')
    expect(screen.getByTestId('conv-row-2').getAttribute('data-active')).toBeNull()
    // Awaiting outranks running across the family, hidden branch included.
    expect(screen.getByTestId('conv-status-1').getAttribute('title')).toBe(cevir('sidebar.statusAwaiting'))
    expect(screen.queryByTestId('conv-status-2')).toBeNull()
  })

  it('a list from an older backend (no parent_id) still shows every chat', () => {
    const old = [{ id: 1, title: 'a' }, { id: 2, title: 'b' }]
    render(<Sidebar {...({} as any)} {...sidebarProps} conversations={old} activeConvId={2} />)
    expect(screen.getByText('a')).toBeTruthy()
    expect(screen.getByTestId('conv-row-2').getAttribute('data-active')).toBe('true')
  })
})

describe('branches · tabs component', () => {
  const tabs = (over: object = {}) => {
    const props = {
      conversations: LIST(), activeConvId: 1, convStatus: { 5: 'unread' } as any, branchBlocked: false,
      onSelect: vi.fn(), onBranch: vi.fn(async () => {}), onClose: vi.fn(), ...over,
    }
    render(<ChatTabs {...props} />)
    return props
  }

  it('renders the root first, then visible branches; the root has no close button', () => {
    tabs()
    const ids = screen.getAllByTestId(/^chat-tab-\d+$/).map(el => el.getAttribute('data-testid'))
    expect(ids).toEqual(['chat-tab-1', 'chat-tab-5'])
    expect(screen.queryByTestId('chat-tab-close-1')).toBeNull()
    expect(screen.getByTestId('chat-tab-close-5')).toBeTruthy()
    expect(screen.getByTestId('tab-status-5').getAttribute('title')).toBe(cevir('sidebar.statusUnread'))
  })

  it('draws nothing for a family without branches', () => {
    const { container } = render(<ChatTabs conversations={LIST()} activeConvId={2} branchBlocked={false}
      onSelect={vi.fn()} onBranch={vi.fn()} onClose={vi.fn()} />)
    expect(container.innerHTML).toBe('')
  })

  it('× hands the branch id to onClose; the closed menu lists hidden branches and selects on pick', () => {
    const p = tabs()
    fireEvent.click(screen.getByTestId('chat-tab-close-5'))
    expect(p.onClose).toHaveBeenCalledWith(5)
    expect(p.onSelect).not.toHaveBeenCalled()

    const menuButton = screen.getByTestId('closed-branches')
    expect(menuButton.textContent).toContain(cevir('branch.closed', { sayi: 1 }))
    fireEvent.click(menuButton)
    fireEvent.click(screen.getByTestId('closed-branch-6'))
    expect(p.onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: 6 }))
  })

  it('"+" branches the active tab, and is disabled while the chat is busy', async () => {
    const p = tabs({ activeConvId: 5 })
    await act(async () => { fireEvent.click(screen.getByTestId('branch-new')) })
    expect(p.onBranch).toHaveBeenCalledWith(5)
    cleanup()

    const onBranch = vi.fn()
    render(<BranchButton sourceId={5} blocked onBranch={onBranch} />)
    const btn = screen.getByTestId('branch-new') as HTMLButtonElement
    expect(btn.disabled).toBe(true)
    expect(btn.getAttribute('title')).toBe(cevir('branch.newBlocked'))
    fireEvent.click(btn)
    expect(onBranch).not.toHaveBeenCalled()
  })
})

describe('branches · hook', () => {
  it('branching posts to the source id, adds the tab and selects it', async () => {
    const { result } = hook()
    await load(result)
    await act(async () => { await result.current.selectConversation(LIST()[2] as any) })
    let id: any
    await act(async () => { id = await result.current.branchConversation(5) })
    expect(mocked.post).toHaveBeenCalledWith(`${API}/conversations/5/branch`)
    expect(id).toBe(7)
    expect(result.current.activeConvId).toBe(7)
    expect(result.current.conversations.some((c: any) => c.id === 7)).toBe(true)
  })

  it('a chat mid-turn or awaiting a card is not branched; a 409 shows the server detail', async () => {
    const { result } = hook()
    await load(result)
    await act(async () => { await result.current.selectConversation(LIST()[2] as any) })
    act(() => { result.current.sendMessage('q', '', 'tr', 'auto', 'medium', vi.fn(), vi.fn()) })
    await flush()
    await act(async () => { await result.current.branchConversation(5) })
    expect(mocked.post).not.toHaveBeenCalledWith(`${API}/conversations/5/branch`)

    streams[5].push({ type: 'command_approval_needed', command: 'ls', gate_id: 'g1' })
    streams[5].push({ type: 'done', stop_reason: 'complete' })
    streams[5].close()
    await flush()
    expect(result.current.loading).toBe(false)
    await act(async () => { await result.current.branchConversation(5) })
    expect(mocked.post).not.toHaveBeenCalledWith(`${API}/conversations/5/branch`)
    expect(showToast).toHaveBeenLastCalledWith(cevir('branch.busy'), 'error')

    // Idle chat, but the server still says busy.
    mocked.post.mockRejectedValueOnce({ response: { status: 409, data: { detail: 'Sohbette süren bir tur var.' } } })
    await act(async () => { await result.current.branchConversation(2) })
    expect(showToast).toHaveBeenLastCalledWith('Sohbette süren bir tur var.', 'error')
  })

  it('closing the tab on screen hides the branch, keeps its data, and moves to the left neighbour', async () => {
    const { result } = hook()
    await load(result)
    await act(async () => { await result.current.selectConversation(LIST()[2] as any) })
    await act(async () => { await result.current.closeBranch(5) })
    expect(mocked.put).toHaveBeenCalledWith(`${API}/conversations/5/hidden`, { hidden: true })
    expect(mocked.delete).not.toHaveBeenCalled()
    expect(result.current.activeConvId).toBe(1)
    expect(result.current.conversations.find((c: any) => c.id === 5)?.hidden).toBe(true)

    // Reopened from the closed menu: unhidden, selected, history read back.
    mocked.get.mockClear()
    await act(async () => { await result.current.selectConversation(result.current.conversations.find((c: any) => c.id === 5)) })
    expect(mocked.put).toHaveBeenCalledWith(`${API}/conversations/5/hidden`, { hidden: false })
    expect(result.current.activeConvId).toBe(5)
    expect(result.current.conversations.find((c: any) => c.id === 5)?.hidden).toBe(false)
    expect(mocked.get).toHaveBeenCalledWith(`${API}/conversations/5/messages`)
  })

  it('a failed hide puts the tab back', async () => {
    const { result } = hook()
    await load(result)
    mocked.put.mockRejectedValueOnce({ response: { status: 400, data: { detail: 'root' } } })
    await act(async () => { await result.current.closeBranch(5) })
    expect(result.current.conversations.find((c: any) => c.id === 5)?.hidden).toBe(false)
    expect(showToast).toHaveBeenCalledWith('root', 'error')
  })

  it('deleting a root drops the runtime of every id the backend reports deleted', async () => {
    const { result } = hook()
    await load(result)
    await act(async () => { await result.current.selectConversation(LIST()[2] as any) })
    act(() => { result.current.sendMessage('q', '', 'tr', 'auto', 'medium', vi.fn(), vi.fn()) })
    await flush()
    expect(result.current.convStatus[5]).toBe('running')

    mocked.delete.mockResolvedValueOnce({ data: { status: 'ok', deleted_ids: [1, 5, 6] } })
    await act(async () => { await result.current.deleteConversation({ stopPropagation: vi.fn() } as any, 1) })
    expect(result.current.convStatus[5]).toBeUndefined()
    expect(result.current.activeConvId).toBeNull()
    // The aborted stream wrote nothing back.
    await flush()
    expect(result.current.convStatus[5]).toBeUndefined()
  })

  it('a delete response without deleted_ids falls back to the one id', async () => {
    const { result } = hook()
    await load(result)
    await act(async () => { await result.current.selectConversation(LIST()[1] as any) })
    mocked.delete.mockResolvedValueOnce({ data: { status: 'ok' } })
    await act(async () => { await result.current.deleteConversation({ stopPropagation: vi.fn() } as any, 2) })
    expect(result.current.activeConvId).toBeNull()
  })

  it('a notification that opens a hidden branch unhides it', async () => {
    const listeners: Record<string, (...a: unknown[]) => void> = {}
    ;(window as any).ipc = {
      invoke: vi.fn(async () => ({})),
      on: (ch: string, cb: any) => { listeners[ch] = cb; return () => { delete listeners[ch] } },
    }
    const { result } = renderHook(() => {
      const chat = useChat(API, USER, CONFIG, '/ws', showToast, vi.fn(), (n: string) => n)
      useChatNotifications({
        conversations: chat.conversations, activeConvId: chat.activeConvId, attention: chat.attention,
        trayGates: [], bridgeSynced: true, screenCardOpen: false,
        onOpenConversation: (c) => { chat.selectConversation(c) },
      })
      return chat
    })
    await load(result)
    await act(async () => { listeners['open-conversation'](6) })
    await flush()
    expect(result.current.activeConvId).toBe(6)
    expect(mocked.put).toHaveBeenCalledWith(`${API}/conversations/6/hidden`, { hidden: false })
    expect(result.current.conversations.find((c: any) => c.id === 6)?.hidden).toBe(false)
  })
})

describe('branches · i18n', () => {
  it('every branch string exists in both languages', async () => {
    const { translations } = await import('../renderer/lib/i18n')
    for (const key of ['branch.tabs', 'branch.new', 'branch.newBlocked', 'branch.close', 'branch.closed', 'branch.busy', 'branch.failed', 'branch.hideFailed']) {
      expect(translations.tr[key]).toBeTruthy()
      expect(translations.en[key]).toBeTruthy()
    }
  })
})
