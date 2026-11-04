import React from 'react'
import fs from 'fs'
import path from 'path'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react'

const mocks = vi.hoisted(() => {
  const invoke = vi.fn()
  ;(globalThis as any).window.ipc = { invoke, registerDroppedFolder: vi.fn(), on: vi.fn(() => () => {}) }
  return { invoke }
})
vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), defaults: { headers: { common: {} } } } }))

import axios from 'axios'
import { WorkspaceScreen } from '../renderer/components/home/WorkspaceScreen'
import { loadCatalog, planInstall } from '../main/helpers/unity-install'
import { resetHubStore, resetInstallStore } from '../renderer/lib/hub'
import { LangContext, ceviriUygula, translations } from '../renderer/lib/i18n'

const en = translations.en

// --- main process: loadCatalog with a requested version ---

const FIX = path.join(__dirname, 'fixtures/unity-install/release-api-latest-lts-windows.json')
const OLD = '6000.0.40f1'

function catalogDeps(urls: string[]) {
  const latest = JSON.parse(fs.readFileSync(FIX, 'utf-8'))
  const old = JSON.parse(JSON.stringify(latest.results[0]))
  old.version = OLD
  old.shortRevision = 'abc123def456'
  return {
    fetch: async (url: string) => {
      urls.push(url)
      const u = new URL(url)
      const v = u.searchParams.get('version')
      const body = v ? { results: v === OLD ? [old] : [] } : u.searchParams.get('stream') === 'LTS' ? latest : { results: [] }
      return { ok: true, status: 200, json: async () => body }
    },
    spawn: () => { throw new Error('no spawn') }, exists: () => false, readFile: () => { throw new Error('ENOENT') },
    statfs: async () => ({ bavail: 1, bsize: 1 }), platform: 'win32', arch: 'x64',
    env: {}, homedir: 'C:/Users/u', now: () => 0, setInterval: () => 0, clearInterval: () => {}, killTree: () => {},
    editors: () => [], findHub: () => null,
  } as any
}

describe('loadCatalog with a requested version', () => {
  it('fetches a version outside the newest lists by exact lookup and reports it found', async () => {
    const urls: string[] = []
    const deps = catalogDeps(urls)
    const cat: any = await loadCatalog(deps, OLD)
    expect(cat.ok).toBe(true)
    expect(cat.releases.map((r: any) => r.version)).toContain(OLD)
    expect(cat.requestedFound).toBe(true)
    expect(urls.filter(u => new URL(u).searchParams.get('version') === OLD)).toHaveLength(1)
    // lastCatalog now holds it, so the plan step accepts it (a CLI is present, so resolveSelection decides).
    const withCli = { ...deps, env: { LOCALAPPDATA: 'C:/L' }, exists: (f: string) => f.endsWith('unity.exe') }
    const plan: any = await planInstall({ version: OLD, modules: [] }, withCli)
    expect(plan.code).not.toBe('unknownVersion')
    expect(plan.code).not.toBe('cliMissing')
    expect(plan.ok).toBe(true)
    const bogus: any = await planInstall({ version: '6000.0.99f9', modules: [] }, withCli)
    expect(bogus).toEqual({ ok: false, code: 'unknownVersion' })
  })

  it('reports a version Unity does not return as not found, with the lists intact', async () => {
    const urls: string[] = []
    const cat: any = await loadCatalog(catalogDeps(urls), '6000.0.99f9')
    expect(cat.ok).toBe(true)
    expect(cat.requestedFound).toBe(false)
    expect(cat.releases.length).toBeGreaterThan(0)
  })

  it('never puts an invalid version into the URL and leaves requestedFound out', async () => {
    for (const bad of ['6000.0.1f1&limit=999', '6000.0.1', '', 'x'.repeat(50), 42 as any]) {
      const urls: string[] = []
      const cat: any = await loadCatalog(catalogDeps(urls), bad)
      expect(cat.ok).toBe(true)
      expect('requestedFound' in cat).toBe(false)
      expect(urls.every(u => !new URL(u).searchParams.has('version'))).toBe(true)
    }
  })

  it('does not look up a requested version that the lists already hold', async () => {
    const urls: string[] = []
    const first: any = await loadCatalog(catalogDeps([]))
    const known = first.releases[0].version
    const cat: any = await loadCatalog(catalogDeps(urls), known)
    expect(cat.requestedFound).toBe(true)
    expect(urls.some(u => new URL(u).searchParams.has('version'))).toBe(false)
  })
})

