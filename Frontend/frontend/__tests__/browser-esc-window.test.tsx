/**
 * Esc in the main window reaches the renderer first: a control that uses it (the composer's slash palette, its mention
 * list, dictation, the Unity "Why?" popover, a confirm dialog) calls preventDefault and keeps it; an Esc nobody used goes
 * to main's one Esc rule through op 'escape' (drop the pick, leave annotate mode, else leave full screen). The renderer
 * hook talks to a real BrowserHost here, so the whole round trip runs. Also: a collapsed floating chat never hides the
 * page, a waiting card opens it, and a closed window leaves main with full screen off.
 */
import React, { useState } from 'react'
import { EventEmitter } from 'events'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const voice = vi.hoisted(() => ({ state: 'idle' as 'idle' | 'recording', cancel: vi.fn() }))
vi.mock('../renderer/hooks/home/useVoiceInput', () => ({
  useVoiceInput: () => ({
    state: voice.state, elapsedMs: 0, error: null, partialText: '',
    start: vi.fn(), stop: vi.fn(), cancel: voice.cancel, clearError: vi.fn(),
  }),
  formatElapsed: () => '00:00',
}))

import { BrowserHost, type BrowserHostDeps } from '../main/helpers/browser-host'
import { useBrowserFull } from '../renderer/lib/browserFull'
import { AnimatedChatInput } from '../renderer/components/ui/animated-ai-chat'
import { UnityMcpToggle } from '../renderer/components/home/UnityMcpToggle'
import { ConfirmDialogHost, confirmDialog } from '../renderer/components/ui/ConfirmDialog'
import { BrowserPanel } from '../renderer/components/home/BrowserPanel'
import { translations } from '../renderer/lib/i18n'

const tr = translations.tr

function fakeWc(): any {
  const wc: any = new EventEmitter()
  Object.assign(wc, {
    url: '', title: '', loading: false, destroyed: false,
    isDestroyed: () => wc.destroyed, getURL: () => wc.url, getTitle: () => wc.title, isLoading: () => wc.loading,
    setWindowOpenHandler: vi.fn(), loadURL: vi.fn(async (u: string) => { wc.pending = u }), stop: vi.fn(), reload: vi.fn(), focus: vi.fn(),
    close: vi.fn(() => { wc.destroyed = true }), sendInputEvent: vi.fn(), executeJavaScript: vi.fn(async () => undefined),
    navigationHistory: { canGoBack: () => false, canGoForward: () => false, goBack: vi.fn(), goForward: vi.fn() },
  })
  return wc
}
const fakeImage = () => ({
  isEmpty: () => false, getSize: () => ({ width: 800, height: 600 }),
  resize: vi.fn(function (this: any, size: any) { return { ...this, size } }), toJPEG: vi.fn(() => Buffer.from('x')),
})
const BODY = { box: { x: 0, y: 0, width: 1000, height: 700 }, id: 'dT-el1', kind: 'element', tag: 'body', role: 'body', name: '', path: 'body', text: 'Satın al' }
const BUY = { box: { x: 100, y: 100, width: 50, height: 20 }, id: 'dT-el2', kind: 'element', tag: 'button', role: 'button', name: 'Satın al', path: 'button#buy', text: 'Satın al' }
const frameOf = () => ({ docId: 'd1', scroll: [0, 0], viewport: { width: 1000, height: 700 }, elements: [BODY, BUY] })

