/** Display name, 2 Oct 2026: independent state and account field behavior. */
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'

vi.mock('axios', () => ({ default: { get: vi.fn(), put: vi.fn() } }))

import axios from 'axios'
import { useDisplayName } from '../renderer/hooks/home/useDisplayName'
import { AccountPage } from '../renderer/components/home/settings/SettingsPages'
import { SettingsScreen, type SettingsScreenProps } from '../renderer/components/home/settings/SettingsScreen'
import { Sidebar } from '../renderer/components/home/Sidebar'
import { aktifDilAyarla, cevir } from '../renderer/lib/i18n'

const API = 'http://backend'
const get = vi.mocked(axios.get)
const put = vi.mocked(axios.put)
const user = { id: 1, name: 'Old name', sessionToken: 'tok' }
const settingsProps = (over: Partial<SettingsScreenProps> = {}): SettingsScreenProps => ({
  open: true, page: 'hesap', aiConfig: { provider_type: 'subscription', model_name: 'claude-sonnet-5', api_key: '', thinking_level: 'medium' },
  providersWithKeys: [], onClose: vi.fn(), onLogout: vi.fn(), onDeleteKey: vi.fn(async () => true),
  unityMcpStatus: 'off', unityMcpToggling: false, onToggleUnityMcp: vi.fn(),
  lang: 'tr', onLangChange: vi.fn(), user, ...over,
})

beforeEach(() => { vi.resetAllMocks(); aktifDilAyarla('tr') })
afterEach(cleanup)

describe('useDisplayName', () => {
  it.each([['Burak', 'Burak'], ['local', '']])('loads %s once after ready', async (raw, expected) => {
    get.mockResolvedValue({ data: { name: raw } })
    const { result, rerender } = renderHook(({ ready }) => useDisplayName(API, ready), { initialProps: { ready: false } })
    expect(get).not.toHaveBeenCalled()
    rerender({ ready: true })
    await waitFor(() => expect(get).toHaveBeenCalledTimes(1))
    await act(async () => { await Promise.resolve() })
    expect(result.current.name).toBe(expected)
    expect(get).toHaveBeenCalledWith(`${API}/me`)
    rerender({ ready: true })
    expect(get).toHaveBeenCalledTimes(1)
  })

  it('keeps an empty name when GET fails', async () => {
    get.mockRejectedValue(new Error('offline'))
    const { result } = renderHook(() => useDisplayName(API, true))
    await act(async () => { await Promise.resolve() })
    expect(result.current.name).toBe('')
  })

  it('trims the PUT body and uses the returned normalized name', async () => {
    put.mockResolvedValue({ data: { name: 'Burak Ada' } })
    const { result } = renderHook(() => useDisplayName(API, false))
    await act(async () => { expect(await result.current.saveName('  Burak   Ada  ')).toBe(true) })
    expect(put).toHaveBeenCalledWith(`${API}/me/name`, { name: 'Burak   Ada' })
    expect(result.current.name).toBe('Burak Ada')
    put.mockResolvedValue({ data: { name: 'local' } })
    await act(async () => { expect(await result.current.saveName('LOCAL')).toBe(true) })
    expect(result.current.name).toBe('')
  })

  it('returns false and preserves the saved name when PUT fails', async () => {
    get.mockResolvedValue({ data: { name: 'Burak' } })
    put.mockRejectedValue(new Error('offline'))
    const { result } = renderHook(() => useDisplayName(API, true))
    await waitFor(() => expect(result.current.name).toBe('Burak'))
    await act(async () => { expect(await result.current.saveName('Ada')).toBe(false) })
    expect(result.current.name).toBe('Burak')
  })
})

