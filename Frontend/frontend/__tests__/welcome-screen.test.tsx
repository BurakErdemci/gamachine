import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act, cleanup, waitFor, within } from '@testing-library/react'

const mocks = vi.hoisted(() => {
  const invoke = vi.fn()
  const registerDroppedFolder = vi.fn()
  ;(globalThis as any).window.ipc = { invoke, registerDroppedFolder, on: vi.fn(() => () => {}) }
  return { invoke, registerDroppedFolder }
})
vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), defaults: { headers: { common: {} } } } }))

import axios from 'axios'
import { WorkspaceScreen, UNITY_DOWNLOAD_URL, relativeWhen } from '../renderer/components/home/WorkspaceScreen'
import { LangContext, ceviriUygula, translations, type Lang } from '../renderer/lib/i18n'

const en = translations.en
const tr = translations.tr
const pad = (n: number) => String(n).padStart(2, '0')
const stamp = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
const hoursAgo = (h: number) => stamp(new Date(Date.now() - h * 3600_000))
// Noon two calendar days back: always past the 24 h "hours" range, whatever the clock says now.
const twoDaysAgo = () => { const d = new Date(); d.setDate(d.getDate() - 2); d.setHours(12, 0, 0, 0); return stamp(d) }

// Backend paths start with /b/, host paths with /h/; /b/nomap cannot be mapped to the host.
const ROWS = [
  { path: '/b/Arena', last_accessed: hoursAgo(2), chat_count: 7 },
  { path: '/b/KagitKart', last_accessed: twoDaysAgo(), chat_count: 0 },
  { path: '/b/Gone', last_accessed: hoursAgo(300), chat_count: 3 },
  { path: '/b/nomap', last_accessed: hoursAgo(400), chat_count: 1 },
]
const INFO: Record<string, { status: string; unityVersion: string | null }> = {
  '/h/Arena': { status: 'ok', unityVersion: '6000.2.4f1' },
  '/h/KagitKart': { status: 'untrusted', unityVersion: null },
  '/h/Gone': { status: 'missing', unityVersion: null },
}

const ipcDefault = async (channel: string, arg: any) => {
  if (channel === 'host-workspace-path') return arg === '/b/nomap' ? '' : arg.replace('/b/', '/h/')
  if (channel === 'workspace-info') return arg.map((path: string) => ({ path, ...INFO[path] }))
  if (channel === 'open-unity-hub') return { opened: true }
  return null
}

const props = () => ({
  api: 'http://backend', user: { id: 7, name: 'Burak', sessionToken: 'tok' }, userName: 'Burak',
  onOpenFolder: vi.fn(async (_p?: string) => null as string | null), onSelectWorkspace: vi.fn(async () => {}),
  onLogout: vi.fn(), showToast: vi.fn(),
})

const view = (p = props(), lang: Lang = 'en') => {
  const value = { lang, setLang: () => {}, t: (k: any, v?: any) => ceviriUygula(lang, k, v) }
  const utils = render(<LangContext.Provider value={value}><WorkspaceScreen {...p} /></LangContext.Provider>)
  return { ...utils, p }
}
const cards = () => screen.queryAllByTestId('welcome-card')
const cardNamed = (name: string) => cards().find(c => c.querySelector('.wl-proj-name')?.textContent === name)!

