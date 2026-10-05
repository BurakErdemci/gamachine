import React, { useEffect } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, renderHook, screen } from '@testing-library/react'
import { AchievementToast, ACHV_LIFE_MS } from '../renderer/components/home/AchievementToast'
import { useProfileStats } from '../renderer/hooks/home/useProfileStats'
import { useAchievementQueue } from '../renderer/lib/achievementQueue'
import { useTurnDone } from '../renderer/lib/turnDone'
import { LangContext, ceviriUygula, type Lang } from '../renderer/lib/i18n'
import { RANK_EVERY, RANKS } from '../renderer/lib/profileStats'
import { EMPTY } from './fixtures/profileStats'

afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks() })

const statsAt = (level: number) => ({
  ...EMPTY, level, xp: level * 100,
  rank: RANKS[Math.min(RANKS.length - 1, Math.floor(level / RANK_EVERY))],
})

async function mount(lang: Lang = 'en', level = 3) {
  vi.useFakeTimers()
  const http = { get: vi.fn().mockResolvedValue({ data: statsAt(level) }) }
  let current!: ReturnType<typeof useProfileStats>
  function Harness({ token, turnSeq }: { token: string; turnSeq: number | null }) {
    current = useProfileStats({ api: 'profile-test', token, http })
    const turn = useTurnDone({ 1: {
      approvals: [], bridgeGates: [], awaiting: false,
      turnEnd: turnSeq === null ? null : { seq: turnSeq, failed: false },
    } }, 1, false)
    const { refresh } = current
    useEffect(() => { if (turn) void refresh() }, [turn?.seq, refresh])
    const band = useAchievementQueue(current.unlocked, current.levelUp)
    return <AchievementToast event={band} achievement={band?.achievement} levelUp={band?.levelUp} />
  }
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <LangContext.Provider value={{ lang, setLang: () => {}, t: (k, v) => ceviriUygula(lang, k, v) }}>
      {children}
    </LangContext.Provider>
  )
  const view = render(<Harness token="first" turnSeq={null} />, { wrapper })
  await act(async () => {})
  expect(current.latest?.level).toBe(level)
  return {
    http, current: () => current,
    refresh: async (data: ReturnType<typeof statsAt>) => {
      http.get.mockResolvedValue({ data })
      await act(async () => { await current.refresh() })
    },
    switchUser: async () => {
      view.rerender(<Harness token="second" turnSeq={null} />)
      await act(async () => {})
    },
    finishTurn: async () => {
      view.rerender(<Harness token="first" turnSeq={1} />)
      await act(async () => {})
    },
  }
}

const toast = () => screen.queryByTestId('achievement-toast')
const title = () => toast()?.querySelector('.achv-title')?.textContent
const advance = (ms: number) => act(() => { vi.advanceTimersByTime(ms) })

