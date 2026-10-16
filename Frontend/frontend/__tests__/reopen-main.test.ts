import { describe, expect, it, vi } from 'vitest'
import { reopenMain } from '../main/helpers/reopen-main'

function fixture({ destroyed = false, minimized = false, visible = true } = {}) {
  const calls: string[] = []
  const win = {
    isDestroyed: () => destroyed,
    isMinimized: () => minimized,
    isVisible: () => visible,
    restore: vi.fn(() => { calls.push('restore') }),
    show: vi.fn(() => { calls.push('show') }),
    focus: vi.fn(() => { calls.push('focus') }),
  }
  return { win, calls }
}

describe('reopenMain', () => {
  it.each([null, undefined])('returns false for %s', (win) => {
    expect(reopenMain(win)).toBe(false)
  })

  it('returns false without touching a destroyed window', () => {
    const { win, calls } = fixture({ destroyed: true })
    const isMinimized = vi.spyOn(win, 'isMinimized')
    const isVisible = vi.spyOn(win, 'isVisible')
    expect(reopenMain(win)).toBe(false)
    expect(calls).toEqual([])
    expect(isMinimized).not.toHaveBeenCalled()
    expect(isVisible).not.toHaveBeenCalled()
  })

  it('restores, shows, then focuses a minimized and hidden window', () => {
    const { win, calls } = fixture({ minimized: true, visible: false })
    expect(reopenMain(win)).toBe(true)
    expect(calls).toEqual(['restore', 'show', 'focus'])
  })

  it('only focuses a visible window that is not minimized', () => {
    const { win, calls } = fixture()
    expect(reopenMain(win)).toBe(true)
    expect(calls).toEqual(['focus'])
  })

  it('shows then focuses a hidden window that is not minimized', () => {
    const { win, calls } = fixture({ visible: false })
    expect(reopenMain(win)).toBe(true)
    expect(calls).toEqual(['show', 'focus'])
  })

  it('still shows and focuses when restore throws', () => {
    const { win, calls } = fixture({ minimized: true, visible: false })
    win.restore.mockImplementation(() => { throw new Error('restore failed') })
    expect(reopenMain(win)).toBe(true)
    expect(calls).toEqual(['show', 'focus'])
  })

  it('still focuses when show throws', () => {
    const { win, calls } = fixture({ minimized: true, visible: false })
    win.show.mockImplementation(() => { throw new Error('show failed') })
    expect(reopenMain(win)).toBe(true)
    expect(calls).toEqual(['restore', 'focus'])
  })
})
