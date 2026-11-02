import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act, cleanup, waitFor, within } from '@testing-library/react'

// A fake desktop bridge replaying the hub-install-* contract shapes; progress goes to `listeners`.
const mocks = vi.hoisted(() => {
  const invoke = vi.fn()
  const listeners = new Map<string, (p: unknown) => void>()
  const on = vi.fn((channel: string, cb: (p: unknown) => void) => { listeners.set(channel, cb); return () => listeners.delete(channel) })
  ;(globalThis as any).window.ipc = { invoke, registerDroppedFolder: vi.fn(), on }
  return { invoke, on, listeners }
})
vi.mock('axios', () => ({ default: { get: vi.fn(async () => ({ data: { workspaces: [] } })), post: vi.fn(), put: vi.fn(), defaults: { headers: { common: {} } } } }))

import { WorkspaceScreen, UNITY_DOWNLOAD_URL } from '../renderer/components/home/WorkspaceScreen'
import { AppearanceContext, DEFAULTS, THEMES, type Theme } from '../renderer/lib/appearance'
import {
  formatSize, getInstall, installFailureText, resetHubStore, resetInstallStore, type InstallErrorCode, type InstallModule, type InstallRelease,
} from '../renderer/lib/hub'
import { LangContext, ceviriUygula, translations, type TKey } from '../renderer/lib/i18n'

const en = translations.en
const tEn = (k: TKey, v?: Record<string, string | number>) => ceviriUygula('en', k, v)
const size = (b: number) => formatSize(b, 'en')

// Modules and sizes from the measured Release API answer for 6000.3.27f2 (Windows, 2 Nov 2026).
const VS_EULA = { label: 'Visual Studio 2026 Community License Terms', url: 'https://go.microsoft.com/fwlink/?linkid=2348932' }
const ANDROID_EULA = { label: 'Android SDK and NDK License Terms from Google', url: 'https://dl.google.com/dl/android/repository/repository2-1.xml' }
const mod = (m: Partial<InstallModule> & Pick<InstallModule, 'id' | 'name' | 'downloadBytes'>): InstallModule => ({
  category: 'PLATFORM', installedBytes: m.downloadBytes * 2, preSelected: false, eulas: [], parts: [], installed: false, ...m,
})
const modules = (installed: string[] = []): InstallModule[] => [
  mod({ id: 'visualstudio', name: 'Microsoft Visual Studio Community 2026', category: 'DEV_TOOL', downloadBytes: 1699733504, preSelected: true, eulas: [VS_EULA] }),
  mod({
    id: 'android', name: 'Android Build Support', downloadBytes: 1549421200, eulas: [ANDROID_EULA],
    parts: [{ id: 'android-open-jdk-17.0.18+8', name: 'OpenJDK', downloadBytes: 118110508 }, { id: 'android-sdk-ndk-tools', name: 'Android SDK & NDK Tools', downloadBytes: 1253588105 }],
  }),
  mod({ id: 'ios', name: 'iOS Build Support', downloadBytes: 343770192 }),
  mod({ id: 'webgl', name: 'Web Build Support', downloadBytes: 930219208 }),
].map(m => ({ ...m, installed: installed.includes(m.id) }))
const rel = (r: Partial<InstallRelease> & Pick<InstallRelease, 'version' | 'stream'>): InstallRelease => ({
  changeset: 'abc', releaseDate: '2026-10-30T16:57:42.531Z', recommended: false, installed: false,
  downloadBytes: 4236703648, installedBytes: 8466397074, modules: modules(), ...r,
})
const RELEASES = (): InstallRelease[] => [
  rel({ version: '6000.6.7f1', stream: 'SUPPORTED', recommended: true, releaseDate: '2026-10-28T10:00:00.000Z', downloadBytes: 4400000000, installedBytes: 8900000000 }),
  rel({ version: '6000.3.27f2', stream: 'LTS', changeset: '22188f149405' }),
  rel({ version: '6000.2.4f1', stream: 'SUPPORTED', installed: true, releaseDate: '2026-09-01T10:00:00.000Z', modules: modules(['webgl']) }),
  rel({ version: '2022.3.55f1', stream: 'LTS', installed: true, releaseDate: '2025-01-10T10:00:00.000Z' }),
]

