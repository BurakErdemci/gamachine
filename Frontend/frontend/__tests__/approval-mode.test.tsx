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
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

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
import { GenerationModeSelector } from '../renderer/components/home/GenerationModeSelector'
import { useChat } from '../renderer/hooks/home/useChat'
import { cevir, aktifDilAyarla, translations } from '../renderer/lib/i18n'

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
    fireEvent.click(screen.getByText(cevir('settings.modeAutoTitle')))
    expect(onChange).toHaveBeenCalledWith('auto')
  })

  it('the auto card is red whether or not it is selected; step is not', () => {
    for (const mode of ['auto', 'step'] as const) {
      render(<SettingsModal {...props} approvalMode={mode} onApprovalModeChange={vi.fn()} />)
      fireEvent.click(screen.getByRole('tab', { name: cevir('settings.tabMode') }))
      const autoTitle = screen.getByText(cevir('settings.modeAutoTitle'))
      const autoCard = autoTitle.closest('button')!
      const stepCard = screen.getByText(cevir('settings.modeStepTitle')).closest('button')!
      expect(autoTitle.className).toContain('text-red-400')
      expect(autoCard.className).toContain('border-l-red-500')
      expect(stepCard.className).not.toContain('red')
      expect(screen.getByText(cevir('settings.modeStepExplain'))).toBeTruthy()
      cleanup()
    }
  })

  it('the new mode-card wording is in both languages, without the removed trust line', () => {
    aktifDilAyarla('tr')
    expect(cevir('settings.modeAutoTitle')).toBe('⚠ OTO MOD — ONAY YOK')
    expect(cevir('settings.modeStepTitle')).toBe('✋ Adım adım')
    expect(cevir('settings.modeAutoExplain')).toContain('izinleri atla')
    aktifDilAyarla('en')
    try {
      expect(cevir('settings.modeAutoTitle')).toBe('⚠ AUTO MODE — NO APPROVALS')
      expect(cevir('settings.modeStepTitle')).toBe('✋ Step by step')
      expect(cevir('settings.modeAutoExplain')).toContain('bypass permissions')
    } finally {
      aktifDilAyarla('tr')
    }
    for (const lang of ['tr', 'en'] as const) {
      expect(translations[lang]['settings.modeAutoExplain']).not.toMatch(/güvendiğin|trusted/i)
    }
  })
})

describe('Auto-mode warning lives on the chat mode selector', () => {
  it('auto shows the red tone, a red dot and the warning tooltip', () => {
    render(<GenerationModeSelector value="auto" onChange={vi.fn()} />)
    const button = screen.getByText(cevir('mode.auto')).closest('button')!
    expect(button.getAttribute('title')).toBe(cevir('mode.autoWarning'))
    expect(button.className).toContain('text-red-400')
    expect(screen.getByTestId('auto-mode-dot')).toBeTruthy()
  })

  it('step keeps its plain look', () => {
    render(<GenerationModeSelector value="step" onChange={vi.fn()} />)
    const button = screen.getByText(cevir('mode.step')).closest('button')!
    expect(button.getAttribute('title')).toBeNull()
    expect(button.className).not.toContain('red')
    expect(screen.queryByTestId('auto-mode-dot')).toBeNull()
  })

  it('the tooltip wording exists in both languages', () => {
    expect(translations.tr['mode.autoWarning']).toBe('Oto mod — onay kartı yok')
    expect(translations.en['mode.autoWarning']).toBe('Auto mode — no approval cards')
  })

  it('the header badge is gone', () => {
    const home = readFileSync(resolve(__dirname, '../renderer/pages/home.tsx'), 'utf8')
    expect(home).not.toContain('mode.indicator')
    expect(home).not.toContain('mode.autoWarning')
    for (const lang of ['tr', 'en'] as const) {
      expect(translations[lang]['mode.indicator']).toBeUndefined()
      expect(translations[lang]['mode.indicatorTitle']).toBeUndefined()
    }
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

  it('migrates the old localStorage value once, over the fresh-install auto', async () => {
    window.localStorage.setItem(KEY, 'step')
    mockedGet.mockResolvedValue({ data: { mode: 'auto', stored: false } })
    ipcInvoke.mockResolvedValue({ mode: 'step', previous: 'auto' })
    const { result } = hook()
    await waitFor(() => expect(ipcInvoke).toHaveBeenCalledWith('approval-mode-set', 'step', 'migrate'))
    await waitFor(() => expect(window.localStorage.getItem(KEY)).toBeNull())
    expect(result.current.generationMode).toBe('step')
  })

  it('a fresh install shows the backend auto and writes nothing', async () => {
    mockedGet.mockResolvedValue({ data: { mode: 'auto', stored: false } })
    const { result } = hook()
    await waitFor(() => expect(result.current.generationMode).toBe('auto'))
    expect(ipcInvoke).not.toHaveBeenCalled()
    expect(window.localStorage.getItem(KEY)).toBeNull()
  })

  it('an invalid old value is not migrated', async () => {
    window.localStorage.setItem(KEY, 'plan')
    mockedGet.mockResolvedValue({ data: { mode: 'auto', stored: false } })
    const { result } = hook()
    await waitFor(() => expect(result.current.generationMode).toBe('auto'))
    expect(ipcInvoke).not.toHaveBeenCalled()
  })

  it('shows step and writes nothing until the backend answers', async () => {
    mockedGet.mockReturnValue(new Promise(() => {}))
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
