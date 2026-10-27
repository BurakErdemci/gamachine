/**
 * Settings > Bilgisayar by platform: the macOS permission steps exist only on darwin,
 * and the texts that name the Mac give way to platform-neutral ones elsewhere.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, waitFor } from '@testing-library/react'

import { ComputerUsePage } from '../renderer/components/home/settings/ComputerUsePage'
import { aktifDilAyarla, cevir } from '../renderer/lib/i18n'
import { parseComputerUseStatus } from '../renderer/lib/computerUse'

let status: Record<string, unknown>
const ops: string[] = []

const invoke = vi.fn(async (_channel: string, req: any) => {
  ops.push(req.op)
  if (req.op === 'status') return status
  return { error: 'Unknown operation or malformed computer-use arguments.' }
})

const hostStatus = (over: Record<string, unknown> = {}) => ({
  available: true, running: false,
  settings: { enabled: false, mode: 'step', active: false, grants: [], runtime: { running: false, driver_path: null, socket_path: null }, halted: [] },
  permissions: { accessibility: false, screen: 'denied' },
  ...over,
})

beforeEach(() => {
  aktifDilAyarla('tr')
  ops.length = 0
  invoke.mockClear()
  ;(window as any).ipc = { invoke }
})
afterEach(() => {
  cleanup()
  delete (window as any).ipc
})

const renderPage = () => render(<ComputerUsePage saved={vi.fn()} showToast={vi.fn()} />)

describe('Settings > Bilgisayar on win32', () => {
  it('hides the macOS permission group and shows the platform-neutral lede', async () => {
    status = hostStatus({ platform: 'win32' })
    renderPage()
    await waitFor(() => expect(screen.getByTestId('cu-driver')).toBeTruthy())
    expect(screen.queryByTestId('cu-perms')).toBeNull()
    expect(screen.queryByTestId('cu-perm-ax')).toBeNull()
    expect(screen.queryByTestId('cu-perm-screen')).toBeNull()
    expect(document.body.textContent).toContain(cevir('cu.set.ledeOther'))
    expect(document.body.textContent).not.toContain(cevir('cu.set.lede'))
    expect((screen.getByTestId('cu-switch') as HTMLButtonElement).disabled).toBe(false)
  })

  it('never restarts the driver for a permission change on win32', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      status = hostStatus({ platform: 'win32', settings: { ...hostStatus().settings, enabled: true }, running: true })
      renderPage()
      await waitFor(() => expect(screen.getByTestId('cu-driver')).toBeTruthy())
      status = hostStatus({ platform: 'win32', settings: { ...hostStatus().settings, enabled: true }, running: true,
        permissions: { accessibility: true, screen: 'granted' } })
      await vi.advanceTimersByTimeAsync(4500)
      expect(ops.filter(op => op === 'status').length).toBeGreaterThan(1)
      expect(ops).not.toContain('restart')
    } finally {
      vi.useRealTimers()
    }
  })

  it('uses the new unavailable text in Turkish and English', async () => {
    status = hostStatus({ platform: 'win32', available: false })
    renderPage()
    await waitFor(() => expect(screen.getByTestId('cu-master-state').textContent).toBe(cevir('cu.set.unavailableOther')))
    expect(cevir('cu.set.unavailableOther')).toBe('Bu bilgisayarda sürücü bulunamadı; bilgisayar kullanımı yalnız macOS ve Windows\'ta çalışır.')
    cleanup()
    aktifDilAyarla('en')
    renderPage()
    await waitFor(() => expect(screen.getByTestId('cu-master-state').textContent)
      .toBe('No driver found on this computer; computer use works only on macOS and Windows.'))
    expect(cevir('cu.set.ledeOther')).toBe('The AI sees and uses the apps on your computer for you, by clicking and typing.')
  })

  it('keeps the grants list on win32', async () => {
    status = hostStatus({ platform: 'win32', settings: { ...hostStatus().settings, grants: [
      { bundle_id: 'win:c:\\x\\y.exe', app_name: 'Paint', granted_at: '2026-10-27T10:00:00Z' }] } })
    renderPage()
    await waitFor(() => expect(screen.getByTestId('cu-apps')).toBeTruthy())
    expect(screen.getByTestId('cu-remove-win:c:\\x\\y.exe')).toBeTruthy()
  })
})

describe('Settings > Bilgisayar on darwin is unchanged', () => {
  it.each([['darwin'], [undefined]])('shows the permissions and the Mac texts (platform=%s)', async platform => {
    status = hostStatus(platform === undefined ? {} : { platform })
    renderPage()
    await waitFor(() => expect(screen.getByTestId('cu-perms')).toBeTruthy())
    expect(screen.getByTestId('cu-perm-ax')).toBeTruthy()
    expect(screen.getByTestId('cu-perm-screen')).toBeTruthy()
    expect(document.body.textContent).toContain(cevir('cu.set.lede'))
    expect(document.body.textContent).not.toContain(cevir('cu.set.ledeOther'))
  })

  it('keeps the Mac unavailable text', async () => {
    status = hostStatus({ platform: 'darwin', available: false })
    renderPage()
    await waitFor(() => expect(screen.getByTestId('cu-master-state').textContent).toBe(cevir('cu.set.unavailable')))
  })
})

describe('parseComputerUseStatus platform', () => {
  it('passes the platform string through and defaults to darwin for older hosts', () => {
    expect(parseComputerUseStatus(hostStatus({ platform: 'win32' }))?.platform).toBe('win32')
    expect(parseComputerUseStatus(hostStatus())?.platform).toBe('darwin')
    expect(parseComputerUseStatus(hostStatus({ platform: 5 }))?.platform).toBe('darwin')
  })
})
