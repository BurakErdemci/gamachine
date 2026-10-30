import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act, cleanup, waitFor, within } from '@testing-library/react'

// A fake desktop bridge: every hub-* channel answers from `answers`, progress goes to `progress`.
const mocks = vi.hoisted(() => {
  const invoke = vi.fn()
  const listeners = new Map<string, (p: unknown) => void>()
  const on = vi.fn((channel: string, cb: (p: unknown) => void) => { listeners.set(channel, cb); return () => listeners.delete(channel) })
  ;(globalThis as any).window.ipc = { invoke, registerDroppedFolder: vi.fn(), on }
  return { invoke, on, listeners }
})
vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), defaults: { headers: { common: {} } } } }))

import axios from 'axios'
import { WorkspaceScreen, UNITY_DOWNLOAD_URL } from '../renderer/components/home/WorkspaceScreen'
import { LangContext, ceviriUygula, translations } from '../renderer/lib/i18n'
import { renderHook } from '@testing-library/react'
import { useFileSystem } from '../renderer/hooks/home/useFileSystem'
import { nameProblem, templateName, joinTarget, clock, resetHubStore, getRun } from '../renderer/lib/hub'

const en = translations.en
const EDITORS = [
  { version: '6000.2.4f1', path: '/Applications/Unity/Hub/Editor/6000.2.4f1/Unity.app/Contents/MacOS/Unity' },
  { version: '2022.3.55f1', path: '/Applications/Unity/Hub/Editor/2022.3.55f1/Unity.app/Contents/MacOS/Unity' },
]
const TEMPLATES = [
  { id: 'com.unity.template.2d-cross-platform-17.0.6', label: '2d', file: 'a.tgz' },
  { id: 'com.unity.template.3d-cross-platform-17.0.6', label: '3d', file: 'b.tgz' },
  { id: 'com.unity.template.universal-3d-17.0.6', label: 'other', file: 'c.tgz' },
]

let editors: unknown
let create: (arg: any) => Promise<any>
const ipc = async (channel: string, arg: any) => {
  if (channel === 'host-workspace-path') return arg.replace('/b/', '/h/')
  if (channel === 'workspace-info') return arg.map((path: string) => ({ path, status: 'ok', unityVersion: '6000.2.4f1' }))
  if (channel === 'hub-list-editors') return editors
  if (channel === 'hub-list-templates') return { templates: arg.version === '6000.2.4f1' ? TEMPLATES : [] }
  if (channel === 'hub-pick-parent-dir') return { path: '/Users/me/Games' }
  if (channel === 'hub-create-project') return create(arg)
  if (channel === 'hub-cancel-create') return { cancelled: true }
  if (channel === 'hub-open-project') return { opened: true }
  return null
}

const props = () => ({
  api: 'http://backend', user: { id: 7, name: 'Burak', sessionToken: 'tok' }, userName: 'Burak',
  onOpenFolder: vi.fn(async () => null as string | null), onSelectWorkspace: vi.fn(async () => true),
  onLogout: vi.fn(), showToast: vi.fn(),
})
const view = (p = props()) => {
  const value = { lang: 'en' as const, setLang: () => {}, t: (k: any, v?: any) => ceviriUygula('en', k, v) }
  render(<LangContext.Provider value={value}><WorkspaceScreen {...p} /></LangContext.Provider>)
  return p
}
const tab = (v: string) => fireEvent.click(screen.getByTestId(`hub-tab-${v}`))
const nameBox = () => screen.getByTestId('hub-name') as HTMLInputElement
const pending = () => {
  let resolve!: (v: any) => void
  create = vi.fn(() => new Promise(r => { resolve = r }))
  return (v: any) => act(async () => { resolve(v) })
}
const fillForm = async (name = 'Night Run') => {
  tab('new')
  await waitFor(() => expect(screen.getByRole('radio', { name: /^3D/ }).getAttribute('aria-checked')).toBe('true'))
  fireEvent.change(nameBox(), { target: { value: name } })
  fireEvent.click(screen.getByTestId('hub-folder'))
  await waitFor(() => expect(screen.getByTestId('hub-target').textContent).toBe(en['hub.target'].replace('{path}', `/Users/me/Games/${name}`)))
}
const progressSubscriptions = () => mocks.on.mock.calls.filter(c => c[0] === 'hub-create-progress').length
const progress = (p: unknown) => act(async () => { mocks.listeners.get('hub-create-progress')!(p) })

