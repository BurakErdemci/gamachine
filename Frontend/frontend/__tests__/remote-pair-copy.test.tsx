/**
 * Settings > Remote control: the pairing row can copy the pairing link so a
 * browser on another computer can pair at <relay>/app/.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react'

import { RemoteControlSection } from '../renderer/components/home/RemoteControlSection'
import { aktifDilAyarla } from '../renderer/lib/i18n'

const RELAY = 'https://relay.gamachine.example'
const QR_URL = `${RELAY}/p#AAAAAAAAAAAAAAAAAAAAAA.BBBB.CCCCCCCCCCCCCCCCCCCCCC`

const status = {
  enabled: true, connected: true, relay_url: RELAY, default_relay_url: RELAY,
  custom_relay: false, last_error: null, retry_at: null, gave_up: false,
  devices: 0, phones_online: 0, keep_awake: false, keep_awake_active: true,
  pairing: { offer_expires_at: null, pending: false },
}

const invoke = vi.fn(async (_channel: string, action: string) => {
  switch (action) {
    case 'status': return { ok: true, data: status }
    case 'devices': return { ok: true, data: { devices: [] } }
    case 'pair-start': return { ok: true, data: { qr_url: QR_URL, expires_at: Date.now() + 300_000, pair_id: 'x' } }
    case 'pair-pending': return { ok: true, data: { pending: null } }
  }
  throw new Error(`unexpected action ${action}`)
})

const showOffer = async () => {
  render(<RemoteControlSection statusPollMs={60_000} pendingPollMs={20} />)
  await waitFor(() => expect((screen.getByTestId('remote-pair') as HTMLButtonElement).disabled).toBe(false))
  fireEvent.click(screen.getByTestId('remote-pair'))
  await screen.findByTestId('remote-qr')
}

beforeEach(() => {
  aktifDilAyarla('en')
  invoke.mockClear()
  ;(window as any).ipc = { invoke }
})

afterEach(() => {
  cleanup()
  delete (window as any).ipc
  vi.unstubAllGlobals()
})

describe('remote pairing · copy link', () => {
  it('copies exactly the offer link and shows the copied label', async () => {
    const writeText = vi.fn(async () => {})
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } })
    await showOffer()
    const btn = screen.getByTestId('remote-pair-copy')
    expect(btn.textContent).toBe('Copy link')
    fireEvent.click(btn)
    await waitFor(() => expect(btn.textContent).toBe('Copied'))
    expect(writeText).toHaveBeenCalledTimes(1)
    expect(writeText).toHaveBeenCalledWith(QR_URL)
  })

  it('a rejected clipboard write shows the failed label', async () => {
    const writeText = vi.fn(async () => { throw new Error('denied') })
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } })
    await showOffer()
    const btn = screen.getByTestId('remote-pair-copy')
    fireEvent.click(btn)
    await waitFor(() => expect(btn.textContent).toBe('Copy failed'))
  })

  it('tells where to paste the link: the relay url plus /app/', async () => {
    await showOffer()
    expect(screen.getByTestId('remote-pair-browser-hint').textContent).toContain(`${RELAY}/app/`)
  })
})
