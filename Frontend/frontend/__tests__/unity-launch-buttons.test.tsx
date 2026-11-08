/**
 * The two visible "launch Unity" buttons: the project picker card and the Unity switch inside an
 * opened project. They share one rule: never offer a launch for a project whose editor is already
 * open (or cannot be started from this page), and never let a second click spawn a second editor.
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act, cleanup, waitFor, renderHook } from '@testing-library/react'

const mocks = vi.hoisted(() => {
  const invoke = vi.fn()
  ;(globalThis as any).window.ipc = { invoke, registerDroppedFolder: vi.fn(), on: vi.fn(() => () => {}) }
  return { invoke, remote: { value: false } }
})
vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), defaults: { headers: { common: {} } } } }))
vi.mock('../renderer/lib/remote/transport', async importOriginal => ({
  ...(await importOriginal<typeof import('../renderer/lib/remote/transport')>()),
  isRemotePage: () => mocks.remote.value,
}))

import axios from 'axios'
import { WorkspaceScreen } from '../renderer/components/home/WorkspaceScreen'
import { UnityMcpToggle } from '../renderer/components/home/UnityMcpToggle'
import { useAIConfig } from '../renderer/hooks/home/useAIConfig'
import { LangContext, ceviriUygula, cevir, translations } from '../renderer/lib/i18n'
import { resetHubStore } from '../renderer/lib/hub'

const en = translations.en
const EDITORS = [{ version: '6000.2.4f1', path: '/Unity/6000.2.4f1/Unity' }]

let info: (path: string) => { path: string; status: string; unityVersion: string | null }
const ipc = async (channel: string, arg: any) => {
  if (channel === 'host-workspace-path') return arg.replace('/b/', '/h/')
  if (channel === 'backend-workspace-path') return arg.replace('/h/', '/b/')
  if (channel === 'workspace-info') return arg.map((path: string) => info(path))
  if (channel === 'hub-list-editors') return { editors: EDITORS }
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

beforeEach(() => {
  resetHubStore()
  mocks.remote.value = false
  info = path => ({ path, status: 'ok', unityVersion: '6000.2.4f1' })
  mocks.invoke.mockReset().mockImplementation(ipc)
  vi.mocked(axios.get).mockReset().mockResolvedValue({ data: { workspaces: [{ path: '/b/Arena', last_accessed: '2026-10-29 10:00:00', chat_count: 2 }] } })
})
afterEach(() => { cleanup(); vi.useRealTimers() })

describe('project card launch button', () => {
  it('launches the card in its version and does not open the project in Gamachine', async () => {
    const p = view()
    fireEvent.click(await screen.findByTestId('welcome-launch'))
    await waitFor(() => expect(mocks.invoke).toHaveBeenCalledWith('hub-open-project', { path: '/h/Arena', version: '6000.2.4f1' }))
    await waitFor(() => expect(p.showToast).toHaveBeenCalledWith(en['hub.launched'], 'success'))
    expect(p.onSelectWorkspace).not.toHaveBeenCalled()
  })

  it('is hidden for a missing project', async () => {
    info = path => ({ path, status: 'missing', unityVersion: null })
    view()
    await screen.findByTestId('welcome-card')
    expect(screen.queryByTestId('welcome-launch')).toBeNull()
  })

  it("is hidden when the card's Unity version is not installed", async () => {
    info = path => ({ path, status: 'ok', unityVersion: '2019.4.1f1' })
    view()
    await waitFor(() => expect(screen.getByTestId('welcome-card').textContent).toContain(en['welcome.versionMissing']))
    expect(screen.queryByTestId('welcome-launch')).toBeNull()
  })

  it('is hidden on a remote page, which cannot spawn Unity on the host', async () => {
    mocks.remote.value = true
    view()
    await screen.findByTestId('welcome-card')
    expect(screen.queryByTestId('welcome-launch')).toBeNull()
  })
})

describe('Unity switch launch button', () => {
  const toggle = (extra: Partial<React.ComponentProps<typeof UnityMcpToggle>> = {}) => (
    <UnityMcpToggle status="off" toggling={false} reason={null} error={null} onToggle={vi.fn()} {...extra} />
  )
  const launchBtn = () => screen.queryByRole('button', { name: cevir('unity.launch') })

  it('shows when the editor is closed and the switch is off', () => {
    render(toggle({ editorOpen: false, onLaunchUnity: vi.fn() }))
    expect(launchBtn()).toBeTruthy()
  })

  it.each([
    ['the editor is open', { editorOpen: true }],
    ['the editor state is unknown', { editorOpen: null }],
    ['it is connected', { editorOpen: false, status: 'connected' as const }],
  ])('is hidden when %s', (_name, extra) => {
    render(toggle({ onLaunchUnity: vi.fn(), ...extra }))
    expect(launchBtn()).toBeNull()
  })

  it('is hidden where no launch handler is given', () => {
    render(toggle({ editorOpen: false }))
    expect(launchBtn()).toBeNull()
  })

  it('is disabled while the launch is pending and hidden once it resolved', async () => {
    let finish!: () => void
    const onLaunchUnity = vi.fn(() => new Promise<void>(r => { finish = r }))
    render(toggle({ editorOpen: false, onLaunchUnity }))
    fireEvent.click(launchBtn()!)
    expect((launchBtn() as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(launchBtn()!)
    expect(onLaunchUnity).toHaveBeenCalledTimes(1)
    await act(async () => { finish() })
    expect(launchBtn()).toBeNull()
  })

  it('comes back after 60 s if the editor never showed up, and not before', async () => {
    vi.useFakeTimers()
    render(toggle({ editorOpen: false, onLaunchUnity: vi.fn(async () => {}) }))
    await act(async () => { fireEvent.click(launchBtn()!) })
    expect(launchBtn()).toBeNull()
    act(() => { vi.advanceTimersByTime(59000) })
    expect(launchBtn()).toBeNull()
    act(() => { vi.advanceTimersByTime(1500) })
    expect(launchBtn()).toBeTruthy()
  })

  it('comes back at once when the editor opens and closes again', async () => {
    const onLaunchUnity = vi.fn(async () => {})
    const { rerender } = render(toggle({ editorOpen: false, onLaunchUnity }))
    await act(async () => { fireEvent.click(launchBtn()!) })
    expect(launchBtn()).toBeNull()
    rerender(toggle({ editorOpen: true, onLaunchUnity }))
    rerender(toggle({ editorOpen: false, onLaunchUnity }))
    expect(launchBtn()).toBeTruthy()
  })

  it('stays available after a launch that threw', async () => {
    render(toggle({ editorOpen: false, onLaunchUnity: vi.fn(async () => { throw new Error('boom') }) }))
    await act(async () => { fireEvent.click(launchBtn()!) })
    expect(launchBtn()).toBeTruthy()
    expect((launchBtn() as HTMLButtonElement).disabled).toBe(false)
  })

  it('stays available after a launch Unity refused, so the user can retry', async () => {
    render(toggle({ editorOpen: false, onLaunchUnity: vi.fn(async () => false) }))
    await act(async () => { fireEvent.click(launchBtn()!) })
    expect(launchBtn()).toBeTruthy()
    expect((launchBtn() as HTMLButtonElement).disabled).toBe(false)
  })
})

describe('useAIConfig editor_open', () => {
  const API = 'http://127.0.0.1:8000'
  const mount = (workspace?: string) => renderHook(() => useAIConfig(API, null, vi.fn(), workspace))
  const statusCall = () => vi.mocked(axios.get).mock.calls.find(c => String(c[0]).endsWith('/mcp/unity/status'))

  it('sends the backend-side workspace and maps editor_open', async () => {
    for (const open of [true, false]) {
      cleanup()
      vi.mocked(axios.get).mockReset().mockResolvedValue({ data: { status: 'off', editor_open: open } })
      const { result } = mount('/h/Arena')
      await waitFor(() => expect(result.current.unityEditorOpen).toBe(open))
      expect(statusCall()![1]).toEqual({ params: { workspace: '/b/Arena' } })
    }
  })

  it('is unknown when the response has no editor_open', async () => {
    vi.mocked(axios.get).mockReset().mockResolvedValue({ data: { status: 'off' } })
    const { result } = mount('/h/Arena')
    await waitFor(() => expect(statusCall()).toBeTruthy())
    await act(async () => {})
    expect(result.current.unityEditorOpen).toBeNull()
  })

  it('sends no workspace and stays unknown without a project', async () => {
    vi.mocked(axios.get).mockReset().mockResolvedValue({ data: { status: 'off', editor_open: true } })
    const { result } = mount(undefined)
    await waitFor(() => expect(statusCall()).toBeTruthy())
    await act(async () => {})
    expect(statusCall()![1]).toBeUndefined()
    expect(result.current.unityEditorOpen).toBeNull()
  })

  it('goes back to unknown when the poll fails', async () => {
    vi.mocked(axios.get).mockReset().mockResolvedValueOnce({ data: { status: 'off', editor_open: false } }).mockRejectedValue(new Error('down'))
    const { result } = mount('/h/Arena')
    await waitFor(() => expect(result.current.unityEditorOpen).toBe(false))
    await act(async () => { await result.current.refreshUnityMcpStatus() })
    expect(result.current.unityEditorOpen).toBeNull()
  })
})