let editors: { version: string; path: string }[]
let catalog: unknown
let enough: boolean
let freeBytes: number | null
let start: (arg: any) => Promise<any>
let cancelAnswer: unknown
const ipc = async (channel: string, arg: any) => {
  if (channel === 'hub-list-editors') return { editors }
  if (channel === 'hub-list-templates') return { templates: [{ id: 'com.unity.template.3d-cross-platform-17.0.6', label: '3d', file: 'a.tgz' }] }
  if (channel === 'workspace-info') return arg.map((path: string) => ({ path, status: 'ok', unityVersion: null }))
  if (channel === 'hub-install-catalog') return catalog
  if (channel === 'hub-install-plan') {
    const r = RELEASES().find(x => x.version === arg.version)!
    const picked = r.modules.filter(m => arg.modules.includes(m.id))
    const own = r.installed ? 0 : r.downloadBytes
    const eulas = picked.flatMap(m => m.eulas)
    return {
      ok: true, installRoot: 'C:\\Program Files\\Unity\\Hub\\Editor', downloadBytes: own + picked.reduce((s, m) => s + m.downloadBytes, 0),
      installedBytes: (r.installed ? 0 : r.installedBytes) + picked.reduce((s, m) => s + m.installedBytes, 0), freeBytes, enough, eulas,
    }
  }
  if (channel === 'hub-install-start') return start(arg)
  if (channel === 'hub-install-cancel') return cancelAnswer
  if (channel === 'open-unity-hub') return { opened: true }
  return null
}

const view = (theme: Theme = 'sade') => {
  const value = { lang: 'en' as const, setLang: () => {}, t: (k: any, v?: any) => ceviriUygula('en', k, v) }
  return render(
    <AppearanceContext.Provider value={{ appearance: { ...DEFAULTS, theme }, setAppearance: () => {} }}>
      <LangContext.Provider value={value}>
        <WorkspaceScreen api="http://backend" user={{ id: 7, name: 'Burak', sessionToken: 'tok' } as any} userName="Burak"
          onOpenFolder={vi.fn(async () => null)} onSelectWorkspace={vi.fn()} onLogout={vi.fn()} showToast={vi.fn()} />
      </LangContext.Provider>
    </AppearanceContext.Provider>,
  )
}
const tab = (v: string) => fireEvent.click(screen.getByTestId(`hub-tab-${v}`))
const calls = (channel: string) => mocks.invoke.mock.calls.filter(c => c[0] === channel)
const progress = (p: Partial<{ phase: string; pct: number | null; message: string; stalled: boolean; retrying: boolean }>) =>
  act(async () => { mocks.listeners.get('hub-install-progress')!({ phase: 'download', pct: null, message: '', stalled: false, retrying: false, elapsedMs: 1000, ...p }) })
const pendingStart = () => {
  let resolve!: (v: any) => void
  start = vi.fn(() => new Promise(r => { resolve = r }))
  return (v: any) => act(async () => { resolve(v) })
}
const checkbox = (id: string) => within(screen.getByTestId(`hub-install-mod-${id}`)).getByRole('checkbox') as HTMLInputElement

/** From the editors tab with Unity installed: open the flow with the entry button and wait for the catalog. */
const openFlow = async () => {
  tab('editors')
  fireEvent.click(await screen.findByTestId('hub-install-entry'))
  return screen.findByTestId('hub-install-recommended')
}
/** Pick the recommended version and wait for the first plan answer. */
const toModules = async () => {
  const card = await openFlow()
  fireEvent.click(within(card).getByRole('button', { name: en['hub.install.pick'] }))
  await waitFor(() => expect(screen.getByTestId('hub-install-total').textContent).toContain(en['hub.install.total'].split(' ')[0]))
}
const nextBtn = () => screen.getByTestId('hub-install-next') as HTMLButtonElement

