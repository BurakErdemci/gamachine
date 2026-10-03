/**
 * The drawer's header on a narrow workspace (M9) and closing a shell from its own chip (M10).
 * jsdom has no layout, so M9 is measured as structure plus the stylesheet rules that decide who shrinks.
 */
import { it, expect, vi, afterEach, beforeEach } from 'vitest'
import React from 'react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { render, screen, cleanup, fireEvent, act, within } from '@testing-library/react'

const ipc = vi.hoisted(() => {
  const api = { invoke: vi.fn(async () => ({ success: true })), on: vi.fn(() => () => {}) }
  ;(globalThis as any).window.ipc = api
  return api
})
vi.mock('@xterm/xterm', () => ({
  Terminal: class {
    options: any; cols = 80; rows = 24
    constructor(opts: any) { this.options = opts }
    loadAddon() {} open() {} write() {} focus() {} clear() {} dispose() {}
    onData() { return { dispose() {} } }
  },
}))
vi.mock('@xterm/addon-fit', () => ({ FitAddon: class { fit() {} } }))
vi.mock('@xterm/xterm/css/xterm.css', () => ({}))
vi.mock('axios', () => ({ default: { get: vi.fn(async () => ({ data: {} })) } }))

import { TerminalPanel } from '../renderer/components/home/TerminalPanel'

let now = 1000
beforeEach(() => { ipc.invoke.mockClear(); vi.spyOn(Date, 'now').mockImplementation(() => ++now) })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

const open = async () => {
  render(<TerminalPanel id="t1" isOpen onClose={() => {}} onOpen={() => {}} workspacePath="/ws" problems={[]} problemsKnown />)
  await act(async () => {})
}
const css = readFileSync(resolve(__dirname, '../renderer/styles/gm/workspace.css'), 'utf8')
const rule = (selector: string) => {
  const at = css.indexOf(`${selector} {`)
  expect(at, selector).toBeGreaterThanOrEqual(0)
  return css.slice(at, css.indexOf('}', at))
}

it('M9: the action buttons sit after the tab list, outside it, and only the tab list shrinks', async () => {
  await open()
  const tablist = screen.getByRole('tablist', { name: 'Alt çekmece' })
  const bar = tablist.parentElement!
  const actions = [screen.getByRole('button', { name: 'Yeni terminal' }), screen.getByRole('button', { name: 'Bu terminali kapat' }),
    screen.getByRole('button', { name: 'Alt çekmeceyi kapat' })]
  for (const button of actions) {
    expect(tablist.contains(button)).toBe(false)
    expect(bar.contains(button)).toBe(true)
    expect(tablist.compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  }
  const tabs = rule('.term-bar > .term-tabs')
  expect(tabs).toContain('min-width: 0')
  expect(tabs).toContain('overflow-x: auto')
  expect(tabs).toMatch(/flex: 0 1 auto/)
  expect(rule('.term-bar > *')).toContain('flex-shrink: 0')
  expect(rule('.term-acts .icon-btn, .term-toggle')).toContain('flex: none')
})

it('M10: the x on a session chip closes that session, not the active one', async () => {
  await open()
  fireEvent.click(screen.getByRole('button', { name: 'Yeni terminal' }))
  fireEvent.click(screen.getByRole('button', { name: 'Yeni terminal' }))
  await act(async () => {})
  const chips = screen.getByRole('tablist', { name: 'Terminaller' })
  expect(within(chips).getAllByRole('tab').map(t => t.textContent)).toEqual(['zsh', 'zsh (2)', 'zsh (3)'])
  expect(within(chips).getByRole('tab', { name: 'zsh (3)' }).getAttribute('aria-selected')).toBe('true')
  const close = within(chips).getByRole('button', { name: 'zsh (2) terminalini kapat' })
  expect(close.tabIndex).toBe(0)
  fireEvent.click(close)
  await act(async () => {})
  expect(within(chips).getAllByRole('tab').map(t => t.textContent)).toEqual(['zsh', 'zsh (3)'])
  expect(within(chips).getByRole('tab', { name: 'zsh (3)' }).getAttribute('aria-selected')).toBe('true')
  const exits = ipc.invoke.mock.calls.filter((c: any[]) => c[0] === 'terminal-write' && c[1]?.data === 'exit\r')
  expect(exits).toHaveLength(1)
})
