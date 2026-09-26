/**
 * Right-click menu on a chat tab: Rename (every tab), Close (branch: hide,
 * nothing deleted), Delete (branch only, confirmed, only that branch). A
 * deleted tab on screen hands the screen to its left neighbour. Escape, an
 * outside click, and picking an item close the menu.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, act, fireEvent, renderHook } from '@testing-library/react'

vi.mock('axios', () => {
  const post = vi.fn(); const get = vi.fn(); const del = vi.fn(); const put = vi.fn()
  return { default: { post, get, delete: del, put }, post, get }
})
vi.mock('../renderer/components/ui/ConfirmDialog', () => ({ confirmDialog: vi.fn() }))

import axios from 'axios'
import { confirmDialog } from '../renderer/components/ui/ConfirmDialog'
import { useChat } from '../renderer/hooks/home/useChat'
import { ChatTabs } from '../renderer/components/home/ChatTabs'
import { cevir, translations } from '../renderer/lib/i18n'

const mocked = axios as unknown as Record<'post' | 'get' | 'delete' | 'put', ReturnType<typeof vi.fn>>
const confirm = confirmDialog as unknown as ReturnType<typeof vi.fn>

const API = 'http://127.0.0.1:8000'
const USER = { id: 1, name: 'b', sessionToken: 'tok' } as any
const CONFIG = { provider_type: 'subscription', model_name: 'claude-opus-5' } as any

const conv = (id: number, extra: object = {}) => ({
  id, title: `chat-${id}`, created_at: `2026-09-26T00:00:0${id}Z`, updated_at: '2026-09-26T00:00:00Z',
  parent_id: null, hidden: false, ...extra,
})
// Root 1 with visible branches 5 and 7 and a hidden branch 6; root 2 alone.
const LIST = () => [conv(1), conv(2), conv(5, { parent_id: 1 }), conv(6, { parent_id: 1, hidden: true }), conv(7, { parent_id: 1 })]

let serverList: any[]
let showToast: ReturnType<typeof vi.fn>

beforeEach(() => {
  serverList = LIST()
  showToast = vi.fn()
  confirm.mockReset().mockResolvedValue(true)
  mocked.get.mockReset().mockImplementation(async (url: string) => {
    const u = String(url)
    if (u.endsWith('/conversations/1')) return { data: serverList.map(c => ({ ...c })) }
    if (u.includes('/context-usage')) return { data: { percent: 0, message_count: 0 } }
    return { data: [] }
  })
  mocked.put.mockReset().mockImplementation(async (url: string, body: any) => {
    const id = Number(String(url).split('/').filter(Boolean).at(-1))
    if (body && 'title' in body) serverList = serverList.map(c => (c.id === id ? { ...c, title: body.title } : c))
    return { data: {} }
  })
  mocked.delete.mockReset().mockImplementation(async (url: string) => {
    const id = Number(String(url).split('/').at(-1))
    serverList = serverList.filter(c => c.id !== id)
    return { data: { status: 'ok', deleted_ids: [id] } }
  })
  mocked.post.mockReset().mockResolvedValue({ data: {} })
  vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})))
})

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

const flush = async () => { await act(async () => { for (let i = 0; i < 10; i++) await Promise.resolve() }) }

describe('tab menu · component', () => {
  const mount = (over: object = {}) => {
    const props = {
      conversations: LIST(), activeConvId: 1, branchBlocked: false,
      onSelect: vi.fn(), onBranch: vi.fn(async () => {}), onClose: vi.fn(),
      onRename: vi.fn(async () => true), onDelete: vi.fn(async () => {}), ...over,
    }
    render(<ChatTabs {...(props as any)} />)
    return props
  }
  const tabButton = (id: number) => screen.getByTestId(`chat-tab-${id}`).querySelector('[role="tab"]') as HTMLElement
  const rightClick = (id: number, at = { clientX: 40, clientY: 20 }) => fireEvent.contextMenu(tabButton(id), at)

  it('the root tab offers Rename only; a branch tab offers Rename, Close and Delete', () => {
    mount()
    const notPrevented = rightClick(1)
    expect(notPrevented).toBe(false)
    expect(screen.getByTestId('tab-menu-rename').textContent).toContain(cevir('branch.menuRename'))
    expect(screen.queryByTestId('tab-menu-close')).toBeNull()
    expect(screen.queryByTestId('tab-menu-delete')).toBeNull()

    rightClick(5)
    expect(screen.getByTestId('tab-menu-rename')).toBeTruthy()
    expect(screen.getByTestId('tab-menu-close').textContent).toContain(cevir('branch.menuClose'))
    expect(screen.getByTestId('tab-menu-delete').textContent).toContain(cevir('branch.menuDelete'))
    expect(screen.getAllByTestId('tab-menu')).toHaveLength(1)
  })

  it('Close hides through onClose, Delete goes to onDelete; either closes the menu', () => {
    const p = mount()
    rightClick(5)
    fireEvent.click(screen.getByTestId('tab-menu-close'))
    expect(p.onClose).toHaveBeenCalledWith(5)
    expect(p.onDelete).not.toHaveBeenCalled()
    expect(screen.queryByTestId('tab-menu')).toBeNull()

    rightClick(7)
    fireEvent.click(screen.getByTestId('tab-menu-delete'))
    expect(p.onDelete).toHaveBeenCalledWith(7)
    expect(screen.queryByTestId('tab-menu')).toBeNull()
    expect(p.onSelect).not.toHaveBeenCalled()
  })

  it('Escape and an outside click close it; a click inside does not', () => {
    mount()
    rightClick(5)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByTestId('tab-menu')).toBeNull()

    rightClick(5)
    fireEvent.mouseDown(screen.getByTestId('tab-menu'))
    expect(screen.getByTestId('tab-menu')).toBeTruthy()
    fireEvent.mouseDown(document.body)
    expect(screen.queryByTestId('tab-menu')).toBeNull()
  })

  it('keyboard: the context-menu key anchors under the tab, focuses the first item, arrows move', () => {
    mount()
    rightClick(5, { clientX: 0, clientY: 0 })
    const menu = screen.getByTestId('tab-menu')
    expect(document.activeElement).toBe(screen.getByTestId('tab-menu-rename'))
    fireEvent.keyDown(menu, { key: 'ArrowDown' })
    expect(document.activeElement).toBe(screen.getByTestId('tab-menu-close'))
    fireEvent.keyDown(menu, { key: 'ArrowUp' })
    fireEvent.keyDown(menu, { key: 'ArrowUp' })
    expect(document.activeElement).toBe(screen.getByTestId('tab-menu-delete'))
  })

  it('stays inside the window near the right edge', () => {
    mount()
    rightClick(5, { clientX: window.innerWidth - 10, clientY: 20 })
    const left = parseFloat(screen.getByTestId('tab-menu').style.left)
    expect(left + 160).toBeLessThanOrEqual(window.innerWidth)
  })

  it('Rename edits in the tab: Enter saves once, Escape and a blank title cancel', async () => {
    const p = mount()
    rightClick(5)
    fireEvent.click(screen.getByTestId('tab-menu-rename'))
    const input = screen.getByTestId('chat-tab-rename-5') as HTMLInputElement
    expect(input.value).toBe('chat-5')
    fireEvent.change(input, { target: { value: 'new name' } })
    await act(async () => { fireEvent.keyDown(input, { key: 'Enter' }) })
    await act(async () => { fireEvent.blur(input) })
    expect(p.onRename).toHaveBeenCalledTimes(1)
    expect(p.onRename).toHaveBeenCalledWith(5, 'new name')
    expect(screen.queryByTestId('chat-tab-rename-5')).toBeNull()

    rightClick(1)
    fireEvent.click(screen.getByTestId('tab-menu-rename'))
    fireEvent.change(screen.getByTestId('chat-tab-rename-1'), { target: { value: 'x' } })
    fireEvent.keyDown(screen.getByTestId('chat-tab-rename-1'), { key: 'Escape' })
    expect(screen.queryByTestId('chat-tab-rename-1')).toBeNull()

    rightClick(1)
    fireEvent.click(screen.getByTestId('tab-menu-rename'))
    fireEvent.change(screen.getByTestId('chat-tab-rename-1'), { target: { value: '   ' } })
    await act(async () => { fireEvent.blur(screen.getByTestId('chat-tab-rename-1')) })
    expect(p.onRename).toHaveBeenCalledTimes(1)
  })

  it('a refused rename brings the editor back with what was typed', async () => {
    mount({ onRename: vi.fn(async () => false) })
    rightClick(5)
    fireEvent.click(screen.getByTestId('tab-menu-rename'))
    fireEvent.change(screen.getByTestId('chat-tab-rename-5'), { target: { value: 'kept' } })
    await act(async () => { fireEvent.keyDown(screen.getByTestId('chat-tab-rename-5'), { key: 'Enter' }) })
    await flush()
    expect((screen.getByTestId('chat-tab-rename-5') as HTMLInputElement).value).toBe('kept')
  })
})

describe('tab menu · wired to useChat', () => {
  const Harness: React.FC<{ onChat: (c: ReturnType<typeof useChat>) => void }> = ({ onChat }) => {
    const chat = useChat(API, USER, CONFIG, '/ws', showToast as any, vi.fn(), (n: string) => n)
    onChat(chat)
    return (
      <ChatTabs
        conversations={chat.conversations} activeConvId={chat.activeConvId} convStatus={chat.convStatus}
        branchBlocked={false} onSelect={chat.selectConversation} onBranch={async () => null}
        onClose={chat.closeBranch} onRename={chat.renameConversation} onDelete={chat.deleteBranch}
      />
    )
  }
  let chat: ReturnType<typeof useChat>
  const mount = async (screenId: number) => {
    render(<Harness onChat={c => { chat = c }} />)
    await act(async () => { await chat.fetchConversations(1) })
    await act(async () => { await chat.selectConversation(chat.conversations.find(c => c.id === screenId)!) })
    await flush()
  }
  const menuPick = async (id: number, item: string) => {
    fireEvent.contextMenu(screen.getByTestId(`chat-tab-${id}`).querySelector('[role="tab"]')!, { clientX: 5, clientY: 5 })
    await act(async () => { fireEvent.click(screen.getByTestId(item)) })
    await flush()
  }

  it('Delete on the tab on screen confirms, deletes only that branch, and moves left', async () => {
    await mount(7)
    await menuPick(7, 'tab-menu-delete')
    expect(confirm).toHaveBeenCalledWith(cevir('branch.deleteConfirm'))
    expect(mocked.delete).toHaveBeenCalledTimes(1)
    expect(mocked.delete).toHaveBeenCalledWith(`${API}/conversations/7`)
    expect(chat.activeConvId).toBe(5)
    expect(screen.queryByTestId('chat-tab-7')).toBeNull()
    expect(chat.conversations.map(c => c.id).sort()).toEqual([1, 2, 5, 6])
  })

  it('the first branch on screen falls back to the root', async () => {
    await mount(5)
    await menuPick(5, 'tab-menu-delete')
    expect(chat.activeConvId).toBe(1)
  })

  it('deleting a tab that is not on screen leaves the screen alone', async () => {
    await mount(1)
    await menuPick(7, 'tab-menu-delete')
    expect(mocked.delete).toHaveBeenCalledWith(`${API}/conversations/7`)
    expect(chat.activeConvId).toBe(1)
  })

  it('a declined confirm deletes nothing', async () => {
    confirm.mockResolvedValueOnce(false)
    await mount(5)
    await menuPick(5, 'tab-menu-delete')
    expect(mocked.delete).not.toHaveBeenCalled()
    expect(chat.activeConvId).toBe(5)
  })

  it('a failed delete says so and keeps the tab on screen', async () => {
    mocked.delete.mockRejectedValueOnce({ response: { status: 500, data: { detail: 'db locked' } } })
    await mount(5)
    await menuPick(5, 'tab-menu-delete')
    expect(showToast).toHaveBeenCalledWith('db locked', 'error')
    expect(chat.activeConvId).toBe(5)
    expect(screen.getByTestId('chat-tab-5')).toBeTruthy()
  })

  it('the hook refuses to delete a root through the branch path', async () => {
    await mount(1)
    let out: any
    await act(async () => { out = await chat.deleteBranch(1) })
    expect(out).toBe(false)
    expect(confirm).not.toHaveBeenCalled()
    expect(mocked.delete).not.toHaveBeenCalled()
  })

  it('Close hides the branch and moves left, deleting nothing', async () => {
    await mount(7)
    await menuPick(7, 'tab-menu-close')
    expect(mocked.put).toHaveBeenCalledWith(`${API}/conversations/7/hidden`, { hidden: true })
    expect(mocked.delete).not.toHaveBeenCalled()
    expect(chat.activeConvId).toBe(5)
  })

  it('Rename stores the title through PUT /conversations/{id} and shows it', async () => {
    await mount(1)
    await menuPick(5, 'tab-menu-rename')
    const input = screen.getByTestId('chat-tab-rename-5')
    fireEvent.change(input, { target: { value: 'Physics pass' } })
    await act(async () => { fireEvent.keyDown(input, { key: 'Enter' }) })
    await flush()
    expect(mocked.put).toHaveBeenCalledWith(`${API}/conversations/5`, { title: 'Physics pass' })
    expect(screen.getByTestId('chat-tab-5').textContent).toContain('Physics pass')
  })

  it('a failed rename shows why', async () => {
    mocked.put.mockRejectedValueOnce(new Error('offline'))
    await mount(1)
    let ok: any
    await act(async () => { ok = await chat.renameConversation(5, 'x') })
    expect(ok).toBe(false)
    expect(showToast).toHaveBeenCalledWith(expect.any(String), 'error')
  })
})

describe('tab menu · strings', () => {
  it('exist in both languages', () => {
    for (const key of ['branch.menuRename', 'branch.menuClose', 'branch.menuDelete', 'branch.deleteConfirm', 'branch.deleteFailed', 'chat.renameFailed']) {
      expect(translations.tr[key as keyof typeof translations.tr]).toBeTruthy()
      expect(translations.en[key as keyof typeof translations.en]).toBeTruthy()
    }
  })
})