beforeEach(() => {
  resetHubStore()
  resetInstallStore()
  editors = [{ version: '6000.2.4f1', path: '/U/Unity' }, { version: '2022.3.55f1', path: '/U2/Unity' }]
  catalog = { ok: true, cli: { available: true }, releases: RELEASES() }
  enough = true
  freeBytes = 200 * 1024 ** 3
  start = vi.fn(async () => ({ ok: true, completed: ['6000.6.7f1'] }))
  cancelAnswer = { cancelled: true }
  mocks.invoke.mockReset().mockImplementation(ipc)
  mocks.listeners.clear()
  mocks.on.mockClear()
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('install: step 1, the version', () => {
  it('shows the recommended release as one card with its sizes', async () => {
    view()
    const card = await openFlow()
    expect(card.textContent).toContain('6000.6.7f1')
    expect(card.textContent).toContain(en['hub.install.recommended'])
    expect(card.textContent).toContain(tEn('hub.install.sizes', { download: size(4400000000), disk: size(8900000000) }))
    expect(calls('hub-install-catalog')).toHaveLength(1)
  })

  it('falls back to the newest LTS when the API recommends none', async () => {
    catalog = { ok: true, cli: { available: true }, releases: RELEASES().map(r => ({ ...r, recommended: false })) }
    view()
    const card = await openFlow()
    expect(card.textContent).toContain('6000.3.27f2')
  })

  it('lists the other versions by stream and installed ones cannot be picked', async () => {
    view()
    await openFlow()
    const more = screen.getByTestId('hub-install-more')
    expect(more.textContent).toContain(en['hub.install.streamLts'])
    expect(more.textContent).toContain(en['hub.install.streamSupported'])
    const installed = screen.getByTestId('hub-install-ver-6000.2.4f1')
    expect(installed.textContent).toContain(en['hub.install.installed'])
    expect(within(installed).queryByRole('button')).toBeNull()
    expect(screen.queryByTestId('hub-install-pick-6000.2.4f1')).toBeNull()
    expect(within(screen.getByTestId('hub-install-ver-6000.3.27f2')).getByRole('button', { name: en['hub.install.pick'] })).toBeTruthy()
  })
})

describe('install: step 2, the modules', () => {
  it('pre-ticks what the user had in another editor, else the API preselection', async () => {
    view()
    await toModules()
    expect(checkbox('webgl').checked).toBe(true)
    expect(screen.getByTestId('hub-install-mod-webgl').textContent).toContain(en['hub.install.hadBefore'])
    expect(checkbox('visualstudio').checked).toBe(false)
    cleanup()

    catalog = { ok: true, cli: { available: true }, releases: RELEASES().map(r => ({ ...r, modules: modules() })) }
    view()
    await toModules()
    expect(checkbox('visualstudio').checked).toBe(true)
    expect(checkbox('webgl').checked).toBe(false)
    expect(screen.queryByText(en['hub.install.hadBefore'])).toBeNull()
  })

  it('shows Android as one line with its parts and total, and explains what a module is', async () => {
    view()
    await toModules()
    const android = screen.getByTestId('hub-install-mod-android')
    expect(within(android).getAllByRole('checkbox')).toHaveLength(1)
    expect(android.textContent).toContain(tEn('hub.install.parts', { names: 'OpenJDK, Android SDK & NDK Tools', size: size(1549421200) }))
    expect(screen.getByTestId('hub-install').textContent).toContain(en['hub.install.modulesLede'])
  })

  it('keeps a live total from the plan and blocks continuing when the disk is too small', async () => {
    view()
    await toModules()
    const lastPlan = () => calls('hub-install-plan').at(-1)![1]
    expect(lastPlan()).toEqual({ version: '6000.6.7f1', modules: ['webgl'] })
    const total = () => screen.getByTestId('hub-install-total')
    expect(total().textContent).toContain(tEn('hub.install.total', {
      download: size(4400000000 + 930219208), disk: size(8900000000 + 930219208 * 2), free: size(200 * 1024 ** 3),
    }))
    expect(nextBtn().disabled).toBe(false)

    enough = false
    fireEvent.click(checkbox('ios'))
    await waitFor(() => expect(total().className).toContain('is-short'))
    expect(lastPlan().modules.sort()).toEqual(['ios', 'webgl'])
    expect(total().textContent).toContain(en['hub.install.notEnough'])
    expect(nextBtn().disabled).toBe(true)

    enough = true
    freeBytes = null
    fireEvent.click(checkbox('ios'))
    await waitFor(() => expect(total().textContent).toBe(tEn('hub.install.totalNoFree', { download: size(4400000000 + 930219208), disk: size(8900000000 + 930219208 * 2) })))
    expect(nextBtn().disabled).toBe(false)
  })

  it('formats sizes with one decimal in the UI language', () => {
    expect(formatSize(4236703648, 'en')).toBe('3.9 GB')
    expect(formatSize(4236703648, 'tr')).toBe('3,9 GB')
    expect(formatSize(343770192, 'en')).toBe('327.8 MB')
  })
})

describe('install: step 3, the licences', () => {
  it('is skipped without licences and the install is started with acceptedEulas false', async () => {
    view()
    await toModules()
    expect(nextBtn().textContent).toBe(en['hub.install.go'])
    expect(screen.getByTestId('hub-install').textContent).toContain(en['hub.install.adminNote'])
    fireEvent.click(nextBtn())
    await waitFor(() => expect(start).toHaveBeenCalledWith({ version: '6000.6.7f1', modules: ['webgl'], acceptedEulas: false }))
    expect(screen.queryByTestId('hub-install-eulas')).toBeNull()
  })

  it('appears with licences, opens their text outside and blocks until every box is ticked', async () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null)
    view()
    await toModules()
    fireEvent.click(checkbox('android'))
    fireEvent.click(checkbox('visualstudio'))
    await waitFor(() => expect(nextBtn().textContent).toBe(en['hub.install.next']))
    fireEvent.click(nextBtn())
    const list = await screen.findByTestId('hub-install-eulas')
    expect(list.textContent).toContain(ANDROID_EULA.label)
    expect(list.textContent).toContain(VS_EULA.label)
    expect(screen.getByTestId('hub-install').textContent).toContain(en['hub.install.adminNote'])
    fireEvent.click(within(list).getAllByRole('button', { name: en['hub.install.openText'] })[0])
    expect(open).toHaveBeenCalledWith(expect.stringMatching(/^https:\/\//), '_blank')

    const go = screen.getByTestId('hub-install-start') as HTMLButtonElement
    const boxes = screen.getAllByTestId('hub-install-eula-check')
    expect(boxes).toHaveLength(2)
    expect(go.disabled).toBe(true)
    fireEvent.click(boxes[0])
    expect(go.disabled).toBe(true)
    fireEvent.click(go)
    expect(start).not.toHaveBeenCalled()
    fireEvent.click(boxes[1])
    expect(go.disabled).toBe(false)
    fireEvent.click(go)
    await waitFor(() => expect(start).toHaveBeenCalledTimes(1))
    const arg = vi.mocked(start).mock.calls[0][0]
    expect(arg.acceptedEulas).toBe(true)
    expect(arg.modules.sort()).toEqual(['android', 'visualstudio', 'webgl'])
  })
})

describe('install: step 4, the run', () => {
  it('walks download, stall, retry and the install phase, then refreshes and opens a new project on that version', async () => {
    const finish = pendingStart()
    view()
    await toModules()
    fireEvent.click(nextBtn())
    await screen.findByTestId('hub-install-progress')
    const cancel = () => screen.getByTestId('hub-install-cancel') as HTMLButtonElement

    await progress({ phase: 'download', pct: 40, message: 'Downloading Web Build Support...' })
    expect(screen.getByTestId('hub-install-message').textContent).toBe('Downloading Web Build Support...')
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('40')
    expect(cancel().disabled).toBe(false)
    expect(screen.getByTestId('hub-tab-editors-n').textContent).toBe('40%')

    await progress({ phase: 'download', pct: 40, message: 'Downloading Web Build Support...', stalled: true })
    expect(screen.getByTestId('hub-install-stalled').textContent).toBe(en['hub.install.stalled'])
    expect(cancel().disabled).toBe(false)

    await progress({ phase: 'download', pct: 55, message: 'Module install failed; retrying (attempt 1 of 2) in 2s...', retrying: true })
    expect(screen.queryByTestId('hub-install-stalled')).toBeNull()
    expect(screen.getByTestId('hub-install-retrying').textContent).toBe(en['hub.install.retrying'])

    await progress({ phase: 'install', pct: 90, message: 'Installing Web Build Support...' })
    expect(cancel().disabled).toBe(true)
    expect(screen.getByTestId('hub-install-finishing').textContent).toBe(en['hub.install.finishing'])

    const listed = calls('hub-list-editors').length
    editors = [{ version: '6000.6.7f1', path: '/U3/Unity' }, ...editors]
    await finish({ ok: true, completed: ['6000.6.7f1', 'webgl'] })
    const done = await screen.findByTestId('hub-install-done')
    expect(done.textContent).toContain(tEn('hub.install.done', { version: '6000.6.7f1' }))
    await waitFor(() => expect(calls('hub-list-editors').length).toBe(listed + 1))
    expect(mocks.listeners.has('hub-install-progress')).toBe(false)

    fireEvent.click(within(done).getByRole('button', { name: en['hub.install.createWith'] }))
    expect(screen.getByTestId('hub-tab-new').getAttribute('aria-selected')).toBe('true')
    await waitFor(() => expect((screen.getByTestId('hub-version') as HTMLSelectElement).value).toBe('6000.6.7f1'))
    expect(getInstall().state).toBe('idle')
  })

  it('a refused cancel in the install phase says it cannot be stopped; a cancel during download ends as cancelled', async () => {
    const finish = pendingStart()
    view()
    await toModules()
    fireEvent.click(nextBtn())
    await screen.findByTestId('hub-install-progress')
    await progress({ phase: 'download', pct: 70, message: 'Downloading...' })
    cancelAnswer = { cancelled: false, reason: 'installing' }
    fireEvent.click(screen.getByTestId('hub-install-cancel'))
    expect(await screen.findByTestId('hub-install-finishing')).toBeTruthy()
    expect((screen.getByTestId('hub-install-cancel') as HTMLButtonElement).disabled).toBe(true)
    expect(calls('hub-install-cancel')).toHaveLength(1)
    await finish({ ok: true, completed: [] })
    expect(await screen.findByTestId('hub-install-done')).toBeTruthy()
    cleanup()

    resetInstallStore()
    const finish2 = pendingStart()
    view()
    await toModules()
    fireEvent.click(nextBtn())
    await screen.findByTestId('hub-install-progress')
    await progress({ phase: 'download', pct: 10, message: 'Downloading...' })
    cancelAnswer = { cancelled: true }
    fireEvent.click(screen.getByTestId('hub-install-cancel'))
    await waitFor(() => expect(calls('hub-install-cancel')).toHaveLength(2))
    await finish2({ ok: false, code: 'cancelled' })
    const stopped = await screen.findByTestId('hub-install-cancelled')
    expect(stopped.textContent).toContain(en['hub.install.error.cancelled'])
  })

  it('a failure gives the plain reason, the CLI message for cliFailed, which parts did install, and Try again repeats the request', async () => {
    let finish = pendingStart()
    view()
    await toModules()
    fireEvent.click(checkbox('ios'))
    await waitFor(() => expect(nextBtn().disabled).toBe(false))
    fireEvent.click(nextBtn())
    await screen.findByTestId('hub-install-progress')
    await finish({ ok: false, code: 'cliFailed', message: 'Disk write failed', completed: ['webgl'] })
    const failed = await screen.findByTestId('hub-install-failed')
    expect(screen.getByTestId('hub-install-reason').textContent).toBe(`${en['hub.install.error.cliFailed']} ${tEn('hub.install.cliSaid', { message: 'Disk write failed' })}`)
    expect(screen.getByTestId('hub-install-completed').textContent).toBe(tEn('hub.install.partsDone', { names: 'Web Build Support' }))

    const first = vi.mocked(start).mock.calls[0][0]
    finish = pendingStart()
    fireEvent.click(within(failed).getByRole('button', { name: en['hub.retry'] }))
    await waitFor(() => expect(start).toHaveBeenCalledWith(first))
    expect(await screen.findByTestId('hub-install-progress')).toBeTruthy()
    await finish({ ok: false, code: 'elevationRefused' })
    expect((await screen.findByTestId('hub-install-reason')).textContent).toBe(en['hub.install.error.elevationRefused'])
    expect(screen.queryByTestId('hub-install-completed')).toBeNull()
  })

  it('has its own plain reason for every InstallErrorCode', () => {
    const codes: InstallErrorCode[] = ['busy', 'cliMissing', 'invalidArguments', 'unknownVersion', 'unknownModule',
      'eulaNotAccepted', 'notEnoughSpace', 'elevationRefused', 'cancelled', 'cliFailed', 'exitCode']
    const texts = codes.map(code => installFailureText({ code }, tEn))
    codes.forEach((code, i) => expect(texts[i]).toBe(en[`hub.install.error.${code}` as TKey]))
    expect(new Set(texts).size).toBe(codes.length)
  })

  it('survives the screen unmounting and remounting', async () => {
    const finish = pendingStart()
    view()
    await toModules()
    fireEvent.click(nextBtn())
    await screen.findByTestId('hub-install-progress')
    await progress({ phase: 'download', pct: 25, message: 'Downloading Unity 6000.6.7f1...' })
    cleanup()

    view('arena')
    expect(screen.getByTestId('hub-tab-editors-n').textContent).toBe('25%')
    tab('editors')
    expect(screen.getByTestId('hub-install-message').textContent).toBe('Downloading Unity 6000.6.7f1...')
    expect(calls('hub-install-start')).toHaveLength(1)
    await progress({ phase: 'download', pct: 60, message: 'Downloading iOS...' })
    expect(screen.getByTestId('hub-install-message').textContent).toBe('Downloading iOS...')
    await finish({ ok: true, completed: [] })
    expect(await screen.findByTestId('hub-install-done')).toBeTruthy()
  })
})

describe('install: adding modules to an installed editor', () => {
  it('starts at step 2 for that version and installs into it', async () => {
    view()
    tab('editors')
    fireEvent.click(await screen.findByTestId('hub-add-modules-6000.2.4f1'))
    await screen.findByTestId('hub-install-modules')
    expect(screen.queryByTestId('hub-install-recommended')).toBeNull()
    expect(checkbox('webgl').checked).toBe(true)
    expect(checkbox('webgl').disabled).toBe(true)
    // No other editor is known to have modules, so the API's preselection is ticked.
    expect(checkbox('visualstudio').checked).toBe(true)
    fireEvent.click(checkbox('visualstudio'))
    await waitFor(() => expect(screen.getByTestId('hub-install-total').textContent).toContain(en['hub.install.total'].split(' ')[0]))
    expect(nextBtn().disabled).toBe(true)
    expect(screen.getByTestId('hub-install').textContent).toContain(en['hub.install.pickSomething'])

    fireEvent.click(checkbox('ios'))
    await waitFor(() => expect(nextBtn().disabled).toBe(false))
    expect(calls('hub-install-plan').at(-1)![1]).toEqual({ version: '6000.2.4f1', modules: ['ios'] })
    fireEvent.click(nextBtn())
    await waitFor(() => expect(start).toHaveBeenCalledWith({ version: '6000.2.4f1', modules: ['ios'], acceptedEulas: false }))
    expect((await screen.findByTestId('hub-install-done')).textContent).toContain(tEn('hub.install.doneModules', { version: '6000.2.4f1' }))
  })
})

describe('install: when the tool or the catalog is missing', () => {
  it('openHubOnce explains the Hub update and opens Unity Hub', async () => {
    editors = []
    catalog = { ok: false, cli: { available: false, reason: 'openHubOnce' }, code: 'catalogFailed' }
    view()
    tab('editors')
    fireEvent.click(await screen.findByTestId('hub-install-empty'))
    const box = await screen.findByTestId('hub-install-cli-openHubOnce')
    expect(box.textContent).toContain(en['hub.install.openHubOnce'])
    fireEvent.click(within(box).getByRole('button', { name: en['hub.install.openHub'] }))
    await waitFor(() => expect(calls('open-unity-hub')).toHaveLength(1))
    catalog = { ok: true, cli: { available: true }, releases: RELEASES() }
    fireEvent.click(within(box).getByRole('button', { name: en['hub.install.recheck'] }))
    expect(await screen.findByTestId('hub-install-recommended')).toBeTruthy()
    expect(calls('hub-install-catalog')).toHaveLength(2)
  })

  it('noHub keeps the Download Unity path', async () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null)
    editors = []
    catalog = { ok: true, cli: { available: false, reason: 'noHub' }, releases: [] }
    view()
    tab('editors')
    fireEvent.click(await screen.findByTestId('hub-install-empty'))
    const box = await screen.findByTestId('hub-install-cli-noHub')
    expect(box.textContent).toContain(en['hub.install.noHub'])
    fireEvent.click(within(box).getByRole('button', { name: en['hub.getUnity'] }))
    expect(open).toHaveBeenCalledWith(UNITY_DOWNLOAD_URL, '_blank')
  })

  it.each([['offline', 'hub.install.offline'], ['catalogFailed', 'hub.install.catalogFailed']] as const)('catalog %s gives the reason and a retry', async (code, key) => {
    catalog = { ok: false, cli: { available: true }, code }
    view()
    tab('editors')
    fireEvent.click(await screen.findByTestId('hub-install-entry'))
    const box = await screen.findByTestId(`hub-install-${code}`)
    expect(box.textContent).toContain(en[key])
    catalog = { ok: true, cli: { available: true }, releases: RELEASES() }
    fireEvent.click(within(box).getByRole('button', { name: en['hub.retry'] }))
    expect(await screen.findByTestId('hub-install-recommended')).toBeTruthy()
  })
})

