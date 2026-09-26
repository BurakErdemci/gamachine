/**
 * Regressions from the Codex audit of tabbed branching (commit 4027b43):
 *  - a branch whose root is no longer listed must still be reachable;
 *  - an older list response must not undo a newer hide, unhide or branch;
 *  - the chat on screen always has a tab, even when reopening it failed.
 *
 * The list mock answers like a server: with its state at the moment the
 * request was made, even when the answer is delivered late.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, renderHook, act } from '@testing-library/react'

vi.mock('axios', () => ({
  default: { get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() },
}))

import axios from 'axios'
import { useChat } from '../renderer/hooks/home/useChat'
import { Sidebar } from '../renderer/components/home/Sidebar'
import { ChatTabs } from '../renderer/components/home/ChatTabs'
import { familyOf, familyRootId, rootsOf } from '../renderer/lib/convFamily'

const mocked = axios as unknown as Record<'post' | 'get' | 'delete' | 'put', ReturnType<typeof vi.fn>>

const API = 'http://127.0.0.1:8000'
const USER = { id: 1, name: 'b', sessionToken: 'tok' } as any
const CONFIG = { provider_type: 'subscription', model_name: 'claude-opus-5' } as any

const conv = (id: number, extra: object = {}) => ({
  id, title: `chat-${id}`, created_at: `2026-09-26T00:00:0${id}Z`, updated_at: '2026-09-26T00:00:00Z',
  parent_id: null as number | null, hidden: false, ...extra,
})

let serverList: any[]
let listCalls: number
// Each held list request answers with the snapshot taken when it was made.
let held: Array<() => void>
let holdNextList: boolean
let heldPut: Array<() => void>
let holdNextPut: boolean

const snapshot = () => serverList.map(c => ({ ...c }))
const hook = () => renderHook(() => useChat(API, USER, CONFIG, '/ws', vi.fn(), vi.fn(), (n: string) => n))
const flush = async () => { await act(async () => { for (let i = 0; i < 10; i++) await Promise.resolve() }) }

beforeEach(() => {
  serverList = [conv(1), conv(2, { parent_id: 1 })]
  listCalls = 0
  held = []
  holdNextList = false
  heldPut = []
  holdNextPut = false
  mocked.get.mockReset().mockImplementation((url: string) => {
    const u = String(url)
    if (u.endsWith('/approval-mode')) return Promise.resolve({ data: { mode: 'step', stored: true } })
    if (u.endsWith('/conversations/1')) {
      listCalls += 1
      const data = snapshot()
      if (holdNextList) {
        holdNextList = false
        return new Promise(resolve => { held.push(() => resolve({ data })) })
      }
      return Promise.resolve({ data })
    }
    if (u.endsWith('/context-usage')) return Promise.resolve({ data: { percent: 0 } })
    return Promise.resolve({ data: [] })
  })
  mocked.put.mockReset().mockImplementation((url: string, body: any) => {
    const id = Number(String(url).split('/').at(-2))
    const apply = () => {
      serverList = serverList.map(c => (c.id === id ? { ...c, hidden: body.hidden } : c))
      return { data: { id, hidden: body.hidden } }
    }
    if (holdNextPut) {
      holdNextPut = false
      return new Promise(resolve => { heldPut.push(() => resolve(apply())) })
    }
    return Promise.resolve(apply())
  })
  mocked.post.mockReset().mockImplementation((url: string) => {
    if (String(url).endsWith('/branch')) {
      const created = conv(3, { parent_id: 1 })
      serverList.push(created)
      return Promise.resolve({ data: { ...created } })
    }
    return Promise.resolve({ data: {} })
  })
})

afterEach(() => { cleanup() })

describe('audit · orphaned branch', () => {
  const orphan = conv(2, { parent_id: 1 })

  it('a branch whose root is not listed is a root of its own', () => {
    const listed = [orphan, conv(4)]
    expect(rootsOf(listed)).toContain(orphan)
    expect(familyRootId(listed, 2)).toBe(2)
    expect(familyOf(listed, 2).root).toEqual(orphan)
    expect(familyOf(listed, 2).branches).toEqual([])
  })

  it('the sidebar lists it and highlights it when it is on screen', () => {
    render(<Sidebar {...({} as any)}
      isSidebarOpen sidebarTab="chats" setSidebarTab={vi.fn()} selectConversation={vi.fn()}
      createNewConversation={vi.fn()} deleteConversation={vi.fn()} editingId={null} setEditingId={vi.fn()}
      tempTitle="" setTempTitle={vi.fn()} saveRename={vi.fn()} user={USER} setShowSettings={vi.fn()} handleLogout={vi.fn()}
      conversations={[orphan, conv(4)]} activeConvId={2}
    />)
    expect(screen.getByText('chat-2')).toBeTruthy()
    expect(screen.getByTestId('conv-row-2').getAttribute('data-active')).toBe('true')
  })

  it('it cannot be closed like a branch, since nothing would list it', async () => {
    serverList = [orphan]
    const { result } = hook()
    await act(async () => { await result.current.fetchConversations(1) })
    let closed: any
    await act(async () => { closed = await result.current.closeBranch(2) })
    expect(closed).toBe(false)
    expect(mocked.put).not.toHaveBeenCalled()
  })
})

describe('audit · stale conversation list', () => {
  it('an older list response does not reopen a branch the user closed', async () => {
    const { result } = hook()
    await act(async () => { await result.current.fetchConversations(1) })
    holdNextList = true
    let pending!: Promise<void>
    act(() => { pending = result.current.fetchConversations(1) })
    await act(async () => { await result.current.closeBranch(2) })
    expect(result.current.conversations.find(c => c.id === 2)?.hidden).toBe(true)
    await act(async () => { held[0](); await pending })
    await flush()
    expect(result.current.conversations.find(c => c.id === 2)?.hidden).toBe(true)
  })

  it('an older list response does not drop a newly created branch', async () => {
    serverList = [conv(1), conv(2)]
    const { result } = hook()
    await act(async () => { await result.current.fetchConversations(1) })
    holdNextList = true
    let pending!: Promise<void>
    act(() => { pending = result.current.fetchConversations(1) })
    await act(async () => { await result.current.branchConversation(1) })
    expect(result.current.activeConvId).toBe(3)
    expect(result.current.conversations.some(c => c.id === 3)).toBe(true)
    await act(async () => { held[0](); await pending })
    await flush()
    expect(result.current.conversations.some(c => c.id === 3)).toBe(true)
  })

  it('a list read while the hide is still in flight keeps the tab closed', async () => {
    const { result } = hook()
    await act(async () => { await result.current.fetchConversations(1) })
    holdNextPut = true
    let closing!: Promise<unknown>
    act(() => { closing = result.current.closeBranch(2) })
    // The server has not stored the hide yet, so this answer still says visible.
    await act(async () => { await result.current.fetchConversations(1) })
    expect(result.current.conversations.find(c => c.id === 2)?.hidden).toBe(true)
    await act(async () => { heldPut[0](); await closing })
    await flush()
    expect(result.current.conversations.find(c => c.id === 2)?.hidden).toBe(true)
  })

  it('a stored hide and a created branch are followed by a fresh list read', async () => {
    const { result } = hook()
    await act(async () => { await result.current.fetchConversations(1) })
    const before = listCalls
    await act(async () => { await result.current.closeBranch(2) })
    await flush()
    expect(listCalls).toBe(before + 1)
    await act(async () => { await result.current.branchConversation(1) })
    await flush()
    expect(listCalls).toBe(before + 2)
  })
})

describe('audit · failed reopen', () => {
  it('the chat on screen keeps its tab when unhiding it fails, and closing it moves left', async () => {
    serverList = [conv(1), conv(2, { parent_id: 1, hidden: true })]
    mocked.put.mockRejectedValueOnce({ response: { status: 500, data: { detail: 'unhide failed' } } })
    const { result } = hook()
    await act(async () => { await result.current.fetchConversations(1) })
    await act(async () => { await result.current.selectConversation(result.current.conversations[1]) })
    await flush()

    const active = result.current.activeConvId
    expect(active).toBe(2)
    const list = result.current.conversations
    const fam = familyOf(list, familyRootId(list, active), active)
    expect(fam.visible.map(c => c.id)).toContain(2)
    expect(fam.hidden.map(c => c.id)).not.toContain(2)

    render(<ChatTabs conversations={list} activeConvId={active} branchBlocked={false}
      onSelect={vi.fn()} onBranch={vi.fn()} onClose={vi.fn()} />)
    expect(screen.getByTestId('chat-tab-2').querySelector('[aria-selected="true"]')).toBeTruthy()
    expect(screen.queryByTestId('closed-branches')).toBeNull()

    await act(async () => { await result.current.closeBranch(2) })
    await flush()
    expect(result.current.activeConvId).toBe(1)
  })
})

// Codex verifyf: sent in parallel, an older hide could be stored after a newer
// reopen and hide the branch again. One chat's writes now go out in order.
describe('audit · hide/unhide write order', () => {
  it('sends a reopen only after the pending hide is stored, so the reopen wins', async () => {
    const outstanding: Array<{ hidden: boolean; resolve: () => void }> = []
    mocked.put.mockImplementation((url: string, body: { hidden: boolean }) => {
      const id = Number(String(url).split('/').at(-2))
      return new Promise(resolve => outstanding.push({
        hidden: body.hidden,
        resolve: () => {
          serverList = serverList.map(c => (c.id === id ? { ...c, hidden: body.hidden } : c))
          resolve({ data: { id, hidden: body.hidden } })
        },
      }))
    })
    const { result } = hook()
    await act(async () => { await result.current.fetchConversations(1) })
    const branch = result.current.conversations.find(c => c.id === 2)!
    await act(async () => { await result.current.selectConversation(branch) })
    let closing!: Promise<boolean>
    act(() => { closing = result.current.closeBranch(2) })
    await flush()
    act(() => { void result.current.selectConversation(result.current.conversations.find(c => c.id === 2)!) })
    await flush()
    expect(outstanding.map(x => x.hidden)).toEqual([true])
    await act(async () => { outstanding[0].resolve(); await closing })
    await flush()
    expect(outstanding.map(x => x.hidden)).toEqual([true, false])
    await act(async () => { outstanding[1].resolve() })
    await flush()
    expect(serverList.find(c => c.id === 2)?.hidden).toBe(false)
    expect(result.current.conversations.find(c => c.id === 2)?.hidden).toBe(false)
  })
})