beforeEach(() => {
  mocks.invoke.mockReset().mockImplementation(ipcDefault)
  mocks.registerDroppedFolder.mockReset()
  vi.mocked(axios.get).mockReset().mockResolvedValue({ data: { workspaces: ROWS } })
  vi.mocked(axios.post).mockReset().mockResolvedValue({ data: { removed: true } })
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('welcome screen: recent projects', () => {
  it('lists the API rows with folder facts from workspace-info', async () => {
    view()
    await waitFor(() => expect(cards()).toHaveLength(4))
    expect(axios.get).toHaveBeenCalledWith('http://backend/recent-workspaces/7',
      expect.objectContaining({ params: { limit: 12 }, headers: { 'X-Session-Token': 'tok' } }))
    // Only mappable host paths are asked about, in one call.
    expect(mocks.invoke).toHaveBeenCalledWith('workspace-info', ['/h/Arena', '/h/KagitKart', '/h/Gone'])
    expect(screen.getByTestId('welcome-count').textContent).toBe('4')

    const arena = cardNamed('Arena')
    expect(arena.className).toContain('is-last')
    expect(arena.dataset.slot).toBe('01')
    expect(arena.querySelector('.wl-proj-tag')?.textContent).toContain(en['welcome.lastTag'])
    expect(arena.querySelector('.wl-proj-path')?.textContent).toBe('/h/Arena')
    expect(arena.querySelector('.wl-ver')?.textContent).toBe('Unity 6000.2.4f1')
    expect(arena.querySelector('.wl-chats')?.textContent).toBe('7 chats')
    expect(arena.querySelector('.wl-when')?.textContent).toBe('2 hours ago')

    const kart = cardNamed('KagitKart')
    expect(kart.className).not.toContain('is-last')
    expect(kart.querySelector('.wl-proj-tag')).toBeNull()
    expect(kart.querySelector('.wl-ver-none')?.textContent).toBe(en['welcome.versionUnknown'])
    // Chat counts start with new chats: 0 hides the line instead of reading "0 chats".
    expect(kart.querySelector('.wl-chats')).toBeNull()

    for (const name of ['Gone', 'nomap']) {
      const c = cardNamed(name)
      expect(c.className).toContain('is-missing')
      expect(c.querySelector('.wl-proj-open')).toBeNull()
      expect(c.querySelector('.wl-gone')?.textContent).toBe(en['welcome.gone'])
    }
    expect(cardNamed('nomap').querySelector('.wl-proj-path')?.textContent).toBe('/b/nomap')
    expect(screen.getByTestId('welcome').hasAttribute('data-wl-first')).toBe(false)
  })

  it('opens an ok project directly and an untrusted one through the preselected dialog', async () => {
    const { p } = view()
    await waitFor(() => expect(cards()).toHaveLength(4))
    fireEvent.click(screen.getByRole('button', { name: 'Open Arena' }))
    await waitFor(() => expect(p.onSelectWorkspace).toHaveBeenCalledWith('/h/Arena'))
    expect(p.onOpenFolder).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Open KagitKart' }))
    await waitFor(() => expect(p.onOpenFolder).toHaveBeenCalledWith('/h/KagitKart'))
    expect(p.onSelectWorkspace).toHaveBeenCalledTimes(1)
  })

  it('missing: Locate opens the dialog on the parent folder and then drops the stale backend entry', async () => {
    const p = props()
    p.onOpenFolder.mockResolvedValue('/h/Moved/Gone')
    view(p)
    await waitFor(() => expect(cards()).toHaveLength(4))
    fireEvent.click(within(cardNamed('Gone')).getByRole('button', { name: en['welcome.locate'] }))
    await waitFor(() => expect(axios.post).toHaveBeenCalledWith('http://backend/remove-workspace',
      { user_id: 7, path: '/b/Gone' }, { headers: { 'X-Session-Token': 'tok' } }))
    expect(p.onOpenFolder).toHaveBeenCalledWith('/h')
  })

  it.each(['rejected', 'non-array'])('uses the dialog when workspace-info is %s', async failure => {
    mocks.invoke.mockImplementation(async (channel: string, arg: any) => {
      if (channel === 'workspace-info') {
        if (failure === 'rejected') throw new Error('inspection failed')
        return { error: 'inspection failed' }
      }
      return ipcDefault(channel, arg)
    })
    const { p } = view()
    await waitFor(() => expect(cards()).toHaveLength(4))
    for (const name of ['Arena', 'KagitKart', 'Gone']) {
      fireEvent.click(screen.getByRole('button', { name: `Open ${name}` }))
      await waitFor(() => expect(p.onOpenFolder).toHaveBeenCalledWith(`/h/${name}`))
    }
    expect(p.onSelectWorkspace).not.toHaveBeenCalled()
    expect(cardNamed('nomap').className).toContain('is-missing')
  })

  it('missing: a cancelled Locate removes nothing', async () => {
    const { p } = view()
    await waitFor(() => expect(cards()).toHaveLength(4))
    fireEvent.click(within(cardNamed('Gone')).getByRole('button', { name: en['welcome.locate'] }))
    await waitFor(() => expect(p.onOpenFolder).toHaveBeenCalled())
    await act(async () => {})
    expect(axios.post).not.toHaveBeenCalled()
  })

  it('remove posts the BACKEND path and reloads the list', async () => {
    view()
    await waitFor(() => expect(cards()).toHaveLength(4))
    vi.mocked(axios.get).mockResolvedValue({ data: { workspaces: ROWS.filter(r => r.path !== '/b/nomap') } })
    fireEvent.click(within(cardNamed('nomap')).getByRole('button', { name: en['welcome.remove'] }))
    await waitFor(() => expect(cards()).toHaveLength(3))
    expect(axios.post).toHaveBeenCalledWith('http://backend/remove-workspace', { user_id: 7, path: '/b/nomap' }, expect.anything())
    expect(axios.get).toHaveBeenCalledTimes(2)
  })

  it('the more menu removes an ok card; a failed remove toasts', async () => {
    const { p } = view()
    await waitFor(() => expect(cards()).toHaveLength(4))
    const more = screen.getByRole('button', { name: 'Options for Arena' })
    expect(more.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(more)
    expect(more.getAttribute('aria-expanded')).toBe('true')
    vi.mocked(axios.post).mockRejectedValueOnce(new Error('500'))
    fireEvent.click(screen.getByRole('menuitem', { name: en['welcome.remove'] }))
    await waitFor(() => expect(p.showToast).toHaveBeenCalledWith(en['welcome.removeFailed'], 'error'))
    expect(axios.post).toHaveBeenCalledWith('http://backend/remove-workspace', { user_id: 7, path: '/b/Arena' }, expect.anything())
    expect(screen.queryByRole('menu')).toBeNull()
  })

  it('search filters by name and path, case-insensitively; the count stays the real total', async () => {
    view()
    await waitFor(() => expect(cards()).toHaveLength(4))
    const box = screen.getByRole('textbox', { name: en['welcome.search'] })
    fireEvent.change(box, { target: { value: 'ARENA' } })
    expect(cards().map(c => c.querySelector('.wl-proj-name')?.textContent)).toEqual(['Arena'])
    fireEvent.change(box, { target: { value: '/b/no' } })
    expect(cards().map(c => c.querySelector('.wl-proj-name')?.textContent)).toEqual(['nomap'])
    fireEvent.change(box, { target: { value: 'zzz' } })
    expect(cards()).toHaveLength(0)
    expect(screen.getByText(en['welcome.searchNone'])).toBeTruthy()
    expect(screen.getByTestId('welcome-count').textContent).toBe('4')
  })
})

describe('welcome screen: first launch and load failure', () => {
  it('an empty list shows the first-launch variant', async () => {
    vi.mocked(axios.get).mockResolvedValue({ data: { workspaces: [] } })
    view()
    await waitFor(() => expect(screen.getByTestId('welcome').hasAttribute('data-wl-first')).toBe(true))
    expect(screen.getByText(en['welcome.subFirst'])).toBeTruthy()
    expect(screen.getByText(en['welcome.step1Title'])).toBeTruthy()
    expect(screen.getByTestId('welcome-drop-lg')).toBeTruthy()
    expect(mocks.invoke).not.toHaveBeenCalledWith('workspace-info', expect.anything())
  })

  it('a failed load is a quiet retry line, not the first-launch variant', async () => {
    vi.mocked(axios.get).mockRejectedValueOnce(new Error('offline'))
    view()
    await screen.findByText(en['welcome.loadFailed'])
    expect(screen.getByTestId('welcome').hasAttribute('data-wl-first')).toBe(false)
    expect(screen.getByText(en['welcome.sub'])).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: en['welcome.retry'] }))
    await waitFor(() => expect(cards()).toHaveLength(4))
    expect(screen.queryByText(en['welcome.loadFailed'])).toBeNull()
  })
})