describe('level-up bands', () => {
  it.each(['tr', 'en'] as const)('announces 3 to 4 once with translated text in %s', async lang => {
    const h = await mount(lang)
    expect(toast()).toBeNull()
    await h.refresh(statsAt(4))
    expect(h.current().levelUp).toMatchObject({ level: 4, rank: 'rookie', rankChanged: false })
    expect(screen.getAllByTestId('achievement-toast')).toHaveLength(1)
    expect(title()).toBe(lang === 'tr' ? 'Seviye 4' : 'Level 4')
    for (const slot of ['.lex-d', '.lex-q', '.lex-s']) {
      expect(toast()?.querySelector(slot)?.textContent).toBe(lang === 'tr' ? 'Seviye atladın' : 'Level up')
    }
    expect(toast()?.getAttribute('role')).toBe('status')
    expect(toast()?.querySelector('.achv-sub')).toBeNull()
    expect(toast()?.querySelector('.achv-xp')).toBeNull()
    advance(1000)
    await h.refresh(statsAt(4))
    advance(ACHV_LIFE_MS - 1000)
    expect(toast()).toBeNull()
    await h.refresh(statsAt(4))
    expect(toast()).toBeNull()
  })

  it('announces only the final level for 4 to 6 in a single refresh', async () => {
    const h = await mount('en', 4)
    await h.refresh(statsAt(6))
    expect(title()).toBe('Level 6')
    expect(screen.getAllByTestId('achievement-toast')).toHaveLength(1)
    advance(ACHV_LIFE_MS)
    expect(toast()).toBeNull()
  })

  it.each(['tr', 'en'] as const)('reuses the profile rank label at the rank boundary in %s', async lang => {
    const h = await mount(lang, RANK_EVERY - 1)
    await h.refresh(statsAt(RANK_EVERY))
    expect(h.current().levelUp).toMatchObject({ rank: 'prototyper', rankChanged: true })
    expect(toast()?.querySelector('.achv-sub')?.textContent).toBe(ceviriUygula(lang, 'pf.rank.prototyper'))
  })

  it('does not announce the first load or a user switch and clears old queued bands', async () => {
    const h = await mount('en', 14)
    expect(h.current().levelUp).toBeNull()
    expect(toast()).toBeNull()
    await h.refresh(statsAt(15))
    await h.refresh(statsAt(16))
    expect(title()).toBe('Level 15')
    h.http.get.mockResolvedValue({ data: statsAt(20) })
    await h.switchUser()
    expect(h.current().latest?.level).toBe(20)
    expect(h.current().levelUp).toBeNull()
    expect(toast()).toBeNull()
    await h.refresh(statsAt(21))
    expect(title()).toBe('Level 21')
    expect(h.current().levelUp?.rankChanged).toBe(false)
  })

  it('discards an old user response that settles after the user switch', async () => {
    const h = await mount()
    let release!: (value: { data: unknown }) => void
    h.http.get.mockReturnValueOnce(new Promise(resolve => { release = resolve }))
    let pending!: Promise<void>
    act(() => { pending = h.current().refresh() })
    h.http.get.mockResolvedValue({ data: statsAt(10) })
    await h.switchUser()
    await act(async () => { release({ data: statsAt(20) }); await pending })
    expect(h.current().latest?.level).toBe(10)
    expect(h.current().levelUp).toBeNull()
    expect(toast()).toBeNull()
  })

  it('queues an unlock and level-up from the same refresh with separate full lifetimes', async () => {
    const h = await mount()
    await h.refresh({ ...statsAt(4), achievements: EMPTY.achievements.map(a => (
      a.id === 'first_task' ? { ...a, unlocked: true, new: true } : a
    )) })
    expect(title()).toBe(ceviriUygula('en', 'pf.ach.first_task.name'))
    advance(ACHV_LIFE_MS - 1)
    expect(title()).toBe(ceviriUygula('en', 'pf.ach.first_task.name'))
    advance(1)
    expect(title()).toBe('Level 4')
    advance(ACHV_LIFE_MS - 1)
    expect(title()).toBe('Level 4')
    advance(1)
    expect(toast()).toBeNull()
  })

  it('preserves arrival order when an unlock arrives after a level-up', () => {
    vi.useFakeTimers()
    const levelUp = { seq: 1, level: 4, rank: 'rookie' as const, rankChanged: false }
    const h = renderHook(({ unlocked }) => useAchievementQueue(unlocked, levelUp), {
      initialProps: { unlocked: null as { seq: number; ids: ['night_owl'] } | null },
    })
    expect(h.result.current?.levelUp).toEqual(levelUp)
    advance(1000)
    h.rerender({ unlocked: { seq: 1, ids: ['night_owl'] } })
    advance(ACHV_LIFE_MS - 1000)
    expect(h.result.current?.achievement).toBe('night_owl')
    advance(ACHV_LIFE_MS)
    expect(h.result.current).toBeNull()
  })

  it('refreshes after a finished turn and counts XP silently without a band', async () => {
    const h = await mount()
    h.http.get.mockResolvedValue({ data: { ...statsAt(3), xp: 337 } })
    await h.finishTurn()
    expect(h.http.get).toHaveBeenCalledTimes(2)
    expect(h.current().gain?.xp).toBe(37)
    expect(h.current().latest?.xp).toBe(337)
    expect(toast()).toBeNull()
  })

  it('does not create level events for failures, unchanged or lower levels, or reset baselines', async () => {
    const h = await mount('en', 4)
    for (const level of [4, 3]) {
      await h.refresh(statsAt(level))
      expect(h.current().levelUp).toBeNull()
    }
    h.http.get.mockRejectedValueOnce(new Error('down'))
    await act(async () => { await h.current().refresh() })
    expect(h.current().levelUp).toBeNull()
    await h.refresh(statsAt(5))
    expect(title()).toBe('Level 5')
    h.http.get.mockResolvedValue({ data: statsAt(10) })
    await act(async () => { await h.current().afterReset() })
    expect(h.current().levelUp).toBeNull()
    expect(toast()).toBeNull()
  })
})
