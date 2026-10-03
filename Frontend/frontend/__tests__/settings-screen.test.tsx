/**
 * The round-11 settings SCREEN (mockup `?screen=ayarlar&set=<page>`), which replaced the
 * "AI Yapılandırması" modal. Pinned here:
 *   - navigation: rail pages, the search, Esc and "Back to chat" returning to the chat;
 *   - every setting the modal had still reaches its handler (one assertion per mapping row,
 *     old place -> new place, listed in the describe names);
 *   - live apply: no Save button anywhere, the "Saved" note flashes after a change, typed
 *     values (API key, custom model id) have their own small button;
 *   - log out exists only on the Hesap page;
 *   - the entry points: sidebar Ayarlar, the phone icon, the top-bar shield.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, fireEvent, act, waitFor, within } from '@testing-library/react'

import { SettingsScreen } from '../renderer/components/home/settings/SettingsScreen'
import { SETTINGS_PAGES } from '../renderer/components/home/settings/pages'
import { ModeChip } from '../renderer/components/home/ModeChip'
import { RemoteBadge } from '../renderer/components/home/RemoteBadge'
import { Sidebar } from '../renderer/components/home/Sidebar'
import { AppearanceProvider } from '../renderer/lib/appearance'
import { aktifDilAyarla, cevir } from '../renderer/lib/i18n'

const base = (over: Record<string, unknown> = {}) => ({
  open: true,
  aiConfig: { provider_type: 'subscription', model_name: 'claude-opus-5-5', api_key: '' },
  availableModels: {
    local: [{ id: 'qwen3:8b', name: 'Qwen3 8B', provider: 'ollama' }],
    subscription: [
      { id: 'claude-opus-5-5', name: 'Claude Opus 5.5 (CLI)', provider: 'subscription' },
      { id: 'claude-sonnet-5-5', name: 'Claude Sonnet 5.5 (CLI)', provider: 'subscription' },
    ],
    cloud: [{ id: 'gpt-9', name: 'GPT-9', provider: 'openai' }],
  },
  providersWithKeys: ['anthropic'],
  onClose: vi.fn(),
  onLogout: vi.fn(),
  onDeleteKey: vi.fn(async () => {}),
  unityMcpStatus: 'off',
  unityMcpToggling: false,
  onToggleUnityMcp: vi.fn(),
  lang: 'tr',
  onLangChange: vi.fn(),
  ...over,
}) as any

const nav = (name: string) => fireEvent.click(screen.getByRole('button', { name }))
const heading = () => screen.getByRole('heading', { level: 1 }).textContent

beforeEach(() => {
  aktifDilAyarla('tr')
  ;(window as any).ipc = { invoke: vi.fn(async () => ({ ok: true, data: { enabled: false, devices: [] } })) }
})
afterEach(() => {
  cleanup()
  delete (window as any).ipc
  vi.useRealTimers()
})

describe('settings screen · navigation', () => {
  it('opens on Genel and every rail item shows its own page', () => {
    render(<AppearanceProvider><SettingsScreen {...base()} /></AppearanceProvider>)
    expect(screen.getByTestId('settings-screen').getAttribute('data-set')).toBe('genel')
    expect(heading()).toBe(cevir('set.nav.genel'))
    const names: Record<string, string> = {
      modeller: cevir('set.nav.modeller'), gorunum: cevir('set.nav.gorunum'), unity: 'Unity',
      onay: cevir('set.nav.onay'), uzak: cevir('set.nav.uzak'), genel: cevir('set.nav.genel'),
    }
    for (const [page, label] of Object.entries(names)) {
      nav(label)
      expect(screen.getByTestId('settings-screen').getAttribute('data-set')).toBe(page)
      const current = document.querySelector('.set-nav-i[aria-current="page"]') as HTMLElement
      expect(current.dataset.setLink).toBe(page)
    }
    // Hesap sits in the rail foot (the user row), not in the list.
    fireEvent.click(document.querySelector('.set-me') as HTMLElement)
    expect(heading()).toBe(cevir('set.nav.hesap'))
    expect(SETTINGS_PAGES).toHaveLength(7)
  })

  it('a controlled page follows the prop and reports clicks', () => {
    const onPageChange = vi.fn()
    const { rerender } = render(<SettingsScreen {...base({ page: 'onay', onPageChange })} />)
    expect(heading()).toBe(cevir('set.nav.onay'))
    nav('Unity')
    expect(onPageChange).toHaveBeenCalledWith('unity')
    rerender(<SettingsScreen {...base({ page: 'unity', onPageChange })} />)
    expect(screen.getByTestId('unity-mcp-row')).toBeTruthy()
  })

  it('Esc and "Back to chat" both return to the chat', () => {
    const onClose = vi.fn()
    render(<SettingsScreen {...base({ onClose })} />)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByTestId('settings-back'))
    expect(onClose).toHaveBeenCalledTimes(2)
    expect(screen.getByTestId('settings-back').textContent).toContain('Esc')
  })

  it('Esc leaves an open confirm dialog to answer first', () => {
    const onClose = vi.fn()
    render(<><SettingsScreen {...base({ onClose })} /><div role="alertdialog" /></>)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).not.toHaveBeenCalled()
  })

  it('closed = nothing rendered, and no Esc listener', () => {
    const onClose = vi.fn()
    render(<SettingsScreen {...base({ open: false, onClose })} />)
    expect(screen.queryByTestId('settings-screen')).toBeNull()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).not.toHaveBeenCalled()
  })

  it('the search narrows the rail and Enter opens the first match', () => {
    render(<AppearanceProvider><SettingsScreen {...base()} /></AppearanceProvider>)
    const box = screen.getByTestId('settings-search')
    fireEvent.change(box, { target: { value: 'yazı tipi' } })
    const items = Array.from(document.querySelectorAll('.set-nav .set-nav-i')).map(b => (b as HTMLElement).dataset.setLink)
    expect(items).toEqual(['gorunum'])
    fireEvent.keyDown(box, { key: 'Enter' })
    expect(heading()).toBe(cevir('set.nav.gorunum'))
    fireEvent.change(box, { target: { value: 'zzzz' } })
    expect(screen.getByText(cevir('set.noMatch'))).toBeTruthy()
  })
})

describe('settings screen · live apply', () => {
  it('has no Save button on any page and flashes "Saved" after a change', async () => {
    vi.useFakeTimers()
    const onToggleAutoTitles = vi.fn(async () => true)
    render(<AppearanceProvider><SettingsScreen {...base({ onToggleAutoTitles, autoTitles: true })} /></AppearanceProvider>)
    expect(screen.getByTestId('settings-saved').textContent).toBe(cevir('set.savedIdle'))
    await act(async () => { fireEvent.click(screen.getByTestId('auto-titles-toggle')) })
    expect(onToggleAutoTitles).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId('settings-saved').textContent).toBe(cevir('set.savedNow'))
    act(() => { vi.advanceTimersByTime(1700) })
    expect(screen.getByTestId('settings-saved').textContent).toBe(cevir('set.savedIdle'))
    vi.useRealTimers()
    for (const page of SETTINGS_PAGES) {
      cleanup()
      render(<AppearanceProvider><SettingsScreen {...base({ page })} /></AppearanceProvider>)
      expect(screen.queryByText(cevir('settings.save'))).toBeNull()
    }
  })
})

describe('settings mapping · old modal -> new pages', () => {
  it('General tab language -> Genel / Interface language', () => {
    const onLangChange = vi.fn()
    render(<SettingsScreen {...base({ onLangChange })} />)
    const group = screen.getByRole('radiogroup', { name: cevir('set.uiLang') })
    expect(within(group).getByRole('radio', { name: 'Türkçe' }).getAttribute('aria-checked')).toBe('true')
    fireEvent.click(within(group).getByRole('radio', { name: 'English' }))
    expect(onLangChange).toHaveBeenCalledWith('en')
  })

  it('General tab auto chat title -> Genel / Chat', () => {
    const onToggleAutoTitles = vi.fn()
    render(<SettingsScreen {...base({ onToggleAutoTitles, autoTitles: false })} />)
    const sw = screen.getByTestId('auto-titles-toggle')
    expect(sw.getAttribute('aria-checked')).toBe('false')
    fireEvent.click(sw)
    expect(onToggleAutoTitles).toHaveBeenCalledTimes(1)
  })

  it('General tab dictation language detect -> Genel / Dictation', () => {
    const onToggleDictationAutoLang = vi.fn()
    render(<SettingsScreen {...base({ onToggleDictationAutoLang, dictationAutoLang: true })} />)
    fireEvent.click(screen.getByTestId('dictation-auto-lang-toggle'))
    expect(onToggleDictationAutoLang).toHaveBeenCalledTimes(1)
  })

  it('General tab Unity MCP toggle -> Unity / Unity connection', () => {
    const onToggleUnityMcp = vi.fn()
    render(<SettingsScreen {...base({ page: 'unity', onToggleUnityMcp, unityMcpStatus: 'connected', unityProjectName: 'Arena' })} />)
    const sw = screen.getByTestId('unity-mcp-toggle')
    expect(sw.getAttribute('aria-checked')).toBe('true')
    expect(screen.getByTestId('unity-mcp-row').textContent).toContain('Arena')
    fireEvent.click(sw)
    expect(onToggleUnityMcp).toHaveBeenCalledTimes(1)
  })

  it('General tab Unity toggle stays disabled while starting or toggling', () => {
    render(<SettingsScreen {...base({ page: 'unity', unityMcpStatus: 'starting' })} />)
    expect((screen.getByTestId('unity-mcp-toggle') as HTMLButtonElement).disabled).toBe(true)
    cleanup()
    render(<SettingsScreen {...base({ page: 'unity', unityMcpToggling: true })} />)
    expect((screen.getByTestId('unity-mcp-toggle') as HTMLButtonElement).disabled).toBe(true)
  })

  it('Mode tab -> Onay modu page', () => {
    const onApprovalModeChange = vi.fn()
    render(<SettingsScreen {...base({ page: 'onay', approvalMode: 'balanced', onApprovalModeChange })} />)
    fireEvent.click(screen.getByText(cevir('set.mode.stepTitle')))
    expect(onApprovalModeChange).toHaveBeenCalledWith('step')
  })

  it('General tab theme / fonts / text size / intro -> Görünüm', () => {
    render(<AppearanceProvider><SettingsScreen {...base({ page: 'gorunum' })} /></AppearanceProvider>)
    expect(screen.getAllByRole('radio').filter(r => r.classList.contains('theme-card'))).toHaveLength(5)
    expect(screen.getByRole('combobox', { name: cevir('settings.appearance.readingFont') })).toBeTruthy()
    expect(screen.getByRole('combobox', { name: cevir('settings.appearance.codeFont') })).toBeTruthy()
    expect(screen.getByRole('radiogroup', { name: cevir('settings.appearance.textSize') })).toBeTruthy()
    expect(screen.getByRole('switch', { name: cevir('settings.appearance.intro') })).toBeTruthy()
  })

  it('General tab provider + model name + Save -> Modeller / Default model (applies at once)', async () => {
    const onSaveDefaultModel = vi.fn(async () => true)
    render(<SettingsScreen {...base({ page: 'modeller', onSaveDefaultModel, providersWithKeys: ['anthropic', 'openai'],
      defaultModel: { provider_type: 'subscription', model_name: 'claude-opus-5-5' } })} />)
    const select = screen.getByTestId('default-model-select') as HTMLSelectElement
    expect(select.value).toBe('subscription\u0000claude-opus-5-5')
    await act(async () => { fireEvent.change(select, { target: { value: 'openai\u0000gpt-9' } }) })
    expect(onSaveDefaultModel).toHaveBeenCalledWith('openai', 'gpt-9')
    expect(screen.getByTestId('settings-saved').textContent).toBe(cevir('set.savedNow'))
  })

  it('General tab API key field + Save -> Modeller / API keys row with its own Save', async () => {
    const onSaveApiKey = vi.fn(async () => true)
    render(<SettingsScreen {...base({ page: 'modeller', onSaveApiKey })} />)
    fireEvent.click(screen.getByTestId('key-add-openai'))
    const input = screen.getByTestId('key-input-openai') as HTMLInputElement
    expect(input.type).toBe('password')
    expect((screen.getByTestId('key-save-openai') as HTMLButtonElement).disabled).toBe(true)
    fireEvent.change(input, { target: { value: 'sk-test-1' } })
    await act(async () => { fireEvent.click(screen.getByTestId('key-save-openai')) })
    expect(onSaveApiKey).toHaveBeenCalledWith('openai', 'sk-test-1')
    await waitFor(() => expect(screen.queryByTestId('key-input-openai')).toBeNull())
  })

  it('a refused key keeps the field open with what was typed', async () => {
    const onSaveApiKey = vi.fn(async () => false)
    render(<SettingsScreen {...base({ page: 'modeller', onSaveApiKey })} />)
    fireEvent.click(screen.getByTestId('key-add-openai'))
    fireEvent.change(screen.getByTestId('key-input-openai'), { target: { value: 'sk-bad' } })
    await act(async () => { fireEvent.click(screen.getByTestId('key-save-openai')) })
    expect((screen.getByTestId('key-input-openai') as HTMLInputElement).value).toBe('sk-bad')
  })

  it('General tab "Delete API key" -> Modeller / API keys row Delete', async () => {
    const confirmation = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const onDeleteKey = vi.fn(async () => true)
    render(<SettingsScreen {...base({ page: 'modeller', onDeleteKey })} />)
    expect(screen.getByTestId('key-row-anthropic').textContent).toContain(cevir('set.key.saved'))
    await act(async () => { fireEvent.click(screen.getByTestId('key-delete-anthropic')) })
    expect(onDeleteKey).toHaveBeenCalledWith('anthropic')
    expect(confirmation).toHaveBeenCalledTimes(1)
    confirmation.mockRestore()
    // A provider without a key has no Delete.
    expect(screen.queryByTestId('key-delete-openai')).toBeNull()
  })

  it('General tab model name typed by hand -> Modeller / Advanced custom model id with "Use"', async () => {
    const onUseCustomModel = vi.fn(async () => true)
    render(<SettingsScreen {...base({ page: 'modeller', onUseCustomModel })} />)
    fireEvent.change(screen.getByTestId('custom-model-input'), { target: { value: 'claude-opus-9-preview' } })
    await act(async () => { fireEvent.click(screen.getByTestId('custom-model-use')) })
    expect(onUseCustomModel).toHaveBeenCalledWith('claude-opus-9-preview')
    expect((screen.getByTestId('custom-model-input') as HTMLInputElement).value).toBe('')
  })

  it('model menu CLI install / sign-in -> Modeller / Subscriptions rows', async () => {
    const http = {
      get: vi.fn(async (url: string) => url.includes('/cli-doctor')
        ? { data: { claude: { installed: true, loggedIn: true }, codex: { installed: true, loggedIn: false }, agy: { installed: false, loggedIn: null } } }
        : { data: { models: [] } }),
      post: vi.fn(async () => ({ data: {} })),
    }
    render(<SettingsScreen {...base({ page: 'modeller', API: 'http://x', http, user: { id: 1, name: 'B', sessionToken: 'tok' } })} />)
    await waitFor(() => expect(screen.getByTestId('cli-login-codex')).toBeTruthy())
    expect(screen.getByTestId('sub-row-claude').textContent).toContain(cevir('set.cli.loggedIn'))
    await act(async () => { fireEvent.click(screen.getByTestId('cli-login-codex')) })
    expect(http.post).toHaveBeenCalledWith('http://x/cli-login/codex', null, { headers: { 'X-Session-Token': 'tok' } })
    await act(async () => { fireEvent.click(screen.getByTestId('cli-install-gemini')) })
    expect(http.post).toHaveBeenCalledWith('http://x/cli-install/agy', null, { headers: { 'X-Session-Token': 'tok' } })
  })

  it('Remote tab -> Uzaktan kontrol page (the section, with its pairing and relay)', async () => {
    render(<SettingsScreen {...base({ page: 'uzak' })} />)
    expect(screen.getByTestId('remote-section')).toBeTruthy()
    expect(screen.getByTestId('remote-pair')).toBeTruthy()
    expect(screen.getByTestId('remote-relay-input')).toBeTruthy()
    expect(screen.getByTestId('remote-forget')).toBeTruthy()
    await waitFor(() => expect((window as any).ipc.invoke).toHaveBeenCalledWith('remote-control', 'status', undefined))
  })

  it('footer Log out -> Hesap only', () => {
    const onLogout = vi.fn()
    for (const page of SETTINGS_PAGES.filter(p => p !== 'hesap')) {
      render(<AppearanceProvider><SettingsScreen {...base({ page, onLogout })} /></AppearanceProvider>)
      expect(screen.queryByTestId('settings-logout')).toBeNull()
      cleanup()
    }
    render(<SettingsScreen {...base({ page: 'hesap', onLogout, user: { id: 1, name: 'Burak', sessionToken: 't' } })} />)
    fireEvent.click(screen.getByTestId('settings-logout'))
    expect(onLogout).toHaveBeenCalledTimes(1)
    expect(screen.getAllByText('Burak').length).toBeGreaterThan(0)
  })
})

describe('settings entry points', () => {
  it('the top-bar shield names the mode and opens the Onay modu page', () => {
    const onOpen = vi.fn()
    render(<ModeChip value="balanced" onOpen={onOpen} />)
    const chip = screen.getByTestId('mode-chip')
    expect(chip.textContent).toContain(cevir('mode.balanced'))
    expect(chip.getAttribute('data-set-link')).toBe('onay')
    fireEvent.click(chip)
    expect(onOpen).toHaveBeenCalledTimes(1)
    // No dropdown of its own any more: the page is where the mode is chosen.
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('sidebar: Ayarlar opens settings, the phone icon opens the remote page', () => {
    const setShowSettings = vi.fn()
    const onOpenRemote = vi.fn()
    const noop = () => {}
    render(<Sidebar {...({
      isSidebarOpen: true, conversations: [], activeConvId: null, convStatus: {},
      selectConversation: noop, createNewConversation: noop, deleteConversation: noop,
      editingId: null, setEditingId: noop, tempTitle: '', setTempTitle: noop, saveRename: noop,
      user: { id: 1, name: 'Burak', sessionToken: 't' }, setShowSettings, handleLogout: noop,
      workspacePath: null, closeWorkspace: noop,
      remoteStatus: { enabled: true, connected: true }, onOpenRemote,
    } as any)} />)
    fireEvent.click(screen.getByText(cevir('sidebar.settings')))
    expect(setShowSettings).toHaveBeenCalledWith(true)
    fireEvent.click(screen.getByTestId('remote-badge'))
    expect(onOpenRemote).toHaveBeenCalledTimes(1)
    expect(setShowSettings).toHaveBeenCalledTimes(1)
  })

  it('the phone icon in the sidebar foot calls its opener', () => {
    const onClick = vi.fn()
    render(<RemoteBadge status={{ enabled: true, connected: true } as any} onClick={onClick} />)
    fireEvent.click(screen.getByTestId('remote-badge'))
    expect(onClick).toHaveBeenCalledTimes(1)
  })
})
