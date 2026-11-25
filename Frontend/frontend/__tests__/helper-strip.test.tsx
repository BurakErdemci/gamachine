/**
 * The helpers strip (4.1.0 design D1, D6): chips up to four, a summary from five on, the "what is
 * it doing" line, the soft "no activity" warning, "Needs you" while a card waits; the list rows as
 * GET /subagents/list sends them (Backend/app/routes/SUBAGENTS_API.md), polled only while one runs.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, fireEvent, renderHook, act } from '@testing-library/react'

import { HelperStrip, doingLine } from '../renderer/components/home/HelperStrip'
import { parseHelperList, useHelpers, type Helper } from '../renderer/hooks/home/useHelpers'
import { aktifDilAyarla, cevir } from '../renderer/lib/i18n'

const NOW = 1_764_090_000_000

const helper = (over: Partial<Helper> & { name: string }): Helper => ({
  agent_id: over.name, chat_id: 40, model_name: 'gpt-6.2-sol', provider_type: 'subscription', effort: 'medium',
  status: 'running', last_result: null,
  progress: {
    phase: 'running_tool', step: { tool: 'Read', target: 'Assets/Scenes/Room2/Layout.prefab', seconds: 40 },
    steps_done: 12, steps_failed: 0, elapsed_s: 370, last_said: 'Blocking out the arena.', idle_s: 2, card: null, last_steps: [],
  },
  ...over,
})

const strip = (helpers: Helper[], extra: Partial<React.ComponentProps<typeof HelperStrip>> = {}) => {
  const calls = { open: [] as string[], stop: [] as string[], stopAll: 0 }
  const view = render(
    <HelperStrip helpers={helpers} fetchedAt={NOW} onOpen={h => calls.open.push(h.agent_id)}
      onStop={id => calls.stop.push(id)} onStopAll={() => { calls.stopAll++ }} {...extra} />,
  )
  return { ...view, calls }
}

beforeEach(() => { aktifDilAyarla('en'); vi.useFakeTimers({ toFake: ['Date'] }); vi.setSystemTime(NOW) })
afterEach(() => { cleanup(); vi.useRealTimers(); aktifDilAyarla('tr') })

describe('helper strip', () => {
  it('is absent for a chat without helpers', () => {
    const { container } = strip([])
    expect(container.innerHTML).toBe('')
  })

  it('1-4 helpers: one chip each, name, status word and what it is doing', () => {
    strip([
      helper({ name: 'level-designer' }),
      helper({ name: 'art', status: 'done', progress: null }),
    ])
    const chip = screen.getByTestId('helper-chip-level-designer')
    expect(chip.textContent).toContain('level-designer')
    expect(chip.querySelector('[data-testid="helper-status"]')!.textContent).toBe('Working')
    expect(chip.querySelector('[data-testid="helper-doing"]')!.textContent).toBe('Read Assets/Scenes/Room2/Layout.prefab · 40 s · 12 steps')
    const done = screen.getByTestId('helper-chip-art')
    expect(done.querySelector('[data-testid="helper-status"]')!.textContent).toBe('Done')
    // A finished helper's chip is quiet: no line, no stop button.
    expect(done.querySelector('[data-testid="helper-doing"]')).toBeNull()
    expect(done.querySelector('.crew-x')).toBeNull()
    expect(screen.queryByTestId('helper-summary')).toBeNull()
  })

  it('a click opens the conversation, the square stops that helper only', () => {
    const { calls } = strip([helper({ name: 'tester' })])
    fireEvent.click(screen.getByTestId('helper-chip-tester').querySelector('.crew-chip-top')!)
    fireEvent.click(screen.getByTestId('helper-chip-tester').querySelector('.crew-x')!)
    expect(calls.open).toEqual(['tester'])
    expect(calls.stop).toEqual(['tester'])
  })

  it('idle for 90 s or more: "no activity for N min", muted warning', () => {
    strip([helper({ name: 'lighting', progress: { ...helper({ name: 'x' }).progress!, idle_s: 125 } })])
    const line = screen.getByTestId('helper-doing')
    expect(line.textContent).toBe('no activity for 2 min')
    expect(line.dataset.tone).toBe('stale')
    expect(line.querySelector('svg')).toBeTruthy()
  })

  it('waiting on an approval: "Needs you" and the wait in m:ss', () => {
    strip([helper({ name: 'tester', progress: { ...helper({ name: 'x' }).progress!, phase: 'waiting_approval', step: null, card: { title: 'Run a command', waiting_s: 14 } } })])
    const chip = screen.getByTestId('helper-chip-tester')
    expect(chip.dataset.s).toBe('asks')
    expect(chip.querySelector('[data-testid="helper-status"]')!.textContent).toBe('Needs you')
    expect(chip.querySelector('[data-testid="helper-doing"]')!.textContent).toBe('waiting for your approval · 0:14')
  })

  it('a card of the helper on the parent screen also makes it "Needs you"', () => {
    strip([helper({ name: 'tester', chat_id: 41 })], { asking: new Set([41]) })
    expect(screen.getByTestId('helper-chip-tester').dataset.s).toBe('asks')
  })

  it('5 or more: one summary sentence, only the problem helpers stay chips, "Show all" lists everyone', () => {
    const many = [
      helper({ name: 'tester', progress: { ...helper({ name: 'x' }).progress!, phase: 'waiting_approval', card: { title: 'x', waiting_s: 14 } } }),
      helper({ name: 'perf-check', status: 'error', progress: null, last_result: 'error: batch mode has no Profiler window' }),
      helper({ name: 'room-2' }),
      helper({ name: 'room-3' }),
      helper({ name: 'art', status: 'done', progress: null }),
      helper({ name: 'vfx', status: 'stopped', progress: null }),
    ]
    const { calls } = strip(many, { fuse: { tripped: true, held: 2 } })
    expect(screen.getAllByTestId(/^helper-chip-/).map(el => el.dataset.testid)).toEqual(['helper-chip-tester', 'helper-chip-perf-check'])
    expect(screen.getByTestId('helper-summary').textContent).toBe(
      '6 helpers·2 working·1 done·1 error·1 waiting for you·1 idle or stopped·Paused, waiting for you')
    expect(screen.queryByTestId('helper-list')).toBeNull()
    fireEvent.click(screen.getByText('Show all'))
    const list = screen.getByTestId('helper-list')
    expect(list.querySelectorAll('[data-testid^="helper-row-"]')).toHaveLength(6)
    expect(screen.getByTestId('helper-row-room-2').textContent).toContain('“Blocking out the arena.”')
    expect(screen.getByTestId('helper-row-perf-check').textContent).toContain('error: batch mode has no Profiler window')
    expect(screen.getByTestId('helper-strip').className).toContain('is-expanded')
    fireEvent.click(screen.getByText('Stop all'))
    expect(calls.stopAll).toBe(1)
  })

  it('the hover card carries the whole record and its own Open / Stop', () => {
    const { calls } = strip([helper({ name: 'tester' })])
    fireEvent.mouseEnter(screen.getByTestId('helper-chip-tester'))
    const pop = screen.getByTestId('helper-pop')
    for (const k of ['Now', 'Steps', 'Elapsed', 'Last said', 'Last activity']) expect(pop.textContent).toContain(k)
    expect(pop.textContent).toContain('12 done · 0 failed')
    expect(pop.textContent).toContain('6:10')
    fireEvent.click(screen.getByText('Open conversation'))
    expect(calls.open).toEqual(['tester'])
  })

  it('counters keep ticking between reads', () => {
    const line = doingLine(helper({ name: 'a' }), cevir as any, 5)
    expect(line?.text).toBe('Read Assets/Scenes/Room2/Layout.prefab · 45 s · 12 steps')
  })
})

describe('helper list from the backend', () => {
  it('reads SUBAGENTS_API.md rows (id, child_conv) and the fuse', () => {
    const { helpers, fuse } = parseHelperList({
      agents: [{ id: 3, parent_conv: 12, child_conv: 40, name: 'tests', provider_type: 'subscription', model_name: 'gpt-6-luna', effort: 'high', status: 'running', progress: null }],
      fuse: { tripped: false, count: 2, limit: 10, paused: false, held: 0 },
    })
    expect(helpers[0]).toMatchObject({ agent_id: '3', chat_id: 40, name: 'tests', status: 'running' })
    expect(fuse).toEqual({ tripped: false, paused: false, held: 0, count: 2, limit: 10 })
  })

  it('polls every 2 s only while a helper runs, and stops when none does', async () => {
    vi.useRealTimers()
    vi.useFakeTimers()
    let status = 'running'
    const http = {
      get: vi.fn(async () => ({ data: { agents: [{ id: 1, child_conv: 40, name: 'a', status }], fuse: null } })),
      post: vi.fn(async () => ({ data: {} })),
    }
    renderHook(() => useHelpers({ API: 'http://x', conversationId: 12, http }))
    await act(async () => { await Promise.resolve() })
    expect(http.get).toHaveBeenCalledWith('http://x/subagents/list?conversation_id=12', undefined)
    const first = http.get.mock.calls.length
    await act(async () => { vi.advanceTimersByTime(2000); await Promise.resolve() })
    expect(http.get.mock.calls.length).toBe(first + 1)
    status = 'done'
    await act(async () => { vi.advanceTimersByTime(2000); await Promise.resolve() })
    const settled = http.get.mock.calls.length
    await act(async () => { vi.advanceTimersByTime(10000); await Promise.resolve() })
    expect(http.get.mock.calls.length).toBe(settled)
  })

  it('a chat without helpers is read once and never polled', async () => {
    vi.useRealTimers()
    vi.useFakeTimers()
    const http = { get: vi.fn(async () => ({ data: { agents: [], fuse: null } })), post: vi.fn() }
    renderHook(() => useHelpers({ API: 'http://x', conversationId: 12, http }))
    await act(async () => { await Promise.resolve() })
    await act(async () => { vi.advanceTimersByTime(20000); await Promise.resolve() })
    expect(http.get).toHaveBeenCalledTimes(1)
  })

  it('Stop sends the helper and its parent chat', async () => {
    const http = { get: vi.fn(async () => ({ data: { agents: [] } })), post: vi.fn(async () => ({ data: {} })) }
    const { result } = renderHook(() => useHelpers({ API: 'http://x', conversationId: 12, http, token: 'tok' }))
    await act(async () => { await result.current.stop('3') })
    expect(http.post).toHaveBeenCalledWith('http://x/subagents/stop', { conversation_id: 12, agent_id: '3' }, { headers: { 'X-Session-Token': 'tok' } })
  })
})
