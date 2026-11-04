// The phone chat's Ekran tab (renderer/components/phone/PhoneScreenTab.tsx): while it is on screen the live
// panel reports `panel-visible`; the desktop renderer (lib/remote/desktopRunner.ts) hands that to main as
// its remote-only `remote-panel-visible`, so the desktop panel's own flag never moves.
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'

vi.mock('axios', () => {
  const post = vi.fn(async () => ({ data: {} }))
  return { default: { post, get: vi.fn() }, post }
})

import { PhoneScreenTab } from '../renderer/components/phone/PhoneScreenTab'
import { PhoneShell } from '../renderer/components/phone/PhoneShell'
import { runRemoteIpc } from '../renderer/lib/remote/desktopRunner'
import { aktifDilAyarla, translations } from '../renderer/lib/i18n'

const tr = translations.tr
const FRAME = 'data:image/png;base64,iVBORw0KGgo='
let enabled: boolean
let listeners: Map<string, (payload: unknown) => void>
let invoke: ReturnType<typeof vi.fn>

const cuStatus = () => ({
  available: true, running: true, platform: 'darwin',
  settings: { enabled, grants: [] }, permissions: { accessibility: true, screen: 'granted' },
})
const active = (frame: string | null) => ({
  active: true, running: true, conversationId: 7, startedAt: Date.now() - 1000,
  last: null, target: { bundle_id: 'com.apple.TextEdit', app_name: 'TextEdit', pid: 4242, window_id: 55 }, frame,
})

beforeEach(() => {
  aktifDilAyarla('tr')
  enabled = true
  listeners = new Map()
  // The browser's window.ipc (the remote shim in the real page): every call goes to the PC.
  invoke = vi.fn(async (channel: string, req: any) => {
    if (channel === 'computer-use') return cuStatus()
    if (channel === 'browser-live') return { enabled: false }
    if (channel === 'computer-use-live' && req?.op === 'status') return { active: false, running: true }
    return { ok: true }
  })
  vi.stubGlobal('ipc', {
    invoke,
    on: vi.fn((channel: string, cb: (payload: unknown) => void) => { listeners.set(channel, cb); return () => listeners.delete(channel) }),
  })
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('PhoneScreenTab', () => {
  it('reports panel-visible while shown, and the desktop runner turns it into remote-panel-visible', async () => {
    const { unmount } = render(<PhoneScreenTab visible />)
    await waitFor(() => expect(invoke).toHaveBeenCalledWith('computer-use-live', { op: 'panel-visible', visible: true }))
    const sent = invoke.mock.calls.find(([c, r]) => c === 'computer-use-live' && r?.op === 'panel-visible')!
    unmount()
    expect(invoke).toHaveBeenLastCalledWith('computer-use-live', { op: 'panel-visible', visible: false })

    // The same call arriving at the desktop renderer as a remote_ipc frame.
    const desktop = vi.fn(async () => ({ remoteVisible: true }))
    vi.stubGlobal('ipc', { invoke: desktop, on: vi.fn() })
    await runRemoteIpc({ requestId: '0123456789abcdef', channel: sent[0], args: sent.slice(1) }, 'http://127.0.0.1:8000', 'tok')
    expect(desktop).toHaveBeenCalledWith('computer-use-live', { op: 'remote-panel-visible', visible: true })
    expect(desktop.mock.calls.some(([, r]: any) => r?.op === 'panel-visible')).toBe(false)
  })

  it('shows the frame, zoomable, with how long ago the last one came; the Stop button stays', async () => {
    render(<PhoneScreenTab visible />)
    expect(await screen.findByTestId('phone-screen-status')).toBeTruthy()
    expect(screen.getByTestId('phone-screen-status').textContent).toBe(tr['phone.screen.statusNoFrame'])
    await waitFor(() => expect(listeners.has('computer-use-live')).toBe(true))
    act(() => { listeners.get('computer-use-live')!(active(FRAME)) })
    const img = await screen.findByRole('img', { name: /TextEdit/ })
    expect(img.closest('.cul-screen')!.hasAttribute('data-zoomable')).toBe(true)
    await waitFor(() => expect(screen.getByTestId('phone-screen-status').textContent).toBe(tr['phone.screen.status'].replace('{n}', '0')))
    expect(screen.getByRole('button', { name: new RegExp(tr['cu.live.stop']) })).toBeTruthy()
  })

  it('computer use off: a plain note, no live panel, no visibility report', async () => {
    enabled = false
    render(<PhoneScreenTab visible />)
    await waitFor(() => expect(invoke).toHaveBeenCalledWith('computer-use', { op: 'status' }))
    expect(screen.getByText(tr['cu.live.off'])).toBeTruthy()
    expect(screen.getByText(tr['cu.live.offHint'])).toBeTruthy()
    expect(invoke.mock.calls.some(([c, r]) => c === 'computer-use-live' && r?.op === 'panel-visible')).toBe(false)
  })

  it('a hidden page (phone locked, app switched) stops the capture, and the page coming back starts it again', async () => {
    const seen = () => invoke.mock.calls.filter(([c, r]) => c === 'computer-use-live' && r?.op === 'panel-visible').map(([, r]) => r.visible)
    let state: DocumentVisibilityState = 'visible'
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state })
    try {
      render(<PhoneScreenTab visible />)
      await waitFor(() => expect(seen()).toEqual([true]))
      state = 'hidden'
      await act(async () => { document.dispatchEvent(new Event('visibilitychange')) })
      await waitFor(() => expect(seen()).toEqual([true, false]))
      state = 'visible'
      await act(async () => { document.dispatchEvent(new Event('visibilitychange')) })
      await waitFor(() => expect(seen()).toEqual([true, false, true]))
    } finally {
      delete (document as any).visibilityState
    }
  })

  it('the link to the PC coming back says "visible" again (the PC may have restarted and forgotten)', async () => {
    const seen = () => invoke.mock.calls.filter(([c, r]) => c === 'computer-use-live' && r?.op === 'panel-visible').map(([, r]) => r.visible)
    const { rerender } = render(<PhoneScreenTab visible connected />)
    await waitFor(() => expect(seen()).toEqual([true]))
    rerender(<PhoneScreenTab visible connected={false} />)
    expect(seen()).toEqual([true])
    rerender(<PhoneScreenTab visible connected />)
    await waitFor(() => expect(seen()).toEqual([true, true]))
  })
})