describe('welcome screen: drop, open and new project', () => {
  const drop = (zone: string, files: any[]) => fireEvent.drop(screen.getByTestId(`welcome-drop-${zone}`), { dataTransfer: { files } })

  it('a drop hands the File itself to registerDroppedFolder and opens the returned path', async () => {
    const { p } = view()
    await waitFor(() => expect(cards()).toHaveLength(4))
    mocks.invoke.mockClear()
    const file = new File([''], 'Arena')
    mocks.registerDroppedFolder.mockResolvedValue({ path: '/h/Dropped' })
    const zone = screen.getByTestId('welcome-drop-list')
    fireEvent.dragOver(zone, { dataTransfer: { files: [file] } })
    expect(zone.className).toContain('is-drag')
    drop('list', [file])
    await waitFor(() => expect(p.onSelectWorkspace).toHaveBeenCalledWith('/h/Dropped'))
    expect(mocks.registerDroppedFolder).toHaveBeenCalledWith(file)
    expect(zone.className).not.toContain('is-drag')
    // No path string travels over invoke for a drop.
    expect(mocks.invoke).not.toHaveBeenCalled()
  })

  it.each([
    ['not-a-folder', 'welcome.dropNotFolder'],
    ['not-a-unity-project', 'welcome.dropNotUnity'],
  ])('a drop refused as %s toasts its own message', async (error, key) => {
    const { p } = view()
    mocks.registerDroppedFolder.mockResolvedValue({ error })
    drop('list', [new File([''], 'x')])
    await waitFor(() => expect(p.showToast).toHaveBeenCalledWith((en as any)[key], 'warning'))
    expect(p.onSelectWorkspace).not.toHaveBeenCalled()
  })

  it('a bridge failure on drop toasts the generic error; the large first-launch zone drops too', async () => {
    vi.mocked(axios.get).mockResolvedValue({ data: { workspaces: [] } })
    const { p } = view()
    await screen.findByTestId('welcome-drop-lg')
    mocks.registerDroppedFolder.mockRejectedValue(new Error('boom'))
    drop('lg', [new File([''], 'x')])
    await waitFor(() => expect(p.showToast).toHaveBeenCalledWith(en['welcome.dropFailed'], 'error'))
    mocks.registerDroppedFolder.mockResolvedValue({ path: '/h/First' })
    drop('lg', [new File([''], 'y')])
    await waitFor(() => expect(p.onSelectWorkspace).toHaveBeenCalledWith('/h/First'))
  })

  it('a click on a drop zone, the open button and Cmd/Ctrl+O open the folder dialog', async () => {
    const { p } = view()
    fireEvent.click(screen.getByTestId('welcome-drop-list'))
    fireEvent.click(screen.getByRole('button', { name: new RegExp(en['welcome.open']) }))
    fireEvent.keyDown(window, { key: 'o', ctrlKey: true, metaKey: true })
    expect(p.onOpenFolder).toHaveBeenCalledTimes(3)
  })

  it('new project opens Unity Hub; when it does not open, it toasts and opens the download page', async () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null)
    const { p } = view()
    const btn = screen.getByRole('button', { name: new RegExp(en['welcome.new']) })
    expect(btn.getAttribute('aria-describedby')).toBe('wl-new-hint')
    fireEvent.click(btn)
    await waitFor(() => expect(mocks.invoke).toHaveBeenCalledWith('open-unity-hub'))
    await act(async () => {})
    expect(open).not.toHaveBeenCalled()

    mocks.invoke.mockImplementation(async (c: string, a: any) => (c === 'open-unity-hub' ? { opened: false } : ipcDefault(c, a)))
    fireEvent.click(btn)
    await waitFor(() => expect(open).toHaveBeenCalledWith(UNITY_DOWNLOAD_URL, '_blank'))
    expect(p.showToast).toHaveBeenCalledWith(en['welcome.hubFailed'], 'info')
  })
})

