import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, act, cleanup, waitFor } from '@testing-library/react'

const mocks = vi.hoisted(() => {
  const invoke = vi.fn()
  ;(globalThis as any).window.ipc = { invoke, registerDroppedFolder: vi.fn(), on: vi.fn(() => () => {}) }
  return { invoke }
})
vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), defaults: { headers: { common: {} } } } }))

import axios from 'axios'
import { WorkspaceScreen } from '../renderer/components/home/WorkspaceScreen'
import { LangContext, ceviriUygula } from '../renderer/lib/i18n'
import { resetHubStore, getRun, startCreate } from '../renderer/lib/hub'

const ipc = async (channel: string, arg: any) => {
  if (channel === 'backend-workspace-path') return arg.replace('/h/', '/b/')
  if (channel === 'host-workspace-path') return arg.replace('/b/', '/h/')
  if (channel === 'workspace-info') return arg.map((path: string) => ({ path, status: 'ok' }))
  if (channel === 'hub-list-editors') return { editors: [{ version: '6000.2.4f1', path: '/u' }] }
  if (channel === 'hub-list-templates') return { templates: [] }
  if (channel === 'hub-create-project') return { ok: true, path: '/h/Night Run' }
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
const recentCalls = () => vi.mocked(axios.get).mock.calls.filter(c => String(c[0]).includes('/recent-workspaces/'))

beforeEach(() => {
  resetHubStore()
  mocks.invoke.mockReset().mockImplementation(ipc)
  vi.mocked(axios.get).mockReset().mockResolvedValue({ data: { workspaces: [] } })
  vi.mocked(axios.post).mockReset().mockResolvedValue({ data: {} })
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('a created project reaches the recents list', () => {
  it('saves the backend path once when the run is done, then reloads the list', async () => {
    view()
    await waitFor(() => expect(recentCalls().length).toBe(1))
    await act(async () => { await startCreate({ parentDir: '/h', name: 'Night Run', version: '6000.2.4f1' }) })
    expect(getRun().state).toBe('done')
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1))
    expect(axios.post).toHaveBeenCalledWith('http://backend/save-workspace', { user_id: 7, path: '/b/Night Run' }, { headers: { 'X-Session-Token': 'tok' } })
    await waitFor(() => expect(recentCalls().length).toBe(2))
    // An unrelated re-render of the same done run saves nothing more.
    cleanup()
    view()
    await waitFor(() => expect(recentCalls().length).toBeGreaterThan(2))
    expect(axios.post).toHaveBeenCalledTimes(1)
  })

  it('a failed save is logged and does not toast', async () => {
    const err = vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.mocked(axios.post).mockRejectedValue(new Error('down'))
    const p = view()
    await act(async () => { await startCreate({ parentDir: '/h', name: 'Night Run', version: '6000.2.4f1' }) })
    await waitFor(() => expect(err).toHaveBeenCalled())
    expect(p.showToast).not.toHaveBeenCalled()
    expect(getRun().state).toBe('done')
  })
})
