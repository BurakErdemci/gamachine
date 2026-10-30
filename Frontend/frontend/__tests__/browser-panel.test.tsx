import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { translations } from '../renderer/lib/i18n'
import { BrowserPanel } from '../renderer/components/home/BrowserPanel'
import { confirmDialog } from '../renderer/components/ui/ConfirmDialog'
import { BrowserUsePage } from '../renderer/components/home/settings/BrowserUsePage'

vi.mock('../renderer/components/ui/ConfirmDialog', () => ({ confirmDialog: vi.fn() }))
const confirm = confirmDialog as unknown as ReturnType<typeof vi.fn>
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
const props = (over: Record<string, unknown> = {}) =>
  ({ running: true, onStop: vi.fn(async () => {}), onNeedChat: vi.fn(async () => 9), ...over }) as any
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
  it('shows the empty state until the chat has a tab', async () => {
    const { container } = render(<BrowserPanel visible conversationId={3} {...props()} />)
    expect(await screen.findByText(tr['bp.idle'])).toBeTruthy()
    expect(container.querySelector('.bp-panel')!.getAttribute('data-state')).toBe('idle')
    expect(screen.getByRole('button', { name: tr['bp.open'] })).toBeTruthy()
  })

  it('asks status for the open chat on mount and again when the chat changes', async () => {
    const { rerender } = render(<BrowserPanel visible conversationId={3} {...props()} />)
    await waitFor(() => expect(calls('status')).toEqual([{ op: 'status', conversationId: 3 }]))
    rerender(<BrowserPanel visible conversationId={4} {...props()} />)
    await waitFor(() => expect(calls('status').at(-1)).toEqual({ op: 'status', conversationId: 4 }))
  })

  it('without a chat it sends no status, and Open is enabled', () => {
    render(<BrowserPanel visible conversationId={null} {...props()} />)
    expect(calls('status')).toHaveLength(0)
    expect(screen.getByText(tr['bp.idle'])).toBeTruthy()
    expect((screen.getByRole('button', { name: tr['bp.open'] }) as HTMLButtonElement).disabled).toBe(false)
  })

  it('Open from a fresh chat creates the chat, then opens with the returned id', async () => {
    const p = props()
    render(<BrowserPanel visible conversationId={null} {...p} />)
    const input = screen.getByLabelText(tr['bp.address'])
    fireEvent.change(input, { target: { value: 'https://example.com' } })
    fireEvent.click(screen.getByRole('button', { name: tr['bp.open'] }))
    await waitFor(() => expect(calls('open')).toEqual([{ op: 'open', conversationId: 9, url: 'https://example.com' }]))
    expect(p.onNeedChat).toHaveBeenCalledTimes(1)
  })

  it('Open from a fresh chat sends nothing when the chat could not be created', async () => {
    const p = props({ onNeedChat: vi.fn(async () => null) })
    render(<BrowserPanel visible conversationId={null} {...p} />)
    fireEvent.change(screen.getByLabelText(tr['bp.address']), { target: { value: 'https://example.com' } })
    fireEvent.click(screen.getByRole('button', { name: tr['bp.open'] }))
    await waitFor(() => expect(p.onNeedChat).toHaveBeenCalled())
    expect(calls('open')).toHaveLength(0)
  })

  it('Open sends open with the typed address and the chat id', async () => {
    render(<BrowserPanel visible conversationId={3} {...props()} />)
    const input = await screen.findByLabelText(tr['bp.address'])
    fireEvent.change(input, { target: { value: 'https://itch.io' } })
    fireEvent.click(screen.getByRole('button', { name: tr['bp.open'] }))
    expect(calls('open')).toEqual([{ op: 'open', conversationId: 3, url: 'https://itch.io' }])
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(calls('open')).toHaveLength(2)
  })

  it('updates the address, title and loading state from browser-live events', async () => {
    render(<BrowserPanel visible conversationId={3} {...props()} />)
    await waitFor(() => expect(listeners.has('browser-live')).toBe(true))
    push(live({ loading: true }))
    const address = screen.getByLabelText(tr['bp.address']) as HTMLInputElement
    expect(address.value).toBe('itch.io')
    expect(address.title).toBe('https://itch.io/game-assets')
    expect(address.readOnly).toBe(false)
    expect(screen.getByText('Free assets')).toBeTruthy()
    expect(screen.getByText(tr['bp.loading'])).toBeTruthy()
    push(live({ loading: false, url: 'https://itch.io/next' }))
    expect(address.title).toBe('https://itch.io/next')
    fireEvent.focus(address)
    expect(address.value).toBe('https://itch.io/next')
    expect(screen.queryByText(tr['bp.loading'])).toBeNull()
  })

  it('ignores browser-live events of another chat', async () => {
    render(<BrowserPanel visible conversationId={3} {...props()} />)
    await waitFor(() => expect(listeners.has('browser-live')).toBe(true))
    push(live({ conversationId: 9, url: 'https://other.example/' }))
    expect(screen.queryByDisplayValue('other.example')).toBeNull()
    expect(screen.getByText(tr['bp.idle'])).toBeTruthy()
    push(live())
    push(live({ conversationId: 9, active: false }))
    expect((screen.getByLabelText(tr['bp.address']) as HTMLInputElement).value).toBe('itch.io')
  })

  it('Enter in the address bar sends navigate; Escape-free typing does not', async () => {
    render(<BrowserPanel visible conversationId={3} {...props()} />)
    await waitFor(() => expect(listeners.has('browser-live')).toBe(true))
    push(live())
    const address = screen.getByLabelText(tr['bp.address']) as HTMLInputElement
    fireEvent.focus(address)
    fireEvent.change(address, { target: { value: 'https://example.com/a' } })
    expect(calls('navigate')).toHaveLength(0)
    fireEvent.keyDown(address, { key: 'Enter' })
    expect(calls('navigate')).toEqual([{ op: 'navigate', conversationId: 3, url: 'https://example.com/a' }])
  })

  it('searches typed words and completes a bare site name', async () => {
    render(<BrowserPanel visible conversationId={3} {...props()} />)
    await waitFor(() => expect(listeners.has('browser-live')).toBe(true))
    push(live())
    const address = screen.getByLabelText(tr['bp.address']) as HTMLInputElement
    fireEvent.focus(address)
    fireEvent.change(address, { target: { value: 'unity hub' } })
    fireEvent.keyDown(address, { key: 'Enter' })
    fireEvent.focus(address)
    fireEvent.change(address, { target: { value: 'github.com' } })
    fireEvent.keyDown(address, { key: 'Enter' })
    expect(calls('navigate').map(c => c.url)).toEqual([
      'https://www.google.com/search?q=unity%20hub', 'https://github.com'])
  })

  it('back, forward and reload carry the chat id', async () => {
    render(<BrowserPanel visible conversationId={3} {...props()} />)
    await waitFor(() => expect(listeners.has('browser-live')).toBe(true))
    push(live())
    fireEvent.click(screen.getByRole('button', { name: tr['bp.back'] }))
    fireEvent.click(screen.getByRole('button', { name: tr['bp.forward'] }))
    fireEvent.click(screen.getByRole('button', { name: tr['bp.reload'] }))
    for (const op of ['back', 'forward', 'reload']) {
      await waitFor(() => expect(calls(op)).toEqual([{ op, conversationId: 3 }]))
    }
  })

  it('Stop halts the page and stops the chat turn, then shows Durduruldu until the next turn', async () => {
    invoke.mockImplementation(async (_c: string, req: any) =>
      (req?.op === 'status' ? { active: false } : req?.op === 'halt' ? { ok: true, halted: true } : { ok: true }))
    const onStop = vi.fn(async () => {})
    const { rerender } = render(<BrowserPanel visible conversationId={3} {...props({ onStop })} />)
    await waitFor(() => expect(listeners.has('browser-live')).toBe(true))
    push(live())
    fireEvent.click(screen.getByRole('button', { name: new RegExp(tr['bp.stop']) }))
    await waitFor(() => expect(calls('halt')).toEqual([{ op: 'halt', conversationId: 3 }]))
    expect(onStop).toHaveBeenCalledTimes(1)
    rerender(<BrowserPanel visible conversationId={3} {...props({ onStop, running: false })} />)
    const done = await screen.findByRole('button', { name: new RegExp(tr['bp.stopped']) }) as HTMLButtonElement
    expect(done.disabled).toBe(true)
    rerender(<BrowserPanel visible conversationId={3} {...props({ onStop, running: true })} />)
    const again = await screen.findByRole('button', { name: new RegExp(`^${tr['bp.stop']}`) }) as HTMLButtonElement
    expect(again.disabled).toBe(false)
  })

  it('Stop is disabled while no turn runs', async () => {
    render(<BrowserPanel visible conversationId={3} {...props({ running: false })} />)
    await waitFor(() => expect(listeners.has('browser-live')).toBe(true))
    push(live())
    expect((screen.getByRole('button', { name: new RegExp(tr['bp.stop']) }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('a failed Stop (halted false, or a throwing stop) shows an alert and re-enables the button', async () => {
    invoke.mockImplementation(async (_c: string, req: any) =>
      (req?.op === 'status' ? { active: false } : req?.op === 'halt' ? { ok: true, halted: false } : { ok: true }))
    const onStop = vi.fn(async () => {})
    const { unmount } = render(<BrowserPanel visible conversationId={3} {...props({ onStop })} />)
    await waitFor(() => expect(listeners.has('browser-live')).toBe(true))
    push(live())
    fireEvent.click(screen.getByRole('button', { name: new RegExp(tr['bp.stop']) }))
    expect((await screen.findByRole('alert')).textContent).toBe(tr['bp.stopFailed'])
    expect((screen.getByRole('button', { name: new RegExp(tr['bp.stop']) }) as HTMLButtonElement).disabled).toBe(false)
    unmount()

    invoke.mockImplementation(async (_c: string, req: any) => (req?.op === 'status' ? { active: false } : { ok: true, halted: true }))
    render(<BrowserPanel visible conversationId={3} {...props({ onStop: vi.fn(async () => { throw new Error('x') }) })} />)
    await waitFor(() => expect(listeners.has('browser-live')).toBe(true))
    push(live())
    fireEvent.click(screen.getByRole('button', { name: new RegExp(tr['bp.stop']) }))
    expect((await screen.findByRole('alert')).textContent).toBe(tr['bp.stopFailed'])
  })

  it('shows the user-control badge and has no take-over or release button', async () => {
    render(<BrowserPanel visible conversationId={3} {...props()} />)
    await waitFor(() => expect(listeners.has('browser-live')).toBe(true))
    push(live())
    expect(screen.queryByText(tr['bp.userControl'])).toBeNull()
    push(live({ userControl: true }))
    expect(screen.getByText(tr['bp.userControl'])).toBeTruthy()
    expect(screen.queryByText(/Devral|Ajana bırak/)).toBeNull()
    expect(screen.queryByRole('button', { name: /take over|release/i })).toBeNull()
    expect(calls('take-over')).toHaveLength(0)
    expect(calls('release')).toHaveLength(0)
  })

  it('sends visible:false while the tab is hidden and visible:true once shown and active', async () => {
    const { rerender } = render(<BrowserPanel visible={false} conversationId={3} {...props()} />)
    await waitFor(() => expect(listeners.has('browser-live')).toBe(true))
    push(live())
    expect(calls('visible').every(c => c.visible === false)).toBe(true)
    rerender(<BrowserPanel visible conversationId={3} {...props()} />)
    await waitFor(() => expect(calls('visible').at(-1)).toEqual({ op: 'visible', conversationId: 3, visible: true }))
    rerender(<BrowserPanel visible={false} conversationId={3} {...props()} />)
    await waitFor(() => expect(calls('visible').at(-1)).toEqual({ op: 'visible', conversationId: 3, visible: false }))
  })

  it('hides the native view while a dialog overlaps the placeholder', async () => {
    render(<BrowserPanel visible conversationId={3} {...props()} />)
    await waitFor(() => expect(listeners.has('browser-live')).toBe(true))
    push(live())
    await waitFor(() => expect(calls('visible').at(-1)).toEqual({ op: 'visible', conversationId: 3, visible: true }))
    const dialog = document.createElement('div')
    dialog.setAttribute('role', 'dialog')
    dialog.dataset.overlay = '1'
    act(() => { document.body.appendChild(dialog) })
    act(() => { observers.forEach(cb => cb()) })
    await waitFor(() => expect(calls('visible').at(-1)).toEqual({ op: 'visible', conversationId: 3, visible: false }))
    act(() => { dialog.remove() })
    act(() => { observers.forEach(cb => cb()) })
    await waitFor(() => expect(calls('visible').at(-1)).toEqual({ op: 'visible', conversationId: 3, visible: true }))
  })

  it('sends set-bounds with the chat id from the ResizeObserver', async () => {
    render(<BrowserPanel visible conversationId={3} {...props()} />)
    await waitFor(() => expect(calls('set-bounds').at(-1)).toEqual({ op: 'set-bounds', conversationId: 3, bounds: { x: 10, y: 20, width: 300, height: 200 } }))
    rect = { left: 10, top: 20, width: 420, height: 260 }
    act(() => { observers.forEach(cb => cb()) })
    await waitFor(() => expect(calls('set-bounds').at(-1)?.bounds).toEqual({ x: 10, y: 20, width: 420, height: 260 }))
  })

  it('hides the view on unmount', async () => {
    const { unmount } = render(<BrowserPanel visible conversationId={3} {...props()} />)
    unmount()
    expect(calls('visible').at(-1)).toEqual({ op: 'visible', conversationId: 3, visible: false })
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

  it('clears browsing data only after the confirm', async () => {
    invoke.mockImplementation(async (_c: string, req: any) => (req.op === 'settings' ? { enabled: true } : { ok: true }))
    render(<BrowserUsePage saved={vi.fn()} />)
    const btn = await screen.findByTestId('bp-clear')
    confirm.mockResolvedValueOnce(false)
    fireEvent.click(btn)
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1))
    expect(calls('clear-browsing-data')).toHaveLength(0)
    confirm.mockResolvedValueOnce(true)
    fireEvent.click(btn)
    await waitFor(() => expect(calls('clear-browsing-data')).toEqual([{ op: 'clear-browsing-data' }]))
    expect(screen.getByTestId('bp-privacy').textContent).toBe(tr['bp.set.privacy'])
  })
})

describe('deleting a chat closes its browser tab', () => {
  it('sends close for the chat once the delete succeeded', async () => {
    vi.resetModules()
    vi.doMock('axios', () => ({ default: { get: vi.fn(async () => ({ data: [] })), put: vi.fn(), post: vi.fn(), delete: vi.fn(async () => ({ data: { deleted_ids: [5] } })) } }))
    const fresh = (await import('../renderer/components/ui/ConfirmDialog')).confirmDialog as unknown as ReturnType<typeof vi.fn>
    fresh.mockResolvedValue(true)
    const { renderHook } = await import('@testing-library/react')
    const { useChat } = await import('../renderer/hooks/home/useChat')
    const { result } = renderHook(() => useChat('http://127.0.0.1:8000', { id: 1, name: 't', sessionToken: 't' } as any,
      { provider_type: 'subscription', model_name: 'claude-opus-5' } as any, '/ws', vi.fn(), vi.fn(), (s: string) => s))
    invoke.mockClear()
    await act(async () => { await result.current.deleteConversation({ stopPropagation() {} } as any, 5) })
    expect(calls('close')).toEqual([{ op: 'close', conversationId: 5 }])
    vi.doUnmock('axios')
  })
})
