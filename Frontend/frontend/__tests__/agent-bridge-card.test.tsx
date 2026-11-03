import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react'

import { ConnectorsPage } from '../renderer/components/home/settings/ConnectorsPage'
import { agentPasteText } from '../renderer/lib/agentBridge'
import { aktifDilAyarla } from '../renderer/lib/i18n'

const API = 'http://127.0.0.1:8000'
const ARGV = ['C:\\Program Files\\Gamachine\\backend.exe', 'agent-mcp-server']

let enabled: boolean
let getFails: boolean
let putFails: boolean
let http: any
const showToast = vi.fn()
const writeText = vi.fn(async (_t: string) => {})

const mount = async () => {
  const r = render(<ConnectorsPage saved={vi.fn()} showToast={showToast} API={API} http={http} token="tok" />)
  await waitFor(() => expect(http.get).toHaveBeenCalled())
  return r
}

beforeEach(() => {
  aktifDilAyarla('tr')
  enabled = false
  getFails = false
  putFails = false
  showToast.mockReset()
  writeText.mockClear()
  ;(window as any).ipc = { invoke: vi.fn(async () => ({ connectors: [] })) }
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
  http = {
    get: vi.fn(async () => {
      if (getFails) throw new Error('down')
      return { data: { enabled, server_command: ARGV } }
    }),
    put: vi.fn(async (_url: string, body: { enabled: boolean }) => {
      if (putFails) throw new Error('nope')
      enabled = body.enabled
      return { data: { enabled } }
    }),
  }
})
afterEach(() => { cleanup(); delete (window as any).ipc })

describe('agent bridge card', () => {
  it('is off by default and shows no paste text', async () => {
    await mount()
    const sw = await screen.findByTestId('agent-bridge-switch')
    expect(sw.getAttribute('aria-checked')).toBe('false')
    expect(screen.queryByTestId('agent-bridge-paste')).toBeNull()
    expect(http.get.mock.calls[0][0]).toBe(`${API}/agent-bridge/settings`)
    expect(http.get.mock.calls[0][1]).toEqual({ headers: { 'X-Session-Token': 'tok' } })
  })

  it('toggling PUTs enabled and then shows the paste box with the command, the CLI lines and the JSON argv', async () => {
    await mount()
    fireEvent.click(await screen.findByTestId('agent-bridge-switch'))
    const box = await screen.findByTestId('agent-bridge-paste')
    expect(http.put.mock.calls[0][0]).toBe(`${API}/agent-bridge/settings`)
    expect(http.put.mock.calls[0][1]).toEqual({ enabled: true })
    const text = box.textContent ?? ''
    expect(text).toContain('"C:\\Program Files\\Gamachine\\backend.exe" "agent-mcp-server"')
    expect(text).toContain('claude mcp add --scope user gamachine -- ')
    expect(text).toContain('codex mcp add gamachine -- ')
    expect(text).toContain('opencode.json')
    expect(text).toContain(JSON.stringify(ARGV))
    expect(screen.getByTestId('agent-bridge-switch').getAttribute('aria-checked')).toBe('true')
  })

  it('Copy writes exactly the box text', async () => {
    enabled = true
    await mount()
    const box = await screen.findByTestId('agent-bridge-paste')
    fireEvent.click(screen.getByTestId('agent-bridge-copy'))
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1))
    expect(writeText).toHaveBeenCalledWith(box.textContent)
    expect(showToast).toHaveBeenCalledWith('Kopyalandı', 'success')
  })

  it('a load failure shows a retry that reads again', async () => {
    getFails = true
    await mount()
    const failed = await screen.findByTestId('agent-bridge-load-failed')
    getFails = false
    fireEvent.click(failed.querySelector('button')!)
    expect((await screen.findByTestId('agent-bridge-switch')).getAttribute('aria-checked')).toBe('false')
    expect(http.get).toHaveBeenCalledTimes(2)
  })

  it('a save failure leaves the switch off and toasts an error', async () => {
    putFails = true
    await mount()
    fireEvent.click(await screen.findByTestId('agent-bridge-switch'))
    await waitFor(() => expect(showToast).toHaveBeenCalledWith('Ayar kaydedilemedi.', 'error'))
    expect(screen.getByTestId('agent-bridge-switch').getAttribute('aria-checked')).toBe('false')
    expect(screen.queryByTestId('agent-bridge-paste')).toBeNull()
  })

  it('is absent without the desktop bridge', async () => {
    delete (window as any).ipc
    render(<ConnectorsPage saved={vi.fn()} showToast={showToast} API={API} http={http} token="tok" />)
    expect(screen.queryByTestId('agent-bridge-card')).toBeNull()
    expect(http.get).not.toHaveBeenCalled()
  })
})

describe('agentPasteText', () => {
  it('quotes args with spaces and escapes quotes, in both languages', () => {
    const argv = ['C:\\Program Files\\g.exe', 'say "hi"']
    for (const lang of ['tr', 'en'] as const) {
      const text = agentPasteText(lang, argv)
      expect(text).toContain('"C:\\Program Files\\g.exe" "say \\"hi\\""')
      expect(text).toContain(`claude mcp add --scope user gamachine -- "C:\\Program Files\\g.exe" "say \\"hi\\""`)
      expect(text).toContain(`"command": ${JSON.stringify(argv)}`)
      expect(text).toContain('gamachine_list_chats')
    }
    expect(agentPasteText('tr', ['a'])).toContain("Gamachine'e bağlanmanı istiyorum")
    expect(agentPasteText('en', ['a'])).toContain('connect to Gamachine')
  })
})
