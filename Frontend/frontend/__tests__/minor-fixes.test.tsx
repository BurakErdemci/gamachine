/** Minor audit fixes, 2 Oct 2026: races, composition, unknown data and key actions. */
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'

vi.mock('axios', () => ({ default: { get: vi.fn(), put: vi.fn() } }))
vi.mock('../renderer/components/ui/ConfirmDialog', () => ({ confirmDialog: vi.fn() }))

import axios from 'axios'
import { confirmDialog } from '../renderer/components/ui/ConfirmDialog'
import { AccountPage } from '../renderer/components/home/settings/SettingsPages'
import { SettingsScreen, type SettingsScreenProps } from '../renderer/components/home/settings/SettingsScreen'
import { ModelsPage, type ModelsPageProps } from '../renderer/components/home/settings/ModelsPage'
import { ProfileView } from '../renderer/components/home/ProfileView'
import { useDisplayName } from '../renderer/hooks/home/useDisplayName'
import { useAIConfig } from '../renderer/hooks/home/useAIConfig'
import { aktifDilAyarla, ceviriUygula, LangContext, type Lang } from '../renderer/lib/i18n'
import { normalizeProfileStats } from '../renderer/lib/profileStats'
import { FULL, LEDGER_DOWN } from './fixtures/profileStats'

const get = vi.mocked(axios.get)
const put = vi.mocked(axios.put)
const confirm = vi.mocked(confirmDialog)
const API = 'http://backend'
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: Error) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const modelsProps = (over: Partial<ModelsPageProps> = {}): ModelsPageProps => ({
  aiConfig: { provider_type: 'subscription', model_name: 'claude-sonnet-5', api_key: '', thinking_level: 'medium' },
  availableModels: { local: [], subscription: [], cloud: [{ id: 'gpt-9', name: 'GPT-9', provider: 'openai' }] },
  providersWithKeys: [], onSaveDefaultModel: vi.fn(async () => true),
  onDeleteKey: vi.fn(async () => true), saved: vi.fn(), ...over,
})
beforeEach(() => {
  vi.resetAllMocks()
  aktifDilAyarla('en')
  get.mockResolvedValue({ data: { status: 'off' } })
})
afterEach(cleanup)

describe('name read invalidation', () => {
  it('keeps a saved name after an older initial GET resolves', async () => {
    const initial = deferred<any>()
    get.mockReturnValueOnce(initial.promise)
    put.mockResolvedValue({ data: { name: 'Ada' } })
    const { result } = renderHook(() => useDisplayName(API, true))
    await act(async () => { expect(await result.current.saveName('Ada')).toBe(true) })
    await act(async () => { initial.resolve({ data: { name: 'Burak' } }) })
    expect(result.current.name).toBe('Ada')
  })

  it('invalidates an earlier GET even when the save fails', async () => {
    const initial = deferred<any>()
    get.mockReturnValueOnce(initial.promise)
    put.mockRejectedValue(new Error('offline'))
    const { result } = renderHook(() => useDisplayName(API, true))
    await act(async () => { expect(await result.current.saveName('Ada')).toBe(false) })
    await act(async () => { initial.resolve({ data: { name: 'Burak' } }) })
    expect(result.current.name).toBe('')
  })

  it('ignores the earlier GET while a save is still pending', async () => {
    const initial = deferred<any>()
    const save = deferred<any>()
    get.mockReturnValueOnce(initial.promise)
    put.mockReturnValueOnce(save.promise)
    const { result } = renderHook(() => useDisplayName(API, true))
    let pending!: Promise<boolean>
    act(() => { pending = result.current.saveName('Ada') })
    await act(async () => { initial.resolve({ data: { name: 'Burak' } }) })
    expect(result.current.name).toBe('')
    await act(async () => { save.resolve({ data: { name: 'Ada' } }); await pending })
    expect(result.current.name).toBe('Ada')
  })
})

describe('account name editing', () => {
  it.each([{ isComposing: true }, { keyCode: 229 }])('ignores composing Enter (%j)', event => {
    const onSaveName = vi.fn(async () => true)
    render(<AccountPage userName="Burak" onSaveName={onSaveName} onLogout={vi.fn()} />)
    const input = screen.getByTestId('settings-name-input')
    fireEvent.change(input, { target: { value: 'Ada' } })
    fireEvent.keyDown(input, { key: 'Enter', ...event })
    expect(onSaveName).not.toHaveBeenCalled()
  })

  it('clears a successful draft when the saved name stays empty', async () => {
    const onSaveName = vi.fn(async () => true)
    render(<AccountPage userName="" onSaveName={onSaveName} onLogout={vi.fn()} />)
    const input = screen.getByTestId('settings-name-input') as HTMLInputElement
    const button = screen.getByTestId('settings-name-save') as HTMLButtonElement
    fireEvent.change(input, { target: { value: 'LOCAL' } })
    fireEvent.click(button)
    await waitFor(() => expect(input.value).toBe(''))
    expect(onSaveName).toHaveBeenCalledExactlyOnceWith('LOCAL')
    expect(button.disabled).toBe(true)
  })

  it('shows the latest saved prop after a successful save', async () => {
    function Harness() {
      const [name, setName] = React.useState('Burak')
      return <AccountPage userName={name} onLogout={vi.fn()} onSaveName={async () => {
        setName('Ada Emre')
        return true
      }} />
    }
    render(<Harness />)
    const input = screen.getByTestId('settings-name-input') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'Ada   Emre' } })
    fireEvent.click(screen.getByTestId('settings-name-save'))
    await waitFor(() => expect(input.value).toBe('Ada Emre'))
    expect((screen.getByTestId('settings-name-save') as HTMLButtonElement).disabled).toBe(true)
  })
})

