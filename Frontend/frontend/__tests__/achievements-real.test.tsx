import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, renderHook, screen, waitFor } from '@testing-library/react'
import { AchievementToast, ACHV_LIFE_MS } from '../renderer/components/home/AchievementToast'
import { ProfileView } from '../renderer/components/home/ProfileView'
import { Sidebar } from '../renderer/components/home/Sidebar'
import { useProfileStats } from '../renderer/hooks/home/useProfileStats'
import { useAchievementQueue } from '../renderer/lib/achievementQueue'
import { latestUnlocked, normalizeProfileStats, type AchievementId, type ProfileAchievement } from '../renderer/lib/profileStats'
import { LangContext, ceviriUygula, type Lang } from '../renderer/lib/i18n'
import { EMPTY, FULL } from './fixtures/profileStats'

afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks() })

const noop = () => {}
const provider = (lang: Lang) => ({ children }: { children: React.ReactNode }) => (
  <LangContext.Provider value={{ lang, setLang: noop, t: (k, v) => ceviriUygula(lang, k, v) }}>{children}</LangContext.Provider>
)
const tr = (key: Parameters<typeof ceviriUygula>[1]) => ceviriUygula('tr', key)

describe('truthful bands', () => {
  it.each(['tr', 'en'] as const)('shows no finished-turn band or task words in %s', lang => {
    render(<AchievementToast event={{ seq: 1 }} />, { wrapper: provider(lang) })
    expect(screen.queryByTestId('achievement-toast')).toBeNull()
  })

  it.each([undefined, null, 0, -1, 1.5, NaN, Infinity])('does not invent XP for %s', xp => {
    const { result } = renderHook(() => {
      const gain = { seq: 1, xp }
      const band = useAchievementQueue(null, null)
      return { gain, band }
    })
    render(<AchievementToast event={result.current.band} />, { wrapper: provider('tr') })
    expect(result.current.gain.xp).toBe(xp)
    expect(screen.queryByTestId('achievement-toast')).toBeNull()
  })

  it('keeps finished turns silent when real XP arrives', () => {
    vi.useFakeTimers()
    const view = render(<AchievementToast event={{ seq: 1 }} />, { wrapper: provider('tr') })
    act(() => { vi.advanceTimersByTime(1000) })
    view.rerender(<AchievementToast event={{ seq: 1 }} />)
    expect(screen.queryByTestId('achievement-toast')).toBeNull()
    act(() => { vi.advanceTimersByTime(ACHV_LIFE_MS - 1000) })
    expect(screen.queryByTestId('achievement-toast')).toBeNull()
  })

  it.each(['tr', 'en'] as const)('shows a real achievement name and done line in every theme in %s', lang => {
    render(<AchievementToast event={{ seq: 1 }} achievement="night_owl" />, { wrapper: provider(lang) })
    const band = screen.getByTestId('achievement-toast')
    for (const slot of ['.lex-d', '.lex-q', '.lex-s']) {
      expect(band.querySelector(slot)?.textContent).toBe(ceviriUygula(lang, 'achv.kicker'))
    }
    expect(band.querySelector('.achv-title')?.textContent).toBe(ceviriUygula(lang, 'pf.ach.night_owl.name'))
    expect(band.querySelector('.achv-sub')?.textContent).toBe(ceviriUygula(lang, 'pf.ach.night_owl.done'))
    expect(band.querySelector('.achv-xp')).toBeNull()
  })
})

