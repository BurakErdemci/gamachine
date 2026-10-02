import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, fireEvent, render, renderHook, screen } from '@testing-library/react'

vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }))
vi.mock('../renderer/components/ui/ConfirmDialog', () => ({ confirmDialog: vi.fn() }))

import axios from 'axios'
import { confirmDialog } from '../renderer/components/ui/ConfirmDialog'
import { SettingsScreen, type SettingsScreenProps } from '../renderer/components/home/settings/SettingsScreen'
import { ChipUse } from '../renderer/components/home/UsageMeters'
import { useUsageLimits, resetUsageLimitsCache } from '../renderer/hooks/home/useUsageLimits'
import { useAutoChatTitles } from '../renderer/hooks/home/useAutoChatTitles'
import { useDictationSettings } from '../renderer/hooks/home/useDictationSettings'
import { useAIConfig } from '../renderer/hooks/home/useAIConfig'
import { aktifDilAyarla, cevir } from '../renderer/lib/i18n'
import { resetLabel, type UsageFamily, type UsageLimits } from '../renderer/lib/usageLimits'

const get = vi.mocked(axios.get)
const post = vi.mocked(axios.post)
const confirm = vi.mocked(confirmDialog)
const API = 'http://backend'
const snapshot = (over: Partial<UsageFamily> = {}): UsageLimits => ({
  now: '2026-10-02T10:00:00Z',
  families: [{ family: 'claude', status: 'ok', stale: false, plan: null, measured_at: null,
    error: null, windows: [{ id: 'five', group: null, label: '5h', kind: '5h', used_pct: 42,
      resets_at: null, resets_text: null }], ...over }],
})
const props = (over: Partial<SettingsScreenProps> = {}): SettingsScreenProps => ({
  open: true, page: 'modeller', aiConfig: { provider_type: 'subscription', model_name: 'claude-sonnet-5', api_key: '', thinking_level: 'medium' },
  availableModels: { local: [], subscription: [], cloud: [{ id: 'gpt-9', name: 'GPT-9', provider: 'openai' }] },
  providersWithKeys: ['anthropic'], onClose: vi.fn(), onLogout: vi.fn(),
  onDeleteKey: vi.fn(async () => true), unityMcpStatus: 'off', unityMcpToggling: false,
  onToggleUnityMcp: vi.fn(async () => true), lang: 'tr', onLangChange: vi.fn(), ...over,
})
const flush = async () => { await act(async () => { await Promise.resolve() }) }
const advance = async (ms: number) => { await act(async () => { vi.advanceTimersByTime(ms) }) }
const saved = () => screen.getByTestId('settings-saved').textContent

beforeEach(() => {
  vi.resetAllMocks()
  resetUsageLimitsCache()
  aktifDilAyarla('tr')
  get.mockResolvedValue({ data: snapshot() })
  confirm.mockResolvedValue(true)
})
afterEach(() => { cleanup(); vi.useRealTimers() })

