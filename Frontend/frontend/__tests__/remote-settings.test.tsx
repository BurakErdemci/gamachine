/**
 * Settings > Remote control (docs/remote-control.md step 4) against a fake
 * backend behind `window.ipc` (the `remote-control` invoke channel). What is
 * asserted: which actions reach the main process, with which argument, and
 * what the user sees back (QR, pairing code, device list, notes, errors).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, fireEvent, waitFor, act } from '@testing-library/react'

import { RemoteControlSection, qrPath } from '../renderer/components/home/RemoteControlSection'
import { RemoteBadge } from '../renderer/components/home/RemoteBadge'
import { SettingsModal } from '../renderer/components/home/SettingsModal'
import { aktifDilAyarla } from '../renderer/lib/i18n'

const DEFAULT_RELAY = 'https://relay.gamachine.example'
const QR_URL = `${DEFAULT_RELAY}/p#AAAAAAAAAAAAAAAAAAAAAA.BBBB.CCCCCCCCCCCCCCCCCCCCCC`

type Device = { device_id: string; name: string; created: number; last_seen: number | null; online?: boolean }

let backend: {
  enabled: boolean; connected: boolean; keep_awake: boolean; relay_url: string
  devices: Device[]; pending: any; failures: Record<string, string>
}
let calls: Array<[string, unknown]>

const status = () => ({
  enabled: backend.enabled, connected: backend.enabled && backend.connected,
  relay_url: backend.relay_url, default_relay_url: DEFAULT_RELAY,
  custom_relay: backend.relay_url !== DEFAULT_RELAY, last_error: null, retry_at: null, gave_up: false,
  devices: backend.devices.length, phones_online: 0, keep_awake: backend.keep_awake,
  keep_awake_active: backend.enabled && backend.keep_awake,
  pairing: { offer_expires_at: null, pending: !!backend.pending },
})

const invoke = vi.fn(async (channel: string, action: string, arg?: unknown) => {
  expect(channel).toBe('remote-control')
  calls.push([action, arg])
  if (backend.failures[action]) return { ok: false, code: backend.failures[action] }
  switch (action) {
    case 'status': return { ok: true, data: status() }
    case 'enable': backend.enabled = true; backend.connected = true; return { ok: true, data: status() }
    case 'disable': backend.enabled = false; return { ok: true, data: status() }
    case 'pair-start': return { ok: true, data: { qr_url: QR_URL, expires_at: Date.now() + 300_000, pair_id: 'x' } }
    case 'pair-pending': return { ok: true, data: { pending: backend.pending } }
    case 'pair-approve': {
      const d = { device_id: 'dev_new', name: backend.pending.device_name, created: Date.now(), last_seen: null }
      backend.devices.push(d)
      backend.pending = null
      return { ok: true, data: { device: d } }
    }
    case 'pair-reject': backend.pending = null; return { ok: true, data: { rejected: true } }
    case 'devices': return { ok: true, data: { devices: backend.devices } }
    case 'remove-device':
      backend.devices = backend.devices.filter(d => d.device_id !== arg)
      return { ok: true, data: { removed: arg } }
    case 'remove-all-devices': {
      const n = backend.devices.length
      backend.devices = []
      return { ok: true, data: { removed: n } }
    }
    case 'set-relay-url': {
      const before = backend.relay_url
      backend.relay_url = (arg as string | null) ?? DEFAULT_RELAY
      return { ok: true, data: { relay_url: backend.relay_url, devices_need_repair: before !== backend.relay_url && backend.devices.length > 0 } }
    }
    case 'set-keep-awake': backend.keep_awake = arg as boolean; return { ok: true, data: { keep_awake: arg } }
    case 'forget':
      backend.enabled = false; backend.devices = []; backend.pending = null
      return { ok: true, data: { ...status(), relay_reset: true } }
  }
  throw new Error(`unexpected action ${action}`)
})

const actions = () => calls.map(c => c[0]).filter(a => a !== 'status' && a !== 'devices' && a !== 'pair-pending')

const section = (onStatus = vi.fn()) => {
  render(<RemoteControlSection onStatus={onStatus} statusPollMs={60_000} pendingPollMs={20} />)
  return onStatus
}

beforeEach(() => {
  aktifDilAyarla('tr')
  backend = {
    enabled: false, connected: false, keep_awake: false, relay_url: DEFAULT_RELAY,
    devices: [], pending: null, failures: {},
  }
  calls = []
  invoke.mockClear()
  ;(window as any).ipc = { invoke }
})

afterEach(() => {
  cleanup()
  delete (window as any).ipc
  vi.unstubAllGlobals()
})

describe('remote settings · on / off', () => {
  it('is off by default; the switch enables it and the status line follows', async () => {
    const onStatus = section()
    await waitFor(() => expect(screen.getByTestId('remote-status').textContent).toBe('Kapalı'))
    expect(screen.getByTestId('remote-toggle').getAttribute('aria-checked')).toBe('false')
    expect((screen.getByTestId('remote-pair') as HTMLButtonElement).disabled).toBe(true)

    fireEvent.click(screen.getByTestId('remote-toggle'))
    await waitFor(() => expect(screen.getByTestId('remote-status').textContent).toBe('Bağlı'))
    expect(actions()).toEqual(['enable'])
    expect(onStatus).toHaveBeenLastCalledWith(expect.objectContaining({ enabled: true }))

    fireEvent.click(screen.getByTestId('remote-toggle'))
    await waitFor(() => expect(screen.getByTestId('remote-status').textContent).toBe('Kapalı'))
    expect(actions()).toEqual(['enable', 'disable'])
  })
})

describe('remote settings · pairing', () => {
  it('pair -> QR with countdown -> request with the code -> approve -> device listed', async () => {
    backend.enabled = true; backend.connected = true
    section()
    await waitFor(() => expect((screen.getByTestId('remote-pair') as HTMLButtonElement).disabled).toBe(false))

    fireEvent.click(screen.getByTestId('remote-pair'))
    const qr = await screen.findByTestId('remote-qr')
    expect(qr.querySelector('path')!.getAttribute('d')).toBe(qrPath(QR_URL).d)
    expect(qrPath(QR_URL).size).toBeGreaterThanOrEqual(21)
    expect(screen.getByTestId('remote-pair-countdown').textContent).toMatch(/Kalan süre: [45]:\d\d/)

    backend.pending = { device_name: 'iPhone', sas: '7314', source: 'qr', expires_at: Date.now() + 60_000 }
    await waitFor(() => expect(screen.getByTestId('remote-sas').textContent).toBe('7314'))
    expect(screen.getByTestId('remote-pair-request').textContent).toContain('iPhone eşleşmek istiyor')
    expect(screen.queryByTestId('remote-qr')).toBeNull()

    fireEvent.click(screen.getByTestId('remote-approve'))
    await waitFor(() => expect(screen.getByTestId('remote-note').textContent).toBe('iPhone eşleşti.'))
    expect(actions()).toEqual(['pair-start', 'pair-approve'])
    await waitFor(() => expect(screen.getAllByTestId('remote-device').map(n => n.textContent)).toEqual([expect.stringContaining('iPhone')]))
  })

  it('reject answers the request and closes the QR', async () => {
    backend.enabled = true; backend.connected = true
    section()
    await waitFor(() => expect((screen.getByTestId('remote-pair') as HTMLButtonElement).disabled).toBe(false))
    fireEvent.click(screen.getByTestId('remote-pair'))
    await screen.findByTestId('remote-qr')
    backend.pending = { device_name: 'Pixel', sas: '0042', source: 'qr', expires_at: Date.now() + 60_000 }
    await screen.findByTestId('remote-sas')
    fireEvent.click(screen.getByTestId('remote-reject'))
    await waitFor(() => expect(screen.getByTestId('remote-note').textContent).toBe('Eşleşme reddedildi.'))
    expect(actions()).toEqual(['pair-start', 'pair-reject'])
    expect(screen.queryByTestId('remote-qr')).toBeNull()
  })

  it('a refused start shows the reason in the app language', async () => {
    backend.enabled = true; backend.connected = true
    backend.failures['pair-start'] = 'relay_unreachable'
    section()
    await waitFor(() => expect((screen.getByTestId('remote-pair') as HTMLButtonElement).disabled).toBe(false))
    fireEvent.click(screen.getByTestId('remote-pair'))
    await waitFor(() => expect(screen.getByTestId('remote-error').textContent).toContain('Relay\'e ulaşılamadı'))
    expect(screen.queryByTestId('remote-qr')).toBeNull()
  })
})

describe('remote settings · devices, relay, keep awake, forget', () => {
  it('Remove and Remove all ask first and send the device id', async () => {
    backend.enabled = true
    backend.devices = [
      { device_id: 'd1', name: 'iPhone', created: 1_700_000_000_000, last_seen: null },
      { device_id: 'd2', name: 'iPad', created: 1_700_000_000_000, last_seen: 1_700_000_100_000 },
    ]
    const confirm = vi.fn(() => true)
    vi.stubGlobal('confirm', confirm)
    section()
    await waitFor(() => expect(screen.getAllByTestId('remote-device').length).toBe(2))
    expect(screen.getAllByTestId('remote-device')[0].textContent).toContain('Henüz bağlanmadı')

    fireEvent.click(screen.getAllByText('Kaldır')[0])
    await waitFor(() => expect(screen.getAllByTestId('remote-device').length).toBe(1))
    expect(calls).toContainEqual(['remove-device', 'd1'])
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('"iPhone" kaldırılsın mı?'))

    fireEvent.click(screen.getByTestId('remote-remove-all'))
    await waitFor(() => expect(screen.queryAllByTestId('remote-device').length).toBe(0))
    expect(actions()).toEqual(['remove-device', 'remove-all-devices'])
  })

  it('my own relay is saved through the channel and warns when phones must pair again', async () => {
    backend.enabled = true
    backend.devices = [{ device_id: 'd1', name: 'iPhone', created: 1, last_seen: null }]
    section()
    await screen.findByText(`Kullanılan: ${DEFAULT_RELAY}`)
    fireEvent.change(screen.getByTestId('remote-relay-input'), { target: { value: ' https://mine.example ' } })
    fireEvent.click(screen.getByTestId('remote-relay-save'))
    await screen.findByTestId('remote-relay-repair')
    expect(calls).toContainEqual(['set-relay-url', 'https://mine.example'])
    await screen.findByText('Kullanılan: https://mine.example')

    fireEvent.click(screen.getByText('Varsayılana dön'))
    await screen.findByText(`Kullanılan: ${DEFAULT_RELAY}`)
    expect(calls).toContainEqual(['set-relay-url', null])
  })

  it('the keep-awake checkbox writes the flag', async () => {
    backend.enabled = true
    section()
    const box = await screen.findByTestId('remote-keep-awake') as HTMLInputElement
    await waitFor(() => expect(box.disabled).toBe(false))
    fireEvent.click(box)
    await waitFor(() => expect(box.checked).toBe(true))
    expect(calls).toContainEqual(['set-keep-awake', true])
  })

  it('forget asks first: cancel sends nothing, confirm turns off and forgets', async () => {
    backend.enabled = true
    backend.devices = [{ device_id: 'd1', name: 'iPhone', created: 1, last_seen: null }]
    const confirm = vi.fn(() => false)
    vi.stubGlobal('confirm', confirm)
    section()
    await waitFor(() => expect(screen.getAllByTestId('remote-device').length).toBe(1))

    fireEvent.click(screen.getByTestId('remote-forget'))
    await act(async () => { await Promise.resolve() })
    expect(confirm).toHaveBeenCalledTimes(1)
    expect(actions()).toEqual([])

    confirm.mockReturnValue(true)
    fireEvent.click(screen.getByTestId('remote-forget'))
    await waitFor(() => expect(screen.getByTestId('remote-note').textContent).toBe('Uzaktan kontrol kapatıldı ve her şey silindi.'))
    expect(actions()).toEqual(['forget'])
    expect(screen.getByTestId('remote-status').textContent).toBe('Kapalı')
    expect(screen.queryAllByTestId('remote-device').length).toBe(0)
  })
})

describe('remote control · always-visible badge and the settings tab', () => {
  it('the badge shows only while remote control is on', () => {
    const { rerender } = render(<RemoteBadge status={null} />)
    expect(screen.queryByTestId('remote-badge')).toBeNull()
    rerender(<RemoteBadge status={{ enabled: false } as any} />)
    expect(screen.queryByTestId('remote-badge')).toBeNull()
    rerender(<RemoteBadge status={{ enabled: true, connected: true } as any} />)
    expect(screen.getByTestId('remote-badge').textContent).toContain('Uzaktan kontrol açık')
  })

  it('the settings modal has a Remote control tab holding the section', async () => {
    render(<SettingsModal {...({
      open: true, providersWithKeys: [], onChange: () => {}, onClose: () => {}, onSave: async () => {},
      onLogout: () => {}, onDeleteKey: async () => {}, unityMcpStatus: 'off', unityMcpToggling: false,
      onToggleUnityMcp: () => {}, lang: 'tr', onLangChange: () => {},
      aiConfig: { provider_type: 'anthropic', model_name: '', api_key: '' },
    } as any)} />)
    expect(screen.queryByTestId('remote-section')).toBeNull()
    fireEvent.click(screen.getByRole('tab', { name: 'Uzaktan kontrol' }))
    expect(screen.getByTestId('remote-section')).toBeTruthy()
    await waitFor(() => expect(calls.some(c => c[0] === 'status')).toBe(true))
  })
})
