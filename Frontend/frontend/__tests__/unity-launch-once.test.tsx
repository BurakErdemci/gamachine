/**
 * A press on a Launch Unity button never asks main for a second editor, and the editor-open state of
 * one project never leaks onto another.
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act, cleanup, waitFor, renderHook } from '@testing-library/react'

const mocks = vi.hoisted(() => {
  const invoke = vi.fn()
  ;(globalThis as any).window.ipc = { invoke, registerDroppedFolder: vi.fn(), on: vi.fn(() => () => {}) }
  return { invoke }
})
vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), defaults: { headers: { common: {} } } } }))

import axios from 'axios'
import { WorkspaceScreen } from '../renderer/components/home/WorkspaceScreen'
import { useAIConfig } from '../renderer/hooks/home/useAIConfig'
import { LangContext, ceviriUygula, translations } from '../renderer/lib/i18n'
import { resetHubStore } from '../renderer/lib/hub'

const EDITORS = [{ version: '6000.2.4f1', path: '/Unity/6000.2.4f1/Unity' }]
let openAnswers: any[]
const ipc = async (channel: string, arg: any) => {
  if (channel === 'host-workspace-path') return arg.replace('/b/', '/h/')
  if (channel === 'backend-workspace-path') return arg.replace('/h/', '/b/')
  if (channel === 'workspace-info') return arg.map((path: string) => ({ path, status: 'ok', unityVersion: '6000.2.4f1' }))
  if (channel === 'hub-list-editors') return { editors: EDITORS }
  if (channel === 'hub-open-project') return openAnswers.shift() ?? { opened: true }
  return null
}

beforeEach(() => {
  resetHubStore()
  openAnswers = []
  mocks.invoke.mockReset().mockImplementation(ipc)
  vi.mocked(axios.get).mockReset().mockResolvedValue({ data: { workspaces: [{ path: '/b/Arena', last_accessed: '2026-10-29 10:00:00', chat_count: 2 }] } })
})
afterEach(() => { cleanup() })

const launches = () => mocks.invoke.mock.calls.filter(c => c[0] === 'hub-open-project').length

describe('card button', () => {
  const show = () => {
    const showToast = vi.fn()
    const p = { api: 'http://backend', user: { id: 7, name: 'B', sessionToken: 'tok' }, userName: 'B',
      onOpenFolder: vi.fn(async () => null), onSelectWorkspace: vi.fn(async () => true), onLogout: vi.fn(), showToast }
    const value = { lang: 'en' as const, setLang: () => {}, t: (k: any, v?: any) => ceviriUygula('en', k, v) }
    render(<LangContext.Provider value={value}><WorkspaceScreen {...(p as any)} /></LangContext.Provider>)
    return showToast
  }

  it('two quick clicks reach main once', async () => {
    show()
    const b = await screen.findByTestId('welcome-launch')
    fireEvent.click(b); fireEvent.click(b)
    await waitFor(() => expect(launches()).toBeGreaterThan(0))
    await act(async () => {})
    expect(launches()).toBe(1)
  })

  it('shows the launching toast once for two quick clicks, before the result', async () => {
    const showToast = show()
    const b = await screen.findByTestId('welcome-launch')
    fireEvent.click(b); fireEvent.click(b)
    await waitFor(() => expect(showToast).toHaveBeenCalledWith(translations.en['hub.launched'], 'success'))
    const launching = showToast.mock.calls.filter(c => c[0] === translations.en['hub.launching'])
    expect(launching).toEqual([[translations.en['hub.launching'], 'info']])
    expect(showToast.mock.calls[0][0]).toBe(translations.en['hub.launching'])
  })

  it('tells the user when main finds the editor already open', async () => {
    openAnswers = [{ opened: true, alreadyOpen: true }]
    const showToast = show()
    fireEvent.click(await screen.findByTestId('welcome-launch'))
    await waitFor(() => expect(showToast).toHaveBeenCalledWith(translations.en['hub.alreadyOpen'], 'info'))
    expect(showToast).not.toHaveBeenCalledWith(translations.en['hub.launched'], 'success')
  })
})

describe('useAIConfig editor state across a workspace switch', () => {
  const API = 'http://127.0.0.1:8000'

  it('resets unityEditorOpen to null at once', async () => {
    vi.mocked(axios.get).mockReset().mockResolvedValue({ data: { status: 'off', editor_open: false } })
    let ws = '/h/A'
    const { result, rerender } = renderHook(() => useAIConfig(API, null, vi.fn(), ws))
    await waitFor(() => expect(result.current.unityEditorOpen).toBe(false))
    ws = '/h/B'
    rerender()
    expect(result.current.unityEditorOpen).toBeNull()
  })

  it('ignores a late answer sent for the previous workspace', async () => {
    let release!: (v: any) => void
    vi.mocked(axios.get).mockReset().mockImplementationOnce(() => new Promise(r => { release = r }))
    let ws = '/h/A'
    const { result, rerender } = renderHook(() => useAIConfig(API, null, vi.fn(), ws))
    await waitFor(() => expect(release).toBeTypeOf('function'))
    ws = '/h/B'; rerender()
    await act(async () => { release({ data: { status: 'off', editor_open: false } }) })
    expect(result.current.unityEditorOpen).toBeNull()
  })
})