// --- renderer: project card menu and the install flow ---

const ROWS = [
  { path: '/b/Old', last_accessed: '2026-11-01 10:00:00', chat_count: 0 },
  { path: '/b/New', last_accessed: '2026-11-01 09:00:00', chat_count: 0 },
]
const INFO: Record<string, { status: string; unityVersion: string }> = {
  '/h/Old': { status: 'ok', unityVersion: OLD },
  '/h/New': { status: 'ok', unityVersion: '6000.6.7f1' },
}
const mod = (id: string) => ({
  id, name: id, category: 'PLATFORM', downloadBytes: 10, installedBytes: 20, preSelected: false, eulas: [], parts: [], installed: false,
})
const release = (version: string, extra: object = {}) => ({
  version, changeset: 'abc', stream: 'LTS', releaseDate: '2026-10-30T16:57:42.531Z', recommended: false, installed: false,
  downloadBytes: 100, installedBytes: 200, modules: [mod('webgl')], ...extra,
})

let editors: unknown
let catalog: (arg: unknown) => unknown
const ipc = async (channel: string, arg: any) => {
  if (channel === 'host-workspace-path') return arg.replace('/b/', '/h/')
  if (channel === 'backend-workspace-path') return arg.replace('/h/', '/b/')
  if (channel === 'workspace-info') return arg.map((p: string) => ({ path: p, ...INFO[p] }))
  if (channel === 'hub-list-editors') return editors
  if (channel === 'hub-list-templates') return { templates: [] }
  if (channel === 'hub-install-catalog') return catalog(arg)
  if (channel === 'hub-install-plan') return { ok: true, installRoot: 'C:\\U', downloadBytes: 1, installedBytes: 2, freeBytes: 1e12, enough: true, eulas: [] }
  return null
}

const view = () => {
  const value = { lang: 'en' as const, setLang: () => {}, t: (k: any, v?: any) => ceviriUygula('en', k, v) }
  return render(
    <LangContext.Provider value={value}>
      <WorkspaceScreen api="http://backend" user={{ id: 7, name: 'Burak', sessionToken: 'tok' } as any} userName="Burak"
        onOpenFolder={vi.fn(async () => null)} onSelectWorkspace={vi.fn()} onLogout={vi.fn()} showToast={vi.fn()} />
    </LangContext.Provider>,
  )
}
const cardNamed = async (name: string) => {
  await waitFor(() => expect(screen.queryAllByTestId('welcome-card')).toHaveLength(2))
  return screen.getAllByTestId('welcome-card').find(c => c.querySelector('.wl-proj-name')?.textContent === name)!
}
const openMenu = (card: HTMLElement) => {
  fireEvent.click(card.querySelector('.wl-proj-more') as HTMLElement)
  return within(card.querySelector('.wl-menu') as HTMLElement)
}

