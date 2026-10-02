/**
 * The maker profile screen (mockup screen 2): each section drawn from a backend answer, the
 * fresh-install empty state, and an unreadable approval ledger. Data mapping is asserted in the
 * DOM, not on helper return values, so a section that stops rendering a number fails here.
 */
import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'

import { ProfileView } from '../renderer/components/home/ProfileView'
import { LangContext, ceviriUygula, tr, en, type Lang } from '../renderer/lib/i18n'
import { normalizeProfileStats, type ProfileRange } from '../renderer/lib/profileStats'
import { EMPTY, FULL, LEDGER_DOWN, dayIndex } from './fixtures/profileStats'

afterEach(() => cleanup())

function view(raw: unknown, { lang = 'en' as Lang, name = 'Burak' as string | null, range = '6m' as ProfileRange, onClose = vi.fn(), onRangeChange = vi.fn() } = {}) {
  const data = normalizeProfileStats(raw)
  expect(data).not.toBeNull()
  const utils = render(
    <LangContext.Provider value={{ lang, setLang: () => {}, t: (k, v) => ceviriUygula(lang, k, v) }}>
      <ProfileView open onClose={onClose} data={data} range={range} onRangeChange={onRangeChange} userName={name} />
    </LangContext.Provider>,
  )
  return { ...utils, onClose, onRangeChange }
}

const q = (sel: string) => document.querySelector(sel) as HTMLElement
const qa = (sel: string) => Array.from(document.querySelectorAll(sel)) as HTMLElement[]
const text = (sel: string) => q(sel)?.textContent?.replace(/\s+/g, ' ').trim()
const tile = (id: string) => q(`.pf-stat[data-stat="${id}"]`)

describe('profile · header', () => {
  it('shows level, name, rank, XP toward the next rank, the range tabs and the records date', () => {
    view(FULL)
    expect(screen.getByTestId('profile-level').getAttribute('aria-label')).toBe('Level 14')
    expect(text('.lvl-lg .lvl-n')).toBe('14')
    expect(text('.pf-name-t')).toBe('Burak')
    expect(text('.pf-rank')).toBe('Scene Master')
    expect(text('.pf-xp-text')).toBe('820 / 1,400 XP · next rank Prefab Wizard (level 15)')
    expect(q('.xp-bar > span').style.width).toBe(`${(820 / 1400) * 100}%`)
    expect(text('.pf-since')).toBe('Records since 14 April 2026')
    const tabs = screen.getAllByRole('tab')
    expect(tabs.map(t => t.textContent)).toEqual(['This month', 'Last 6 months', 'All time'])
    expect(tabs.map(t => t.getAttribute('aria-selected'))).toEqual(['false', 'true', 'false'])
  })

  it('speaks Turkish with Turkish number and date forms', () => {
    view(FULL, { lang: 'tr' })
    expect(text('.pf-rank')).toBe('Sahne Ustası')
    expect(text('.pf-xp-text')).toBe('820 / 1.400 XP · sonraki rütbe Prefab Büyücüsü (seviye 15)')
    expect(text('.pf-since')).toBe('Kayıtlar 14 Nisan 2026 tarihinden beri')
    expect(text('.pf-stat[data-stat="cards"] .pf-stat-v')).toBe('1.148')
  })

  it('the quip is the user\'s own running streak', () => {
    view(FULL)
    expect(text('.pf-quip')).toBe('6 days without a break. Come back tomorrow and the streak reaches 7.')
  })

  it('a range tab asks for its range; the selected one does not ask again', () => {
    const { onRangeChange } = view(FULL)
    fireEvent.click(screen.getByRole('tab', { name: 'This month' }))
    fireEvent.click(screen.getByRole('tab', { name: 'Last 6 months' }))
    expect(onRangeChange.mock.calls).toEqual([['month']])
  })

  it('the top rank has no next rank', () => {
    view({ ...FULL, rank: 'engine_whisperer', level: 22 })
    expect(text('.pf-xp-text')).toBe('820 / 1,400 XP · you hold the top rank')
  })

  it('a missing or "local" name shows no name, the rank stands alone', () => {
    view(FULL, { name: 'local' })
    expect(text('.pf-name')).toBe('Scene Master')
    expect(document.body.textContent).not.toMatch(/local/i)
  })
})

