/**
 * Terminal > Clear Terminal in the app menu: the renderer gets `menu-clear-terminal` and the
 * drawer clears the active shell's screen (xterm only, nothing is written to the PTY).
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import React from 'react'
import { render, cleanup, act } from '@testing-library/react'

const ipc = vi.hoisted(() => {
  const api = { invoke: vi.fn(async () => ({ success: true })), on: vi.fn((_ch: string, _cb: () => void) => () => {}) }
  ;(globalThis as any).window.ipc = api
  return api
})

const terms = vi.hoisted(() => [] as any[])
vi.mock('@xterm/xterm', () => ({
  Terminal: class {
    options: any
    cols = 80; rows = 24
    clear = vi.fn()
    constructor(opts: any) { this.options = opts; terms.push(this) }
    loadAddon() {} open() {} write() {} focus() {} dispose() {}
    onData() { return { dispose() {} } }
    onSelectionChange() { return { dispose() {} } }
  },
}))
vi.mock('@xterm/addon-fit', () => ({ FitAddon: class { fit() {} } }))
vi.mock('@xterm/xterm/css/xterm.css', () => ({}))
vi.mock('axios', () => ({ default: { get: vi.fn(async () => ({ data: {} })) } }))

import { TerminalPanel } from '../renderer/components/home/TerminalPanel'

beforeEach(() => { ipc.invoke.mockClear(); ipc.on.mockClear(); terms.length = 0 })
afterEach(cleanup)

const clearHandlers = () => ipc.on.mock.calls.filter((c: any[]) => c[0] === 'menu-clear-terminal')

describe('menu-clear-terminal', () => {
  it('clears the active shell, writes nothing to the PTY, and unsubscribes on unmount', async () => {
    vi.useFakeTimers()
    const off = vi.fn()
    ipc.on.mockImplementation((ch: string) => (ch === 'menu-clear-terminal' ? off : () => {}))
    const { unmount } = render(
      <TerminalPanel id="t1" isOpen onClose={() => {}} onOpen={() => {}} workspacePath="/ws" problems={[]} problemsKnown={false} />,
    )
    await act(async () => { vi.advanceTimersByTime(500) })
    expect(terms).toHaveLength(1)
    expect(clearHandlers()).toHaveLength(1)

    const writesBefore = ipc.invoke.mock.calls.filter((c: any[]) => c[0] === 'terminal-write').length
    const handler = clearHandlers()[0][1] as () => void
    act(() => { handler() })
    expect(terms[0].clear).toHaveBeenCalledTimes(1)
    expect(ipc.invoke.mock.calls.filter((c: any[]) => c[0] === 'terminal-write')).toHaveLength(writesBefore)

    expect(off).not.toHaveBeenCalled()
    unmount()
    expect(off).toHaveBeenCalledTimes(1)
    vi.useRealTimers()
  })

  it('does nothing while no shell exists', () => {
    render(<TerminalPanel id="t1" isOpen={false} onClose={() => {}} onOpen={() => {}} workspacePath="/ws" problems={[]} problemsKnown={false} />)
    const handler = clearHandlers()[0][1] as () => void
    expect(() => act(() => { handler() })).not.toThrow()
    expect(terms).toHaveLength(0)
  })
})