describe('PhoneScreenTab covered or re-rendered', () => {
  const seen = () => invoke.mock.calls.filter(([c, r]) => c === 'computer-use-live' && r?.op === 'panel-visible').map(([, r]) => r.visible)

  it('visible can flip without breaking the hooks, and a hidden tab is not watched', async () => {
    const { rerender } = render(<PhoneScreenTab visible={false} />)
    rerender(<PhoneScreenTab visible />)
    await waitFor(() => expect(seen().at(-1)).toBe(true))
    rerender(<PhoneScreenTab visible={false} />)
    await waitFor(() => expect(seen().at(-1)).toBe(false))
  })

  it('a full-screen page over the shell (Settings, Profile, Guide) stops the capture, and uncovering starts it again', async () => {
    const route = { screen: 'chat', chatTab: 'screen', back: vi.fn(), setChatTab: vi.fn(), openChat: vi.fn() } as any
    const props = {
      route, conversations: [{ id: 1, title: 'A', created_at: '', updated_at: '', parent_id: null }], activeConvId: 1, convStatus: {},
      unknownGates: 0, projectName: 'P', onSwitchProject: vi.fn(), onOpenSettings: vi.fn(), selectConversation: vi.fn(),
      createNewConversation: vi.fn(), session: null,
      chat: { thread: <div />, composer: <textarea />, menu: { models: { local: [], cloud: [], subscription: [] }, currentModel: { provider_type: 'x', model_name: 'y' }, effortLevels: null, thinkingLevel: 'auto', onEffortChanged: vi.fn(), mode: 'step', loading: false, isCompacting: false, onCompact: vi.fn(), onStop: vi.fn() } },
    } as any
    const { rerender } = render(<PhoneShell {...props} />)
    await waitFor(() => expect(seen()).toEqual([true]))
    rerender(<PhoneShell {...props} covered />)
    await waitFor(() => expect(seen()).toEqual([true, false]))
    rerender(<PhoneShell {...props} />)
    await waitFor(() => expect(seen()).toEqual([true, false, true]))
  })
})