describe('profile · stat tiles', () => {
  it('draws exactly the six tiles that have a data source', () => {
    view(FULL)
    expect(qa('.pf-stat').map(e => e.dataset.stat)).toEqual(['tasks', 'cards', 'days', 'streak', 'hour', 'phone'])
    // Mockup tiles with no data yet are left out, not filled with a placeholder number.
    expect(document.body.textContent).not.toMatch(/Files written|Unity actions|Yazılan dosya|Unity işlemi/)
  })

  it('maps every tile to its backend field', () => {
    view(FULL)
    expect(within(tile('tasks')).getByText('312')).toBeTruthy()
    expect(text('.pf-stat[data-stat="tasks"] .pf-stat-s')).toBe('This month 48, 11 more than last month')
    expect(text('.pf-stat[data-stat="cards"] .pf-stat-v')).toBe('1,148')
    expect(text('.pf-stat[data-stat="cards"] .pf-stat-s')).toBe('The 37 you rejected saved the day too')
    expect(text('.pf-stat[data-stat="days"] .pf-stat-v')).toBe('163')
    expect(text('.pf-stat[data-stat="streak"] .pf-stat-v')).toBe('23 days')
    expect(text('.pf-stat[data-stat="streak"] .pf-stat-s')).toBe('Ended on 26 Aug')
    expect(text('.pf-stat[data-stat="hour"] .pf-stat-v')).toBe('23:00')
    expect(text('.pf-stat[data-stat="phone"] .pf-stat-v')).toBe('18')
  })

  it('fewer tasks than last month and a streak still running read as such', () => {
    view({ ...FULL, counts: { ...FULL.counts, tasks_this_month: 30 }, streak: { current: 23, longest: 23, longest_end: '2026-10-02' } })
    expect(text('.pf-stat[data-stat="tasks"] .pf-stat-s')).toBe('This month 30, 7 fewer than last month')
    expect(text('.pf-stat[data-stat="streak"] .pf-stat-s')).toBe('Still going')
  })
})

describe('profile · heat map', () => {
  it('lays 182 days out Monday-first in 26 columns, the days still to come as future cells', () => {
    view(FULL)
    const cells = qa('.heat-grid > i')
    expect(cells).toHaveLength(182)
    expect(cells[0].dataset.day).toBe('2026-04-06')
    expect(cells[7].dataset.day).toBe('2026-04-13') // column 2 starts the next Monday
    const future = cells.filter(c => c.classList.contains('h-x')).map(c => c.dataset.day)
    expect(future).toEqual(['2026-10-03', '2026-10-04'])
    const peak = cells[dayIndex('2026-08-19')]
    expect(peak.className).toBe('h4')
    expect(peak.getAttribute('title')).toBe('19 Aug: 27 tasks')
    expect(cells[dayIndex('2026-10-02')].className).toBe('h2')
    expect(cells[dayIndex('2026-05-01')].className).toBe('h0')
  })

  it('labels months where they start and sums the window in the meta and the facts', () => {
    view(FULL)
    // A month is labelled on the first column whose Monday falls in its first week (mockup rule):
    // Oct has none yet in this window (its first Monday is 5 Oct).
    expect(qa('.heat-months span').map(s => [s.textContent, s.style.gridColumn])).toEqual([
      ['Apr', '1'], ['May', '5'], ['Jun', '9'], ['Jul', '14'], ['Aug', '18'], ['Sept', '23'],
    ])
    expect(text('[data-testid="heat-meta"]')).toBe('You worked 6 days · busiest day 19 Aug (27 tasks)')
    const facts = qa('.heat-facts dd').map(d => d.textContent?.replace(/\s+/g, ' ').trim())
    // current streak, active days this month so far, busiest weekday (1 = Tuesday), tasks per active day
    expect(facts).toEqual(['6 days', '2 / 2', 'Tuesday', '1.9 tasks'])
  })
})

describe('profile · model mix and favourite', () => {
  it('draws one bar segment and one row per family with its share and count', () => {
    view(FULL)
    const segs = qa('.mix .mix-seg')
    expect(segs.map(s => [s.dataset.m, s.style.width])).toEqual([
      ['claude', '58%'], ['codex', '27%'], ['gemini', '11%'], ['other', '4%'],
    ])
    const rows = qa('.mix-list li')
    expect(rows.map(r => r.querySelector('.model-chip')?.textContent)).toEqual(['Claude Code', 'Codex', 'Antigravity', 'Ollama'])
    expect(rows.map(r => r.querySelector('.mix-n')?.textContent)).toEqual([
      '58% · 181 tasks', '27% · 84 tasks', '11% · 34 tasks', '4% · 13 tasks',
    ])
    // Logos from ModelLogos.tsx, one per named family.
    expect(rows.slice(0, 3).every(r => r.querySelector('svg.mix-logo'))).toBe(true)
  })

  it('names the favourite model with its family and its turn count', () => {
    view(FULL)
    const fav = screen.getByTestId('profile-favourite')
    expect(within(fav).getByText('claude-opus-5-5')).toBeTruthy()
    expect(within(fav).getByText('Claude Code')).toBeTruthy()
    expect(within(fav).getByText('37 tasks')).toBeTruthy()
  })

  it('folds families past the fifth into "Others" and names an unrecorded one', () => {
    view({
      ...FULL,
      models: {
        favourite: null,
        mix: ['claude', 'codex', 'agy', 'cursor', 'unknown', 'kimi'].map((family, i) => ({ family, turns: 10 - i, share: 0 })),
      },
    })
    expect(qa('.mix-list .model-chip').map(c => c.textContent)).toEqual(['Claude Code', 'Codex', 'Antigravity', 'Cursor', 'Others'])
    view({ ...FULL, models: { favourite: null, mix: [{ family: 'unknown', turns: 3, share: 1 }] } })
    expect(qa('.mix-list .model-chip').map(c => c.textContent)).toContain('Not recorded')
  })
})

