import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react'

const mocks = vi.hoisted(() => {
  const invoke = vi.fn()
  ;(globalThis as any).window.ipc = { invoke, registerDroppedFolder: vi.fn(), on: vi.fn(() => () => {}) }
  return { invoke, confirm: vi.fn() }
})
vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), defaults: { headers: { common: {} } } } }))
vi.mock('../renderer/components/ui/ConfirmDialog', () => ({ confirmDialog: mocks.confirm }))

import axios from 'axios'
import { WorkspaceScreen } from '../renderer/components/home/WorkspaceScreen'
import { LangContext, ceviriUygula, translations, type Lang } from '../renderer/lib/i18n'

const en = translations.en
const ROWS = [
  { path: '/b/Arena', last_accessed: '2026-10-30 10:00:00', chat_count: 1 },
  { path: '/b/Gone', last_accessed: '2026-10-01 10:00:00', chat_count: 0 },
  { path: '/b/nomap', last_accessed: '2026-10-01 10:00:00', chat_count: 0 },
]
const INFO: Record<string, string> = { '/h/Arena': 'ok', '/h/Gone': 'missing' }

const ipcDefault = async (channel: string, arg: any) => {
  if (channel === 'host-workspace-path') return arg === '/b/nomap' ? '' : arg.replace('/b/', '/h/')
  if (channel === 'workspace-info') return arg.map((path: string) => ({ path, status: INFO[path] ?? 'ok', unityVersion: null }))
  return null
}

const view = (lang: Lang = 'en') => {
  const p = {
    api: 'http://backend', user: { id: 7, name: 'Burak', sessionToken: 'tok' }, userName: 'Burak',
    onOpenFolder: vi.fn(async () => null as string | null), onSelectWorkspace: vi.fn(async () => {}),
    onLogout: vi.fn(), showToast: vi.fn(),
  }
  const value = { lang, setLang: () => {}, t: (k: any, v?: any) => ceviriUygula(lang, k, v) }
  render(<LangContext.Provider value={value}><WorkspaceScreen {...p} /></LangContext.Provider>)
  return p
}
const card = (name: string) => screen.queryAllByTestId('welcome-card').find(c => c.querySelector('.wl-proj-name')?.textContent === name)!
const openMenu = async (name: string, label = `Options for ${name}`) => {
  await waitFor(() => expect(card(name)).toBeTruthy())
  fireEvent.click(within(card(name)).getByRole('button', { name: label }))
}
const platform = (value: string) => vi.spyOn(window.navigator, 'platform', 'get').mockReturnValue(value)

