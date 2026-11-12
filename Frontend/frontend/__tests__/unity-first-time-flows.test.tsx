/**
 * A first-time user without Unity, or with Unity still loading (Mac and Windows, 12 Nov 2026):
 * - Launch with the editor missing: one message, not beside "starting", readable, with the install action.
 * - The install flow offers the versions the user's projects use, marked as such.
 * - The app version line never sits on top of the hub's scrolling lists.
 * - While the project's editor is still opening, the switch says so instead of "project not open".
 */
import React from 'react'
import fs from 'fs'
import path from 'path'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup, waitFor, act, renderHook, within } from '@testing-library/react'

const mocks = vi.hoisted(() => {
  const invoke = vi.fn()
  ;(globalThis as any).window.ipc = { invoke, registerDroppedFolder: vi.fn(), on: vi.fn(() => () => {}) }
  return { invoke }
})
vi.mock('axios', () => {
  const get = vi.fn(); const post = vi.fn(); const put = vi.fn()
  return { default: { get, post, put, defaults: { headers: { common: {} } } }, get, post, put }
})

import axios from 'axios'
import { ToastContainer, useToast } from '../renderer/components/ui/Toast'
import { WorkspaceScreen } from '../renderer/components/home/WorkspaceScreen'
import { UnityMcpToggle } from '../renderer/components/home/UnityMcpToggle'
import { useAIConfig } from '../renderer/hooks/home/useAIConfig'
import * as hub from '../renderer/lib/hub'
import { loadCatalog } from '../main/helpers/unity-install'
import { LangContext, aktifDilAyarla, ceviriUygula, translations } from '../renderer/lib/i18n'

const en = translations.en
const tEn = (k: any, v?: any) => ceviriUygula('en', k, v)
const MISSING = '6000.4.4f1'

let launchAnswer: any
let editors: any
let catalog: (arg: unknown) => unknown
const ROWS = [{ path: '/b/Deneme', last_accessed: '2026-11-12 10:00:00', chat_count: 0 }]
const ipc = async (channel: string, arg: any) => {
  if (channel === 'host-workspace-path') return arg.replace('/b/', '/h/')
  if (channel === 'backend-workspace-path') return arg.replace('/h/', '/b/')
  if (channel === 'workspace-info') return arg.map((p: string) => ({ path: p, status: 'ok', unityVersion: MISSING }))
  if (channel === 'hub-list-editors') return editors
  if (channel === 'hub-list-templates') return { templates: [] }
  if (channel === 'hub-open-project') return launchAnswer
  if (channel === 'hub-install-catalog') return catalog(arg)
  if (channel === 'hub-install-plan') return { ok: true, installRoot: 'C:\\U', downloadBytes: 1, installedBytes: 2, freeBytes: 1e12, enough: true, eulas: [] }
  return null
}
const mod = (id: string) => ({ id, name: id, category: 'PLATFORM', downloadBytes: 10, installedBytes: 20, preSelected: false, eulas: [], parts: [], installed: false })
const release = (version: string, extra: object = {}) => ({
  version, changeset: 'abc', stream: 'LTS', releaseDate: '2026-10-30T16:57:42.531Z', recommended: false, installed: false,
  downloadBytes: 100, installedBytes: 200, modules: [mod('webgl')], ...extra,
})

beforeEach(() => {
  hub.resetHubStore()
  hub.resetInstallStore()
  hub.takeInstallRequest?.()
  launchAnswer = { opened: false, error: `Unity ${MISSING} is not installed.`, code: 'editorMissing', params: { version: MISSING } }
  editors = { editors: [] }
  catalog = () => ({ ok: true, cli: { available: true }, releases: [release('6000.6.7f1', { recommended: true }), release(MISSING)] })
  mocks.invoke.mockReset().mockImplementation(ipc)
  vi.mocked(axios.get).mockReset().mockResolvedValue({ data: { workspaces: ROWS } })
  vi.mocked(axios.post).mockReset()
})
afterEach(() => { cleanup(); vi.useRealTimers(); aktifDilAyarla(null) })

// --- 1. Launch with the editor missing ---

/** The real toast stack with a Launch button wired the way the top bar wires it. */
const TopBar = ({ onInstall }: { onInstall: (v: string) => void }) => {
  const { toasts, showToast, dismissToast } = useToast()
  return (
    <LangContext.Provider value={{ lang: 'en', setLang: () => {}, t: tEn }}>
      <button type="button" onClick={() => void hub.launchWithToast('/h/Deneme', undefined, showToast, tEn, onInstall)}>launch</button>
      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </LangContext.Provider>
  )
}