describe('profile gain and unlock events', () => {
  it('compares successful complete answers, skips partial XP, and starts fresh after reset', async () => {
    const http = { get: vi.fn().mockResolvedValue({ data: { ...EMPTY, xp: 100 } }) }
    const { result } = renderHook(() => useProfileStats({ api: 'profile-test', http }))
    await waitFor(() => expect(result.current.latest?.xp).toBe(100))
    expect(result.current.gain).toBeNull()
    http.get.mockResolvedValue({ data: { ...EMPTY, xp: 137 } })
    await act(async () => { await result.current.refresh() })
    expect(result.current.gain).toMatchObject({ xp: 37 })
    const first = result.current.gain!
    http.get.mockRejectedValue(new Error('down'))
    await act(async () => { await result.current.refresh() })
    expect(result.current.gain).toBe(first)
    for (const [xp, partial] of [[137, false], [130, false], [140, true], [160, false]] as const) {
      http.get.mockResolvedValue({ data: { ...EMPTY, xp, xp_partial: partial } })
      await act(async () => { await result.current.refresh() })
      expect(result.current.gain).toBe(first)
    }
    http.get.mockResolvedValue({ data: { ...EMPTY, xp: 170 } })
    await act(async () => { await result.current.refresh() })
    expect(result.current.gain?.xp).toBe(10)
    expect(result.current.gain!.seq).toBeGreaterThan(first.seq)
    http.get.mockResolvedValue({ data: { ...EMPTY, xp: 900 } })
    await act(async () => { await result.current.afterReset() })
    expect(result.current.gain).toBeNull()
    http.get.mockResolvedValue({ data: { ...EMPTY, xp: 905 } })
    await act(async () => { await result.current.refresh() })
    expect(result.current.gain?.xp).toBe(5)
  })

  it('announces only first-time unlocked new ids, in canonical order, and clears on reset', async () => {
    const achievements = FULL.achievements.slice().reverse().map(a => ({
      ...a, new: ['first_task', 'night_owl', 'pocket'].includes(a.id),
    }))
    const http = { get: vi.fn().mockResolvedValue({ data: { ...FULL, achievements } }) }
    const { result } = renderHook(() => useProfileStats({ api: 'profile-test', http }))
    await waitFor(() => expect(result.current.unlocked?.ids).toEqual(['first_task', 'night_owl']))
    const first = result.current.unlocked!
    await act(async () => { await result.current.refresh() })
    expect(result.current.unlocked).toBe(first)
    http.get.mockResolvedValue({ data: { ...FULL, achievements: FULL.achievements.map(a => ({ ...a, new: false })) } })
    await act(async () => { await result.current.refresh() })
    expect(result.current.unlocked).toBe(first)
    http.get.mockResolvedValue({ data: { ...FULL, achievements: FULL.achievements.map(a => ({ ...a, new: a.id === 'polyglot' })) } })
    await act(async () => { await result.current.refresh() })
    expect(result.current.unlocked?.ids).toEqual(['polyglot'])
    expect(result.current.unlocked!.seq).toBeGreaterThan(first.seq)
    http.get.mockResolvedValue({ data: EMPTY })
    await act(async () => { await result.current.afterReset() })
    expect(result.current.unlocked).toBeNull()
  })
})

type QueueProps = Parameters<typeof useAchievementQueue>
const queue = (initial: QueueProps) => renderHook(({ args }) => useAchievementQueue(...args), { initialProps: { args: initial } })

describe('band queue', () => {
  it('shows no turn band, gives each achievement its full lifetime and appends new events', () => {
    vi.useFakeTimers()
    const h = queue([null, null])
    expect(h.result.current).toBeNull()
    const unlocked = { seq: 1, ids: ['first_task', 'night_owl'] as AchievementId[] }
    act(() => { vi.advanceTimersByTime(1000) })
    h.rerender({ args: [unlocked, null] })
    expect(h.result.current?.achievement).toBe('first_task')
    h.rerender({ args: [unlocked, null] })
    act(() => { vi.advanceTimersByTime(ACHV_LIFE_MS - 1) })
    expect(h.result.current?.achievement).toBe('first_task')
    h.rerender({ args: [{ seq: 2, ids: ['polyglot'] }, null] })
    act(() => { vi.advanceTimersByTime(1) })
    expect(h.result.current?.achievement).toBe('night_owl')
    act(() => { vi.advanceTimersByTime(ACHV_LIFE_MS) })
    expect(h.result.current?.achievement).toBe('polyglot')
    act(() => { vi.advanceTimersByTime(ACHV_LIFE_MS) })
    expect(h.result.current).toBeNull()
  })

  it('does not create bands when turns or gains arrive without unlocks or level-ups', () => {
    vi.useFakeTimers()
    const h = renderHook(({ turn, gain }) => ({ turn, gain, band: useAchievementQueue(null, null) }), {
      initialProps: { turn: { seq: 1 } as { seq: number } | null, gain: { seq: 1, xp: 40 } },
    })
    expect(h.result.current.band).toBeNull()
    act(() => { vi.advanceTimersByTime(1000) })
    h.rerender({ turn: { seq: 1 }, gain: { seq: 2, xp: 10 } })
    expect(h.result.current.band).toBeNull()
    act(() => { vi.advanceTimersByTime(ACHV_LIFE_MS - 1000) })
    expect(h.result.current.band).toBeNull()
    h.rerender({ turn: { seq: 1 }, gain: { seq: 3, xp: 20 } })
    expect(h.result.current.band).toBeNull()
    h.rerender({ turn: { seq: 2 }, gain: { seq: 3, xp: 20 } })
    expect(h.result.current.band).toBeNull()
    h.rerender({ turn: null, gain: { seq: 3, xp: 20 } })
    expect(h.result.current.band).toBeNull()
  })
})

