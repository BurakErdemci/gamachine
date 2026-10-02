/**
 * How the maker profile is reached and fed: the stats hook (range refetch, the one-time "new"
 * mark), the sidebar card's real level, Settings > Hesap (profile link, statistics reset through
 * the main process), and open/close wired the way home.tsx wires it.
 */
import React, { useState } from 'react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'

import { useProfileStats } from '../renderer/hooks/home/useProfileStats'
import { ProfileView } from '../renderer/components/home/ProfileView'
import { Sidebar } from '../renderer/components/home/Sidebar'
import { SettingsScreen } from '../renderer/components/home/settings/SettingsScreen'
import { LangContext, ceviriUygula } from '../renderer/lib/i18n'
import { FULL, MONTH } from './fixtures/profileStats'

// English, explicitly: outside a provider the components follow the active language.
const EN = ({ children }: { children: React.ReactNode }) => (
  <LangContext.Provider value={{ lang: 'en', setLang: () => {}, t: (k, v) => ceviriUygula('en', k, v) }}>{children}</LangContext.Provider>
)
const renderEn = (ui: React.ReactElement) => render(ui, { wrapper: EN })

const API = 'http://127.0.0.1:9'

function fakeHttp(byRange: Record<string, unknown>) {
  return {
    get: vi.fn(async (_url: string, config: any) => {
      const answer = byRange[config?.params?.range]
      if (answer instanceof Error) throw answer
      return { data: answer }
    }),
  }
}

beforeEach(() => vi.stubGlobal('ipc', { invoke: vi.fn().mockResolvedValue({ cleared: 3 }) }))
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('useProfileStats', () => {
  it('reads the 6-month range first, with the session token', async () => {
    const http = fakeHttp({ '6m': FULL })
    const { result } = renderHook(() => useProfileStats({ api: API, token: 'tok', http }))
    await waitFor(() => expect(result.current.data?.level).toBe(14))
    expect(http.get).toHaveBeenCalledWith(`${API}/profile/stats`, { params: { range: '6m' }, headers: { 'X-Session-Token': 'tok' } })
    expect(result.current.latest?.xp).toBe(8420)
  })

  it('a range change refetches that range and shows its answer', async () => {
    const http = fakeHttp({ '6m': FULL, month: MONTH })
    const { result } = renderHook(() => useProfileStats({ api: API, token: 'tok', http }))
    await waitFor(() => expect(result.current.data?.counts.tasks).toBe(312))
    act(() => result.current.setRange('month'))
    await waitFor(() => expect(result.current.data?.counts.tasks).toBe(48))
    expect(http.get.mock.calls.map(c => c[1].params.range)).toEqual(['6m', 'month'])
  })

  it('keeps an achievement "new" for the session after the backend stops reporting it', async () => {
    const http = fakeHttp({ '6m': FULL })
    const { result } = renderHook(() => useProfileStats({ api: API, token: 'tok', http }))
    await waitFor(() => expect(result.current.data).not.toBeNull())
    // The second read (e.g. the sidebar's refresh) no longer carries the backend's one-time flag.
    http.get.mockResolvedValue({ data: { ...FULL, achievements: FULL.achievements.map(a => ({ ...a, new: false })) } })
    await act(async () => { await result.current.refresh() })
    expect(result.current.data?.achievements.find(a => a.id === 'night_owl')?.new).toBe(true)
  })

  it('a failed read keeps the last answer and reports the failure', async () => {
    const http = fakeHttp({ '6m': FULL })
    const { result } = renderHook(() => useProfileStats({ api: API, token: 'tok', http }))
    await waitFor(() => expect(result.current.data).not.toBeNull())
    http.get.mockRejectedValue(new Error('down'))
    await act(async () => { await result.current.refresh() })
    expect(result.current.failed).toBe(true)
    expect(result.current.data?.level).toBe(14)
  })

  it('does nothing until enabled', async () => {
    const http = fakeHttp({ '6m': FULL })
    renderHook(() => useProfileStats({ api: API, token: 'tok', http, enabled: false }))
    await new Promise(r => setTimeout(r, 10))
    expect(http.get).not.toHaveBeenCalled()
  })
})

// ---------- sidebar card ----------

const noop = () => {}
function sidebarProps(over: Record<string, unknown> = {}) {
  return {
    ...({} as any),
    isSidebarOpen: true, sidebarTab: 'chats', setSidebarTab: noop,
    conversations: [], activeConvId: null, convStatus: {},
    selectConversation: noop, createNewConversation: noop, deleteConversation: noop,
    editingId: null, setEditingId: noop, tempTitle: '', setTempTitle: noop, saveRename: noop,
    fileTree: [], treeContextMenu: null, setTreeContextMenu: noop,
    user: { id: 1, name: 'Burak', sessionToken: 't' }, setShowSettings: noop, handleLogout: noop,
    workspacePath: null, closeWorkspace: noop,
    ...over,
  }
}