beforeEach(() => {
  mocks.invoke.mockReset().mockImplementation(ipcDefault)
  mocks.confirm.mockReset().mockResolvedValue(true)
  vi.mocked(axios.get).mockReset().mockResolvedValue({ data: { workspaces: ROWS } })
  vi.mocked(axios.post).mockReset().mockResolvedValue({ data: { removed: true } })
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('welcome screen: delete project', () => {
  it('adds a destructive "Delete project" item after "Remove from list", only for projects whose folder exists', async () => {
    view()
    await openMenu('Arena')
    const items = screen.getAllByRole('menuitem').map(b => b.textContent)
    expect(items.slice(-2)).toEqual([en['welcome.remove'], en['welcome.trash']])
    expect(screen.getByRole('menuitem', { name: en['welcome.trash'] }).className).toContain('wl-menu-danger')
    expect(card('Gone').querySelector('.wl-proj-more')).toBeNull()
    expect(card('nomap').querySelector('.wl-proj-more')).toBeNull()
  })

  it('hides the item once the desktop bridge is gone', async () => {
    view()
    await waitFor(() => expect(card('Arena')).toBeTruthy())
    const ipc = (window as any).ipc
    ;(window as any).ipc = undefined
    try {
      fireEvent.click(within(card('Arena')).getByRole('button', { name: 'Options for Arena' }))
      expect(screen.getByRole('menuitem', { name: en['welcome.remove'] })).toBeTruthy()
      expect(screen.queryByRole('menuitem', { name: en['welcome.trash'] })).toBeNull()
    } finally {
      ;(window as any).ipc = ipc
    }
  })

  it.each([
    ['MacIntel', 'Trash'],
    ['Linux x86_64', 'Trash'],
    ['Win32', 'Recycle Bin'],
  ])('confirms with the name, full path and the bin on %s', async (nav, bin) => {
    platform(nav)
    view()
    await openMenu('Arena')
    fireEvent.click(screen.getByRole('menuitem', { name: en['welcome.trash'] }))
    await waitFor(() => expect(mocks.confirm).toHaveBeenCalledTimes(1))
    const [message, go, cancel] = mocks.confirm.mock.calls[0]
    expect(message).toContain('"Arena"')
    expect(message).toContain('/h/Arena')
    expect(message).toContain(`moved to the ${bin}`)
    expect(message).toContain(`restore it from the ${bin}`)
    expect([go, cancel]).toEqual([`Move to ${bin}`, 'Cancel'])
  })

  it.each([
    ['Win32', "Geri Dönüşüm Kutusu'na taşı"],
    ['MacIntel', "Çöp Kutusu'na taşı"],
  ])('uses the Turkish wording on %s', async (nav, go) => {
    platform(nav)
    view('tr')
    await openMenu('Arena', 'Arena için seçenekler')
    fireEvent.click(screen.getByRole('menuitem', { name: 'Projeyi sil…' }))
    await waitFor(() => expect(mocks.confirm).toHaveBeenCalledTimes(1))
    const [message, goLabel, cancel] = mocks.confirm.mock.calls[0]
    expect(goLabel).toBe(go)
    expect(cancel).toBe('Vazgeç')
    expect(message).toContain('/h/Arena')
    expect(message).toContain(nav === 'Win32' ? 'Geri Dönüşüm Kutusu' : 'Çöp Kutusu')
  })

  it('on success trashes through the channel, removes the entry and toasts', async () => {
    mocks.invoke.mockImplementation(async (c: string, a: any) => c === 'workspace-trash' ? { ok: true } : ipcDefault(c, a))
    const p = view()
    await openMenu('Arena')
    fireEvent.click(screen.getByRole('menuitem', { name: en['welcome.trash'] }))
    await waitFor(() => expect(axios.post).toHaveBeenCalledWith('http://backend/remove-workspace',
      { user_id: 7, path: '/b/Arena' }, { headers: { 'X-Session-Token': 'tok' } }))
    expect(mocks.invoke).toHaveBeenCalledWith('workspace-trash', '/h/Arena')
    expect(p.showToast).toHaveBeenCalledWith(expect.stringContaining('"Arena" was moved to the'), 'success')
  })

  it('does nothing when the confirmation is declined', async () => {
    mocks.confirm.mockResolvedValue(false)
    const p = view()
    await openMenu('Arena')
    fireEvent.click(screen.getByRole('menuitem', { name: en['welcome.trash'] }))
    await waitFor(() => expect(mocks.confirm).toHaveBeenCalled())
    expect(mocks.invoke.mock.calls.some(c => c[0] === 'workspace-trash')).toBe(false)
    expect(axios.post).not.toHaveBeenCalled()
    expect(p.showToast).not.toHaveBeenCalled()
  })

  it.each([
    ['not-a-unity-project', 'welcome.trashErr.not-a-unity-project'],
    ['unity-open', 'welcome.trashErr.unity-open'],
    ['open-workspace', 'welcome.trashErr.open-workspace'],
    ['protected', 'welcome.trashErr.protected'],
    ['not-found', 'welcome.trashErr.not-found'],
    ['trash-failed', 'welcome.trashErr.other'],
    ['something-new', 'welcome.trashErr.other'],
  ])('refusal %s shows its reason and keeps the entry', async (code, key) => {
    mocks.invoke.mockImplementation(async (c: string, a: any) => c === 'workspace-trash' ? { error: code } : ipcDefault(c, a))
    const p = view()
    await openMenu('Arena')
    fireEvent.click(screen.getByRole('menuitem', { name: en['welcome.trash'] }))
    await waitFor(() => expect(p.showToast).toHaveBeenCalledTimes(1))
    const text = (en as any)[key].replace(/\{bin\}/g, /Win/.test(navigator.platform) ? 'Recycle Bin' : 'Trash')
    expect(p.showToast).toHaveBeenCalledWith(text, 'error')
    expect(axios.post).not.toHaveBeenCalled()
    expect(card('Arena')).toBeTruthy()
  })

  it('treats a rejected bridge call as a failure, not a success', async () => {
    mocks.invoke.mockImplementation(async (c: string, a: any) => { if (c === 'workspace-trash') throw new Error('boom'); return ipcDefault(c, a) })
    const p = view()
    await openMenu('Arena')
    fireEvent.click(screen.getByRole('menuitem', { name: en['welcome.trash'] }))
    await waitFor(() => expect(p.showToast).toHaveBeenCalledWith(expect.any(String), 'error'))
    expect(axios.post).not.toHaveBeenCalled()
  })
})
