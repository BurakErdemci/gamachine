/**
 * Settings > Remote control, desktop section: two ways in (phone, another
 * computer), a device-neutral approval text and the device list with kinds.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, fireEvent, waitFor, within } from '@testing-library/react'

import { RemoteControlSection } from '../renderer/components/home/RemoteControlSection'
import { SettingsScreen } from '../renderer/components/home/settings/SettingsScreen'
import { aktifDilAyarla } from '../renderer/lib/i18n'

const RELAY = 'https://relay.gamachine.example'
const QR_URL = `${RELAY}/p#AAAAAAAAAAAAAAAAAAAAAA.BBBB.CCCCCCCCCCCCCCCCCCCCCC`

let devices: any[]
let pending: any

const status = () => ({
  enabled: true, connected: true, relay_url: RELAY, default_relay_url: RELAY,
  custom_relay: false, last_error: null, retry_at: null, gave_up: false,
  devices: devices.length, phones_online: 0, keep_awake: false, keep_awake_active: true,
  pairing: { offer_expires_at: null, pending: !!pending },
})

const invoke = vi.fn(async (_channel: string, action: string) => {
  switch (action) {
    case 'status': return { ok: true, data: status() }
    case 'devices': return { ok: true, data: { devices } }
    case 'pair-start': return { ok: true, data: { qr_url: QR_URL, expires_at: Date.now() + 300_000, pair_id: 'x' } }
    case 'pair-pending': return { ok: true, data: { pending } }
  }
  throw new Error(`unexpected action ${action}`)
})

const startOffer = async () => {
  render(<RemoteControlSection statusPollMs={60_000} pendingPollMs={20} />)
  await waitFor(() => expect((screen.getByTestId('remote-pair') as HTMLButtonElement).disabled).toBe(false))
  fireEvent.click(screen.getByTestId('remote-pair'))
  await screen.findByTestId('remote-qr')
}

beforeEach(() => {
  aktifDilAyarla('tr')
  devices = []
  pending = null
  invoke.mockClear()
  ;(window as any).ipc = { invoke }
})

afterEach(() => {
  cleanup()
  delete (window as any).ipc
})

describe('remote screen · two ways in', () => {
  it('one "Cihaz eşleştir" action opens a phone card with the QR and a computer card with two steps', async () => {
    render(<RemoteControlSection statusPollMs={60_000} pendingPollMs={20} />)
    await waitFor(() => expect((screen.getByTestId('remote-pair') as HTMLButtonElement).disabled).toBe(false))
    expect(screen.getByTestId('remote-pair').textContent).toBe('Cihaz eşleştir')
    expect(screen.queryByTestId('remote-opt-phone')).toBeNull()
    fireEvent.click(screen.getByTestId('remote-pair'))
    await screen.findByTestId('remote-qr')

    const phone = screen.getByTestId('remote-opt-phone')
    expect(phone.textContent).toContain('Telefon')
    expect(phone.textContent).toContain('İzle, onayla, yaz.')
    expect(within(phone).getByTestId('remote-qr')).toBeTruthy()

    const pc = screen.getByTestId('remote-opt-pc')
    expect(pc.textContent).toContain('Başka bir bilgisayar')
    expect(pc.textContent).toContain('Masaüstüyle aynı ekran, tam kontrol.')
    expect(within(pc).queryByTestId('remote-qr')).toBeNull()
    const steps = pc.querySelectorAll('ol > li')
    expect(steps.length).toBe(2)
    expect(steps[0].textContent).toContain(`${RELAY}/app/`)
    expect(within(steps[0] as HTMLElement).getByTestId('remote-copy-address')).toBeTruthy()
    expect(within(steps[1] as HTMLElement).getByTestId('remote-pair-copy')).toBeTruthy()
  })

  it('the page top line names both devices', () => {
    render(<SettingsScreen {...({
      open: true, providersWithKeys: [], onChange: () => {}, onClose: () => {}, onSave: async () => {},
      onLogout: () => {}, onDeleteKey: async () => {}, unityMcpStatus: 'off', unityMcpToggling: false,
      onToggleUnityMcp: () => {}, lang: 'tr', onLangChange: () => {},
      aiConfig: { provider_type: 'anthropic', model_name: '', api_key: '' },
    } as any)} />)
    fireEvent.click(screen.getByRole('button', { name: 'Uzaktan kontrol' }))
    expect(screen.getByText(/telefonundan ya da başka bir bilgisayarın tarayıcısından/)).toBeTruthy()
  })

  it('relay, keep awake and forget live in the collapsed advanced part', async () => {
    render(<RemoteControlSection statusPollMs={60_000} pendingPollMs={20} />)
    const details = (await screen.findByTestId('remote-forget')).closest('details')!
    expect(details.open).toBe(false)
    expect(details.contains(screen.getByTestId('remote-keep-awake'))).toBe(true)
    expect(details.contains(screen.getByTestId('remote-relay-input'))).toBe(true)
  })
})

describe('remote screen · approval text and device kinds', () => {
  it.each([
    ['phone', 'Telefon (iPhone) eşleşmek istiyor'],
    ['web', 'Tarayıcı (iPhone) eşleşmek istiyor'],
    [null, 'Cihaz (iPhone) eşleşmek istiyor'],
    [undefined, 'Cihaz (iPhone) eşleşmek istiyor'],
  ])('kind %s reads "%s" and asks for the same code on that device', async (kind, text) => {
    await startOffer()
    pending = { device_name: 'iPhone', sas: '7314', source: 'qr', kind, expires_at: Date.now() + 60_000 }
    await screen.findByTestId('remote-sas')
    const req = screen.getByTestId('remote-pair-request').textContent!
    expect(req).toContain(text)
    expect(req).toContain('Aynı kodu o cihazda görüyorsan onayla.')
    expect(req).not.toContain('telefonda')
  })

  it('the list is titled "Eşleşmiş cihazlar" and each row shows its kind, name and remove', async () => {
    devices = [
      { device_id: 'd1', name: 'iPhone', kind: 'phone', created: 1, last_seen: null, online: true },
      { device_id: 'd2', name: 'Tarayıcı', kind: 'web', created: 1, last_seen: 1_700_000_000_000 },
      { device_id: 'd3', name: 'Eski', kind: null, created: 1, last_seen: null },
      { device_id: 'd4', name: 'Eski2', created: 1, last_seen: null },
    ]
    render(<RemoteControlSection statusPollMs={60_000} pendingPollMs={20} />)
    await waitFor(() => expect(screen.getAllByTestId('remote-device').length).toBe(4))
    expect(screen.getByText('Eşleşmiş cihazlar')).toBeTruthy()
    expect(screen.getAllByTestId('remote-device-kind').map(n => n.textContent))
      .toEqual(['Telefon', 'Tarayıcı', 'Cihaz', 'Cihaz'])
    const rows = screen.getAllByTestId('remote-device')
    expect(rows[0].textContent).toContain('iPhone')
    expect(rows[0].textContent).toContain('Şu an bağlı')
    expect(rows[1].textContent).toContain('Son görülme')
    rows.forEach(r => expect(within(r).getByText('Kaldır')).toBeTruthy())
  })

  it('an empty list says "Eşleşmiş cihaz yok."', async () => {
    render(<RemoteControlSection statusPollMs={60_000} pendingPollMs={20} />)
    expect(await screen.findByText('Eşleşmiş cihaz yok.')).toBeTruthy()
  })
})

describe('instructions that name the pairing button', () => {
  it('use the button\'s current name in every language', async () => {
    const { readFileSync } = await import('node:fs')
    const { resolve } = await import('node:path')
    const src = readFileSync(resolve(__dirname, '../renderer/lib/i18n.tsx'), 'utf8')
    expect(src).not.toMatch(/Telefon eşleştir|Pair phone/)
  })
})