/** A real BrowserHost whose main window is this jsdom renderer: its sends reach the hook's listeners. */
function hostRig() {
  const views: any[] = []
  const makeView = () => {
    const view: any = { webContents: fakeWc(), setBounds: vi.fn(), setVisible: vi.fn(), setBackgroundColor: vi.fn() }
    const wc = view.webContents
    Object.assign(wc, {
      lastFrame: null as any,
      capturePage: vi.fn(async () => fakeImage()),
      executeJavaScriptInIsolatedWorld: vi.fn(async (_w: number, scripts: { code: string }[]) => {
        const code = scripts[0].code
        if (code.includes('.frame(')) { wc.lastFrame = frameOf(); return wc.lastFrame }
        if (code.includes('.mark(')) return { docId: wc.lastFrame?.docId, scroll: wc.lastFrame?.scroll, viewport: wc.lastFrame?.viewport }
        return {}
      }),
    })
    views.push(view)
    return view
  }
  const listeners = new Map<string, (payload: unknown) => void>()
  const send = vi.fn((channel: string, payload: unknown) => { act(() => { listeners.get(channel)?.(payload) }) })
  const win: any = { isDestroyed: () => false, contentView: { addChildView: vi.fn(), removeChildView: vi.fn() }, webContents: { send } }
  const session: any = new EventEmitter()
  Object.assign(session, { setPermissionRequestHandler: vi.fn(), setPermissionCheckHandler: vi.fn(), webRequest: { onBeforeRequest: vi.fn(), onResponseStarted: vi.fn(), onCompleted: vi.fn() } })
  const deps: BrowserHostDeps = {
    window: () => win, createView: () => makeView(), fromPartition: () => session,
    http: { request: vi.fn(async () => ({ data: {} })) } as any,
    baseUrl: () => 'http://127.0.0.1:9', appToken: 't', uiSecret: 's', lookup: async () => [{ address: '93.184.216.34', family: 4 }],
    platform: 'win32', now: () => 1000,
    setTimeout: (() => 0) as any, clearTimeout: (() => {}) as any,
  }
  const host = new BrowserHost(deps)
  const invoke = vi.spyOn(host, 'invoke')
  vi.stubGlobal('ipc', {
    invoke: (channel: string, req: unknown) => (channel === 'browser-live' ? host.invoke(req) : Promise.resolve(undefined)),
    on: (channel: string, cb: (payload: unknown) => void) => { listeners.set(channel, cb); return () => listeners.delete(channel) },
  })
  const loaded = (marker: string) => views.filter((v) => v.webContents.loadURL.mock.calls.some((c: any[]) => decodeURIComponent(String(c[0])).includes(marker)))
  const page = () => views.filter((v) => v.webContents.loadURL.mock.calls.some((c: any[]) => c[0] === 'https://example.com/')).at(-1)
  const overlay = () => loaded('İşaretleme · bir öğeye').at(-1)
  const tab = () => [...(host as any).tabs.values()].at(-1)
  const leaves = () => send.mock.calls.filter(([c, p]) => c === 'browser-full-toggle' && (p as any)?.on === false).length
  const escapes = () => invoke.mock.calls.filter(([req]) => (req as any)?.op === 'escape').length
  const isFull = () => (host as any).full === true
  return { host, page, overlay, tab, leaves, escapes, isFull }
}
type Rig = ReturnType<typeof hostRig>

const settle = async () => { for (let i = 0; i < 6; i++) await new Promise(r => setImmediate(r)) }

async function openPage(r: Rig) {
  await r.host.invoke({ op: 'set-bounds', conversationId: 4, bounds: { x: 0, y: 0, width: 1400, height: 900 } })
  const reply = r.host.handle({ op: 'navigate', conversation_id: 4, args: { url: 'https://example.com/' } })
  await vi.waitFor(() => expect(r.page()?.webContents.pending).toBe('https://example.com/'))
  const wc = r.page().webContents
  wc.pending = undefined
  wc.url = 'https://example.com/'
  wc.title = 'Example'
  wc.emit('did-finish-load')
  await reply
  await r.host.invoke({ op: 'visible', conversationId: 4, visible: true })
}

/** home.tsx's hook wiring in miniature, around whatever the window shows. */
function Frame({ children }: { children?: React.ReactNode }) {
  const bf = useBrowserFull(true, 4)
  return (
    <div className="app" data-testid="app" data-browser-full={bf.full ? 'true' : undefined}>
      <button type="button" onClick={bf.toggle}>full</button>
      {children}
    </div>
  )
}
const appFull = () => screen.getByTestId('app').getAttribute('data-browser-full') === 'true'
/** The hook decides once every keydown listener has run (a 0 ms timer); then main's reply has to land. */
const flush = async () => { await act(async () => { await new Promise(r => setTimeout(r, 0)); await settle() }) }

async function enterFull(r: Rig) {
  fireEvent.click(screen.getByText('full'))
  expect(appFull()).toBe(true)
  await waitFor(() => expect(r.isFull()).toBe(true))
}

function Composer({ chats }: { chats?: any[] }) {
  const [value, setValue] = useState('')
  return <AnimatedChatInput value={value} setValue={setValue} onSendMessage={() => {}} isLoading={false}
    api="http://127.0.0.1:1" chats={chats as any} currentChatId={7} />
}