describe('launch with the editor missing', () => {
  it('leaves exactly one message, the reason with the install action, not beside "starting"', async () => {
    const onInstall = vi.fn()
    const { container } = render(<TopBar onInstall={onInstall} />)
    fireEvent.click(screen.getByText('launch'))
    const reason = en['hub.launchFailed'].replace('{error}', en['hub.error.editorMissing'].replace('{version}', MISSING))
    await waitFor(() => expect(container.querySelector('.gm-toast[data-status="error"]')).not.toBeNull())
    const shown = [...container.querySelectorAll('.gm-toast')]
    expect(shown).toHaveLength(1)
    expect(shown[0].textContent).toContain(reason)
    expect(screen.queryByText(en['hub.launching'])).toBeNull()
    // Long enough to read: well past the 4 s default.
    expect(parseInt((shown[0] as HTMLElement).style.getPropertyValue('--toast-life'), 10)).toBeGreaterThanOrEqual(15000)
    fireEvent.click(within(shown[0] as HTMLElement).getByRole('button', { name: `Install Unity ${MISSING}` }))
    expect(onInstall).toHaveBeenCalledWith(MISSING)
    expect(container.querySelectorAll('.gm-toast')).toHaveLength(0)
  })

  it('a successful launch keeps "starting" then "launched"', async () => {
    launchAnswer = { opened: true }
    const showToast = vi.fn()
    await hub.launchWithToast('/h/Deneme', undefined, showToast, tEn, vi.fn())
    expect(showToast.mock.calls).toEqual([[en['hub.launching'], 'info'], [en['hub.launched'], 'success']])
  })

  it('the top bar request opens the install flow at that version once the project screen mounts', async () => {
    hub.requestInstallVersion(MISSING)
    render(
      <LangContext.Provider value={{ lang: 'en', setLang: () => {}, t: tEn }}>
        <WorkspaceScreen api="http://backend" user={{ id: 7, name: 'B', sessionToken: 'tok' } as any} userName="B"
          onOpenFolder={vi.fn(async () => null)} onSelectWorkspace={vi.fn()} onLogout={vi.fn()} showToast={vi.fn()} />
      </LangContext.Provider>,
    )
    await screen.findByTestId('hub-install-modules')
    expect(screen.getByRole('heading', { level: 4 }).textContent).toContain(MISSING)
    expect(hub.takeInstallRequest()).toBe('')
  })
})

// --- 2. The install flow offers the projects' versions ---

const hubScreen = () => render(
  <LangContext.Provider value={{ lang: 'en', setLang: () => {}, t: tEn }}>
    <WorkspaceScreen api="http://backend" user={{ id: 7, name: 'B', sessionToken: 'tok' } as any} userName="B"
      onOpenFolder={vi.fn(async () => null)} onSelectWorkspace={vi.fn()} onLogout={vi.fn()} showToast={vi.fn()} />
  </LangContext.Provider>,
)

describe('install flow and the versions the projects use', () => {
  it('asks main for them and offers them in the open, marked with the project', async () => {
    editors = { editors: [{ version: '6000.6.7f1', path: '/U/Unity' }] }
    hubScreen()
    await waitFor(() => expect(screen.queryAllByTestId('welcome-card')).toHaveLength(1))
    await waitFor(() => expect(screen.getAllByTestId('welcome-card')[0].querySelector('.wl-ver-missing')).not.toBeNull())
    fireEvent.click(screen.getByTestId('hub-tab-editors'))
    fireEvent.click(await screen.findByTestId('hub-install-entry'))
    const group = await screen.findByTestId('hub-install-for-projects')
    expect(mocks.invoke).toHaveBeenCalledWith('hub-install-catalog', { requested: undefined, projects: [MISSING] })
    expect(group.textContent).toContain(MISSING)
    expect(within(group).getByTestId(`hub-install-used-${MISSING}`).textContent).toBe('Used by Deneme')
    fireEvent.click(within(group).getByTestId(`hub-install-pick-${MISSING}`))
    await screen.findByTestId('hub-install-modules')
    expect(screen.getByRole('heading', { level: 4 }).textContent).toContain(MISSING)
  })

  it('main looks up a project version that is outside the newest lists', async () => {
    const urls: string[] = []
    const fix = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/unity-install/release-api-latest-lts-windows.json'), 'utf-8'))
    const old = { ...JSON.parse(JSON.stringify(fix.results[0])), version: MISSING, shortRevision: 'abc123def456' }
    const deps: any = {
      fetch: async (url: string) => {
        urls.push(url)
        const v = new URL(url).searchParams.get('version')
        return { ok: true, status: 200, json: async () => (v ? { results: v === MISSING ? [old] : [] } : new URL(url).searchParams.get('stream') === 'LTS' ? fix : { results: [] }) }
      },
      spawn: () => { throw new Error('no spawn') }, exists: () => false, readFile: () => { throw new Error('ENOENT') },
      statfs: async () => ({ bavail: 1, bsize: 1 }), platform: 'win32', arch: 'x64', env: {}, homedir: 'C:/Users/u',
      now: () => 0, setInterval: () => 0, clearInterval: () => {}, killTree: () => {}, editors: () => [], findHub: () => null,
    }
    const cat: any = await loadCatalog(deps, undefined, [MISSING, 'not a version&x=1'])
    expect(cat.releases.map((r: any) => r.version)).toContain(MISSING)
    expect(urls.filter(u => new URL(u).searchParams.has('version')).map(u => new URL(u).searchParams.get('version'))).toEqual([MISSING])
  })
})