beforeEach(() => {
  resetHubStore()
  editors = { editors: EDITORS }
  create = vi.fn(async () => ({ ok: true, path: '/Users/me/Games/Night Run' }))
  mocks.invoke.mockReset().mockImplementation(ipc)
  mocks.listeners.clear()
  mocks.on.mockClear()
  vi.mocked(axios.get).mockReset().mockResolvedValue({ data: { workspaces: [{ path: '/b/Arena', last_accessed: '2026-10-29 10:00:00', chat_count: 2 }] } })
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('hub: installed editors', () => {
  it('lists the editors newest first with how many recent projects use each', async () => {
    view()
    tab('editors')
    const list = await screen.findByTestId('hub-editors')
    expect(list.textContent).toContain('6000.2.4f1')
    expect(list.textContent).toContain(en['hub.editorLatest'])
    expect(list.textContent).toContain('2022.3.55f1')
    await waitFor(() => expect(list.textContent).toContain(en['hub.editorUsedBy'].replace('{n}', '1')))
    expect(screen.getByTestId('hub-tab-editors').textContent).toContain('2')
  })

  it('with none installed it explains how to get Unity and links the download page', async () => {
    editors = { editors: [] }
    const open = vi.spyOn(window, 'open').mockImplementation(() => null)
    view()
    tab('editors')
    const empty = await screen.findByTestId('hub-editors-empty')
    expect(empty.textContent).toContain(en['hub.editorsEmptyTitle'])
    expect(empty.textContent).toContain(en['hub.editorsEmpty'])
    fireEvent.click(within(empty).getByRole('button', { name: en['hub.getUnity'] }))
    expect(open).toHaveBeenCalledWith(UNITY_DOWNLOAD_URL, '_blank')
    editors = { editors: EDITORS }
    fireEvent.click(within(empty).getByRole('button', { name: en['hub.refresh'] }))
    expect(await screen.findByTestId('hub-editors')).toBeTruthy()
  })

  it('a failed read says so instead of claiming nothing is installed', async () => {
    editors = null
    view()
    tab('editors')
    expect((await screen.findByTestId('hub-editors-empty')).textContent).toContain(en['hub.editorsFailed'])
  })
})

describe('hub: new project form', () => {
  it('shows the engine name rule inline and the reason once it is broken', async () => {
    view()
    tab('new')
    expect(screen.getByTestId('hub-name-note').textContent).toBe(en['hub.nameRule'])
    for (const [value, key] of [['a/b', 'hub.nameChars'], [' lead', 'hub.nameSpaces'], ['x'.repeat(65), 'hub.nameLong']] as const) {
      fireEvent.change(nameBox(), { target: { value } })
      expect(screen.getByTestId('hub-name-note').textContent).toBe(en[key])
      expect(nameBox().getAttribute('aria-invalid')).toBe('true')
    }
    fireEvent.change(nameBox(), { target: { value: 'Oyun_2 ğüş' } })
    expect(screen.getByTestId('hub-name-note').textContent).toBe(en['hub.nameRule'])
    expect(nameBox().hasAttribute('aria-invalid')).toBe(false)
  })

  it('a press on Create without a folder names what is missing and creates nothing', async () => {
    view()
    tab('new')
    fireEvent.change(nameBox(), { target: { value: 'Game' } })
    fireEvent.click(screen.getByTestId('hub-create'))
    expect((await screen.findByTestId('hub-folder-note')).textContent).toBe(en['hub.folderMissing'])
    expect(mocks.invoke).not.toHaveBeenCalledWith('hub-create-project', expect.anything())
  })

  it('templates follow the chosen version; an editor without templates says an empty project is made', async () => {
    view()
    tab('new')
    await waitFor(() => expect(screen.getAllByRole('radio')).toHaveLength(3))
    expect(mocks.invoke).toHaveBeenCalledWith('hub-list-templates', { version: '6000.2.4f1' })
    fireEvent.change(screen.getByTestId('hub-version'), { target: { value: '2022.3.55f1' } })
    expect(await screen.findByText(en['hub.templatesNone'])).toBeTruthy()
    expect(mocks.invoke).toHaveBeenCalledWith('hub-list-templates', { version: '2022.3.55f1' })
  })
})

describe('hub: creating a project', () => {
  it('sends the form to the engine, shows elapsed time and the log tail, then opens the result', async () => {
    const finish = pending()
    const p = view()
    await fillForm()
    fireEvent.click(screen.getByRole('radio', { name: /URP/ }))
    fireEvent.click(screen.getByTestId('hub-create'))
    await waitFor(() => expect(create).toHaveBeenCalledWith({
      parentDir: '/Users/me/Games', name: 'Night Run', version: '6000.2.4f1', templateId: 'com.unity.template.universal-3d-17.0.6',
    }))
    expect(screen.getByTestId('hub-progress')).toBeTruthy()
    await progress({ phase: 'running', elapsedMs: 63_400, logTail: 'old line\nImporting Assets/Scenes/SampleScene.unity\n' })
    expect(screen.getByTestId('hub-elapsed').textContent).toBe('01:03')
    expect(screen.getByTestId('hub-log').textContent).toContain('Importing Assets/Scenes/SampleScene.unity')
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('63')

    await finish({ ok: true, path: '/Users/me/Games/Night Run' })
    const done = screen.getByTestId('hub-result-done')
    expect(done.textContent).toContain(en['hub.doneTitle'].replace('{name}', 'Night Run'))
    fireEvent.click(screen.getByTestId('hub-open-created'))
    await waitFor(() => expect(p.onSelectWorkspace).toHaveBeenCalledWith('/Users/me/Games/Night Run'))
  })

  it('Cancel asks the engine to stop and the cancelled result offers the form back', async () => {
    const finish = pending()
    view()
    await fillForm()
    fireEvent.click(screen.getByTestId('hub-create'))
    await screen.findByTestId('hub-progress')
    fireEvent.click(screen.getByTestId('hub-cancel'))
    await waitFor(() => expect(mocks.invoke).toHaveBeenCalledWith('hub-cancel-create'))
    expect(screen.getByTestId('hub-cancel').textContent).toBe(en['hub.cancelling'])
    await finish({ ok: false, error: 'cancelled' })
    expect(screen.getByTestId('hub-result-cancelled').textContent).toContain(en['hub.cancelledText'])
    fireEvent.click(screen.getByTestId('hub-back'))
    expect(nameBox().value).toBe('Night Run')
  })

  it('a failure shows the engine text as it came and keeps the answers', async () => {
    const error = 'Unity exited with code 1. The partial folder could not be removed: /Users/me/Games/Night Run'
    create = vi.fn(async () => ({ ok: false, error }))
    view()
    await fillForm()
    fireEvent.click(screen.getByTestId('hub-create'))
    const failed = await screen.findByTestId('hub-result-failed')
    expect(within(failed).getByRole('alert').textContent).toBe(error)
    fireEvent.click(screen.getByTestId('hub-back'))
    expect(nameBox().value).toBe('Night Run')
    expect(screen.getByTestId('hub-folder').textContent).toContain('/Users/me/Games')
  })

  it('the view can change while Unity works and the run is still there on return', async () => {
    pending()
    view()
    await fillForm()
    fireEvent.click(screen.getByTestId('hub-create'))
    await screen.findByTestId('hub-progress')
    tab('projects')
    expect(screen.queryByTestId('hub-progress')).toBeNull()
    tab('new')
    expect(screen.getByTestId('hub-progress')).toBeTruthy()
  })
})

describe('hub: recent projects', () => {
  it('a trusted recent project can be launched in its Unity version from the more menu', async () => {
    view()
    fireEvent.click(await screen.findByRole('button', { name: 'Options for Arena' }))
    fireEvent.click(screen.getByRole('menuitem', { name: en['hub.launchWith'].replace('{version}', '6000.2.4f1') }))
    await waitFor(() => expect(mocks.invoke).toHaveBeenCalledWith('hub-open-project', { path: '/h/Arena', version: '6000.2.4f1' }))
  })

  it('a launch the engine refuses is reported with its reason', async () => {
    mocks.invoke.mockImplementation(async (c: string, a: any) => (c === 'hub-open-project' ? { opened: false, error: 'Unity 6000.2.4f1 is not installed.' } : ipc(c, a)))
    const p = view()
    fireEvent.click(await screen.findByRole('button', { name: 'Options for Arena' }))
    fireEvent.click(screen.getByRole('menuitem', { name: en['hub.launchWith'].replace('{version}', '6000.2.4f1') }))
    await waitFor(() => expect(p.showToast).toHaveBeenCalledWith(en['hub.launchFailed'].replace('{error}', 'Unity 6000.2.4f1 is not installed.'), 'error'))
  })
})

describe('hub: the run outlives the screen', () => {
  it('a remounted screen shows the running creation, its progress and then the Open offer', async () => {
    const finish = pending()
    const p = props()
    view(p)
    await fillForm()
    fireEvent.click(screen.getByTestId('hub-create'))
    await screen.findByTestId('hub-progress')
    cleanup()
    view(p)
    expect(screen.getByTestId('hub-progress')).toBeTruthy()
    expect(screen.queryByTestId('hub-create')).toBeNull()
    await progress({ phase: 'running', elapsedMs: 42_000, logTail: 'x\n' })
    expect(screen.getByTestId('hub-elapsed').textContent).toBe('00:42')
    expect(create).toHaveBeenCalledTimes(1)
    await finish({ ok: true, path: '/Users/me/Games/Night Run' })
    // A finished run that was not opened is still offered after a remount.
    cleanup()
    view(p)
    expect(screen.getByTestId('hub-result-done')).toBeTruthy()
    fireEvent.click(screen.getByTestId('hub-open-created'))
    await waitFor(() => expect(p.onSelectWorkspace).toHaveBeenCalledWith('/Users/me/Games/Night Run'))
    // Opened: consumed, so a later mount shows the plain form.
    cleanup()
    view(p)
    expect(screen.queryByTestId('hub-result-done')).toBeNull()
    expect(screen.getByTestId('hub-tab-projects').getAttribute('aria-selected')).toBe('true')
  })

  it('a project launched from the result is consumed too', async () => {
    const finish = pending()
    const p = props()
    view(p)
    await fillForm()
    fireEvent.click(screen.getByTestId('hub-create'))
    await screen.findByTestId('hub-progress')
    await finish({ ok: true, path: '/Users/me/Games/Night Run' })
    fireEvent.click(screen.getByTestId('hub-launch-created'))
    await waitFor(() => expect(mocks.invoke).toHaveBeenCalledWith('hub-open-project', { path: '/Users/me/Games/Night Run', version: '6000.2.4f1' }))
    await waitFor(() => expect(p.showToast).toHaveBeenCalledWith(en['hub.launched'], 'success'))
    cleanup()
    view(p)
    expect(screen.queryByTestId('hub-result-done')).toBeNull()
  })

  it('a failed creation can be retried after a remount with the same request', async () => {
    create = vi.fn(async () => ({ ok: false, error: 'boom' }))
    const p = props()
    view(p)
    await fillForm('Night Run')
    fireEvent.click(screen.getByRole('radio', { name: /URP/ }))
    fireEvent.click(screen.getByTestId('hub-create'))
    await screen.findByTestId('hub-result-failed')
    const first = (create as any).mock.calls[0][0]
    cleanup()
    view(p)
    await screen.findByTestId('hub-result-failed')
    fireEvent.click(screen.getByTestId('hub-back'))
    expect(nameBox().value).toBe('Night Run')
    expect(screen.getByTestId('hub-folder').textContent).toContain('/Users/me/Games')
    await waitFor(() => expect(screen.getByRole('radio', { name: /URP/ }).getAttribute('aria-checked')).toBe('true'))
    fireEvent.click(screen.getByTestId('hub-create'))
    await waitFor(() => expect((create as any).mock.calls.length).toBe(2))
    expect((create as any).mock.calls[1][0]).toEqual(first)
  })

  it('unmounting drops the screen from the run; the only progress listener is the store\'s and ends with the run', async () => {
    const finish = pending()
    view()
    await fillForm()
    fireEvent.click(screen.getByTestId('hub-create'))
    await screen.findByTestId('hub-progress')
    expect(mocks.listeners.has('hub-create-progress')).toBe(true)
    expect(progressSubscriptions()).toBe(1)
    cleanup()
    // the run goes on without a screen, still with exactly one listener
    expect(mocks.listeners.has('hub-create-progress')).toBe(true)
    expect(progressSubscriptions()).toBe(1)
    await finish({ ok: true, path: '/Users/me/Games/Night Run' })
    expect(mocks.listeners.has('hub-create-progress')).toBe(false)
  })

  it('no listener is left after a run that failed', async () => {
    create = vi.fn(async () => ({ ok: false, error: 'boom' }))
    view()
    await fillForm()
    fireEvent.click(screen.getByTestId('hub-create'))
    await screen.findByTestId('hub-result-failed')
    expect(mocks.listeners.has('hub-create-progress')).toBe(false)
  })
})

describe('hub: consuming a finished run', () => {
  const finishedRun = async (p: ReturnType<typeof props>) => {
    const finish = pending()
    view(p)
    await fillForm()
    fireEvent.click(screen.getByTestId('hub-create'))
    await screen.findByTestId('hub-progress')
    await finish({ ok: true, path: '/Users/me/Games/Night Run' })
    await screen.findByTestId('hub-result-done')
  }

  it('Open here keeps the finished run when the workspace did not open', async () => {
    const p = props()
    p.onSelectWorkspace = vi.fn(async () => false)
    await finishedRun(p)
    fireEvent.click(screen.getByTestId('hub-open-created'))
    await waitFor(() => expect(p.onSelectWorkspace).toHaveBeenCalledTimes(1))
    await act(async () => {})
    cleanup()
    view(p)
    expect(screen.getByTestId('hub-result-done')).toBeTruthy()
  })

  it('a late Open here answer does not consume a newer run', async () => {
    const p = props()
    let answer!: (v: boolean) => void
    p.onSelectWorkspace = vi.fn(() => new Promise<boolean>(r => { answer = r }))
    await finishedRun(p)
    fireEvent.click(screen.getByTestId('hub-open-created'))
    await waitFor(() => expect(p.onSelectWorkspace).toHaveBeenCalledTimes(1))
    // Another run takes the store before the answer lands.
    fireEvent.click(screen.getByText(en['hub.another']))
    const finishSecond = pending()
    await fillForm('Second')
    fireEvent.click(screen.getByTestId('hub-create'))
    await screen.findByTestId('hub-progress')
    await finishSecond({ ok: true, path: '/Users/me/Games/Second' })
    await screen.findByTestId('hub-result-done')
    await act(async () => { answer(true) })
    expect(getRun().state).toBe('done')
    expect((getRun() as any).path).toBe('/Users/me/Games/Second')
  })

  it('a late launch answer does not consume a newer run', async () => {
    const p = props()
    let answer!: (v: unknown) => void
    await finishedRun(p)
    mocks.invoke.mockImplementation(async (channel: string, arg: any) => (
      channel === 'hub-open-project' ? new Promise(r => { answer = r }) : ipc(channel, arg)))
    fireEvent.click(screen.getByTestId('hub-launch-created'))
    await waitFor(() => expect(answer).toBeTypeOf('function'))
    fireEvent.click(screen.getByText(en['hub.another']))
    const finishSecond = pending()
    await fillForm('Second')
    fireEvent.click(screen.getByTestId('hub-create'))
    await screen.findByTestId('hub-progress')
    await finishSecond({ ok: true, path: '/Users/me/Games/Night Run' })
    await screen.findByTestId('hub-result-done')
    const second = getRun()
    await act(async () => { answer({ opened: true }) })
    expect(getRun()).toBe(second)
  })

  it('logging out drops a finished run; a run still creating stays', async () => {
    const p = props()
    await finishedRun(p)
    fireEvent.click(screen.getByText(en['welcome.logout']))
    expect(p.onLogout).toHaveBeenCalledTimes(1)
    expect(getRun().state).toBe('idle')
    cleanup()
    const finish = pending()
    view(p)
    await fillForm('Third')
    fireEvent.click(screen.getByTestId('hub-create'))
    await screen.findByTestId('hub-progress')
    fireEvent.click(screen.getByText(en['welcome.logout']))
    expect(getRun().state).toBe('creating')
    await finish({ ok: true, path: '/Users/me/Games/Third' })
  })
})

describe('useFileSystem.selectWorkspace result', () => {
  const mount = () => renderHook(() => useFileSystem('', null, () => {}))

  it('is true for a normal selection, false for a missing folder', async () => {
    mocks.invoke.mockImplementation(async (channel: string, arg: any) => (
      channel === 'path-exists' ? arg === '/here' : channel === 'read-directory' ? [] : null))
    const { result } = mount()
    let ok: boolean | undefined, missing: boolean | undefined
    await act(async () => { ok = await result.current.selectWorkspace('/here') })
    await act(async () => { missing = await result.current.selectWorkspace('/gone') })
    expect(ok).toBe(true)
    expect(missing).toBe(false)
  })

  it('is false for a selection a newer one superseded', async () => {
    let release!: (v: boolean) => void
    mocks.invoke.mockImplementation((channel: string, arg: any) => {
      if (channel === 'path-exists') return arg === '/first' ? new Promise(r => { release = r }) : Promise.resolve(true)
      return Promise.resolve(channel === 'read-directory' ? [] : null)
    })
    const { result } = mount()
    let first!: Promise<boolean>
    act(() => { first = result.current.selectWorkspace('/first') })
    await act(async () => { await result.current.selectWorkspace('/second') })
    let got: boolean | undefined
    await act(async () => { release(true); got = await first })
    expect(got).toBe(false)
  })
})

describe('hub: a rejected invoke', () => {
  it('shows the thrown message for a creation instead of "no error"', async () => {
    create = vi.fn(async () => { throw new Error('main exploded') })
    view()
    await fillForm()
    fireEvent.click(screen.getByTestId('hub-create'))
    const failed = await screen.findByTestId('hub-result-failed')
    expect(within(failed).getByRole('alert').textContent).toBe('main exploded')
  })

  it('shows the thrown message for a launch', async () => {
    mocks.invoke.mockImplementation(async (c: string, a: any) => { if (c === 'hub-open-project') throw new Error('launch exploded'); return ipc(c, a) })
    const p = view()
    fireEvent.click(await screen.findByRole('button', { name: 'Options for Arena' }))
    fireEvent.click(screen.getByRole('menuitem', { name: en['hub.launchWith'].replace('{version}', '6000.2.4f1') }))
    await waitFor(() => expect(p.showToast).toHaveBeenCalledWith(en['hub.launchFailed'].replace('{error}', 'launch exploded'), 'error'))
  })

  it('keeps the old text for a failure with no message', async () => {
    create = vi.fn(async () => { throw new Error('') })
    view()
    await fillForm()
    fireEvent.click(screen.getByTestId('hub-create'))
    const failed = await screen.findByTestId('hub-result-failed')
    expect(within(failed).getByRole('alert').textContent).toBe(en['hub.failedUnknown'])
  })
})

describe('hub: templates that cannot be read', () => {
  it('shows an error with a retry, keeps Create off and never creates without a template id', async () => {
    let fail = true
    mocks.invoke.mockImplementation(async (channel: string, arg: any) => {
      if (channel === 'hub-list-templates' && fail) throw new Error('ipc down')
      return ipc(channel, arg)
    })
    view()
    tab('new')
    expect(await screen.findByTestId('hub-templates-failed')).toBeTruthy()
    fireEvent.change(nameBox(), { target: { value: 'Game' } })
    fireEvent.click(screen.getByTestId('hub-folder'))
    await waitFor(() => expect(screen.getByTestId('hub-target')).toBeTruthy())
    const btn = screen.getByTestId('hub-create') as HTMLButtonElement
    expect(btn.disabled).toBe(true)
    fireEvent.click(btn)
    fireEvent.submit(btn.closest('form')!)
    expect(mocks.invoke).not.toHaveBeenCalledWith('hub-create-project', expect.anything())
    fail = false
    fireEvent.click(screen.getByTestId('hub-templates-retry'))
    await waitFor(() => expect(screen.getAllByRole('radio')).toHaveLength(3))
    expect((screen.getByTestId('hub-create') as HTMLButtonElement).disabled).toBe(false)
  })
})

describe('hub: StrictMode', () => {
  it('still lists the editors when effects run twice', async () => {
    const value = { lang: 'en' as const, setLang: () => {}, t: (k: any, v?: any) => ceviriUygula('en', k, v) }
    render(<React.StrictMode><LangContext.Provider value={value}><WorkspaceScreen {...props()} /></LangContext.Provider></React.StrictMode>)
    tab('editors')
    expect((await screen.findByTestId('hub-editors')).textContent).toContain('6000.2.4f1')
    tab('new')
    await waitFor(() => expect(screen.getAllByRole('radio')).toHaveLength(3))
  })
})

describe('hub helpers', () => {
  it('mirror the engine name rule', () => {
    expect(nameProblem('')).toBe('empty')
    expect(nameProblem('My Game-2_x')).toBeNull()
    expect(nameProblem('Oyunum çğ')).toBeNull()
    expect(nameProblem('a.b')).toBe('chars')
    expect(nameProblem('a ')).toBe('spaces')
    expect(nameProblem('x'.repeat(64))).toBeNull()
    expect(nameProblem('x'.repeat(65))).toBe('long')
  })

  it('name templates, join paths in the parent style and format the clock', () => {
    expect(templateName({ id: 'com.unity.template.universal-2d-17.0.2', label: 'other', file: '' })).toBe('URP 2D')
    expect(templateName({ id: 'com.unity.template.hdrp-blank-17.0.2', label: 'hdrp', file: '' })).toBe('HDRP')
    expect(templateName({ id: 'com.unity.template.mobile-3d-17.0.2', label: 'other', file: '' })).toBe('Mobile 3D')
    expect(joinTarget('C:\\Games\\', 'Run')).toBe('C:\\Games\\Run')
    expect(joinTarget('/Users/me/Games', 'Run')).toBe('/Users/me/Games/Run')
    expect(clock(105_900)).toBe('01:45')
  })
})