describe('profile · achievement shelf', () => {
  it('shows all eight: unlocked with date, the new one marked, locked with hint and progress', () => {
    view(FULL)
    expect(text('.pf-shelf .pf-card-meta')).toBe('6 / 8 unlocked')
    const tiles = qa('.achv-tile')
    expect(tiles.map(t => t.dataset.ach)).toEqual(['first_task', 'tasks_100', 'tasks_1000', 'night_owl', 'streak_7', 'pocket', 'careful', 'polyglot'])
    const first = q('.achv-tile[data-ach="first_task"]')
    expect(first.classList.contains('is-got')).toBe(true)
    expect(first.querySelector('.achv-d')?.textContent).toBe('You finished your first task · 14 Apr')
    expect(q('.achv-tile[data-ach="night_owl"]').classList.contains('is-new')).toBe(true)
    expect(q('.achv-tile[data-ach="tasks_100"]').classList.contains('is-new')).toBe(false)
    const pocket = q('.achv-tile[data-ach="pocket"]')
    expect(pocket.classList.contains('is-locked')).toBe(true)
    expect(pocket.querySelector('.achv-t')?.firstChild?.textContent).toBe('From my pocket')
    expect(pocket.querySelector('.achv-hint')?.textContent).toBe('Approve 50 cards from your phone')
    expect(pocket.querySelector('.achv-pl .num')?.textContent).toBe('18 / 50')
    expect((pocket.querySelector('.achv-prog > span') as HTMLElement).style.width).toBe('36%')
    expect(q('.achv-tile[data-ach="tasks_1000"] .achv-pl .num').textContent).toBe('312 / 1,000')
  })

  it('names the achievements in Turkish', () => {
    view(FULL, { lang: 'tr' })
    const names = qa('.achv-t').map(t => t.firstChild?.textContent)
    expect(names).toEqual(['İlk görev', '100 görev', '1000 görev', 'Gece kuşu', 'Seri', 'Cebimden yönetirim', 'Dikkatli yapımcı', 'Model avcısı'])
  })
})

describe('profile · fresh install (all zeros)', () => {
  it('renders every section with its empty state and no invented number', () => {
    view(EMPTY, { name: null })
    for (const sel of ['.pf-head', '.pf-stats', '.pf-heat', '.pf-models', '.pf-shelf']) expect(q(sel)).toBeTruthy()
    expect(text('.lvl-lg .lvl-n')).toBe('1')
    expect(text('.pf-name')).toBe('Rookie')
    expect(text('.pf-xp-text')).toBe('0 / 100 XP · next rank Prototyper (level 5)')
    expect(text('.pf-since')).toBe('No records yet; they start with your first finished task.')
    expect(q('.pf-quip')).toBeNull()
    // tiles
    expect(qa('.pf-stat')).toHaveLength(6)
    expect(text('.pf-stat[data-stat="tasks"] .pf-stat-v')).toBe('0')
    expect(text('.pf-stat[data-stat="tasks"] .pf-stat-s')).toBe('Your first finished task is counted here')
    expect(text('.pf-stat[data-stat="streak"] .pf-stat-s')).toBe('Starts with your first task')
    expect(text('.pf-stat[data-stat="hour"] .pf-stat-v')).toBe('—')
    expect(text('.pf-stat[data-stat="hour"] .pf-stat-s')).toBe('Shows after 10 tasks')
    // heat map: drawn, empty, with its line
    expect(qa('.heat-grid > i')).toHaveLength(182)
    expect(qa('.heat-grid > i').filter(c => /^h[1-4]$/.test(c.className))).toHaveLength(0)
    expect(text('[data-testid="heat-meta"]')).toBe('Your first task starts filling this')
    expect(q('.pf-heat').classList.contains('is-empty')).toBe(true)
    expect(qa('.heat-facts dd').map(d => d.textContent)).toEqual(['0 days', '0 / 2', '—', '—'])
    // models
    expect(q('.mix')).toBeNull()
    expect(text('.pf-models .pf-empty')).toMatch(/^No tasks yet/)
    expect(text('[data-testid="profile-favourite"] .pf-fav-v')).toBe('Not enough use yetShows once one model finishes 5 tasks within 30 days.')
    // shelf: all eight locked, each with its hint and a 0 / goal bar
    expect(qa('.achv-tile.is-locked')).toHaveLength(8)
    expect(text('.pf-shelf .pf-card-meta')).toBe('0 / 8 unlocked')
    expect(qa('.achv-hint').every(h => (h.textContent || '').length > 0)).toBe(true)
    expect(q('.achv-tile[data-ach="first_task"] .achv-pl .num').textContent).toBe('0 / 1')
  })

  it('in Turkish the empty heat map and favourite say so', () => {
    view(EMPTY, { lang: 'tr', name: null })
    expect(text('[data-testid="heat-meta"]')).toBe('İlk görevinle burası dolmaya başlar')
    expect(text('[data-testid="profile-favourite"] .pf-fav-v > span')).toBe('Henüz yeterli kullanım yok')
  })
})

