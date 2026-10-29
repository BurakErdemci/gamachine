import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react'

const mocks = vi.hoisted(() => {
  const invoke = vi.fn()
  ;(globalThis as any).window.ipc = { invoke, registerDroppedFolder: vi.fn(), on: vi.fn(() => () => {}) }
  return { invoke }
})
vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), defaults: { headers: { common: {} } } } }))

import axios from 'axios'
import { WorkspaceScreen } from '../renderer/components/home/WorkspaceScreen'
import { LangContext, ceviriUygula } from '../renderer/lib/i18n'

let authorize: () => Promise<any>
const ipc = async (channel: string, arg: any) => {
  if (channel === 'host-workspace-path') return arg.replace('/b/', '/h/')
  if (channel === 'workspace-info') return arg.map((path: string) => ({ path, status: 'untrusted', unityVersion: null }))
  if (channel === 'authorize-workspace') return authorize()
  return null
}

const props = () => ({
  api: 'http://backend', user: { id: 7, name: 'Burak', sessionToken: 'tok' }, userName: 'Burak',
  onOpenFolder: vi.fn(async (_p?: string) => null as string | null), onSelectWorkspace: vi.fn(async () => {}),
  onLogout: vi.fn(), showToast: vi.fn(),
})

const open = async (p = props()) => {
  const value = { lang: 'en' as const, setLang: () => {}, t: (k: any, v?: any) => ceviriUygula('en', k, v) }
  render(<LangContext.Provider value={value}><WorkspaceScreen {...p} /></LangContext.Provider>)
  fireEvent.click(await screen.findByRole('button', { name: 'Open Game' }))
  return p
}

beforeEach(() => {
  mocks.invoke.mockReset().mockImplementation(ipc)
  vi.mocked(axios.get).mockReset().mockResolvedValue({
    data: { workspaces: [{ path: '/b/Game', last_accessed: '2026-10-29 10:00:00', chat_count: 0 }] },
  })
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('untrusted recent project click', () => {
  it('opens the project when the native box is approved', async () => {
    authorize = async () => ({ trusted: true })
    const p = await open()
    await waitFor(() => expect(p.onSelectWorkspace).toHaveBeenCalledWith('/h/Game'))
    expect(mocks.invoke).toHaveBeenCalledWith('authorize-workspace', '/h/Game')
    expect(p.onOpenFolder).not.toHaveBeenCalled()
  })

  it.each([
    ['denied', async () => ({ trusted: false })],
    ['rejected', async () => { throw new Error('boom') }],
  ])('does nothing when %s', async (_name, fn) => {
    authorize = fn as any
    const p = await open()
    await waitFor(() => expect(mocks.invoke).toHaveBeenCalledWith('authorize-workspace', '/h/Game'))
    await new Promise(r => setTimeout(r, 20))
    expect(p.onSelectWorkspace).not.toHaveBeenCalled()
    expect(p.onOpenFolder).not.toHaveBeenCalled()
    expect(p.showToast).not.toHaveBeenCalled()
  })
})