describe('settings audit usage follow-ups', () => {
  it('reads loading again at 4 seconds without refresh, then stops on fresh data', async () => {
    vi.useFakeTimers()
    get.mockResolvedValueOnce({ data: snapshot({ status: 'loading', windows: [] }) })
    const { result } = renderHook(() => useUsageLimits({ api: API, token: 'tok' }))
    await flush()
    await advance(3999)
    expect(get).toHaveBeenCalledTimes(1)
    await advance(1)
    expect(get).toHaveBeenCalledTimes(2)
    expect(get.mock.calls[1][1]).toEqual({ params: undefined, headers: { 'X-Session-Token': 'tok' } })
    expect(result.current.data?.families[0].status).toBe('ok')
    await advance(30000)
    expect(get).toHaveBeenCalledTimes(2)
  })

  it('a stale chip becomes undimmed after the follow-up', async () => {
    vi.useFakeTimers()
    get.mockResolvedValueOnce({ data: snapshot({ stale: true }) })
    function Harness() {
      const usage = useUsageLimits({ api: API })
      return <ChipUse limits={usage.data} family="claude" providerName="Claude" />
    }
    render(<Harness />)
    await flush()
    expect(screen.getByTestId('chip-use').getAttribute('data-state')).toBe('stale')
    await advance(4000)
    expect(screen.getByTestId('chip-use').getAttribute('data-state')).toBe('ok')
  })

  it('persistent loading uses 4, 8, 16 and capped 30 second delays', async () => {
    vi.useFakeTimers()
    get.mockResolvedValue({ data: snapshot({ status: 'loading', windows: [] }) })
    renderHook(() => useUsageLimits({ api: API }))
    await flush()
    let calls = 1
    for (const delay of [4000, 8000, 16000, 30000, 30000]) {
      await advance(delay - 1)
      expect(get).toHaveBeenCalledTimes(calls)
      await advance(1)
      expect(get).toHaveBeenCalledTimes(++calls)
    }
  })

  it('unmount clears the timer, including an in-flight loading answer', async () => {
    vi.useFakeTimers()
    get.mockResolvedValue({ data: snapshot({ status: 'loading' }) })
    const first = renderHook(() => useUsageLimits({ api: API }))
    await flush()
    first.unmount()
    await advance(60000)
    expect(get).toHaveBeenCalledTimes(1)
    let resolve!: (value: any) => void
    get.mockImplementationOnce(() => new Promise(r => { resolve = r }))
    const second = renderHook(() => useUsageLimits({ api: API }))
    second.unmount()
    await act(async () => { resolve({ data: snapshot({ status: 'loading' }) }) })
    await advance(60000)
    expect(get).toHaveBeenCalledTimes(2)
  })

  it('a newer read cancels the old follow-up', async () => {
    vi.useFakeTimers()
    get.mockResolvedValueOnce({ data: snapshot({ stale: true }) })
    const { result } = renderHook(() => useUsageLimits({ api: API }))
    await flush()
    await advance(2000)
    await act(async () => { await result.current.reload(true) })
    await advance(4000)
    expect(get).toHaveBeenCalledTimes(2)
  })

  it('error with stale windows schedules no follow-up', async () => {
    vi.useFakeTimers()
    get.mockResolvedValue({ data: snapshot({ status: 'error', stale: true }) })
    renderHook(() => useUsageLimits({ api: API }))
    await flush()
    await advance(60000)
    expect(get).toHaveBeenCalledTimes(1)
  })

  it('opening Modeller refreshes once each time it opens', async () => {
    const p = props({ API, user: { id: 1, name: 'B', sessionToken: 'tok' } })
    const { rerender } = render(<SettingsScreen {...p} />)
    await flush()
    expect(get).toHaveBeenCalledTimes(1)
    expect(get.mock.calls[0][1]?.params).toEqual({ refresh: 1 })
    rerender(<SettingsScreen {...p} />)
    await flush()
    expect(get).toHaveBeenCalledTimes(1)
    rerender(<SettingsScreen {...p} open={false} />)
    rerender(<SettingsScreen {...p} />)
    await flush()
    expect(get).toHaveBeenCalledTimes(2)
    expect(get.mock.calls[1][1]?.params).toEqual({ refresh: 1 })
  })

  it('Modeller uses its supplied HTTP client and session token for usage', async () => {
    const http = { get: vi.fn(async (url: string) => ({ data: url.endsWith('/usage/limits') ? snapshot() : {} })), post: vi.fn() }
    render(<SettingsScreen {...props({ API, http, user: { id: 1, name: 'B', sessionToken: 'tok' } })} />)
    await flush()
    expect(http.get).toHaveBeenCalledWith(`${API}/usage/limits`, { params: { refresh: 1 }, headers: { 'X-Session-Token': 'tok' } })
    expect(get).not.toHaveBeenCalled()
  })

  it('removes bidi controls from backend reset text', () => {
    const window = snapshot().families[0].windows[0]
    expect(resetLabel({ ...window, resets_text: '\u202e3 days\u202c\u2066' }, 'en')).toBe('3 days')
  })
})