describe('sidebar profile card', () => {
  it('shows the real level, total XP and the bar toward the next level', () => {
    renderEn(<Sidebar {...sidebarProps({ profileLevel: { level: 14, xp: 8420, levelXp: 820, levelNeed: 1400 } })} />)
    const card = screen.getByTestId('sidebar-profile')
    expect(card.tagName).toBe('BUTTON')
    expect(card.querySelector('.lvl-n')?.textContent).toBe('14')
    expect(card.querySelector('.side-profile-meta .level')?.textContent).toBe('Level 14')
    expect(card.querySelector('.side-profile-meta .xp')?.textContent).toBe(' · 8,420 XP')
    expect((card.querySelector('.xp-mini > span') as HTMLElement).style.width).toBe(`${(820 / 1400) * 100}%`)
  })

  it('before the first answer it claims no level and no XP', () => {
    renderEn(<Sidebar {...sidebarProps({ profileLevel: null })} />)
    const card = screen.getByTestId('sidebar-profile')
    expect(card.querySelector('.lvl-n')?.textContent).toBe('–')
    expect(card.querySelector('.side-profile-meta .xp')).toBeNull()
    expect((card.querySelector('.xp-mini > span') as HTMLElement).style.width).toBe('0%')
  })

  it('a click opens the profile; while open the card is marked active', () => {
    const onOpenProfile = vi.fn()
    const { rerender } = renderEn(<Sidebar {...sidebarProps({ onOpenProfile })} />)
    fireEvent.click(screen.getByTestId('sidebar-profile'))
    expect(onOpenProfile).toHaveBeenCalledTimes(1)
    rerender(<Sidebar {...sidebarProps({ onOpenProfile, profileOpen: true })} />)
    expect(screen.getByTestId('sidebar-profile').classList.contains('is-active')).toBe(true)
    expect(screen.getByTestId('sidebar-profile').getAttribute('aria-current')).toBe('page')
  })
})

// ---------- Settings > Hesap ----------

function hesap(over: Record<string, unknown> = {}) {
  const props: React.ComponentProps<typeof SettingsScreen> = {
    open: true, page: 'hesap', providersWithKeys: [], onClose: () => {},
    onLogout: () => {}, onDeleteKey: async () => {}, unityMcpStatus: 'off', unityMcpToggling: false,
    onToggleUnityMcp: () => {}, lang: 'en', onLangChange: () => {},
    aiConfig: { provider_type: 'anthropic', model_name: '', api_key: '', thinking_level: 'off' },
    user: { id: 1, name: 'Burak', sessionToken: 't' } as any,
    ...over,
  }
  return renderEn(<SettingsScreen {...props} />)
}
const flush = () => act(async () => { for (let i = 0; i < 8; i++) await Promise.resolve() })

describe('Settings > Hesap', () => {
  it('the "Maker profile" button opens the profile', () => {
    const onOpenProfile = vi.fn()
    hesap({ onOpenProfile })
    fireEvent.click(screen.getByTestId('settings-open-profile'))
    expect(onOpenProfile).toHaveBeenCalledTimes(1)
  })

  it('reset asks first, then resets through the main process (which adds the UI secret)', async () => {
    // No ConfirmDialogHost here, so confirmDialog falls back to window.confirm.
    const ask = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const onProfileReset = vi.fn()
    const showToast = vi.fn()
    hesap({ onProfileReset, showToast })
    fireEvent.click(screen.getByTestId('settings-reset-stats'))
    await flush()
    expect(ask).toHaveBeenCalledWith(expect.stringMatching(/^Reset your profile statistics\?/))
    const invoke = (globalThis as any).ipc.invoke
    expect(invoke).toHaveBeenCalledWith('profile-reset')
    // The renderer never sends a secret, a path or a body of its own.
    expect(invoke.mock.calls[0]).toEqual(['profile-reset'])
    expect(onProfileReset).toHaveBeenCalledTimes(1)
    expect(showToast).toHaveBeenCalledWith('Statistics reset', 'success')
  })

  it('a cancelled confirm resets nothing', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    const onProfileReset = vi.fn()
    hesap({ onProfileReset })
    fireEvent.click(screen.getByTestId('settings-reset-stats'))
    await flush()
    expect((globalThis as any).ipc.invoke).not.toHaveBeenCalled()
    expect(onProfileReset).not.toHaveBeenCalled()
  })

  it('a refused reset says so and does not report success', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    ;(globalThis as any).ipc.invoke.mockRejectedValue(new Error('Profile reset requires the app UI'))
    const onProfileReset = vi.fn()
    const showToast = vi.fn()
    hesap({ onProfileReset, showToast })
    fireEvent.click(screen.getByTestId('settings-reset-stats'))
    await flush()
    expect(onProfileReset).not.toHaveBeenCalled()
    expect(showToast).toHaveBeenCalledWith('Statistics could not be reset', 'error')
  })
})