const achievement = (id: AchievementId, at: string | null, unlocked = true): ProfileAchievement => ({
  id, unlocked, unlocked_at: at, goal: 1, progress: 1, new: false,
})

describe('latest earned achievement', () => {
  it('returns null without a dated unlock and skips unparseable or locked dates', () => {
    expect(latestUnlocked([])).toBeNull()
    expect(latestUnlocked([achievement('first_task', null), achievement('night_owl', 'invalid')])).toBeNull()
    const earned = achievement('first_task', '2026-10-01 12:00:00')
    expect(latestUnlocked([earned, achievement('night_owl', '2026-10-02', false)])).toBe(earned)
  })

  it('compares timestamps and resolves ties by canonical id order regardless of input order', () => {
    const later = achievement('first_task', '2026-10-02 12:00:00')
    expect(latestUnlocked([achievement('polyglot', '2026-10-02 11:00:00'), later])).toBe(later)
    const tie = achievement('polyglot', later.unlocked_at)
    expect(latestUnlocked([tie, later])).toBe(tie)
  })

  it.each(['tr', 'en'] as const)('shows the profile line with the translated name and date in %s', lang => {
    const view = render(<ProfileView open onClose={noop} data={normalizeProfileStats(FULL)} range="6m" onRangeChange={noop} />, { wrapper: provider(lang) })
    const line = screen.getByTestId('profile-last-achievement')
    expect(line.closest('.pf-xp')).not.toBeNull()
    expect(line.textContent).toBe(ceviriUygula(lang, 'pf.lastAch', {
      ad: ceviriUygula(lang, 'pf.ach.night_owl.name'), tarih: lang === 'tr' ? '2 Ekim 2026' : '2 October 2026',
    }))
    view.rerender(<ProfileView open onClose={noop} data={normalizeProfileStats(EMPTY)} range="6m" onRangeChange={noop} />)
    expect(screen.queryByTestId('profile-last-achievement')).toBeNull()
  })

  it.each(['tr', 'en'] as const)('uses the sidebar achievement tooltip unless XP is partial in %s', lang => {
    const props: React.ComponentProps<typeof Sidebar> = {
      isSidebarOpen: true, conversations: [], activeConvId: null,
      selectConversation: noop, createNewConversation: noop, deleteConversation: noop,
      editingId: null, setEditingId: noop, tempTitle: '', setTempTitle: noop, saveRename: noop,
      user: { id: 1, name: 'Burak', sessionToken: 't' }, setShowSettings: noop, handleLogout: noop,
      workspacePath: null, closeWorkspace: noop,
      profileLevel: { level: 1, xp: 10, levelXp: 10, levelNeed: 100, lastAch: 'night_owl' },
    }
    const view = render(<Sidebar {...props} />, { wrapper: provider(lang) })
    expect(screen.getByTestId('sidebar-profile').title).toBe(ceviriUygula(lang, 'pf.lastAchShort', { ad: ceviriUygula(lang, 'pf.ach.night_owl.name') }))
    view.rerender(<Sidebar {...props} profileLevel={{ ...props.profileLevel!, xp_partial: true }} />)
    expect(screen.getByTestId('sidebar-profile').title).toBe(ceviriUygula(lang, 'pf.xpPartial'))
    view.rerender(<Sidebar {...props} profileLevel={{ ...props.profileLevel!, lastAch: null }} />)
    expect(screen.getByTestId('sidebar-profile').title).toBe('Burak')
  })
})
