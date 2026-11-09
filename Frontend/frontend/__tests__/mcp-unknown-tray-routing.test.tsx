/**
 * The unowned-requests tray answers each entry on the path its in-chat card
 * uses: computer_* and browser_* approvals need the UI secret held by the
 * desktop host, so posting them as plain MCP decisions is refused (HTTP 403).
 */
import React from 'react'
import { render, fireEvent, act, cleanup } from '@testing-library/react'
import { it, expect, vi, afterEach } from 'vitest'

const calls: string[] = []
vi.mock('../renderer/hooks/home/gateResponse', async (orig) => {
  const real: any = await orig()
  return {
    ...real,
    postMcpDecision: vi.fn(async () => { calls.push('mcp'); return null }),
    postComputerDecision: vi.fn(async () => { calls.push('computer'); return null }),
    postBrowserDecision: vi.fn(async () => { calls.push('browser'); return null }),
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
  expect(await decideFirst('computer_window_state')).toEqual(['computer'])
})

it('approving a browser_* entry uses the browser path', async () => {
  expect(await decideFirst('browser_navigate')).toEqual(['browser'])
})

it('a Unity tool entry keeps the plain MCP path', async () => {
  expect(await decideFirst('manage_scene')).toEqual(['mcp'])
})

it('denying a computer_* entry also uses the computer path', async () => {
  expect(await decideFirst('computer_click', false)).toEqual(['computer'])
})
