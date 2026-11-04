// Settings > Bilgisayar and > Tarayıcı on a paired browser: the switches work through the remote IPC
// shim (the dispatcher's ops), while the ops that open dialogs on the PC and the secrets stay refused.
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const page = vi.hoisted(() => ({ remote: true }))
vi.mock('../renderer/lib/remote/transport', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../renderer/lib/remote/transport')>()
  return { ...actual, isRemotePage: () => page.remote }
})

import { ComputerUsePage } from '../renderer/components/home/settings/ComputerUsePage'
import { BrowserUsePage } from '../renderer/components/home/settings/BrowserUsePage'
import { hasComputerUseIpc, hasComputerUseSettingsIpc } from '../renderer/lib/computerUse'
import { remoteIpcAllowed } from '../renderer/lib/remote/desktopRunner'
import { createIpcShim } from '../renderer/lib/remote/ipcShim'
import { RemoteError, type RemoteTransport } from '../renderer/lib/remote/transport'
import { aktifDilAyarla } from '../renderer/lib/i18n'

let enabled: { cu: boolean; browser: boolean }
let reached: Array<[string, any]>

// The paired browser's transport: what the desktop runner would accept runs, the rest is remote_unavailable.
const transport = {
  async ipc(channel: string, args: unknown[]) {
    if (!remoteIpcAllowed(channel, args)) throw new RemoteError('remote_unavailable')
    const req = args[0] as any
    reached.push([channel, req])
    if (channel === 'computer-use') {
      if (req.op === 'enable' || req.op === 'disable') enabled.cu = req.op === 'enable'
      return { available: true, running: enabled.cu, platform: 'win32',
        settings: { enabled: enabled.cu, grants: [] }, permissions: { accessibility: true, screen: 'granted' },
        ...(req.op === 'enable' || req.op === 'disable' ? { enabled: enabled.cu } : {}) }
    }
    if (req.op === 'enable' || req.op === 'disable') enabled.browser = req.op === 'enable'
    if (req.op === 'settings') return { enabled: enabled.browser, running: false, cardsOff: false }
    if (req.op === 'enable' || req.op === 'disable') return { enabled: enabled.browser }
    return { error: 'x' }
  },
  subscribe: () => () => {},
} as unknown as RemoteTransport

beforeEach(() => {
  aktifDilAyarla('tr')
  page.remote = true
  enabled = { cu: false, browser: false }
  reached = []
  ;(window as any).ipc = createIpcShim(transport, () => {})
})
afterEach(() => {
  cleanup()
  delete (window as any).ipc
})

describe('remote settings', () => {
  it('the computer-use switch is usable and sends enable to the desktop', async () => {
    expect(hasComputerUseSettingsIpc()).toBe(true)
    expect(hasComputerUseIpc()).toBe(false) // card answers keep their own remote path
    render(<ComputerUsePage saved={vi.fn()} showToast={vi.fn()} />)
    expect(screen.getByTestId('cu-page').getAttribute('data-desktop')).toBe('true')
    const sw = await waitFor(() => {
      const el = screen.getByTestId('cu-switch') as HTMLButtonElement
      expect(el.disabled).toBe(false)
      return el
    })
    await act(async () => { fireEvent.click(sw) })
    await waitFor(() => expect(reached.some(([c, r]) => c === 'computer-use' && r.op === 'enable')).toBe(true))
  })

  it('the agent browser switch is usable and sends enable to the desktop', async () => {
    render(<BrowserUsePage saved={vi.fn()} showToast={vi.fn()} />)
    const sw = await waitFor(() => {
      const el = screen.getByTestId('bp-switch') as HTMLButtonElement
      expect(el.disabled).toBe(false)
      return el
    })
    await act(async () => { fireEvent.click(sw) })
    await waitFor(() => expect(reached.some(([c, r]) => c === 'browser-live' && r.op === 'enable')).toBe(true))
  })

  it('native dialogs on the PC and secrets stay refused', async () => {
    const ipc = (window as any).ipc
    for (const [channel, op] of [['computer-use', 'request-accessibility'], ['computer-use', 'open-screen-settings'],
      ['computer-use-live', 'panel-visible'], ['browser-live', 'set-bounds'], ['browser-live', 'clear-browsing-data']]) {
      await expect(ipc.invoke(channel, { op })).rejects.toThrow('remote_unavailable')
    }
    for (const channel of ['app-token-get-secret', 'profile-reset', 'remote-control']) {
      await expect(ipc.invoke(channel, {})).rejects.toThrow('remote_unavailable')
    }
    expect(reached).toEqual([])
  })
})