beforeEach(() => {
  voice.state = 'idle'
  voice.cancel.mockClear()
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

describe('main-window Esc: the renderer first', () => {
  it('with the slash palette open, Esc closes the palette and full screen stays', async () => {
    const r = hostRig()
    render(<Frame><Composer /></Frame>)
    await enterFull(r)
    const box = screen.getByRole('textbox') as HTMLTextAreaElement
    act(() => { box.focus() })
    fireEvent.change(box, { target: { value: '/' } })
    await waitFor(() => expect(document.querySelector('.composer-pop')).not.toBeNull())
    fireEvent.keyDown(box, { key: 'Escape' })
    await flush()
    // The palette animates out.
    await waitFor(() => expect(document.querySelector('.composer-pop')).toBeNull())
    expect(appFull()).toBe(true)
    expect(r.isFull()).toBe(true)
    expect(r.escapes()).toBe(0)
    expect(r.leaves()).toBe(0)
  })

  it('with the mention list open, Esc closes the list and full screen stays', async () => {
    const r = hostRig()
    const chats = [{ id: 12, title: 'Selam', created_at: '2026-09-27T00:00:12Z', updated_at: '2026-09-27T00:00:00Z', parent_id: null, hidden: false }]
    render(<Frame><Composer chats={chats} /></Frame>)
    await enterFull(r)
    const box = screen.getByRole('textbox') as HTMLTextAreaElement
    act(() => { box.focus() })
    fireEvent.change(box, { target: { value: '@' } })
    box.setSelectionRange(1, 1)
    fireEvent.select(box)
    await screen.findByTestId('mention-menu')
    fireEvent.keyDown(box, { key: 'Escape' })
    await flush()
    await waitFor(() => expect(screen.queryByTestId('mention-menu')).toBeNull())
    expect(appFull()).toBe(true)
    expect(r.escapes()).toBe(0)
    expect(r.leaves()).toBe(0)
  })

  it('while dictating, Esc cancels the recording and full screen stays', async () => {
    const r = hostRig()
    voice.state = 'recording'
    render(<Frame><Composer /></Frame>)
    await enterFull(r)
    const box = screen.getByRole('textbox') as HTMLTextAreaElement
    fireEvent.keyDown(box, { key: 'Escape' })
    await flush()
    expect(voice.cancel).toHaveBeenCalled()
    expect(appFull()).toBe(true)
    expect(r.escapes()).toBe(0)
    expect(r.leaves()).toBe(0)
  })

  it('with the Unity "Why?" popover open, Esc closes it and full screen stays', async () => {
    const r = hostRig()
    render(<Frame><UnityMcpToggle status="blocked" toggling={false} reason="8080 is taken" error={null} onToggle={vi.fn()} /></Frame>)
    await enterFull(r)
    const why = () => document.querySelector('.unity-why-btn') as HTMLElement
    expect(why().getAttribute('aria-expanded')).toBe('true')
    fireEvent.keyDown(document.body, { key: 'Escape' })
    await flush()
    expect(why().getAttribute('aria-expanded')).toBe('false')
    expect(appFull()).toBe(true)
    expect(r.escapes()).toBe(0)
    expect(r.leaves()).toBe(0)
  })

  it('a confirm dialog answers Esc and full screen stays', async () => {
    const r = hostRig()
    render(<Frame />)
    await enterFull(r)
    render(<ConfirmDialogHost />)
    let answer: boolean | undefined
    await act(async () => { void confirmDialog('delete?').then(v => { answer = v }) })
    expect(document.querySelector('[role="alertdialog"]')).not.toBeNull()
    fireEvent.keyDown(window, { key: 'Escape' })
    await flush()
    expect(answer).toBe(false)
    expect(appFull()).toBe(true)
    expect(r.escapes()).toBe(0)
    expect(r.leaves()).toBe(0)
  })

  it('with nothing open, Esc goes to main through op escape and leaves full screen', async () => {
    const r = hostRig()
    render(<Frame><Composer /></Frame>)
    await enterFull(r)
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Escape' })
    await flush()
    expect(r.escapes()).toBe(1)
    expect(r.leaves()).toBe(1)
    await waitFor(() => expect(appFull()).toBe(false))
    expect(r.isFull()).toBe(false)
  })

  it('while annotating with nothing open, Esc drops the open pick and keeps the mode and full screen', async () => {
    const r = hostRig()
    await openPage(r)
    render(<Frame />)
    await enterFull(r)
    await r.host.invoke({ op: 'annotate', conversationId: 4, tabId: r.tab().id, on: true })
    r.overlay().webContents.emit('page-title-updated', {}, JSON.stringify({ q: 1, t: 'pick', x: 110, y: 105 }))
    await vi.waitFor(() => expect(r.tab().pick).not.toBeNull())
    await settle()
    fireEvent.keyDown(document.body, { key: 'Escape' })
    await flush()
    expect(r.escapes()).toBe(1)
    expect(r.tab().pick).toBeNull()
    expect(r.tab().annotating).toBe(true)
    expect(r.leaves()).toBe(0)
    expect(appFull()).toBe(true)
  })
})

describe('the window going away', () => {
  it('destroyView turns full screen off: a later Esc in a page view passes to the page and sends no leave', async () => {
    const r = hostRig()
    await openPage(r)
    await r.host.invoke({ op: 'full', on: true })
    r.host.destroyView()
    await openPage(r)
    const event = { preventDefault: vi.fn() }
    r.page().webContents.emit('before-input-event', event, { type: 'keyDown', key: 'Escape' })
    expect(event.preventDefault).not.toHaveBeenCalled()
    expect(r.leaves()).toBe(0)
  })
})

describe('the collapsed floating chat', () => {
  let invoke: ReturnType<typeof vi.fn>
  const live = () => ({ active: true, conversationId: 3, url: 'https://itch.io/x', title: 't', loading: false,
    tabs: [{ id: 1, url: 'https://itch.io/x', title: 't', loading: false, favicon: '', agent: true }], activeTabId: 1 })
  const visibles = () => invoke.mock.calls.filter(c => c[1]?.op === 'visible').map(c => c[1].visible)
  let viewWidth = 800

  /** home.tsx in miniature: the floating chat (.app-main) holds the approval card, the page view is beside it. */
  function Home({ waiting }: { waiting: boolean }) {
    const bf = useBrowserFull(true, 3, waiting)
    return (
      <div className="app" data-testid="app" data-browser-full={bf.full ? 'true' : undefined}
        data-bf-chat={bf.full && !bf.chatOpen ? 'closed' : undefined}>
        <div className="app-body">
          <main className="app-main">
            {bf.full && <button type="button" onClick={bf.toggleChat}>{tr['bf.chatHide']}</button>}
            <div className="approval-slot" />
          </main>
          <BrowserPanel visible conversationId={3} running={false} onStop={async () => {}} onNeedChat={async () => 3}
            full={bf.full} onToggleFull={bf.toggle} chatOpen={bf.chatOpen} onToggleChat={bf.toggleChat} />
        </div>
      </div>
    )
  }
  const chatState = () => screen.getByTestId('app').getAttribute('data-bf-chat')

  beforeEach(() => {
    viewWidth = 800
    invoke = vi.fn(async (_c: string, req: any) => (req?.op === 'panel-status' ? live() : { ok: true }))
    vi.stubGlobal('ipc', { invoke, on: vi.fn(() => () => {}) })
    vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
    // The page area ends at 800 with the chat open and spans 1100 collapsed; the card keeps its rect in the gutter.
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      const r = this.classList.contains('bp-view') ? { left: 0, top: 50, width: viewWidth, height: 600 }
        : this.classList.contains('approval-slot') ? { left: 810, top: 400, width: 290, height: 100 } : { left: 0, top: 0, width: 0, height: 0 }
      return { ...r, right: r.left + r.width, bottom: r.top + r.height, x: r.left, y: r.top, toJSON() {} } as DOMRect
    })
  })

  it('a hidden card inside the collapsed chat does not hide the page view', async () => {
    render(<Home waiting={false} />)
    await screen.findByRole('combobox')
    fireEvent.click(screen.getByRole('button', { name: /Tam ekran|Full screen/ }))
    await waitFor(() => expect(visibles().at(-1)).toBe(true))
    viewWidth = 1100
    fireEvent.click(screen.getAllByRole('button', { name: tr['bf.chatHide'] })[0])
    expect(chatState()).toBe('closed')
    await act(async () => { await new Promise(r => setTimeout(r, 50)) })
    expect(visibles().at(-1)).toBe(true)
  })

  it('a pending card opens the collapsed chat and keeps it open; once answered the user can collapse it again', async () => {
    const { rerender } = render(<Home waiting={false} />)
    await screen.findByRole('combobox')
    fireEvent.click(screen.getByRole('button', { name: /Tam ekran|Full screen/ }))
    fireEvent.click(screen.getAllByRole('button', { name: tr['bf.chatHide'] })[0])
    expect(chatState()).toBe('closed')
    rerender(<Home waiting />)
    expect(chatState()).toBeNull()
    // Collapsing while the card waits does nothing.
    fireEvent.click(screen.getAllByRole('button', { name: tr['bf.chatHide'] })[0])
    expect(chatState()).toBeNull()
    await act(async () => { await new Promise(r => setTimeout(r, 50)) })
    expect(visibles().at(-1)).toBe(true)
    rerender(<Home waiting={false} />)
    expect(chatState()).toBeNull()
    fireEvent.click(screen.getAllByRole('button', { name: tr['bf.chatHide'] })[0])
    expect(chatState()).toBe('closed')
  })
})