// ---------- open / close, wired as home.tsx wires it ----------

/**
 * home.tsx cannot render in jsdom (Monaco, electron IPC, many hooks; see toast-visible.test.tsx),
 * so this harness repeats its wiring with the real components: one stats hook, the sidebar card
 * and the settings Hesap button open the profile, opening it closes settings, Esc closes it.
 * The source checks below pin that home.tsx really does wire it this way.
 */
function Harness({ http }: { http: ReturnType<typeof fakeHttp> }) {
  const stats = useProfileStats({ api: API, token: 'tok', http })
  const [open, setOpen] = useState(false)
  const [settings, setSettings] = useState(false)
  const openProfile = () => { setSettings(false); setOpen(true); void stats.refresh() }
  return (
    <div className="app" data-screen={settings ? 'ayarlar' : open ? 'profil' : undefined} data-testid="app">
      <SettingsScreen open={settings} page="hesap" providersWithKeys={[]} onClose={() => setSettings(false)}
        onLogout={noop} onDeleteKey={async () => {}} unityMcpStatus="off" unityMcpToggling={false} onToggleUnityMcp={noop}
        lang="en" onLangChange={noop} aiConfig={{ provider_type: 'anthropic', model_name: '', api_key: '', thinking_level: 'off' }}
        onOpenProfile={openProfile} />
      <ProfileView open={open} onClose={() => setOpen(false)} data={stats.data} range={stats.range} onRangeChange={stats.setRange} userName="Burak" />
      <Sidebar {...sidebarProps({
        setShowSettings: (v: boolean) => setSettings(v),
        onOpenProfile: openProfile, profileOpen: open && !settings,
        profileLevel: stats.latest ? { level: stats.latest.level, xp: stats.latest.xp, levelXp: stats.latest.level_xp, levelNeed: stats.latest.level_need } : null,
      })} />
      <main className="app-main" data-testid="chat-stage">chat</main>
    </div>
  )
}

describe('profile open / close', () => {
  it('the sidebar card opens it over the chat stage, which stays mounted; Esc returns', async () => {
    const http = fakeHttp({ '6m': FULL, month: MONTH })
    renderEn(<Harness http={http} />)
    await waitFor(() => expect(screen.getByTestId('sidebar-profile').querySelector('.lvl-n')?.textContent).toBe('14'))
    fireEvent.click(screen.getByTestId('sidebar-profile'))
    expect(screen.getByTestId('app').dataset.screen).toBe('profil')
    expect(screen.getByTestId('profile-view')).toBeTruthy()
    expect(screen.getByTestId('chat-stage')).toBeTruthy()
    // range tab refetches through the shared hook and the view shows the new range's numbers
    fireEvent.click(screen.getByRole('tab', { name: 'This month' }))
    await waitFor(() => expect(document.querySelector('.pf-stat[data-stat="tasks"] .pf-stat-v')?.textContent).toBe('48'))
    expect(http.get.mock.calls.map(c => c[1].params.range)).toContain('month')
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByTestId('profile-view')).toBeNull()
    expect(screen.getByTestId('app').dataset.screen).toBeUndefined()
  })

  it('Settings > Hesap "Maker profile" closes settings and opens the profile', async () => {
    const http = fakeHttp({ '6m': FULL })
    renderEn(<Harness http={http} />)
    fireEvent.click(screen.getByText('Settings'))
    expect(screen.getByTestId('settings-screen')).toBeTruthy()
    fireEvent.click(screen.getByTestId('settings-open-profile'))
    expect(screen.queryByTestId('settings-screen')).toBeNull()
    expect(screen.getByTestId('app').dataset.screen).toBe('profil')
    await waitFor(() => expect(screen.getByTestId('profile-level').textContent).toContain('14'))
  })
})

describe('home.tsx wires the profile', () => {
  const src = readFileSync(resolve(__dirname, '../renderer/pages/home.tsx'), 'utf8')
  it('mounts ProfileView with the shared stats hook and the open state', () => {
    expect(src).toMatch(/const profileStats = useProfileStats\(/)
    expect(src).toMatch(/<ProfileView[\s\S]{0,80}open=\{profileOpen\} onClose=\{closeProfile\}/)
    expect(src).toMatch(/data-screen=\{ai\.showSettings \? 'ayarlar' : profileOpen \? 'profil' : undefined\}/)
  })
  it('both entry points open it and the sidebar card reads the real level', () => {
    expect(src).toMatch(/onOpenProfile=\{openProfile\} onProfileReset=/)
    expect(src).toMatch(/profileLevel=\{profileStats\.latest \?/)
    expect(src).toMatch(/onOpenProfile=\{openProfile\}\n\s*\/>/)
  })
})
