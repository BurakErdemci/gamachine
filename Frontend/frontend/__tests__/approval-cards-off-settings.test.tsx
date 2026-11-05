/**
 * The "turn approval cards off" row on Settings > Tarayici and Settings > Bilgisayar, against a
 * fake main process behind `window.ipc`. Asserted: the row is disabled while its feature is off,
 * turning it on asks first (with the risk text) and cancel sends nothing, confirm sends
 * `cards-off` and shows the warning line, turning it off sends `cards-on` without a dialog,
 * and a failed change shows the message.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react'

import { BrowserUsePage } from '../renderer/components/home/settings/BrowserUsePage'
import { ComputerUsePage } from '../renderer/components/home/settings/ComputerUsePage'
import { confirmDialog } from '../renderer/components/ui/ConfirmDialog'
import { aktifDilAyarla, cevir } from '../renderer/lib/i18n'

vi.mock('../renderer/components/ui/ConfirmDialog', () => ({ confirmDialog: vi.fn() }))
const confirm = confirmDialog as unknown as ReturnType<typeof vi.fn>

let host: { enabled: boolean; cardsOff: boolean; fail: boolean }
let ops: string[]

const browserInvoke = vi.fn(async (channel: string, req: any) => {
  expect(channel).toBe('browser-live')
  ops.push(req.op)
  switch (req.op) {
    case 'settings': return { enabled: host.enabled, running: false, cardsOff: host.cardsOff }
    case 'cards-off': case 'cards-on':
      if (host.fail) return { error: 'Browser settings request failed.' }
      host.cardsOff = req.op === 'cards-off'
      return { cardsOff: host.cardsOff }
    case 'sites': return { sites: [] }
    case 'history': return { entries: [] }
    default: return { error: 'x' }
  }
})

const computerInvoke = vi.fn(async (channel: string, req: any) => {
  expect(channel).toBe('computer-use')
  ops.push(req.op)
  switch (req.op) {
    case 'status':
      return { available: true, running: host.enabled, platform: 'win32',
        settings: { enabled: host.enabled, active: host.enabled, grants: [], cardsOff: host.cardsOff },
        permissions: { accessibility: true, screen: 'granted' } }
    case 'cards-off': case 'cards-on':
      if (host.fail) return { error: 'Computer-use request failed.' }
      host.cardsOff = req.op === 'cards-off'
      return { cardsOff: host.cardsOff }
    default: return { error: 'x' }
  }
})

beforeEach(() => {
  aktifDilAyarla('tr')
  host = { enabled: true, cardsOff: false, fail: false }
  ops = []
  confirm.mockReset()
  browserInvoke.mockClear()
  computerInvoke.mockClear()
})
afterEach(() => {
  cleanup()
  delete (window as any).ipc
})

const cases = [
  { name: 'Tarayici', p: 'bp', invoke: browserInvoke, ready: 'bp-master-state',
    mount: () => render(<BrowserUsePage saved={vi.fn()} showToast={vi.fn()} />), msg: 'bp-msg' },
  { name: 'Bilgisayar', p: 'cu', invoke: computerInvoke, ready: 'cu-master-state',
    mount: () => render(<ComputerUsePage saved={vi.fn()} showToast={vi.fn()} />), msg: 'cu-msg' },
] as const

describe.each(cases)('approval cards switch · $name', ({ p, invoke, mount, msg }) => {
  const sw = () => screen.getByTestId(`${p}-cards-switch`) as HTMLButtonElement
  const setup = async (over: Partial<typeof host> = {}) => {
    Object.assign(host, over)
    ;(window as any).ipc = { invoke }
    mount()
    await waitFor(() => expect(ops).toContain(p === 'bp' ? 'settings' : 'status'))
    await waitFor(() => expect(screen.getByTestId(`${p}-master-state`).textContent).not.toBe(cevir(`${p}.set.loading` as 'bp.set.loading')))
  }

  it('is disabled while the feature itself is off', async () => {
    await setup({ enabled: false })
    expect(sw().disabled).toBe(true)
  })

  it('asks first with the risk text, and cancel sends nothing', async () => {
    confirm.mockResolvedValue(false)
    await setup()
    await waitFor(() => expect(sw().disabled).toBe(false))
    fireEvent.click(sw())
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1))
    expect(confirm).toHaveBeenCalledWith(cevir(`${p}.set.cardsConfirm` as 'bp.set.cardsConfirm'),
      cevir(`${p}.set.cardsConfirmBtn` as 'bp.set.cardsConfirmBtn'), cevir('confirm.cancel'))
    expect(ops.some(o => o.startsWith('cards-'))).toBe(false)
    expect(sw().getAttribute('aria-checked')).toBe('false')
  })

  it('confirm sends cards-off and shows the warning; turning off sends cards-on with no dialog', async () => {
    confirm.mockResolvedValue(true)
    await setup()
    await waitFor(() => expect(sw().disabled).toBe(false))
    fireEvent.click(sw())
    await waitFor(() => expect(ops).toContain('cards-off'))
    await waitFor(() => expect(screen.getByTestId(`${p}-cards-warn`).textContent)
      .toBe(cevir(`${p}.set.cardsWarn` as 'bp.set.cardsWarn')))
    expect(sw().getAttribute('aria-checked')).toBe('true')
    confirm.mockClear()
    await waitFor(() => expect(sw().disabled).toBe(false))
    fireEvent.click(sw())
    await waitFor(() => expect(ops).toContain('cards-on'))
    expect(confirm).not.toHaveBeenCalled()
    await waitFor(() => expect(screen.queryByTestId(`${p}-cards-warn`)).toBeNull())
  })

  it('shows the failure message when the change fails', async () => {
    confirm.mockResolvedValue(true)
    await setup({ fail: true })
    await waitFor(() => expect(sw().disabled).toBe(false))
    fireEvent.click(sw())
    await waitFor(() => expect(screen.getByTestId(msg).textContent)
      .toBe(cevir(`${p}.set.failed` as 'bp.set.failed')))
    expect(sw().getAttribute('aria-checked')).toBe('false')
  })
})

describe('computer use confirm text on macOS', () => {
  it('names the macOS halt shortcut and apps, not the Windows ones', async () => {
    const mac = cevir('cu.set.cardsConfirmMac')
    expect(mac).toContain('Cmd+Shift+Esc')
    expect(mac).not.toMatch(/Ctrl\+Alt\+Esc|Görev Yöneticisi|Dosya Gezgini/)
    aktifDilAyarla('en')
    expect(cevir('cu.set.cardsConfirmMac')).not.toMatch(/Task Manager|File Explorer|Ctrl\+Alt\+Esc/)
    aktifDilAyarla('tr')
    computerInvoke.mockImplementationOnce(async (_c: string, req: any) => {
      ops.push(req.op)
      return { available: true, running: true, platform: 'darwin',
        settings: { enabled: true, active: true, grants: [], cardsOff: false },
        permissions: { accessibility: true, screen: 'granted' } }
    })
    confirm.mockResolvedValue(false)
    ;(window as any).ipc = { invoke: computerInvoke }
    render(<ComputerUsePage saved={vi.fn()} showToast={vi.fn()} />)
    await waitFor(() => expect((screen.getByTestId('cu-cards-switch') as HTMLButtonElement).disabled).toBe(false))
    fireEvent.click(screen.getByTestId('cu-cards-switch'))
    await waitFor(() => expect(confirm).toHaveBeenCalledWith(mac, cevir('cu.set.cardsConfirmBtn'), cevir('confirm.cancel')))
  })
})

describe('risk texts', () => {
  it('keep the fixed bans in both languages', () => {
    expect(cevir('bp.set.cardsConfirm')).toContain('Durdur düğmesi her zaman çalışır')
    expect(cevir('cu.set.cardsConfirm')).toContain('Ctrl+Alt+Esc ajanı her zaman durdurur')
    aktifDilAyarla('en')
    expect(cevir('bp.set.cardsWarn')).toBe('Approval cards are off: the agent acts without asking.')
    expect(cevir('cu.set.cardsConfirmBtn')).toBe('Turn cards off')
  })
})
