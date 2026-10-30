/**
 * Settings > Tarayici is its own page right after Bilgisayar: the rail item, the search terms
 * that find it, the page mount, and the master row's flex layout (no one-letter-per-line text).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'

import { SettingsScreen } from '../renderer/components/home/settings/SettingsScreen'
import { aktifDilAyarla, cevir } from '../renderer/lib/i18n'

const base = (over: any = {}) => ({
  open: true, aiConfig: { provider_type: 'anthropic', model_name: '', api_key: '' },
  availableModels: { local: [], subscription: [], cloud: [] }, providersWithKeys: [],
  onClose: vi.fn(), onLogout: vi.fn(), onDeleteKey: vi.fn(), unityMcpStatus: 'off',
  unityMcpToggling: false, onToggleUnityMcp: vi.fn(), lang: 'tr', onLangChange: vi.fn(), ...over,
}) as any

const rail = () => Array.from(document.querySelectorAll('.set-nav .set-nav-i')).map(b => (b as HTMLElement).dataset.setLink)

beforeEach(() => {
  aktifDilAyarla('tr')
  ;(window as any).ipc = { invoke: vi.fn(async () => ({ error: 'x' })) }
})
afterEach(() => {
  cleanup()
  delete (window as any).ipc
})

describe('Settings > Tarayici', () => {
  it('sits right after Bilgisayar in the rail with its own label', () => {
    render(<SettingsScreen {...base()} />)
    const items = rail()
    expect(items.indexOf('tarayici')).toBe(items.indexOf('bilgisayar') + 1)
    expect(screen.getByRole('button', { name: cevir('set.nav.tarayici') })).toBeTruthy()
    expect(cevir('set.nav.tarayici')).toBe('Tarayıcı')
  })

  it.each(['tarayıcı', 'çerez'])('searching "%s" finds only the Tarayici page', (q) => {
    render(<SettingsScreen {...base()} />)
    fireEvent.change(screen.getByTestId('settings-search'), { target: { value: q } })
    expect(rail()).toEqual(['tarayici'])
  })

  it('the Bilgisayar page no longer renders the browser settings', () => {
    render(<SettingsScreen {...base()} />)
    fireEvent.click(screen.getByRole('button', { name: cevir('set.nav.bilgisayar') }))
    expect(screen.queryByTestId('bp-settings')).toBeNull()
  })

  it('the Tarayici page renders them, and the master row uses the flex layout class', () => {
    render(<SettingsScreen {...base()} />)
    fireEvent.click(screen.getByRole('button', { name: cevir('set.nav.tarayici') }))
    expect(screen.getByTestId('bp-settings')).toBeTruthy()
    expect(screen.getByTestId('bp-master-row').className).toContain('cu-master-row')
  })
})
