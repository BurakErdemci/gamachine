/**
 * "Detect language automatically" for dictation on a CPU-only machine
 * (useDictationSettings.ts). Mirrors auto-chat-titles.test.tsx's settings-toggle
 * half: same shape (GET on mount, optimistic POST toggle, rollback + toast on
 * failure), same mock-axios approach, just a different endpoint and prop names.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, renderHook, act, fireEvent } from '@testing-library/react'

vi.mock('axios', () => {
  const post = vi.fn()
  const get = vi.fn()
  return { default: { post, get }, post, get }
})

import axios from 'axios'
import { SettingsScreen } from '../renderer/components/home/settings/SettingsScreen'
import { useDictationSettings } from '../renderer/hooks/home/useDictationSettings'
import { cevir } from '../renderer/lib/i18n'

const mocked = axios as unknown as { get: ReturnType<typeof vi.fn>; post: ReturnType<typeof vi.fn> }
const API = 'http://127.0.0.1:8000'

afterEach(() => cleanup())

const temel: any = {
  open: true,
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
  aiConfig: { provider_type: 'subscription', model_name: 'claude-opus-5', api_key: '' },
  availableModels: { local: [], subscription: [], cloud: [] },
}

describe('settings · dictation auto-language toggle', () => {
  it('shows its state as a switch and reports clicks', () => {
    const onToggle = vi.fn()
    const { rerender } = render(
      <SettingsScreen {...temel} dictationAutoLang onToggleDictationAutoLang={onToggle} />,
    )
    const sw = screen.getByTestId('dictation-auto-lang-toggle')
    expect(sw.getAttribute('role')).toBe('switch')
    expect(sw.getAttribute('aria-checked')).toBe('true')
    expect(screen.getByText(cevir('settings.dictationAutoLang'))).toBeTruthy()
    fireEvent.click(sw)
    expect(onToggle).toHaveBeenCalledTimes(1)
    rerender(<SettingsScreen {...temel} dictationAutoLang={false} onToggleDictationAutoLang={onToggle} />)
    expect(screen.getByTestId('dictation-auto-lang-toggle').getAttribute('aria-checked')).toBe('false')
  })

  it('is not rendered at all without a handler', () => {
    render(<SettingsScreen {...temel} />)
    expect(screen.queryByTestId('dictation-auto-lang-toggle')).toBeNull()
  })

  it('is disabled while the change is being saved', () => {
    render(<SettingsScreen {...temel} dictationAutoLang dictationAutoLangSaving onToggleDictationAutoLang={vi.fn()} />)
    expect((screen.getByTestId('dictation-auto-lang-toggle') as HTMLButtonElement).disabled).toBe(true)
  })
})

describe('useDictationSettings', () => {
  beforeEach(() => {
    mocked.get.mockReset()
    mocked.post.mockReset()
  })

  it('reads the stored setting and saves a toggle to the backend', async () => {
    mocked.get.mockResolvedValue({ data: { auto_language_cpu: false } })
    mocked.post.mockResolvedValue({ data: { auto_language_cpu: true } })
    const { result } = renderHook(() => useDictationSettings(API, 1, vi.fn()))
    await act(async () => { await Promise.resolve() })
    expect(mocked.get).toHaveBeenCalledWith(`${API}/transcribe/settings`)
    expect(result.current.autoLanguageCpu).toBe(false)

    await act(async () => { await result.current.toggleAutoLanguageCpu() })
    expect(mocked.post).toHaveBeenCalledWith(`${API}/transcribe/settings`, { auto_language_cpu: true })
    expect(result.current.autoLanguageCpu).toBe(true)
  })

  it('defaults to off and rolls back with a toast when saving fails', async () => {
    mocked.get.mockRejectedValue(new Error('offline'))
    mocked.post.mockRejectedValue(new Error('offline'))
    const toast = vi.fn()
    const { result } = renderHook(() => useDictationSettings(API, 1, toast))
    await act(async () => { await Promise.resolve() })
    expect(result.current.autoLanguageCpu).toBe(false)

    await act(async () => { await result.current.toggleAutoLanguageCpu() })
    // Optimistic flip, then rolled back once the POST rejects.
    expect(result.current.autoLanguageCpu).toBe(false)
    expect(toast).toHaveBeenCalledWith(cevir('settings.dictationAutoLangFailed'), 'error')
  })

  it('does not read the backend before a user id is known', async () => {
    renderHook(() => useDictationSettings(API, null, vi.fn()))
    await act(async () => { await Promise.resolve() })
    expect(mocked.get).not.toHaveBeenCalled()
  })

  it('a toggle made before the GET answers is not undone by that answer landing late', async () => {
    let resolveGet: (v: any) => void = () => {}
    mocked.get.mockImplementation(() => new Promise(r => { resolveGet = r }))
    mocked.post.mockResolvedValue({ data: {} })
    const { result } = renderHook(() => useDictationSettings(API, 1, vi.fn()))

    await act(async () => { await result.current.toggleAutoLanguageCpu() })
    expect(result.current.autoLanguageCpu).toBe(true)

    // The GET that started before the toggle now answers with the stale value.
    await act(async () => { resolveGet({ data: { auto_language_cpu: false } }); await Promise.resolve() })
    expect(result.current.autoLanguageCpu).toBe(true)
  })
})
