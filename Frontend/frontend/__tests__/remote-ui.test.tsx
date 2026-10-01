import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

vi.mock('axios', () => ({ default: { put: vi.fn() } }))

import axios from 'axios'
import { EFFORT_REPORT_EVERY_MS, useRemoteUi, type RemoteUiOptions } from '../renderer/lib/remoteControl'

const put = vi.mocked(axios.put)
const initial: RemoteUiOptions = { api: 'http://127.0.0.1:8000', token: 'tok', lang: 'tr', theme: 'arena' }
const page = (props = initial) => renderHook(useRemoteUi, { initialProps: props })

beforeEach(() => { put.mockReset().mockResolvedValue({ data: {} }) })
afterEach(() => { cleanup(); vi.useRealTimers() })

describe('reporting the desktop UI', () => {
  it('PUTs language and theme on mount with the app token', () => {
    page()
    expect(put.mock.calls).toEqual([[
      `${initial.api}/remote/desktop-ui`, { lang: 'tr', theme: 'arena' },
      { headers: { 'X-Session-Token': 'tok' } },
    ]])
  })

  it('reports again on theme or language changes', () => {
    const { rerender } = page()
    rerender({ ...initial, theme: 'sade' })
    rerender({ ...initial, theme: 'sade', lang: 'en' })
    expect(put.mock.calls.map(c => c[1])).toEqual([
      { lang: 'tr', theme: 'arena' }, { lang: 'tr', theme: 'sade' }, { lang: 'en', theme: 'sade' },
    ])
  })

  it('does not report without an API or token', () => {
    const { rerender } = page({ ...initial, token: undefined })
    rerender({ ...initial, api: undefined })
    expect(put).not.toHaveBeenCalled()
    rerender(initial)
    expect(put).toHaveBeenCalledTimes(1)
  })

  it('swallows rejected reports and retries periodically', async () => {
    vi.useFakeTimers()
    put.mockRejectedValue(new Error('offline'))
    const { unmount } = page()
    await act(async () => { await Promise.resolve() })
    await act(async () => { vi.advanceTimersByTime(EFFORT_REPORT_EVERY_MS) })
    expect(put).toHaveBeenCalledTimes(2)
    unmount()
    await act(async () => { vi.advanceTimersByTime(EFFORT_REPORT_EVERY_MS) })
    expect(put).toHaveBeenCalledTimes(2)
  })

  it('replaces the periodic report when settings change', () => {
    vi.useFakeTimers()
    const { rerender } = page()
    rerender({ ...initial, theme: 'pafta' })
    act(() => { vi.advanceTimersByTime(EFFORT_REPORT_EVERY_MS) })
    expect(put).toHaveBeenCalledTimes(3)
    expect(put.mock.calls.at(-1)?.[1]).toEqual({ lang: 'tr', theme: 'pafta' })
  })

  it('home reports its language and appearance theme after effort', () => {
    const home = readFileSync(resolve('renderer/pages/home.tsx'), 'utf8')
    expect(home).toContain('theme: useAppearance().appearance.theme')
    expect(home).toContain('token: auth.user?.sessionToken, lang,')
    expect(home.indexOf('useRemoteUi({')).toBeGreaterThan(home.indexOf('useRemoteEffort({'))
  })
})
