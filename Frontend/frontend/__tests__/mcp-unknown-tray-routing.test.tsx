/**
 * The unowned-requests tray answers each entry on the path its in-chat card
 * uses: computer_* and browser_* approvals need the UI secret held by the
 * desktop host, so posting them as plain MCP decisions is refused (HTTP 403).
 */
import React from 'react'
import { render, fireEvent, act, cleanup } from '@testing-library/react'
import { it, expect, vi, afterEach } from 'vitest'

const calls: unknown[][] = []
vi.mock('../renderer/hooks/home/gateResponse', async (orig) => {
  const real: any = await orig()
  return {
    ...real,
    postMcpDecision: vi.fn(async (...a: unknown[]) => { calls.push(['mcp', ...a]); return null }),
    postComputerDecision: vi.fn(async (...a: unknown[]) => { calls.push(['computer', ...a]); return null }),
    postBrowserDecision: vi.fn(async (...a: unknown[]) => { calls.push(['browser', ...a]); return null }),
  }
})
import { McpUnknownTray } from '../renderer/components/home/McpUnknownTray'

afterEach(cleanup)

const decideFirst = async (tool: string, approve = true) => {
  calls.length = 0
  const { container } = render(
    <McpUnknownTray gates={[{ gateId: 'g1', tool, params: {}, workspacePath: '' }]}
      apiBase="http://x" sessionToken="t" showToast={() => {}} />)
  const btn = container.querySelector(approve ? 'button.btn-primary' : 'button.btn-ghost') as HTMLButtonElement
  await act(async () => { fireEvent.click(btn) })
  return [...calls]
}

it('approving a computer_* entry uses the computer path', async () => {
  expect(await decideFirst('computer_window_state')).toEqual([['computer', 'http://x', 'g1', true, false, 't']])
})

it('approving a browser_* entry uses the browser path', async () => {
  expect(await decideFirst('browser_navigate')).toEqual([['browser', 'g1', true]])
})

it('a Unity tool entry keeps the plain MCP path', async () => {
  expect(await decideFirst('manage_scene')).toEqual([['mcp', 'http://x', 'g1', true, 't']])
})

it('denying a computer_* entry also uses the computer path', async () => {
  expect(await decideFirst('computer_click', false)).toEqual([['computer', 'http://x', 'g1', false, false, 't']])
})

it('denying a browser_* entry sends a denial', async () => {
  expect(await decideFirst('browser_click', false)).toEqual([['browser', 'g1', false]])
})