describe('settings audit save outcomes', () => {
  it('cancelled deletion makes no delete call', async () => {
    confirm.mockResolvedValue(false)
    const p = props()
    render(<SettingsScreen {...p} />)
    await act(async () => { fireEvent.click(screen.getByTestId('key-delete-anthropic')) })
    expect(confirm).toHaveBeenCalledWith(cevir('set.key.deleteConfirm', { saglayici: 'Anthropic' }), cevir('set.key.delete'), cevir('confirm.cancel'))
    expect(p.onDeleteKey).not.toHaveBeenCalled()
    expect(saved()).toBe(cevir('set.savedIdle'))
  })

  it.each([false, true])('confirmed deletion flashes only on success (%s)', async success => {
    const onDeleteKey = vi.fn(async () => success)
    render(<SettingsScreen {...props({ onDeleteKey })} />)
    await act(async () => { fireEvent.click(screen.getByTestId('key-delete-anthropic')) })
    expect(onDeleteKey).toHaveBeenCalledExactlyOnceWith('anthropic')
    expect(saved()).toBe(cevir(success ? 'set.savedNow' : 'set.savedIdle'))
  })

  it.each([false, true])('Unity flashes only on success (%s)', async success => {
    render(<SettingsScreen {...props({ page: 'unity', onToggleUnityMcp: vi.fn(async () => success) })} />)
    await act(async () => { fireEvent.click(screen.getByTestId('unity-mcp-toggle')) })
    expect(saved()).toBe(cevir(success ? 'set.savedNow' : 'set.savedIdle'))
  })

  it('blocked Unity still retries but does not flash Saved', async () => {
    const onToggleUnityMcp = vi.fn(async () => true)
    render(<SettingsScreen {...props({ page: 'unity', unityMcpStatus: 'blocked', onToggleUnityMcp })} />)
    await act(async () => { fireEvent.click(screen.getByTestId('unity-mcp-toggle')) })
    expect(onToggleUnityMcp).toHaveBeenCalledTimes(1)
    expect(saved()).toBe(cevir('set.savedIdle'))
  })

  it.each(['auto-titles-toggle', 'dictation-auto-lang-toggle'])('%s waits for a successful answer', async id => {
    let resolve!: (success: boolean) => void
    const handler = vi.fn(() => new Promise<boolean>(r => { resolve = r }))
    render(<SettingsScreen {...props({ page: 'genel', onToggleAutoTitles: handler, onToggleDictationAutoLang: handler })} />)
    fireEvent.click(screen.getByTestId(id))
    expect(saved()).toBe(cevir('set.savedIdle'))
    await act(async () => { resolve(false) })
    expect(saved()).toBe(cevir('set.savedIdle'))
    fireEvent.click(screen.getByTestId(id))
    await act(async () => { resolve(true) })
    expect(saved()).toBe(cevir('set.savedNow'))
  })

  it.each(['titles', 'dictation'])('%s hook returns success, failure and skipped outcomes', async kind => {
    post.mockResolvedValue({ data: {} })
    const { result, rerender } = renderHook(({ api }) => kind === 'titles'
      ? useAutoChatTitles(api, 1).toggleAutoTitles : useDictationSettings(api, 1).toggleAutoLanguageCpu,
    { initialProps: { api: API } })
    await flush()
    await act(async () => { expect(await result.current()).toBe(true) })
    post.mockRejectedValue(new Error('offline'))
    await act(async () => { expect(await result.current()).toBe(false) })
    rerender({ api: '' })
    await act(async () => { expect(await result.current()).toBe(false) })
  })

  it.each(['titles', 'dictation'])('%s hook skips a toggle while saving', async kind => {
    let resolve!: (value: any) => void
    post.mockImplementation(() => new Promise(r => { resolve = r }))
    const { result } = renderHook(() => kind === 'titles'
      ? useAutoChatTitles(API, 1).toggleAutoTitles : useDictationSettings(API, 1).toggleAutoLanguageCpu)
    await flush()
    let pending!: Promise<boolean>
    act(() => { pending = result.current() })
    await act(async () => { expect(await result.current()).toBe(false) })
    expect(post).toHaveBeenCalledTimes(1)
    await act(async () => { resolve({ data: {} }); expect(await pending).toBe(true) })
  })

  it('the real key deletion handler reports failed, successful and skipped deletes', async () => {
    vi.useFakeTimers()
    get.mockResolvedValue({ data: { status: 'off', providers_with_keys: [] } })
    const toast = vi.fn()
    const { result, rerender } = renderHook(({ api }) => useAIConfig(api, { id: 1, name: 'B', sessionToken: 'tok' }, toast),
      { initialProps: { api: API } })
    await flush()
    vi.mocked(axios.delete).mockRejectedValueOnce(new Error('offline'))
    await act(async () => { expect(await result.current.deleteApiKey('anthropic')).toBe(false) })
    expect(toast).toHaveBeenCalledWith(cevir('settings.keyDeleteError'), 'error')
    vi.mocked(axios.delete).mockResolvedValueOnce({ data: {} })
    await act(async () => { expect(await result.current.deleteApiKey('anthropic')).toBe(true) })
    expect(axios.delete).toHaveBeenCalledWith(`${API}/api-keys/1/anthropic`)
    rerender({ api: '' })
    await act(async () => { expect(await result.current.deleteApiKey('anthropic')).toBe(false) })
    expect(axios.delete).toHaveBeenCalledTimes(2)
  })

  it('the real Unity handler returns success and skips while starting', async () => {
    vi.useFakeTimers()
    get.mockResolvedValue({ data: { status: 'off' } })
    post.mockResolvedValue({ data: {} })
    const { result } = renderHook(() => useAIConfig(API, null, vi.fn()))
    await flush()
    await act(async () => { expect(await result.current.toggleUnityMcp()).toBe(true) })
    expect(result.current.unityMcpStatus).toBe('starting')
    await act(async () => { expect(await result.current.toggleUnityMcp()).toBe(false) })
    expect(post).toHaveBeenCalledTimes(1)
  })

  it('the real Unity handler reports failure', async () => {
    vi.useFakeTimers()
    get.mockResolvedValue({ data: { status: 'blocked' } })
    post.mockRejectedValue(new Error('blocked'))
    const { result } = renderHook(() => useAIConfig(API, null, vi.fn()))
    await flush()
    await act(async () => { expect(await result.current.toggleUnityMcp()).toBe(false) })
    expect(result.current.unityMcpStatus).toBe('blocked')
  })
})

