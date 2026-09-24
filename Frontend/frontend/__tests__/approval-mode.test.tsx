/**
 * Global approval mode in the UI (closed-loop.md §5).
 *
 * The mode lives in the backend; the renderer reads it with the normal token
 * and writes it only through Electron main ('approval-mode-set'), never to
 * localStorage. The old localStorage value is migrated once.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, fireEvent, renderHook, act, waitFor } from '@testing-library/react'

const ipcInvoke = vi.hoisted(() => {
  const invoke = vi.fn()
  ;(globalThis as any).window.ipc = { invoke }
  return invoke
})

vi.mock('axios', () => {
  const get = vi.fn()
  const post = vi.fn()
  return { default: { get, post, delete: vi.fn(), put: vi.fn() }, get, post }
})

import axios from 'axios'
import { SettingsModal } from '../renderer/components/home/SettingsModal'
import { useChat } from '../renderer/hooks/home/useChat'
import { cevir } from '../renderer/lib/i18n'

const mockedGet = (axios as any).get as ReturnType<typeof vi.fn>
const KEY = 'unityai-generation-mode'

afterEach(() => { cleanup() })

describe('SettingsModal · Çalışma modu sekmesi', () => {
  const props: any = {
    open: true,
    aiConfig: { provider_type: 'anthropic', model_name: '', api_key: '' },
    availableModels: { local: [], subscription: [], cloud: [] },
    providersWithKeys: [],
    onChange: () => {},
    onClose: () => {},
    onSave: async () => {},
    onLogout: () => {},
    onDeleteKey: async () => {},
    unityMcpStatus: 'off',
    unityMcpToggling: false,
    onToggleUnityMcp: () => {},
    lang: 'tr',
    onLangChange: () => {},
  }

  it('existing fields stay on the first tab', () => {
    render(<SettingsModal {...props} approvalMode="step" onApprovalModeChange={vi.fn()} />)
    expect(screen.getByText(cevir('settings.provider'))).toBeTruthy()
    expect(screen.getByText('Unity MCP')).toBeTruthy()
    expect(screen.getByText(cevir('settings.language'))).toBeTruthy()
  })

  it('mode tab explains auto and writes through the handler', () => {
    const onChange = vi.fn()
    render(<SettingsModal {...props} approvalMode="step" onApprovalModeChange={onChange} />)
    fireEvent.click(screen.getByRole('tab', { name: cevir('settings.tabMode') }))
    expect(screen.getByText(cevir('settings.modeAutoExplain'))).toBeTruthy()
    fireEvent.click(screen.getByText(cevir('mode.auto')))
    expect(onChange).toHaveBeenCalledWith('auto')
  })
})

const hook = (showToast = vi.fn()) => renderHook(() => useChat(
  'http://127.0.0.1:8000',
  { id: 1, name: 'b', sessionToken: 'tok' } as any,
  { provider_type: 'anthropic', model_name: 'm' } as any,
  '/ws',
  showToast,
  vi.fn(),
  (n: string) => n,
))

describe('useChat · backend-owned mode', () => {
  beforeEach(() => {
    ipcInvoke.mockReset()
    mockedGet.mockReset()
    window.localStorage.clear()
  })

  it('reads the stored backend mode and drops the legacy key', async () => {
    window.localStorage.setItem(KEY, 'step')
    mockedGet.mockResolvedValue({ data: { mode: 'auto', stored: true } })
    const { result } = hook()
    await waitFor(() => expect(result.current.generationMode).toBe('auto'))
    expect(mockedGet.mock.calls[0][0]).toContain('/approval-mode')
    expect(ipcInvoke).not.toHaveBeenCalled()
    expect(window.localStorage.getItem(KEY)).toBeNull()
  })

  it('migrates the old localStorage value once when the backend has none', async () => {
    window.localStorage.setItem(KEY, 'auto')
    mockedGet.mockResolvedValue({ data: { mode: 'step', stored: false } })
    ipcInvoke.mockResolvedValue({ mode: 'auto', previous: 'step' })
    const { result } = hook()
    await waitFor(() => expect(result.current.generationMode).toBe('auto'))
    expect(ipcInvoke).toHaveBeenCalledWith('approval-mode-set', 'auto', 'migrate')
    expect(window.localStorage.getItem(KEY)).toBeNull()
  })

  it('defaults to step with nothing stored anywhere', async () => {
    mockedGet.mockResolvedValue({ data: { mode: 'step', stored: false } })
    const { result } = hook()
    await waitFor(() => expect(mockedGet).toHaveBeenCalled())
    expect(result.current.generationMode).toBe('step')
    expect(ipcInvoke).not.toHaveBeenCalled()
  })

  it('the selector writes through Electron main, not localStorage', async () => {
    mockedGet.mockResolvedValue({ data: { mode: 'step', stored: true } })
    ipcInvoke.mockResolvedValue({ mode: 'auto', previous: 'step' })
    const { result } = hook()
    await waitFor(() => expect(mockedGet).toHaveBeenCalled())
    await act(async () => { await result.current.setGenerationMode('auto') })
    expect(ipcInvoke).toHaveBeenCalledWith('approval-mode-set', 'auto', 'chat')
    expect(result.current.generationMode).toBe('auto')
    expect(window.localStorage.getItem(KEY)).toBeNull()
  })

  it('a refused write keeps the old mode and says so', async () => {
    mockedGet.mockResolvedValue({ data: { mode: 'step', stored: true } })
    ipcInvoke.mockRejectedValue(new Error('reddedildi'))
    const showToast = vi.fn()
    const { result } = hook(showToast)
    await waitFor(() => expect(mockedGet).toHaveBeenCalled())
    await act(async () => { await result.current.setGenerationMode('auto') })
    expect(result.current.generationMode).toBe('step')
    expect(showToast).toHaveBeenCalledWith(expect.stringContaining('reddedildi'), 'error')
  })
})