describe('profile shelf counts', () => {
  it.each(['en', 'tr'] as const)('includes unknown achievements in %s', lang => {
    const { container } = renderProfile(LEDGER_DOWN, lang)
    expect(container.querySelector('.pf-shelf .pf-card-meta')?.textContent).toBe(
      lang === 'en' ? '5 / 8 unlocked · 2 unknown' : '5 / 8 açıldı · 2 bilinmiyor',
    )
  })

  it('keeps the original count when every achievement is known', () => {
    const { container } = renderProfile(FULL, 'en')
    expect(container.querySelector('.pf-shelf .pf-card-meta')?.textContent).toBe('6 / 8 unlocked')
  })
})
function renderProfile(raw: unknown, lang: Lang) {
  return render(<LangContext.Provider value={{ lang, setLang: vi.fn(), t: (k, v) => ceviriUygula(lang, k, v) }}>
    <ProfileView open onClose={vi.fn()} data={normalizeProfileStats(raw)} range="6m" onRangeChange={vi.fn()} />
  </LangContext.Provider>)
}

describe('provider key loading', () => {
  it('stays unloaded after failure, then stays loaded after the first successful answer', async () => {
    const answer = deferred<any>()
    get.mockImplementation(async url => {
      if (String(url).includes('/api-keys/')) return answer.promise
      return { data: { status: 'off' } }
    })
    const { result } = renderHook(() => useAIConfig(API, null, vi.fn()))
    expect(result.current.providersWithKeysLoaded).toBe(false)
    let pending!: Promise<void>
    act(() => { pending = result.current.fetchProvidersWithKeys(1) })
    expect(result.current.providersWithKeysLoaded).toBe(false)
    await act(async () => { answer.reject(new Error('offline')); await pending })
    expect(result.current.providersWithKeysLoaded).toBe(false)
    get.mockResolvedValue({ data: { providers_with_keys: [] } })
    await act(async () => { await result.current.fetchProvidersWithKeys(1) })
    expect(result.current.providersWithKeysLoaded).toBe(true)
    get.mockRejectedValue(new Error('offline'))
    await act(async () => { await result.current.fetchProvidersWithKeys(1) })
    expect(result.current.providersWithKeysLoaded).toBe(true)
  })

  it.each([false, true, undefined])('cloud default pick respects loaded=%s', async loaded => {
    const p = modelsProps({ providersWithKeysLoaded: loaded })
    render(<ModelsPage {...p} />)
    await act(async () => { fireEvent.change(screen.getByTestId('default-model-select'), {
      target: { value: 'openai\u0000gpt-9' },
    }) })
    if (loaded === false) expect(p.onSaveDefaultModel).toHaveBeenCalledExactlyOnceWith('openai', 'gpt-9')
    else {
      expect(p.onSaveDefaultModel).not.toHaveBeenCalled()
      expect(screen.getByTestId('key-input-openai')).toBeTruthy()
    }
  })

  it('passes loading state through SettingsScreen', async () => {
    const p: SettingsScreenProps = {
      ...modelsProps(), open: true, page: 'modeller', providersWithKeysLoaded: false,
      availableModels: { local: [], subscription: [], cloud: [{ id: 'gpt-9', name: 'GPT-9', provider: 'openai' }] },
      onClose: vi.fn(), onLogout: vi.fn(), unityMcpStatus: 'off', unityMcpToggling: false,
      onToggleUnityMcp: vi.fn(), lang: 'en', onLangChange: vi.fn(),
    }
    render(<SettingsScreen {...p} />)
    await act(async () => { fireEvent.change(screen.getByTestId('default-model-select'), {
      target: { value: 'openai\u0000gpt-9' },
    }) })
    expect(p.onSaveDefaultModel).toHaveBeenCalledExactlyOnceWith('openai', 'gpt-9')
  })
})

describe('key deletion confirmation', () => {
  it('opens one dialog across repeated clicks and providers, then deletes once', async () => {
    const answer = deferred<boolean>()
    confirm.mockReturnValueOnce(answer.promise)
    const p = modelsProps({ providersWithKeys: ['anthropic', 'openai'] })
    render(<ModelsPage {...p} />)
    fireEvent.click(screen.getByTestId('key-delete-anthropic'))
    fireEvent.click(screen.getByTestId('key-delete-anthropic'))
    fireEvent.click(screen.getByTestId('key-delete-openai'))
    expect(confirm).toHaveBeenCalledTimes(1)
    expect(p.onDeleteKey).not.toHaveBeenCalled()
    await act(async () => { answer.resolve(true) })
    expect(p.onDeleteKey).toHaveBeenCalledExactlyOnceWith('anthropic')
    expect(p.saved).toHaveBeenCalledTimes(1)
    confirm.mockResolvedValueOnce(false)
    await act(async () => { fireEvent.click(screen.getByTestId('key-delete-openai')) })
    expect(confirm).toHaveBeenCalledTimes(2)
    expect(p.onDeleteKey).toHaveBeenCalledTimes(1)
  })

  it('releases the guard after cancellation', async () => {
    confirm.mockResolvedValueOnce(false).mockResolvedValueOnce(true)
    const p = modelsProps({ providersWithKeys: ['anthropic'] })
    render(<ModelsPage {...p} />)
    await act(async () => { fireEvent.click(screen.getByTestId('key-delete-anthropic')) })
    await act(async () => { fireEvent.click(screen.getByTestId('key-delete-anthropic')) })
    expect(confirm).toHaveBeenCalledTimes(2)
    expect(p.onDeleteKey).toHaveBeenCalledExactlyOnceWith('anthropic')
  })
})