describe('welcome screen: language and name', () => {
  it('renders in English', async () => {
    view()
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Welcome, Burak')
    expect(screen.getByRole('button', { name: en['welcome.logout'] })).toBeTruthy()
    await waitFor(() => expect(cardNamed('KagitKart').querySelector('.wl-when')?.textContent).toBe('2 days ago'))
  })

  it('renders in Turkish', async () => {
    view(props(), 'tr')
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Hoş geldin, Burak')
    expect(screen.getByRole('button', { name: tr['welcome.logout'] })).toBeTruthy()
    await waitFor(() => expect(cardNamed('Arena').querySelector('.wl-chats')?.textContent).toBe('7 sohbet'))
    expect(cardNamed('KagitKart').querySelector('.wl-when')?.textContent).toBe('2 gün önce')
    expect(screen.getByRole('button', { name: 'Arena projesini aç' })).toBeTruthy()
  })

  it('without a name: no name part, the footer says "Your account"', () => {
    const p = props()
    p.userName = 'local'
    view(p)
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(en['workspace.welcomeNoName'])
    expect(document.querySelector('.wl-name-part')).toBeNull()
    expect(document.querySelector('.wl-me-name')?.textContent).toBe(en['welcome.account'])
  })

  it('older than a week reads as a short date', () => {
    const now = new Date(2026, 9, 2, 12, 0, 0).getTime()
    expect(relativeWhen('2026-10-01 11:00:00', 'en', now)).toBe('yesterday')
    expect(relativeWhen('2026-10-02 11:59:30', 'en', now)).toBe('now')
    expect(relativeWhen('2026-09-14 09:00:00', 'en', now)).toBe('Sep 14')
    expect(relativeWhen('2025-09-14 09:00:00', 'tr', now)).toMatch(/14 Eyl.*2025/)
    expect(relativeWhen('', 'en', now)).toBe('')
  })
})