beforeEach(() => {
  resetHubStore()
  resetInstallStore()
  editors = { editors: [{ version: '6000.6.7f1', path: '/U/Unity' }] }
  catalog = () => ({ ok: true, cli: { available: true }, releases: [release(OLD), release('6000.6.7f1', { installed: true })] })
  mocks.invoke.mockReset().mockImplementation(ipc)
  vi.mocked(axios.get).mockReset().mockResolvedValue({ data: { workspaces: ROWS } })
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('project card with an editor that is not installed', () => {
  it('offers Install instead of Launch and marks the version', async () => {
    view()
    const old = await cardNamed('Old')
    await waitFor(() => expect(old.querySelector('.wl-ver-missing')).not.toBeNull())
    expect(old.querySelector('.wl-ver')?.textContent).toContain(en['welcome.versionMissing'])
    const menu = openMenu(old)
    expect(menu.getByRole('menuitem', { name: `Install Unity ${OLD}` })).toBeTruthy()
    expect(menu.queryByRole('menuitem', { name: `Launch in Unity ${OLD}` })).toBeNull()
  })

  it('keeps Launch and no mark for an installed version', async () => {
    view()
    const fresh = await cardNamed('New')
    await waitFor(() => expect(mocks.invoke).toHaveBeenCalledWith('hub-list-editors'))
    await waitFor(() => expect(fresh.querySelector('.wl-ver')).not.toBeNull())
    expect(fresh.querySelector('.wl-ver-missing')).toBeNull()
    const menu = openMenu(fresh)
    expect(menu.getByRole('menuitem', { name: 'Launch in Unity 6000.6.7f1' })).toBeTruthy()
    expect(menu.queryByRole('menuitem', { name: /Install Unity/ })).toBeNull()
  })

  it('keeps Launch when the editors could not be listed', async () => {
    editors = null
    view()
    const old = await cardNamed('Old')
    await waitFor(() => expect(mocks.invoke).toHaveBeenCalledWith('hub-list-editors'))
    expect(old.querySelector('.wl-ver-missing')).toBeNull()
    expect(openMenu(old).getByRole('menuitem', { name: `Launch in Unity ${OLD}` })).toBeTruthy()
  })

  it('opens the install flow with that version preselected and asks for it', async () => {
    view()
    const old = await cardNamed('Old')
    await waitFor(() => expect(old.querySelector('.wl-ver-missing')).not.toBeNull())
    fireEvent.click(openMenu(old).getByRole('menuitem', { name: `Install Unity ${OLD}` }))
    await screen.findByTestId('hub-install-modules')
    expect(screen.getByRole('heading', { level: 4 }).textContent).toContain(OLD)
    expect(mocks.invoke).toHaveBeenCalledWith('hub-install-catalog', OLD)
    // Nothing is auto-accepted or started: the flow waits on the modules step.
    expect(mocks.invoke.mock.calls.some(c => c[0] === 'hub-install-start')).toBe(false)
  })

  it('says so plainly and links to the archive when Unity does not return the version', async () => {
    catalog = () => ({ ok: true, cli: { available: true }, requestedFound: false, releases: [release('6000.6.7f1', { recommended: true })] })
    const open = vi.spyOn(window, 'open').mockImplementation(() => null)
    view()
    const old = await cardNamed('Old')
    await waitFor(() => expect(old.querySelector('.wl-ver-missing')).not.toBeNull())
    fireEvent.click(openMenu(old).getByRole('menuitem', { name: `Install Unity ${OLD}` }))
    const note = await screen.findByTestId('hub-install-want-missing')
    expect(note.textContent).toContain(ceviriUygula('en', 'hub.install.versionMissing', { version: OLD }))
    fireEvent.click(screen.getByTestId('hub-install-archive'))
    expect(open).toHaveBeenCalledWith('https://unity.com/releases/editor/archive', '_blank')
  })
})

describe('editors list re-read and the card version', () => {
  const listCalls = () => mocks.invoke.mock.calls.filter(c => c[0] === 'hub-list-editors').length
  const nowAt = (ms: number) => vi.spyOn(Date, 'now').mockReturnValue(ms)

  it('shows Launch after the editor was installed elsewhere and the window regains focus', async () => {
    const t0 = Date.now()
    view()
    const old = await cardNamed('Old')
    await waitFor(() => expect(old.querySelector('.wl-ver-missing')).not.toBeNull())
    editors = { editors: [{ version: '6000.6.7f1', path: '/U/Unity' }, { version: OLD, path: '/U/Old' }] }
    nowAt(t0 + 6000)
    fireEvent.focus(window)
    await waitFor(() => expect(old.querySelector('.wl-ver-missing')).toBeNull())
    expect(openMenu(old).getByRole('menuitem', { name: `Launch in Unity ${OLD}` })).toBeTruthy()
  })

  it('throttles focus re-reads to one per 5 seconds', async () => {
    const t0 = Date.now()
    view()
    await cardNamed('Old')
    await waitFor(() => expect(listCalls()).toBe(1))
    nowAt(t0 + 1000)
    fireEvent.focus(window)
    fireEvent(document, new Event('visibilitychange'))
    expect(listCalls()).toBe(1)
    nowAt(t0 + 6000)
    fireEvent.focus(window)
    await waitFor(() => expect(listCalls()).toBe(2))
    nowAt(t0 + 7000)
    fireEvent.focus(window)
    expect(listCalls()).toBe(2)
  })

  it('re-reads the editors when the card menu opens', async () => {
    view()
    const old = await cardNamed('Old')
    await waitFor(() => expect(old.querySelector('.wl-ver-missing')).not.toBeNull())
    const before = listCalls()
    editors = { editors: [{ version: '6000.6.7f1', path: '/U/Unity' }, { version: OLD, path: '/U/Old' }] }
    fireEvent.click(old.querySelector('.wl-proj-more') as HTMLElement)
    await waitFor(() => expect(listCalls()).toBe(before + 1))
    await waitFor(() => expect(within(old.querySelector('.wl-menu') as HTMLElement).getByRole('menuitem', { name: `Launch in Unity ${OLD}` })).toBeTruthy())
  })

  it('applies the card version once: the user pick survives a tab switch', async () => {
    const OTHER = '6000.2.4f1'
    catalog = () => ({ ok: true, cli: { available: true }, releases: [release(OLD), release('6000.6.7f1', { recommended: true }), release(OTHER)] })
    view()
    const old = await cardNamed('Old')
    await waitFor(() => expect(old.querySelector('.wl-ver-missing')).not.toBeNull())
    fireEvent.click(openMenu(old).getByRole('menuitem', { name: `Install Unity ${OLD}` }))
    await screen.findByTestId('hub-install-modules')
    expect(screen.getByRole('heading', { level: 4 }).textContent).toContain(OLD)
    fireEvent.click(screen.getByRole('button', { name: ceviriUygula('en', 'hub.install.backStep') }))
    fireEvent.click(await screen.findByTestId(`hub-install-pick-${OTHER}`))
    await screen.findByTestId('hub-install-modules')
    fireEvent.click(screen.getByTestId('hub-tab-projects'))
    fireEvent.click(screen.getByTestId('hub-tab-editors'))
    await waitFor(() => expect(screen.queryByTestId('hub-install-modules') ?? screen.queryByTestId('hub-install-recommended')).toBeTruthy())
    // Back at the version step (the list names every version), or still on the user's pick: never on the card's version.
    if (screen.queryByTestId('hub-install-modules')) {
      const flow = screen.getByTestId('hub-install').textContent || ''
      expect(flow).not.toContain(OLD)
      expect(flow).toContain(OTHER)
    }
  })

  it('shows only the not-listed message when the release list is empty', async () => {
    catalog = () => ({ ok: true, cli: { available: true }, requestedFound: false, releases: [] })
    view()
    const old = await cardNamed('Old')
    await waitFor(() => expect(old.querySelector('.wl-ver-missing')).not.toBeNull())
    fireEvent.click(openMenu(old).getByRole('menuitem', { name: `Install Unity ${OLD}` }))
    const note = await screen.findByTestId('hub-install-want-missing')
    expect(note.textContent).toContain(ceviriUygula('en', 'hub.install.versionMissing', { version: OLD }))
    expect(screen.queryByText(ceviriUygula('en', 'hub.install.noReleases'))).toBeNull()
  })
})
