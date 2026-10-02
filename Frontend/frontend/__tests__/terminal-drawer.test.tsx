/**
 * The terminal drawer (P3): a strip with Terminal / Console / Problems and a short status when
 * closed, the output when open. The shell is spawned once and survives collapsing the drawer and
 * switching its tabs (the old panel returned null when closed and lost its xterm host).
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, fireEvent, act } from '@testing-library/react'

const ipc = vi.hoisted(() => {
  const api = { invoke: vi.fn(async () => ({ success: true })), on: vi.fn(() => () => {}) }
  ;(globalThis as any).window.ipc = api
  return api
})

// xterm needs a real canvas; the drawer's lifecycle is what is measured here.
const terms = vi.hoisted(() => [] as any[])
vi.mock('@xterm/xterm', () => ({
  Terminal: class {
    options: any
    cols = 80; rows = 24
    constructor(opts: any) { this.options = opts; terms.push(this) }
    loadAddon() {} open() {} write() {} focus() {} clear() {} dispose() {}
    onData() { return { dispose() {} } }
  },
}))
vi.mock('@xterm/addon-fit', () => ({ FitAddon: class { fit() {} } }))
vi.mock('@xterm/xterm/css/xterm.css', () => ({}))
vi.mock('axios', () => ({ default: { get: vi.fn(async () => ({ data: {} })) } }))

import { TerminalPanel, xtermThemeFromTokens } from '../renderer/components/home/TerminalPanel'
import { translations } from '../renderer/lib/i18n'

const tr = translations.tr

beforeEach(() => { ipc.invoke.mockClear(); terms.length = 0 })
afterEach(cleanup)

function Harness({ problems = [] as any[], known = false }) {
  const [open, setOpen] = React.useState(false)
  return (
    <>
      <button type="button" onClick={() => setOpen(o => !o)}>toggle-from-outside</button>
      <TerminalPanel id="t1" isOpen={open} onClose={() => setOpen(false)} onOpen={() => setOpen(true)}
        workspacePath="/ws" problems={problems} problemsKnown={known} />
    </>
  )
}

const spawns = () => ipc.invoke.mock.calls.filter((c: any[]) => c[0] === 'terminal-spawn').length

describe('terminal drawer', () => {
  it('closed, it is a strip with the three tabs and the counts once known', () => {
    render(<Harness known problems={[{ severity: 'Warning', message: 'unused', line: 17, column: 3 }]} />)
    const drawer = screen.getByTestId('terminal-drawer')
    expect(drawer.getAttribute('data-open')).toBe('false')
    for (const k of ['terminal.tabTerminal', 'terminal.tabConsole', 'terminal.tabProblems'] as const) {
      expect(screen.getByRole('tab', { name: new RegExp(tr[k]) })).toBeTruthy()
    }
    expect(drawer.textContent).toContain('0 hata · 1 uyarı')
  })

  it('says nothing about errors before diagnostics have reported', () => {
    render(<Harness />)
    expect(screen.getByTestId('terminal-drawer').textContent).not.toContain('hata')
  })

  it('a tab on the strip opens the drawer on that tab', () => {
    render(<Harness />)
    fireEvent.click(screen.getByRole('tab', { name: new RegExp(tr['terminal.tabProblems']) }))
    const drawer = screen.getByTestId('terminal-drawer')
    expect(drawer.getAttribute('data-open')).toBe('true')
    expect(drawer.getAttribute('data-term')).toBe('sorunlar')
  })

  it('spawns one shell, and collapsing or switching tabs never spawns another', async () => {
    vi.useFakeTimers()
    render(<Harness />)
    expect(spawns()).toBe(0)
    await act(async () => { fireEvent.click(screen.getByText('toggle-from-outside')) })
    expect(spawns()).toBe(1)
    await act(async () => { fireEvent.click(screen.getByRole('tab', { name: new RegExp(tr['terminal.tabProblems']) })) })
    await act(async () => { fireEvent.click(screen.getByText('toggle-from-outside')) }) // collapse
    await act(async () => { fireEvent.click(screen.getByText('toggle-from-outside')) }) // reopen
    await act(async () => { fireEvent.click(screen.getByRole('tab', { name: new RegExp(tr['terminal.tabTerminal']) })) })
    await act(async () => { vi.advanceTimersByTime(500) })
    expect(spawns()).toBe(1)
    expect(terms).toHaveLength(1)
    vi.useRealTimers()
  })

  it('recolours the shell from the tokens when the theme changes', async () => {
    vi.useFakeTimers()
    render(<Harness />)
    await act(async () => { fireEvent.click(screen.getByText('toggle-from-outside')) })
    const before = terms[0].options.theme
    document.documentElement.style.setProperty('--term-bg', '#eae4d8')
    document.documentElement.setAttribute('data-theme', 'atolye')
    await act(async () => { await Promise.resolve() })
    expect(terms[0].options.theme).not.toBe(before)
    expect(terms[0].options.theme.background).toBe('#eae4d8')
    document.documentElement.style.removeProperty('--term-bg')
    document.documentElement.removeAttribute('data-theme')
    vi.useRealTimers()
  })
})

describe('xterm palette from tokens', () => {
  const dark = {
    '--term-bg': '#10141e', '--term-text': '#e1e3ea', '--term-dim': '#8c95ab', '--term-prompt': '#8ccfc2',
    '--term-warn': '#e9c98a', '--ed-kw': '#8fb3f0', '--ed-num': '#f2a585', '--ed-line': '#2b3248',
    '--diff-del-mark': '#f2937c', '--diff-add-mark': '#4fd8c8',
  }
  it('dark ground: white is the text colour', () => {
    const th = xtermThemeFromTokens(dark)
    expect(th.background).toBe('#10141e')
    expect(th.white).toBe('#e1e3ea')
  })
  it('light ground: "white" text is drawn dim, never paper-white on paper', () => {
    const th = xtermThemeFromTokens({ ...dark, '--term-bg': '#eae4d8', '--term-text': '#241e18', '--term-dim': '#5a4d3f' })
    expect(th.white).toBe('#5a4d3f')
    expect(th.black).toBe('#241e18')
  })
})
