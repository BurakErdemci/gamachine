/**
 * AI chat titles (Burak, 27 Sep 2026): the backend names a chat after its 1st
 * and 3rd reply and pushes the title on /wake-stream-all. Tabs, sidebar and
 * mention chips all read `chat.conversations`, so the frame must land there
 * without a reload, and an older list read must not put the old title back.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, renderHook, act, fireEvent } from '@testing-library/react'

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
import { SettingsModal } from '../renderer/components/home/SettingsModal'
import { Sidebar } from '../renderer/components/home/Sidebar'
import { MarkdownRenderer } from '../renderer/components/home/MarkdownRenderer'
import { useChat } from '../renderer/hooks/home/useChat'
import { useAutoChatTitles } from '../renderer/hooks/home/useAutoChatTitles'
import { cevir, ceviriUygula } from '../renderer/lib/i18n'

const mocked = axios as unknown as {
  get: ReturnType<typeof vi.fn>; post: ReturnType<typeof vi.fn>; put: ReturnType<typeof vi.fn>
}
const API = 'http://127.0.0.1:8000'
const USER = { id: 1, name: 'b', sessionToken: 'tok' }

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

// ── Settings toggle ─────────────────────────────────────────────────────────

const temel: any = {
  open: true,
  providersWithKeys: [],
  onChange: () => {},
  onClose: () => {},
  onSave: async () => {},
  onLogout: () => {},
  onDeleteKey: async () => {},
  unityMcpStatus: 'off',
  unityMcpToggling: false,
  onToggleUnityMcp: () => {},
  lang: 'tr',
  onLangChange: () => {},
  aiConfig: { provider_type: 'subscription', model_name: 'claude-opus-5', api_key: '' },
  availableModels: { local: [], subscription: [], cloud: [] },
}

describe('settings · auto chat titles toggle', () => {
  it('shows its state as a switch and reports clicks', () => {
    const onToggle = vi.fn()
    const { rerender } = render(<SettingsModal {...temel} autoTitles onToggleAutoTitles={onToggle} />)
    const sw = screen.getByTestId('auto-titles-toggle')
    expect(sw.getAttribute('role')).toBe('switch')
    expect(sw.getAttribute('aria-checked')).toBe('true')
    expect(screen.getByText(cevir('settings.autoTitles'))).toBeTruthy()
    fireEvent.click(sw)
    expect(onToggle).toHaveBeenCalledTimes(1)
    rerender(<SettingsModal {...temel} autoTitles={false} onToggleAutoTitles={onToggle} />)
    expect(screen.getByTestId('auto-titles-toggle').getAttribute('aria-checked')).toBe('false')
  })

  it('says in both languages which models write titles', () => {
    // Mirrors chat_titles.title_model_for: Copilot, Cursor and Kimi keep the first message.
    for (const lang of ['tr', 'en'] as const) {
      const hint = ceviriUygula(lang, 'settings.autoTitlesHint')
      for (const name of ['Claude Code', 'Codex', 'Antigravity', 'OpenCode', 'API']) {
        expect(hint).toContain(name)
      }
      expect(hint).not.toMatch(/Copilot|Cursor|Kimi|cheapest|en ucuz/)
    }
    expect(ceviriUygula('en', 'settings.autoTitlesHint')).toContain('first-message title')
    expect(ceviriUygula('tr', 'settings.autoTitlesHint')).toContain('ilk mesajdan')
  })

  it('is disabled while the change is being saved', () => {
    render(<SettingsModal {...temel} autoTitles autoTitlesSaving onToggleAutoTitles={vi.fn()} />)
    expect((screen.getByTestId('auto-titles-toggle') as HTMLButtonElement).disabled).toBe(true)
  })
})

describe('useAutoChatTitles', () => {
  beforeEach(() => {
    mocked.get.mockReset()
    mocked.post.mockReset()
  })

  it('reads the stored setting and saves a toggle to the backend', async () => {
    mocked.get.mockResolvedValue({ data: { enabled: false } })
    mocked.post.mockResolvedValue({ data: { enabled: true } })
    const { result } = renderHook(() => useAutoChatTitles(API, 1, vi.fn()))
    await act(async () => { await Promise.resolve() })
    expect(mocked.get).toHaveBeenCalledWith(`${API}/chat-title-setting`)
    expect(result.current.autoTitles).toBe(false)
    await act(async () => { await result.current.toggleAutoTitles() })
    expect(mocked.post).toHaveBeenCalledWith(`${API}/chat-title-setting`, { enabled: true })
    expect(result.current.autoTitles).toBe(true)
  })

  it('defaults to on and rolls back with a toast when saving fails', async () => {
    mocked.get.mockRejectedValue(new Error('offline'))
    mocked.post.mockRejectedValue(new Error('offline'))
    const toast = vi.fn()
    const { result } = renderHook(() => useAutoChatTitles(API, 1, toast))
    await act(async () => { await Promise.resolve() })
    expect(result.current.autoTitles).toBe(true)
    await act(async () => { await result.current.toggleAutoTitles() })
    expect(result.current.autoTitles).toBe(true)
    expect(toast).toHaveBeenCalledWith(cevir('settings.autoTitlesFailed'), 'error')
  })
})

// ── Live title frame ────────────────────────────────────────────────────────

/** /wake-stream-all whose frames the test pushes one by one. */
function wakeChannel() {
  const queue: string[] = []
  let wake: (() => void) | null = null
  const push = (frame: object) => {
    queue.push(`data: ${JSON.stringify(frame)}\n\n`)
    wake?.()
  }
  const fetchMock = vi.fn().mockImplementation((url: string) => {
    if (!String(url).includes('/wake-stream-all')) return new Promise(() => {})
    return Promise.resolve({
      ok: true,
      body: {
        getReader: () => ({
          read: () => new Promise(resolve => {
            const give = () => resolve({ done: false, value: new TextEncoder().encode(queue.shift()!) })
            if (queue.length) give()
            else wake = () => { wake = null; give() }
          }),
        }),
      },
    })
  })
  return { fetchMock, push }
}