describe('profile · approval ledger unreadable (ledger_ok false)', () => {
  it('card-based counts show a dash with a tooltip, never 0', () => {
    view(LEDGER_DOWN)
    for (const id of ['cards', 'phone']) {
      const el = tile(id)
      expect(el.querySelector('.pf-stat-v')?.textContent).toBe('—')
      expect(el.getAttribute('title')).toBe('Approval records could not be read right now: the number is unknown, not zero. Try again shortly.')
      expect(el.classList.contains('is-unknown')).toBe(true)
      expect(el.querySelector('.pf-stat-s')?.textContent).toBe('Approval records could not be read')
    }
    // Task counts do not depend on the ledger and stay.
    expect(text('.pf-stat[data-stat="tasks"] .pf-stat-v')).toBe('312')
  })

  it('a ledger_ok false answer with numbers in it is still shown as unknown', () => {
    view({ ...FULL, ledger_ok: false })
    expect(text('.pf-stat[data-stat="cards"] .pf-stat-v')).toBe('—')
  })
})

describe('profile · close', () => {
  it('Esc and the back button return to the chat', () => {
    const { onClose } = view(FULL)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByTestId('profile-back'))
    expect(onClose).toHaveBeenCalledTimes(2)
  })

  it('Esc belongs to the settings screen while it is open above the profile', () => {
    const { onClose } = view(FULL)
    const settings = document.createElement('section')
    settings.dataset.testid = 'settings-screen'
    document.body.appendChild(settings)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).not.toHaveBeenCalled()
    settings.remove()
  })

  it('renders nothing while closed', () => {
    render(<ProfileView open={false} onClose={() => {}} data={normalizeProfileStats(FULL)} range="6m" onRangeChange={() => {}} />)
    expect(screen.queryByTestId('profile-view')).toBeNull()
  })
})

describe('profile · i18n', () => {
  const keys = Object.keys(tr).filter(k => k.startsWith('pf.') || k.startsWith('set.hesap.'))
  it('every profile string exists, non-empty, in both languages', () => {
    expect(keys.length).toBeGreaterThan(100)
    for (const k of keys) {
      expect((en as Record<string, string>)[k], k).toBeTruthy()
      expect((tr as Record<string, string>)[k], k).toBeTruthy()
    }
  })

  it('rank and achievement names are the agreed ones', () => {
    const rank = (l: Lang) => ['rookie', 'prototyper', 'scene_master', 'prefab_wizard', 'engine_whisperer'].map(r => ceviriUygula(l, `pf.rank.${r}`))
    expect(rank('tr')).toEqual(['Çaylak', 'Prototipçi', 'Sahne Ustası', 'Prefab Büyücüsü', 'Motor Fısıldayan'])
    expect(rank('en')).toEqual(['Rookie', 'Prototyper', 'Scene Master', 'Prefab Wizard', 'Engine Whisperer'])
    const ach = (l: Lang) => ['first_task', 'tasks_100', 'tasks_1000', 'night_owl', 'streak_7', 'pocket', 'careful', 'polyglot'].map(a => ceviriUygula(l, `pf.ach.${a}.name`))
    expect(ach('tr')).toEqual(['İlk görev', '100 görev', '1000 görev', 'Gece kuşu', 'Seri', 'Cebimden yönetirim', 'Dikkatli yapımcı', 'Model avcısı'])
    expect(ach('en')).toEqual(['First task', '100 tasks', '1000 tasks', 'Night owl', 'On a roll', 'From my pocket', 'Careful maker', 'Model hunter'])
  })
})
