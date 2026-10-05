/**
 * Subscription usage (`GET /usage/limits`, contract fixed 2 Oct 2026): the hook that reads it,
 * the window picking, and the meters' four states. Nothing here may draw a number the backend
 * did not send: loading = skeleton, error + windows = the old numbers dimmed with "stale",
 * unavailable or a missing family = no meters at all.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, renderHook, screen, cleanup, act } from '@testing-library/react'

vi.mock('axios', () => {
  const get = vi.fn()
  return { default: { get, post: vi.fn(), defaults: { headers: { common: {} } } }, get }
})

import axios from 'axios'
import { useUsageLimits, resetUsageLimitsCache, USAGE_POLL_IDLE_MS, USAGE_POLL_OPEN_MS } from '../renderer/hooks/home/useUsageLimits'
import { pickPair, meterState, familyFor, resetLabel, type UsageFamily, type UsageLimits } from '../renderer/lib/usageLimits'
import { Meter, UseBlock, UsePair, ChipUse } from '../renderer/components/home/UsageMeters'
import { aktifDilAyarla, cevir } from '../renderer/lib/i18n'

const get = (axios as any).get as ReturnType<typeof vi.fn>
const API = 'http://127.0.0.1:8000'
const NOW = '2026-10-02T12:00:00Z'

const win = (id: string, kind: '5h' | 'week', used_pct: number, group: string | null = null) => ({
  id, group, label: id, kind, used_pct, resets_at: kind === '5h' ? '2026-10-02T15:40:00Z' : '2026-10-06T07:00:00Z', resets_text: null,
})
const fam = (family: UsageFamily['family'], over: Partial<UsageFamily> = {}): UsageFamily => ({
  family, status: 'ok', plan: 'Max', measured_at: '2026-10-02T11:58:00Z', stale: false, error: null,
  windows: [win('five_hour', '5h', 62), win('week', 'week', 41), win('week_opus', 'week', 90)],
  ...over,
})
const limits = (...families: UsageFamily[]): UsageLimits => ({ now: NOW, families })

beforeEach(() => {
  aktifDilAyarla('tr')
  get.mockReset()
  resetUsageLimitsCache()
})
afterEach(() => { cleanup(); vi.useRealTimers() })

describe('window picking', () => {
  it('Claude: the 5 h window and the window with id "week", not the first weekly one', () => {
    const f = fam('claude', { windows: [win('week_opus', 'week', 90), win('five_hour', '5h', 62), win('week', 'week', 41)] })
    const { five, week } = pickPair(f, 'claude-opus-5-5')
    expect(five!.used_pct).toBe(62)
    expect(week!.id).toBe('week')
  })

  it('Codex: the first window of each kind', () => {
    const f = fam('codex', { windows: [win('primary', '5h', 86), win('secondary', 'week', 55)] })
    expect(pickPair(f, 'gpt-6-sol')).toMatchObject({ five: { used_pct: 86 }, week: { used_pct: 55 } })
  })

  it('Antigravity: the group of the model on screen, else the fullest window', () => {
    const f = fam('agy', { windows: [
      win('g5', '5h', 12, 'Gemini'), win('gw', 'week', 20, 'Gemini'),
      win('c5', '5h', 70, 'Claude and GPT'), win('cw', 'week', 33, 'Claude and GPT'),
    ] })
    expect(pickPair(f, 'gemini-3.8-flash')).toMatchObject({ five: { id: 'g5' }, week: { id: 'gw' } })
    expect(pickPair(f, 'agy-claude-sonnet-5-5')).toMatchObject({ five: { id: 'c5' }, week: { id: 'cw' } })
    // A model of another family on screen (or none): the limit that bites first.
    expect(pickPair(f, null)).toMatchObject({ five: { id: 'c5' }, week: { id: 'cw' } })
  })

  it('a family missing from the response has no pair', () => {
    expect(familyFor(limits(fam('claude')), 'codex')).toBeNull()
    expect(pickPair(null, 'x')).toEqual({ five: null, week: null })
  })

  it('meter state per status', () => {
    expect(meterState(fam('claude'))).toBe('ok')
    expect(meterState(fam('claude', { status: 'loading', windows: [] }))).toBe('loading')
    expect(meterState(fam('claude', { status: 'error', stale: true }))).toBe('stale')
    expect(meterState(fam('claude', { status: 'error', windows: [] }))).toBe('none')
    expect(meterState(fam('claude', { status: 'unavailable' }))).toBe('none')
    expect(meterState(null)).toBe('none')
  })

  it('the reset time is a clock time within a day, weekday + time further out, else the backend text', () => {
    expect(resetLabel(win('five_hour', '5h', 1), 'en', NOW)).toMatch(/^\d\d:\d\d$/)
    expect(resetLabel(win('week', 'week', 1), 'en', NOW)).toMatch(/^[A-Za-z]{3} \d\d:\d\d$/)
    expect(resetLabel({ ...win('x', 'week', 1), resets_at: null, resets_text: 'in 3 days' }, 'en', NOW)).toBe('in 3 days')
    expect(resetLabel({ ...win('x', 'week', 1), resets_at: null }, 'en', NOW)).toBe('')
  })
})

describe('meters: the four states', () => {
  it('ok: the numbers, ten segments, accent at >= 80 %', () => {
    render(<UseBlock fam={fam('claude', { windows: [win('five_hour', '5h', 86), win('week', 'week', 41)] })} nowIso={NOW} />)
    const block = screen.getByTestId('use-block')
    expect(block.getAttribute('data-state')).toBe('ok')
    expect(block.textContent).toContain('%86')
    expect(block.textContent).toContain('%41')
    const meters = block.querySelectorAll('.meter')
    expect(meters[0].querySelectorAll('i')).toHaveLength(10)
    expect(meters[0].querySelectorAll('i.on')).toHaveLength(9)
    expect(meters[0].classList.contains('is-hot')).toBe(true)
    expect(meters[1].classList.contains('is-hot')).toBe(false)
    expect(screen.queryByTestId('use-stale')).toBeNull()
  })

  it('loading: skeleton meters, no number', () => {
    render(<UseBlock fam={fam('claude', { status: 'loading', windows: [] })} />)
    const block = screen.getByTestId('use-block')
    expect(block.getAttribute('data-state')).toBe('loading')
    expect(block.querySelectorAll('.meter.is-loading')).toHaveLength(2)
    expect(block.querySelectorAll('i.on')).toHaveLength(0)
    expect(block.textContent).not.toMatch(/%\d/)
  })

  it('error with stale windows: the last numbers, dimmed, with the "stale" note', () => {
    render(<UseBlock fam={fam('claude', { status: 'error', stale: true, error: 'timeout' })} nowIso={NOW} />)
    expect(screen.getByTestId('use-block').getAttribute('data-state')).toBe('stale')
    expect(screen.getByTestId('use-block').textContent).toContain('%62')
    expect(screen.getByTestId('use-stale').textContent).toBe(cevir('use.stale'))
  })

  it('unavailable, or a family the backend did not send: no meters at all', () => {
    const { container } = render(<>
      <UseBlock fam={fam('claude', { status: 'unavailable' })} />
      <UsePair fam={null} />
      <ChipUse limits={limits(fam('claude'))} family="codex" providerName="Codex" />
      <ChipUse limits={limits(fam('claude'))} family={null} providerName="OpenRouter" />
    </>)
    expect(container.querySelectorAll('.meter, .chip-use')).toHaveLength(0)
    expect(container.textContent).toBe('')
  })

  it('the chip hairlines carry the 5 h and week values and turn hot at 80 %', () => {
    render(<ChipUse limits={limits(fam('codex', { windows: [win('primary', '5h', 86), win('secondary', 'week', 55)] }))}
      family="codex" modelId="gpt-6-sol" providerName="Codex" />)
    const bars = screen.getByTestId('chip-use').querySelectorAll('i')
    expect((bars[0] as HTMLElement).style.getPropertyValue('--u')).toBe('86%')
    expect((bars[1] as HTMLElement).style.getPropertyValue('--u')).toBe('55%')
    expect(bars[0].classList.contains('is-hot')).toBe(true)
    expect(bars[1].classList.contains('is-hot')).toBe(false)
    expect(screen.getByText(cevir('use.sr', { ad: 'Codex', bes: 86, hafta: 55 }))).toBeTruthy()
  })

  it('a bare Meter clamps and rounds: never above ten segments', () => {
    const { container } = render(<><Meter value={140} /><Meter value={-5} /><Meter value={null} /></>)
    const m = container.querySelectorAll('.meter')
    expect(m[0].querySelectorAll('i.on')).toHaveLength(10)
    expect(m[1].querySelectorAll('i.on')).toHaveLength(0)
    expect(m[2].querySelectorAll('i.on')).toHaveLength(0)
    expect(m[2].getAttribute('data-v')).toBeNull()
  })
})

describe('useUsageLimits', () => {
  const ok = (data: UsageLimits) => get.mockResolvedValue({ data })

  it('reads once with the session token, then every 5 minutes', async () => {
    vi.useFakeTimers()
    ok(limits(fam('claude')))
    const { result } = renderHook(() => useUsageLimits({ api: API, token: 'tok' }))
    await act(async () => { await Promise.resolve() })
    expect(get).toHaveBeenCalledTimes(1)
    expect(get).toHaveBeenCalledWith(`${API}/usage/limits`, { params: undefined, headers: { 'X-Session-Token': 'tok' } })
    expect(result.current.data!.families[0].family).toBe('claude')
    await act(async () => { vi.advanceTimersByTime(USAGE_POLL_IDLE_MS - 1) })
    expect(get).toHaveBeenCalledTimes(1)
    await act(async () => { vi.advanceTimersByTime(1) })
    expect(get).toHaveBeenCalledTimes(2)
  })

  it('opening the menu asks once with ?refresh=1, then polls every minute', async () => {
    vi.useFakeTimers()
    ok(limits(fam('claude')))
    const { rerender } = renderHook(({ open }) => useUsageLimits({ api: API, token: 't', menuOpen: open }), { initialProps: { open: false } })
    await act(async () => { await Promise.resolve() })
    get.mockClear()
    rerender({ open: true })
    await act(async () => { await Promise.resolve() })
    expect(get).toHaveBeenCalledTimes(1)
    expect(get.mock.calls[0][1].params).toEqual({ refresh: 1 })
    await act(async () => { vi.advanceTimersByTime(USAGE_POLL_OPEN_MS) })
    expect(get).toHaveBeenCalledTimes(2)
    expect(get.mock.calls[1][1].params).toBeUndefined()
    // Closing goes back to the slow poll without an extra read.
    rerender({ open: false })
    await act(async () => { vi.advanceTimersByTime(USAGE_POLL_OPEN_MS) })
    expect(get).toHaveBeenCalledTimes(2)
  })

  it('a failed read keeps the last answer; a remount shows the cached one at once', async () => {
    ok(limits(fam('claude')))
    const first = renderHook(() => useUsageLimits({ api: API, token: 't' }))
    await act(async () => { await Promise.resolve() })
    expect(first.result.current.data).not.toBeNull()
    first.unmount()

    get.mockRejectedValue(new Error('offline'))
    const second = renderHook(() => useUsageLimits({ api: API, token: 't' }))
    // Drawn from the cache before the (failing) request answers.
    expect(second.result.current.data!.families[0].family).toBe('claude')
    await act(async () => { await Promise.resolve() })
    expect(second.result.current.failed).toBe(true)
    expect(second.result.current.data!.families[0].family).toBe('claude')
  })

  it('no answer at all (endpoint missing): no data, so no meters — nothing is made up', async () => {
    get.mockRejectedValue(Object.assign(new Error('404'), { response: { status: 404 } }))
    const { result } = renderHook(() => useUsageLimits({ api: API, token: 't' }))
    await act(async () => { await Promise.resolve() })
    expect(result.current.data).toBeNull()
    expect(result.current.failed).toBe(true)
  })

  it('a malformed body is not taken as data', async () => {
    get.mockResolvedValue({ data: { families: 'nope' } })
    const { result } = renderHook(() => useUsageLimits({ api: API, token: 't' }))
    await act(async () => { await Promise.resolve() })
    expect(result.current.data).toBeNull()
  })

  it('does nothing until enabled', async () => {
    renderHook(() => useUsageLimits({ api: API, token: 't', enabled: false, menuOpen: true }))
    await act(async () => { await Promise.resolve() })
    expect(get).not.toHaveBeenCalled()
  })
})
