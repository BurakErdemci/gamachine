import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { translations } from '../renderer/lib/i18n'
import { BrowserPanel } from '../renderer/components/home/BrowserPanel'
import { BrowserUsePage } from '../renderer/components/home/settings/BrowserUsePage'

const tr = translations.tr

let listeners: Map<string, (payload: unknown) => void>
let invoke: ReturnType<typeof vi.fn>
let observers: Array<() => void>
let rect: { left: number; top: number; width: number; height: number }

function push(payload: unknown) {
  act(() => { listeners.get('browser-live')?.(payload) })
}
const live = (over: Record<string, unknown> = {}) =>
  ({ active: true, conversationId: 3, url: 'https://itch.io/game-assets', title: 'Free assets', loading: false, ...over })
const calls = (op: string) => invoke.mock.calls.filter(c => c[0] === 'browser-live' && c[1]?.op === op).map(c => c[1])

beforeEach(() => {
  listeners = new Map()
  observers = []
  rect = { left: 10, top: 20, width: 300, height: 200 }
  invoke = vi.fn(async (_channel: string, req: any) => (req?.op === 'status' ? { active: false } : { ok: true }))
  vi.stubGlobal('ipc', {
    invoke,
    on: vi.fn((channel: string, cb: (payload: unknown) => void) => { listeners.set(channel, cb); return () => listeners.delete(channel) }),
  })
  vi.stubGlobal('ResizeObserver', class {
    constructor(cb: () => void) { observers.push(cb) }
    observe() {}
    disconnect() {}
  })
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    if (this.classList.contains('bp-view')) {
      return { ...rect, right: rect.left + rect.width, bottom: rect.top + rect.height, x: rect.left, y: rect.top, toJSON() {} } as DOMRect
    }
    if (this.dataset.overlay) {
      return { left: 0, top: 0, right: 400, bottom: 400, width: 400, height: 400, x: 0, y: 0, toJSON() {} } as DOMRect
    }
    return { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0, toJSON() {} } as DOMRect
  })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

describe('BrowserPanel', () => {
  it('shows the idle state until the agent uses the browser', async () => {
    const { container } = render(<BrowserPanel visible />)
    expect(await screen.findByText(tr['bp.idle'])).toBeTruthy()
    expect(container.querySelector('.bp-panel')!.getAttribute('data-state')).toBe('idle')
  })

  it('updates the address, title and loading state from browser-live events', async () => {
    render(<BrowserPanel visible />)
    await waitFor(() => expect(listeners.has('browser-live')).toBe(true))
    push(live({ loading: true }))
    const address = screen.getByLabelText(tr['bp.address']) as HTMLInputElement
    expect(address.value).toBe('https://itch.io/game-assets')
    expect(address.readOnly).toBe(true)
    expect(screen.getByText('Free assets')).toBeTruthy()
    expect(screen.getByText(tr['bp.loading'])).toBeTruthy()
    push(live({ loading: false, url: 'https://itch.io/next' }))
    expect(address.value).toBe('https://itch.io/next')
    expect(screen.queryByText(tr['bp.loading'])).toBeNull()
  })

  it('Stop invokes halt', async () => {
    render(<BrowserPanel visible />)
    await waitFor(() => expect(listeners.has('browser-live')).toBe(true))
    push(live())
    fireEvent.click(screen.getByRole('button', { name: new RegExp(tr['bp.stop']) }))
    await waitFor(() => expect(calls('halt')).toHaveLength(1))
  })

  it('sends visible:false while the tab is hidden and visible:true once shown and active', async () => {
    const { rerender } = render(<BrowserPanel visible={false} />)
    await waitFor(() => expect(listeners.has('browser-live')).toBe(true))
    push(live())
    expect(calls('visible').every(c => c.visible === false)).toBe(true)
    rerender(<BrowserPanel visible />)
    await waitFor(() => expect(calls('visible').at(-1)).toEqual({ op: 'visible', visible: true }))
    rerender(<BrowserPanel visible={false} />)
    await waitFor(() => expect(calls('visible').at(-1)).toEqual({ op: 'visible', visible: false }))
  })

  it('hides the native view while a dialog overlaps the placeholder', async () => {
    render(<BrowserPanel visible />)
    await waitFor(() => expect(listeners.has('browser-live')).toBe(true))
    push(live())
    await waitFor(() => expect(calls('visible').at(-1)).toEqual({ op: 'visible', visible: true }))
    const dialog = document.createElement('div')
    dialog.setAttribute('role', 'dialog')
    dialog.dataset.overlay = '1'
    act(() => { document.body.appendChild(dialog) })
    act(() => { observers.forEach(cb => cb()) })
    await waitFor(() => expect(calls('visible').at(-1)).toEqual({ op: 'visible', visible: false }))
    act(() => { dialog.remove() })
    act(() => { observers.forEach(cb => cb()) })
    await waitFor(() => expect(calls('visible').at(-1)).toEqual({ op: 'visible', visible: true }))
  })

  it('sends set-bounds from the ResizeObserver', async () => {
    render(<BrowserPanel visible />)
    await waitFor(() => expect(calls('set-bounds').at(-1)?.bounds).toEqual({ x: 10, y: 20, width: 300, height: 200 }))
    rect = { left: 10, top: 20, width: 420, height: 260 }
    act(() => { observers.forEach(cb => cb()) })
    await waitFor(() => expect(calls('set-bounds').at(-1)?.bounds).toEqual({ x: 10, y: 20, width: 420, height: 260 }))
  })

  it('hides the view on unmount', async () => {
    const { unmount } = render(<BrowserPanel visible />)
    unmount()
    expect(calls('visible').at(-1)).toEqual({ op: 'visible', visible: false })
  })
})

describe('BrowserUsePage', () => {
  it('reads the switch and flips it through the browser-live ops', async () => {
    let enabled = false
    invoke.mockImplementation(async (_c: string, req: any) => {
      if (req.op === 'settings') return { enabled, running: false }
      if (req.op === 'enable') { enabled = true; return { enabled } }
      if (req.op === 'disable') { enabled = false; return { enabled } }
      return { ok: true }
    })
    const saved = vi.fn()
    render(<BrowserUsePage saved={saved} />)
    const sw = await screen.findByTestId('bp-switch')
    await waitFor(() => expect((sw as HTMLButtonElement).disabled).toBe(false))
    fireEvent.click(sw)
    await waitFor(() => expect(saved).toHaveBeenCalled())
    expect(calls('enable')).toHaveLength(1)
    expect(screen.getByTestId('bp-master-state').textContent).toBe(tr['bp.set.masterOn'])
  })
})