describe('install: every theme', () => {
  const WORDS: Record<Theme, { entry: TKey; title: TKey }> = {
    arena: { entry: 'hub.install.entryQuest', title: 'hub.install.titleQuest' },
    sade: { entry: 'hub.install.entry', title: 'hub.install.title' },
    pafta: { entry: 'hub.install.entryNote', title: 'hub.install.titleNote' },
    atolye: { entry: 'hub.install.entryShop', title: 'hub.install.titleShop' },
    ozel: { entry: 'hub.install.entry', title: 'hub.install.title' },
  }
  it('covers every theme', () => expect(Object.keys(WORDS).sort()).toEqual([...THEMES].sort()))

  it.each(THEMES)('%s: the editors view has the install entry in its own words and Add modules on each row', async theme => {
    const { container } = view(theme)
    tab('editors')
    const entry = await screen.findByTestId('hub-install-entry')
    expect(entry.textContent).toBe(en[WORDS[theme].entry])
    expect(screen.getByTestId('hub-add-modules-6000.2.4f1').textContent).toBe(en['hub.install.addModules'])
    expect(screen.getByTestId('hub-add-modules-2022.3.55f1')).toBeTruthy()
    fireEvent.click(entry)
    await screen.findByTestId('hub-install-recommended')
    expect(container.querySelector(`.hub[data-hub="${theme}"] .hub-inst`)?.textContent).toContain(en[WORDS[theme].title])
    fireEvent.click(screen.getByTestId('hub-install-close'))
    expect(await screen.findByTestId('hub-editors')).toBeTruthy()
  })
})
