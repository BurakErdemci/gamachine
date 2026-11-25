/**
 * Settings > Agent > Tools > Script tool (4.1.0 design B): on by default, wired to
 * GET/PUT /agent-settings/script-tool (SUBAGENTS_API.md), flips at once, puts itself back and
 * says so when the write fails, and says the change applies from the next turn.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, fireEvent, act, waitFor } from '@testing-library/react'

import { AgentGuidePage, __resetAgentGuideCacheForTests } from '../renderer/components/home/settings/AgentGuidePage'
import { aktifDilAyarla, cevir } from '../renderer/lib/i18n'

const API = 'http://x'
const guide = { language: 'en', sections: [{ id: 'ALWAYS', when: 'always', text: 'rule' }], addendum: '' }

const setup = (enabled: boolean | 'fail' = true) => {
  const http = { get: vi.fn(async () => ({ data: guide })), put: vi.fn() }
  const toolsHttp = {
    get: vi.fn(async () => (enabled === 'fail' ? Promise.reject(new Error('down')) : { data: { enabled } })),
    put: vi.fn(async (_url: string, body: { enabled: boolean }) => ({ data: { enabled: body.enabled } })),
  }
  const saved = vi.fn()
  render(<AgentGuidePage lang="en" API={API} http={http} toolsHttp={toolsHttp} token="tok" saved={saved} />)
  return { http, toolsHttp, saved }
}
const toggle = () => screen.getByTestId('script-tool-toggle') as HTMLButtonElement

beforeEach(() => aktifDilAyarla('en'))
afterEach(() => { cleanup(); __resetAgentGuideCacheForTests(); aktifDilAyarla('tr') })

describe('Script tool switch', () => {
  it('sits in a Tools group at the top of the Agent page, above the fixed guide', async () => {
    const { toolsHttp } = setup(true)
    await waitFor(() => expect(toggle().disabled).toBe(false))
    expect(toolsHttp.get).toHaveBeenCalledWith(`${API}/agent-settings/script-tool`, { headers: { 'X-Session-Token': 'tok' } })
    const groups = Array.from(document.querySelectorAll('.set-gk')).map(g => g.textContent)
    expect(groups[0]).toBe(cevir('set.ajan.tools'))
    expect(groups.findIndex(g => g!.startsWith(cevir('set.ajan.base')))).toBeGreaterThan(0)
    expect(toggle().getAttribute('aria-checked')).toBe('true')
    const row = screen.getByTestId('agent-tools').textContent!
    expect(row).toContain('Script tool')
    expect(row).toContain('Every step inside still asks for your approval')
    expect(row).toContain('The change applies from the next turn.')
  })

  it('shows the stored value and writes the flip at once', async () => {
    const { toolsHttp, saved } = setup(false)
    await waitFor(() => expect(toggle().getAttribute('aria-checked')).toBe('false'))
    await act(async () => { fireEvent.click(toggle()) })
    expect(toolsHttp.put).toHaveBeenCalledWith(`${API}/agent-settings/script-tool`, { enabled: true }, { headers: { 'X-Session-Token': 'tok' } })
    expect(toggle().getAttribute('aria-checked')).toBe('true')
    expect(saved).toHaveBeenCalledTimes(1)
  })

  it('a failed write puts the switch back and says so', async () => {
    const { toolsHttp, saved } = setup(true)
    await waitFor(() => expect(toggle().disabled).toBe(false))
    toolsHttp.put.mockRejectedValueOnce(new Error('400'))
    await act(async () => { fireEvent.click(toggle()) })
    expect(toggle().getAttribute('aria-checked')).toBe('true')
    expect(screen.getByRole('alert').textContent).toBe(cevir('set.ajan.scriptToolFailed'))
    expect(saved).not.toHaveBeenCalled()
  })

  it('stays on (the default) when the setting cannot be read', async () => {
    setup('fail')
    await waitFor(() => expect(toggle().disabled).toBe(false))
    expect(toggle().getAttribute('aria-checked')).toBe('true')
  })

  it('no group without its connection, so the guide page alone is unchanged', async () => {
    render(<AgentGuidePage lang="en" API={API} http={{ get: vi.fn(async () => ({ data: guide })) }} saved={() => {}} />)
    await screen.findByTestId('agent-guide-addendum')
    expect(screen.queryByTestId('agent-tools')).toBeNull()
  })

  it('has its words in both languages', () => {
    aktifDilAyarla('tr')
    expect(cevir('set.ajan.scriptTool')).toBe('Betik aracı')
    aktifDilAyarla('en')
    expect(cevir('set.ajan.scriptTool')).toBe('Script tool')
  })
})