describe('account name field', () => {
  it('saves once on Enter and reports success', async () => {
    const onSaveName = vi.fn(async () => true)
    const saved = vi.fn()
    render(<AccountPage user={user} userName="Burak" onSaveName={onSaveName} onLogout={vi.fn()} saved={saved} />)
    const input = screen.getByTestId('settings-name-input') as HTMLInputElement
    expect(input.value).toBe('Burak')
    expect(input.maxLength).toBe(40)
    expect(input.autocomplete).toBe('off')
    expect((screen.getByTestId('settings-name-save') as HTMLButtonElement).disabled).toBe(true)
    fireEvent.change(input, { target: { value: 'Ada' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(saved).toHaveBeenCalledTimes(1))
    expect(onSaveName).toHaveBeenCalledTimes(1)
    expect(onSaveName).toHaveBeenCalledWith('Ada')
  })

  it('keeps the typed value and shows an error on false', async () => {
    const onSaveName = vi.fn(async () => false)
    const saved = vi.fn()
    const showToast = vi.fn()
    render(<AccountPage userName="Burak" onSaveName={onSaveName} onLogout={vi.fn()} saved={saved} showToast={showToast} />)
    fireEvent.change(screen.getByTestId('settings-name-input'), { target: { value: 'Ada' } })
    fireEvent.click(screen.getByTestId('settings-name-save'))
    await waitFor(() => expect(showToast).toHaveBeenCalledWith(cevir('set.hesap.nameFailed'), 'error'))
    expect(saved).not.toHaveBeenCalled()
    expect((screen.getByTestId('settings-name-input') as HTMLInputElement).value).toBe('Ada')
  })

  it('disables unchanged trimmed values and guards Enter while saving', async () => {
    let finish!: (ok: boolean) => void
    const onSaveName = vi.fn(() => new Promise<boolean>(resolve => { finish = resolve }))
    render(<AccountPage userName="Burak" onSaveName={onSaveName} onLogout={vi.fn()} />)
    const input = screen.getByTestId('settings-name-input')
    const button = screen.getByTestId('settings-name-save') as HTMLButtonElement
    fireEvent.change(input, { target: { value: ' Burak ' } })
    expect(button.disabled).toBe(true)
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onSaveName).not.toHaveBeenCalled()
    fireEvent.change(input, { target: { value: 'Ada' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(button.disabled).toBe(true)
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onSaveName).toHaveBeenCalledTimes(1)
    await act(async () => { finish(true) })
  })

  it('resets and blurs on first Esc, then closes on second Esc', () => {
    const p = settingsProps({ userName: 'Burak', onSaveName: vi.fn(async () => true) })
    render(<SettingsScreen {...p} />)
    const input = screen.getByTestId('settings-name-input') as HTMLInputElement
    input.focus()
    fireEvent.change(input, { target: { value: 'Unsaved' } })
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(input.value).toBe('Burak')
    expect(document.activeElement).not.toBe(input)
    expect(p.onClose).not.toHaveBeenCalled()
    expect(p.onSaveName).not.toHaveBeenCalled()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(p.onClose).toHaveBeenCalledTimes(1)
  })

  it('syncs the input with a changed saved name and hides it without a save handler', () => {
    const p = settingsProps({ userName: 'Burak', onSaveName: vi.fn(async () => true) })
    const { rerender } = render(<SettingsScreen {...p} />)
    rerender(<SettingsScreen {...p} userName="Ada" />)
    expect((screen.getByTestId('settings-name-input') as HTMLInputElement).value).toBe('Ada')
    rerender(<SettingsScreen {...p} onSaveName={undefined} />)
    expect(screen.queryByTestId('settings-name-input')).toBeNull()
  })

  it('shows the independent name in both settings hero and rail', () => {
    render(<SettingsScreen {...settingsProps({ userName: 'Burak' })} />)
    expect(screen.getAllByText('Burak')).toHaveLength(2)
    expect(screen.queryByText('Old name')).toBeNull()
  })

  it('shows the independent name in Sidebar, including an explicit empty override', () => {
    const p: React.ComponentProps<typeof Sidebar> = {
      isSidebarOpen: true, conversations: [], activeConvId: null,
      selectConversation: vi.fn(), createNewConversation: vi.fn(), deleteConversation: vi.fn(),
      editingId: null, setEditingId: vi.fn(), tempTitle: '', setTempTitle: vi.fn(), saveRename: vi.fn(),
      workspacePath: null, closeWorkspace: vi.fn(),
      user, setShowSettings: vi.fn(), handleLogout: vi.fn(), userName: 'Burak',
    }
    const { rerender } = render(<Sidebar {...p} />)
    expect(screen.getByText('Burak')).toBeTruthy()
    expect(screen.queryByText('Old name')).toBeNull()
    rerender(<Sidebar {...p} userName="" />)
    expect(screen.queryByText('Burak')).toBeNull()
    expect(screen.queryByText('Old name')).toBeNull()
  })
})
