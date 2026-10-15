/**
 * Settings > Bilgisayar against a fake main process behind `window.ipc`
 * (the `computer-use` invoke channel). Asserted: which ops reach the main
 * process with which arguments, and what the page shows back, including the
 * desktop-only state when there is no `window.ipc` at all.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, fireEvent, waitFor, act } from '@testing-library/react'

import { ComputerUsePage } from '../renderer/components/home/settings/ComputerUsePage'
import { SettingsScreen } from '../renderer/components/home/settings/SettingsScreen'
import { aktifDilAyarla, cevir } from '../renderer/lib/i18n'
import { haltComputerUse, isCodeEditorBundle, parseComputerUseStatus } from '../renderer/lib/computerUse'

let host: {
  available: boolean; running: boolean; enabled: boolean
  accessibility: boolean; screen: string
  grants: { bundle_id: string; app_name: string; granted_at: string }[]
  fail: Record<string, boolean>
}
let ops: any[]

const invoke = vi.fn(async (channel: string, req: any) => {
  expect(channel).toBe('computer-use')
  ops.push(req)
  if (host.fail[req.op]) return { error: 'Computer-use request failed.' }
  switch (req.op) {
    case 'status':
      return { available: host.available, running: host.running,
        settings: { enabled: host.enabled, mode: 'step', active: host.enabled, grants: host.grants,
          runtime: { running: host.running, driver_path: null, socket_path: null }, halted: [] },
        permissions: { accessibility: host.accessibility, screen: host.screen } }
    case 'enable': case 'disable':
      host.enabled = req.op === 'enable'
      host.running = host.enabled
      return { enabled: host.enabled, active: host.enabled }
    case 'request-accessibility': return { accessibility: host.accessibility }
    case 'open-screen-settings': return { opened: true }
    case 'restart': host.running = host.enabled; return { running: host.running }
    case 'remove-grant':
      host.grants = host.grants.filter(g => g.bundle_id !== req.bundleId)
      return { removed: true }
    case 'halt': return { halted: true, conversation_id: req.conversationId }
    default: return { error: 'Unknown operation or malformed computer-use arguments.' }
  }
})

const opNames = () => ops.map(o => o.op)

beforeEach(() => {
  aktifDilAyarla('tr')
  host = { available: true, running: false, enabled: false, accessibility: false, screen: 'denied',
    grants: [], fail: {} }
  ops = []
  invoke.mockClear()
  ;(window as any).ipc = { invoke }
})
afterEach(() => {
  cleanup()
  delete (window as any).ipc
  vi.useRealTimers()
})

const renderPage = (over: Partial<React.ComponentProps<typeof ComputerUsePage>> = {}) =>
  render(<ComputerUsePage saved={vi.fn()} showToast={vi.fn()} {...over} />)

describe('Settings > Bilgisayar · without Electron', () => {
  it('shows a desktop-only state, the rules and the hard-denied line, and calls nothing', () => {
    delete (window as any).ipc
    renderPage()
    expect(screen.getByTestId('cu-page').getAttribute('data-desktop')).toBe('false')
    expect(screen.getByTestId('cu-master-state').textContent).toBe(cevir('cu.set.desktopOnly'))
    expect((screen.getByTestId('cu-switch') as HTMLButtonElement).disabled).toBe(true)
    const notice = screen.getByTestId('cu-notice').textContent
    for (const k of ['modes', 'first', 'risky', 'editors', 'stop'] as const) {
      expect(notice).toContain(cevir(`cu.set.warn.${k}`))
    }
    expect(screen.getByTestId('cu-denied').textContent).toBe(cevir('cu.set.denied'))
    expect(screen.queryByTestId('cu-perms')).toBeNull()
    expect(screen.queryByTestId('cu-apps')).toBeNull()
    expect(invoke).not.toHaveBeenCalled()
  })

  it('the lib wrappers answer no_desktop instead of throwing', async () => {
    delete (window as any).ipc
    expect(await haltComputerUse(3)).toEqual({ error: 'no_desktop' })
  })

  it('the English page states the mode independence too', () => {
    delete (window as any).ipc
    aktifDilAyarla('en')
    renderPage()
    expect(screen.getByTestId('cu-notice').textContent).toContain('independent of the approval mode')
  })
})

describe('Settings > Bilgisayar · with the main process', () => {
  it('reads status and turns computer use on in any mode', async () => {
    const saved = vi.fn()
    renderPage({ saved })
    await waitFor(() => expect(screen.getByTestId('cu-master-state').textContent).toBe(cevir('cu.set.masterOff')))
    const sw = screen.getByTestId('cu-switch')
    expect(sw.getAttribute('aria-checked')).toBe('false')
    await act(async () => { fireEvent.click(sw) })
    expect(ops).toContainEqual({ op: 'enable' })
    await waitFor(() => expect(screen.getByTestId('cu-switch').getAttribute('aria-checked')).toBe('true'))
    expect(screen.getByTestId('cu-master-state').textContent).toBe(cevir('cu.set.masterOn'))
    expect(saved).toHaveBeenCalledTimes(1)
    await act(async () => { fireEvent.click(screen.getByTestId('cu-switch')) })
    expect(ops).toContainEqual({ op: 'disable' })
    await waitFor(() => expect(screen.getByTestId('cu-switch').getAttribute('aria-checked')).toBe('false'))
  })

  it('a failed switch shows an error and keeps the old state', async () => {
    host.fail.enable = true
    const showToast = vi.fn()
    renderPage({ showToast })
    await waitFor(() => expect(screen.getByTestId('cu-switch').hasAttribute('disabled')).toBe(false))
    await act(async () => { fireEvent.click(screen.getByTestId('cu-switch')) })
    await waitFor(() => expect(screen.getByTestId('cu-msg').textContent).toBe(cevir('cu.set.failed')))
    expect(showToast).toHaveBeenCalledWith(cevir('cu.set.failed'), 'error')
    expect(screen.getByTestId('cu-switch').getAttribute('aria-checked')).toBe('false')
  })

  it('missing permissions offer their buttons with the fixed ops', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByTestId('cu-perms')).toBeTruthy())
    expect(screen.getByTestId('cu-perm-ax').textContent).toContain(cevir('cu.set.missing'))
    expect(screen.getByTestId('cu-perm-screen').textContent).toContain(cevir('cu.set.missing'))
    await act(async () => { fireEvent.click(screen.getByTestId('cu-ask-ax')) })
    await act(async () => { fireEvent.click(screen.getByTestId('cu-open-screen')) })
    expect(ops).toContainEqual({ op: 'request-accessibility' })
    expect(ops).toContainEqual({ op: 'open-screen-settings' })
    expect(screen.getByText(cevir('cu.set.afterGrant'))).toBeTruthy()
  })

  it('a permission granted while the page is open restarts the driver once', async () => {
    host.enabled = true
    host.running = true
    renderPage()
    await waitFor(() => expect(screen.getByTestId('cu-perm-ax').textContent).toContain(cevir('cu.set.missing')))
    expect(opNames()).not.toContain('restart')
    host.accessibility = true
    await act(async () => { window.dispatchEvent(new Event('focus')) })
    await waitFor(() => expect(opNames().filter(o => o === 'restart')).toHaveLength(1))
    await waitFor(() => expect(screen.getByTestId('cu-perm-ax').textContent).toContain(cevir('cu.set.granted')))
    await act(async () => { window.dispatchEvent(new Event('focus')) })
    expect(opNames().filter(o => o === 'restart')).toHaveLength(1)
  })

  it('no restart for a grant while computer use is off', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByTestId('cu-perms')).toBeTruthy())
    host.accessibility = true
    host.screen = 'granted'
    await act(async () => { window.dispatchEvent(new Event('focus')) })
    await waitFor(() => expect(screen.getByTestId('cu-perm-ax').textContent).toContain(cevir('cu.set.granted')))
    expect(opNames()).not.toContain('restart')
  })

  it('shows the driver state and restarts it on request', async () => {
    host.enabled = true
    host.running = false
    host.accessibility = true
    host.screen = 'granted'
    renderPage()
    await waitFor(() => expect(screen.getByTestId('cu-driver-state').textContent).toBe(cevir('cu.set.driverDown')))
    await act(async () => { fireEvent.click(screen.getByTestId('cu-restart')) })
    expect(ops).toContainEqual({ op: 'restart' })
    await waitFor(() => expect(screen.getByTestId('cu-driver-state').textContent).toBe(cevir('cu.set.driverRunning')))
    expect(screen.getByTestId('cu-msg').textContent).toBe(cevir('cu.set.restarted'))
  })

  it('lists apps answered "Always" and removes one by its bundle id', async () => {
    host.grants = [
      { bundle_id: 'com.apple.TextEdit', app_name: 'TextEdit', granted_at: '2026-10-15T09:00:00+00:00' },
      { bundle_id: 'com.apple.Notes', app_name: 'Notes', granted_at: '2026-10-14T09:00:00+00:00' },
    ]
    const saved = vi.fn()
    renderPage({ saved })
    await waitFor(() => expect(screen.getByTestId('cu-grant-com.apple.TextEdit')).toBeTruthy())
    expect(screen.getByTestId('cu-grant-com.apple.TextEdit').textContent).toContain('com.apple.TextEdit')
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: cevir('cu.set.removeLabel', { ad: 'TextEdit' }) })) })
    expect(ops).toContainEqual({ op: 'remove-grant', bundleId: 'com.apple.TextEdit' })
    await waitFor(() => expect(screen.queryByTestId('cu-grant-com.apple.TextEdit')).toBeNull())
    expect(screen.getByTestId('cu-grant-com.apple.Notes')).toBeTruthy()
    expect(saved).toHaveBeenCalledTimes(1)
  })

  it('an empty grant list says so', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByTestId('cu-apps-empty').textContent).toBe(cevir('cu.set.appsEmpty')))
  })

  it('an unavailable driver disables the switch and hides the driver block', async () => {
    host.available = false
    renderPage()
    await waitFor(() => expect(screen.getByTestId('cu-master-state').textContent).toBe(cevir('cu.set.unavailable')))
    expect((screen.getByTestId('cu-switch') as HTMLButtonElement).disabled).toBe(true)
    expect(screen.queryByTestId('cu-driver')).toBeNull()
  })

  it('a malformed status answer reads as a failure, not as "off"', async () => {
    ;(window as any).ipc = { invoke: vi.fn(async () => ({ ok: true, data: { enabled: false } })) }
    renderPage()
    await waitFor(() => expect(screen.getByTestId('cu-master-state').textContent).toBe(cevir('cu.set.readFailed')))
    expect((screen.getByTestId('cu-switch') as HTMLButtonElement).disabled).toBe(true)
  })

  it('haltComputerUse sends the halt op with the conversation id', async () => {
    expect(await haltComputerUse(42)).toEqual({ halted: true, conversation_id: 42 })
    expect(ops).toEqual([{ op: 'halt', conversationId: 42 }])
  })
})

describe('Settings > Bilgisayar · in the settings screen', () => {
  const base = (over: any = {}) => ({
    open: true, aiConfig: { provider_type: 'anthropic', model_name: '', api_key: '' },
    availableModels: { local: [], subscription: [], cloud: [] }, providersWithKeys: [],
    onClose: vi.fn(), onLogout: vi.fn(), onDeleteKey: vi.fn(), unityMcpStatus: 'off',
    unityMcpToggling: false, onToggleUnityMcp: vi.fn(), lang: 'tr', onLangChange: vi.fn(), ...over,
  }) as any

  it('sits after Onay modu in the rail and opens the page', async () => {
    render(<SettingsScreen {...base()} />)
    const rail = Array.from(document.querySelectorAll('.set-nav .set-nav-i')).map(b => (b as HTMLElement).dataset.setLink)
    expect(rail.indexOf('bilgisayar')).toBe(rail.indexOf('onay') + 1)
    fireEvent.click(screen.getByRole('button', { name: cevir('set.nav.bilgisayar') }))
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(cevir('set.nav.bilgisayar'))
    await waitFor(() => expect(ops).toContainEqual({ op: 'status' }))
  })

  it('the search finds it by a permission name', () => {
    render(<SettingsScreen {...base()} />)
    fireEvent.change(screen.getByTestId('settings-search'), { target: { value: 'erişilebilirlik' } })
    const items = Array.from(document.querySelectorAll('.set-nav .set-nav-i')).map(b => (b as HTMLElement).dataset.setLink)
    expect(items).toEqual(['bilgisayar'])
  })
})

describe('computerUse helpers', () => {
  it.each([
    ['com.microsoft.VSCode', true], ['com.todesktop.230313mzl4w4u92', true], ['com.jetbrains.intellij', true],
    ['com.jetbrains.pycharm.ce', true], ['com.apple.TextEdit', false], [null, false], ['', false],
  ])('isCodeEditorBundle(%j) = %s', (id, expected) => {
    expect(isCodeEditorBundle(id)).toBe(expected)
  })

  it('parseComputerUseStatus drops malformed grant rows', () => {
    const parsed = parseComputerUseStatus({ available: true, running: false,
      settings: { enabled: true, grants: [null, { bundle_id: 'a.b', app_name: 'A', granted_at: 'x' }, { bundle_id: 1 }] },
      permissions: { accessibility: 'yes', screen: 5 } })
    expect(parsed?.settings.grants).toHaveLength(1)
    expect(parsed?.permissions).toEqual({ accessibility: false, screen: 'not-determined' })
    expect(parseComputerUseStatus({ error: 'x' })).toBeNull()
  })
})
