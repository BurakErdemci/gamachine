/**
 * The achievement band ("Done" toast) fires on the app's existing "finished" moment:
 * `attention[id].turnEnd`, the event behind the desktop "finished" notification. These tests pin
 * when it fires and, as importantly, when it does not.
 */
import { describe, it, expect, afterEach, vi } from 'vitest'
import React from 'react'
import { renderHook, render, screen, cleanup, act } from '@testing-library/react'
import { useTurnDone, cardOnScreen } from '../renderer/lib/turnDone'
import { AchievementToast, ACHV_LIFE_MS } from '../renderer/components/home/AchievementToast'

afterEach(() => { cleanup(); vi.useRealTimers() })

const att = (seq: number | null, extra: Partial<{ failed: boolean; awaiting: boolean }> = {}) => ({
  approvals: [], bridgeGates: [], awaiting: !!extra.awaiting,
  turnEnd: seq == null ? null : { seq, failed: !!extra.failed },
})

const mount = (initial: any, active = 1, card = false) =>
  renderHook(({ a, id, c }) => useTurnDone(a, id, c), { initialProps: { a: initial, id: active as number | null, c: card } })

describe('useTurnDone', () => {
  it('fires when the on-screen chat finishes a turn', () => {
    const h = mount({ 1: att(null) })
    expect(h.result.current).toBeNull()
    h.rerender({ a: { 1: att(5) }, id: 1, c: false })
    expect(h.result.current).toEqual({ seq: 5, convId: 1 })
  })

  it('does not replay a turn end that was already there when the chat was first seen', () => {
    const h = mount({ 1: att(3) })
    expect(h.result.current).toBeNull()
    h.rerender({ a: { 1: att(3) }, id: 1, c: false })
    expect(h.result.current).toBeNull()
  })

  it('stays quiet for a failed turn, a turn ending on a card, and a background chat', () => {
    const h = mount({ 1: att(null), 2: att(null) })
    h.rerender({ a: { 1: att(1, { failed: true }), 2: att(null) }, id: 1, c: false })
    expect(h.result.current).toBeNull()
    h.rerender({ a: { 1: att(2, { awaiting: true }), 2: att(null) }, id: 1, c: false })
    expect(h.result.current).toBeNull()
    h.rerender({ a: { 1: att(3), 2: att(null) }, id: 1, c: true })
    expect(h.result.current).toBeNull()
    h.rerender({ a: { 1: att(3), 2: att(4) }, id: 1, c: false })
    expect(h.result.current).toBeNull()
  })

  it('drops the band when the user switches to another chat', () => {
    const h = mount({ 1: att(null), 2: att(null) })
    h.rerender({ a: { 1: att(7), 2: att(null) }, id: 1, c: false })
    expect(h.result.current?.convId).toBe(1)
    h.rerender({ a: { 1: att(7), 2: att(null) }, id: 2, c: false })
    expect(h.result.current).toBeNull()
  })
})

describe('AchievementToast', () => {
  it('shows the finished chat and leaves after its lifetime', () => {
    vi.useFakeTimers()
    render(<AchievementToast event={{ seq: 1 }} title="Score board" />)
    const band = screen.getByTestId('achievement-toast')
    expect(band.getAttribute('role')).toBe('status')
    expect(band.textContent).toContain('Score board')
    act(() => { vi.advanceTimersByTime(ACHV_LIFE_MS) })
    expect(screen.queryByTestId('achievement-toast')).toBeNull()
  })

  it('draws nothing without an event', () => {
    render(<AchievementToast event={null} title="x" />)
    expect(screen.queryByTestId('achievement-toast')).toBeNull()
  })
})

describe('cardOnScreen (what home.tsx hands useTurnDone)', () => {
  it('counts the diff card and an unityMCP gate, not only delete / create cards', () => {
    expect(cardOnScreen({})).toBe(false)
    expect(cardOnScreen({ pendingFix: { data: {} } })).toBe(true)
    expect(cardOnScreen({ activeGate: { gateId: 'g1' } })).toBe(true)
    expect(cardOnScreen({ pendingDelete: { path: 'a' } })).toBe(true)
    expect(cardOnScreen({ pendingGenFiles: { files: [] } })).toBe(true)
  })
})