// --- 3. The version line and the hub's lists ---

describe('app version line', () => {
  it('the hub grows with its content instead of shrinking under the version line', () => {
    const css = fs.readFileSync(path.join(__dirname, '../renderer/styles/gm/hub.css'), 'utf-8')
    const rule = /(?:^|\n)\.hub \{([^}]*)\}/.exec(css)?.[1] ?? ''
    // .wl-main scrolls; a flex item that may shrink (flex: 1 / min-height: 0) overflows onto the line after it.
    expect(rule).toMatch(/flex:\s*1 0 auto/)
    expect(rule).not.toMatch(/min-height:\s*0/)
  })
})

// --- 4. The project's editor is still opening ---

const toggleBase = { toggling: false, reason: null, error: null, onToggle: vi.fn() }
const inEn = (ui: React.ReactElement) => <LangContext.Provider value={{ lang: 'en', setLang: vi.fn(), t: tEn }}>{ui}</LangContext.Provider>

describe('switch while the editor is opening', () => {
  it('says Unity is still opening the project, with the time, and offers no second launch', () => {
    const since = Date.now() - 134_000
    render(inEn(<UnityMcpToggle {...toggleBase} status="off" editorOpen={false} targetProject="Deneme"
      onLaunchUnity={vi.fn()} editorLoadingSince={since} />))
    expect(screen.getByTestId('unity-loading').textContent).toBe('Unity is still opening Deneme · 2:14')
    expect(screen.queryByTestId('unity-launch')).toBeNull()
  })

  it('keeps the launch button and the old hint when nothing is opening', () => {
    render(inEn(<UnityMcpToggle {...toggleBase} status="off" editorOpen={false} targetProject="Deneme" onLaunchUnity={vi.fn()} />))
    expect(screen.queryByTestId('unity-loading')).toBeNull()
    expect(screen.getByTestId('unity-launch')).toBeTruthy()
    expect(screen.getByText(en['unity.hintOff'])).toBeTruthy()
  })

  it('the status poll carries the opening state and a refused toggle says the editor is still opening', async () => {
    aktifDilAyarla('en')
    const showToast = vi.fn()
    const mismatch = "Unity'de bu proje açık değil: Deneme. Unity'de ve Gamachine'de aynı projeyi açıp tekrar dene."
    vi.mocked(axios.get).mockReset().mockResolvedValue({ data: { status: 'off', editor_open: false, editor_loading: true, editor_loading_s: 60 } })
    vi.mocked(axios.post).mockRejectedValue({ response: { status: 409, data: { detail: mismatch } } })
    const { result } = renderHook(() => useAIConfig('http://127.0.0.1:8000', null, showToast, '/h/Deneme'))
    await waitFor(() => expect(result.current.unityEditorLoadingSince).not.toBeNull())
    expect(Math.round((Date.now() - result.current.unityEditorLoadingSince!) / 1000)).toBeGreaterThanOrEqual(60)
    await act(async () => { await result.current.toggleUnityMcp() })
    const said = ceviriUygula('en', 'unity.loadingRefused', { name: 'Deneme' })
    expect(showToast).toHaveBeenCalledWith(said, 'info')
    expect(showToast).not.toHaveBeenCalledWith(mismatch, 'error')
    expect(result.current.unityMcpError).toBe(said)
  })

  it('a genuinely different project keeps the backend reason', async () => {
    const showToast = vi.fn()
    const mismatch = "Unity'de bu proje açık değil: Deneme. Unity'de açık olan: Other."
    vi.mocked(axios.get).mockReset().mockResolvedValue({ data: { status: 'off', editor_open: false } })
    vi.mocked(axios.post).mockRejectedValue({ response: { status: 409, data: { detail: mismatch } } })
    const { result } = renderHook(() => useAIConfig('http://127.0.0.1:8000', null, showToast, '/h/Deneme'))
    await waitFor(() => expect(result.current.unityEditorOpen).toBe(false))
    await act(async () => { await result.current.toggleUnityMcp() })
    expect(showToast).toHaveBeenCalledWith(mismatch, 'error')
    expect(result.current.unityEditorLoadingSince).toBeNull()
  })
})