const hook = () => renderHook(() => useChat(
  API, USER as any, { provider_type: 'subscription', model_name: 'claude-opus-5' } as any,
  '/ws', vi.fn(), vi.fn(), (n: string) => n,
))

const tick = () => act(async () => { await new Promise(r => setTimeout(r, 20)) })

describe('useChat · AI title frames', () => {
  let serverList: any[]

  beforeEach(() => {
    serverList = [
      { id: 5, title: 'zıplama mekaniği nasıl olmalı ya ben...', parent_id: null, hidden: false },
      { id: 6, title: 'Diğer sohbet', parent_id: null, hidden: false },
    ]
    mocked.get.mockReset().mockImplementation((url: string) => (
      String(url).endsWith('/conversations/1')
        ? Promise.resolve({ data: serverList.map(c => ({ ...c })) })
        : Promise.resolve({ data: [] })
    ))
    mocked.put.mockReset().mockResolvedValue({ data: { status: 'success' } })
  })

  it('a title frame renames the chat in the list without a refetch', async () => {
    const ch = wakeChannel()
    vi.stubGlobal('fetch', ch.fetchMock)
    const { result } = hook()
    await act(async () => { await result.current.fetchConversations(1) })
    const reads = mocked.get.mock.calls.length
    ch.push({ type: 'title', conversation_id: 5, title: 'Zıplama Mekaniği Tasarımı' })
    await tick()
    expect(result.current.conversations.find(c => c.id === 5)?.title).toBe('Zıplama Mekaniği Tasarımı')
    expect(result.current.conversations.find(c => c.id === 6)?.title).toBe('Diğer sohbet')
    expect(mocked.get.mock.calls.length).toBe(reads)
  })

  it('a list read started before the frame cannot put the old title back', async () => {
    const ch = wakeChannel()
    vi.stubGlobal('fetch', ch.fetchMock)
    const { result } = hook()
    await act(async () => { await result.current.fetchConversations(1) })
    let release: (v: any) => void = () => {}
    mocked.get.mockImplementationOnce(() => new Promise(r => { release = r }))
    let slowRead: Promise<void> = Promise.resolve()
    act(() => { slowRead = result.current.fetchConversations(1) })
    ch.push({ type: 'title', conversation_id: 5, title: 'Yeni Başlık' })
    await tick()
    // The slow read was answered from the DB before the title was written.
    await act(async () => { release({ data: serverList.map(c => ({ ...c })) }); await slowRead })
    expect(result.current.conversations.find(c => c.id === 5)?.title).toBe('Yeni Başlık')
    // A read started after the frame is the DB's truth again.
    serverList[0].title = 'Yeni Başlık'
    await act(async () => { await result.current.fetchConversations(1) })
    expect(result.current.conversations.find(c => c.id === 5)?.title).toBe('Yeni Başlık')
  })

  it('a hand rename after the frame is not overridden by it', async () => {
    const ch = wakeChannel()
    vi.stubGlobal('fetch', ch.fetchMock)
    const { result } = hook()
    await act(async () => { await result.current.fetchConversations(1) })
    ch.push({ type: 'title', conversation_id: 5, title: 'Yapay Zeka Adı' })
    await tick()
    serverList[0].title = 'Benim adım'
    await act(async () => { await result.current.renameConversation(5, 'Benim adım') })
    await tick()
    expect(result.current.conversations.find(c => c.id === 5)?.title).toBe('Benim adım')
  })

  it('ignores malformed title frames and still starts no wake turn for them', async () => {
    const ch = wakeChannel()
    vi.stubGlobal('fetch', ch.fetchMock)
    const { result } = hook()
    await act(async () => { await result.current.fetchConversations(1) })
    ch.push({ type: 'title', conversation_id: 'x', title: 'Bozuk' })
    ch.push({ type: 'title', conversation_id: 5, title: '   ' })
    await tick()
    expect(result.current.conversations.find(c => c.id === 5)?.title).toBe(serverList[0].title)
    expect(ch.fetchMock.mock.calls.some(c => String(c[0]).endsWith('/chat-stream'))).toBe(false)
  })

  it('the new title reaches the sidebar and the mention chip', async () => {
    const ch = wakeChannel()
    vi.stubGlobal('fetch', ch.fetchMock)
    const { result } = hook()
    await act(async () => { await result.current.fetchConversations(1) })
    ch.push({ type: 'title', conversation_id: 5, title: 'Kamera Takibi' })
    await tick()
    const conversations = result.current.conversations
    render(<Sidebar
      {...({} as any)}
      isSidebarOpen sidebarTab="chats" setSidebarTab={vi.fn()}
      conversations={conversations} activeConvId={5} selectConversation={vi.fn()}
      createNewConversation={vi.fn()} deleteConversation={vi.fn()}
      editingId={null} setEditingId={vi.fn()} tempTitle="" setTempTitle={vi.fn()} saveRename={vi.fn()}
      user={USER} setShowSettings={vi.fn()} handleLogout={vi.fn()}
      convStatus={{}}
    />)
    expect(screen.getByText('Kamera Takibi')).toBeTruthy()
    const titles = new Map(conversations.map(c => [c.id, c.title]))
    render(<MarkdownRenderer content="bak @5" mentionTitles={titles} />)
    const chip = document.querySelector('[data-mention="5"]')
    expect(chip?.textContent).toBe('@Kamera Takibi')
  })
})