describe('settings audit fields and cloud defaults', () => {
  it('first Esc cancels and clears a key edit; second Esc closes settings', () => {
    const p = props()
    render(<SettingsScreen {...p} />)
    fireEvent.click(screen.getByTestId('key-change-anthropic'))
    const input = screen.getByTestId('key-input-anthropic')
    fireEvent.change(input, { target: { value: 'secret' } })
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(p.onClose).not.toHaveBeenCalled()
    expect(screen.queryByTestId('key-input-anthropic')).toBeNull()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(p.onClose).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByTestId('key-change-anthropic'))
    expect((screen.getByTestId('key-input-anthropic') as HTMLInputElement).value).toBe('')
  })

  it.each(['settings-search', 'custom-model-input'])('Esc clears and blurs %s before closing', id => {
    const p = props()
    render(<SettingsScreen {...p} />)
    const input = screen.getByTestId(id) as HTMLInputElement
    input.focus()
    fireEvent.change(input, { target: { value: 'claude' } })
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(input.value).toBe('')
    expect(document.activeElement).not.toBe(input)
    expect(p.onClose).not.toHaveBeenCalled()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(p.onClose).toHaveBeenCalledTimes(1)
  })

  it('a keyless cloud default warns and focuses its key row instead of saving', async () => {
    const onSaveDefaultModel = vi.fn(async () => true)
    const showToast = vi.fn()
    render(<SettingsScreen {...props({ onSaveDefaultModel, showToast })} />)
    await act(async () => { fireEvent.change(screen.getByTestId('default-model-select'), { target: { value: 'openai\u0000gpt-9' } }) })
    expect(onSaveDefaultModel).not.toHaveBeenCalled()
    expect(showToast).toHaveBeenCalledWith(`openai ${cevir('models.apiKeyNeeded')}`, 'warning')
    expect(document.activeElement).toBe(screen.getByTestId('key-input-openai'))
    expect(saved()).toBe(cevir('set.savedIdle'))
  })
})
