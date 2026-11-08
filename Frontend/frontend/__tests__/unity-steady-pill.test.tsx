import React from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { act, cleanup, render, renderHook, screen } from '@testing-library/react'
import { UnityMcpToggle, UNITY_DROP_GRACE_MS, useShownUnityStatus, useUnityLinkPulse } from '../renderer/components/home/UnityMcpToggle'
import type { UnityMCPStatus } from '../renderer/hooks/home/useAIConfig'
import { tr } from '../renderer/lib/i18n'

afterEach(() => { cleanup(); vi.useRealTimers() })
const base = { toggling: false, reason: null, error: null, onToggle: vi.fn() }
const ON = tr['unity.wordOn']
const OFF_WORD = 'Bağlı değil'

it('a drop after connected keeps "Bağlı" for 60 s, then shows "Bağlı değil"', () => {
  vi.useFakeTimers()
  const view = render(<UnityMcpToggle {...base} status="connected" />)
  view.rerender(<UnityMcpToggle {...base} status="running" />)
  act(() => { vi.advanceTimersByTime(30_000) })
  expect(screen.getByText(ON)).toBeTruthy()
  expect(screen.queryByText(OFF_WORD)).toBeNull()
  act(() => { vi.advanceTimersByTime(29_000) })
  expect(screen.getByText(ON)).toBeTruthy()
  act(() => { vi.advanceTimersByTime(1_000) })
  expect(screen.getByText(OFF_WORD)).toBeTruthy()
  expect(screen.queryByText(/Bağlanıyor/)).toBeNull()
  expect(UNITY_DROP_GRACE_MS).toBe(60_000)
})

it('unmounting mid-grace leaves no timer behind', () => {
  vi.useFakeTimers()
  const view = render(<UnityMcpToggle {...base} status="connected" />)
  view.rerender(<UnityMcpToggle {...base} status="running" />)
  expect(vi.getTimerCount()).toBeGreaterThan(0)
  view.unmount()
  expect(vi.getTimerCount()).toBe(0)
})

it('inside the grace nothing on the pill contradicts "Bağlı"; after it the waiting extras appear', () => {
  vi.useFakeTimers()
  const extras = { slowConnect: true, editorOpen: false, onLaunchUnity: vi.fn(),
    reasonCode: 'bridge_compile_failed', reasonLines: ['A.cs(1,1): error CS1002'] }
  const view = render(<UnityMcpToggle {...base} {...extras} status="connected" />)
  view.rerender(<UnityMcpToggle {...base} {...extras} status="running" />)
  act(() => { vi.advanceTimersByTime(45_000) })
  expect(screen.getByText(ON)).toBeTruthy()
  expect(screen.queryByText(tr['unity.hintBringForwardGeneric'])).toBeNull()
  expect(screen.queryByText(tr['unity.launch'])).toBeNull()
  expect(screen.queryByText(/CS1002/)).toBeNull()
  view.rerender(<UnityMcpToggle {...base} {...extras} status="running" dialog={{ title: 'Save Scene', buttons: ['OK'] }} />)
  expect(screen.queryByText(/Save Scene/)).toBeNull()
  act(() => { vi.advanceTimersByTime(15_000) })
  // After the grace the compile reason takes the hint's place; without it the dialog hint shows.
  view.rerender(<UnityMcpToggle {...base} {...extras} reasonCode={null} status="running"
    dialog={{ title: 'Save Scene', buttons: ['OK'] }} />)
  expect(screen.getByText(/Save Scene/)).toBeTruthy()
  expect(screen.getByText(OFF_WORD)).toBeTruthy()
  expect(screen.getByText(tr['unity.launch'])).toBeTruthy()
})

it('connected -> running -> connected inside the grace never shows "Bağlı değil" and does not pulse', () => {
  vi.useFakeTimers()
  const seen: string[] = []
  const { result, rerender } = renderHook(({ s }: { s: UnityMCPStatus }) => {
    const shown = useShownUnityStatus(s)
    seen.push(shown)
    return useUnityLinkPulse(shown)
  }, { initialProps: { s: 'connected' as UnityMCPStatus } })
  rerender({ s: 'running' })
  act(() => { vi.advanceTimersByTime(20_000) })
  rerender({ s: 'connected' })
  expect(result.current).toBe(false)
  expect(seen.every(s => s === 'connected')).toBe(true)
})

it('starting from off shows "Bağlı değil", never "Bağlanıyor"', () => {
  const view = render(<UnityMcpToggle {...base} status="off" />)
  view.rerender(<UnityMcpToggle {...base} status="starting" />)
  expect(screen.getByText(OFF_WORD)).toBeTruthy()
  expect(screen.queryByText(/Bağlanıyor/)).toBeNull()
})

it('off and blocked show at once after connected and reset the grace', () => {
  vi.useFakeTimers()
  const view = render(<UnityMcpToggle {...base} status="connected" />)
  view.rerender(<UnityMcpToggle {...base} status="off" />)
  expect(screen.getByText(tr['unity.wordOff'])).toBeTruthy()
  view.rerender(<UnityMcpToggle {...base} status="running" />)
  expect(screen.getByText(OFF_WORD)).toBeTruthy()
  view.rerender(<UnityMcpToggle {...base} status="connected" />)
  view.rerender(<UnityMcpToggle {...base} status="blocked" />)
  expect(screen.getByText(tr['unity.wordBlocked'])).toBeTruthy()
})
