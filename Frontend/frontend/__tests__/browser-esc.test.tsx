/**
 * The one Esc rule in main (browser-host.ts escapeStep()): in a tab view, the annotate overlay and the comment box an
 * Esc keyDown without modifiers first drops the open pick (or the click waiting on its frame) or else leaves annotate
 * mode, then ends browser full screen, and otherwise passes untouched. The main window's renderer runs the same rule
 * through op 'escape', for an Esc none of its own controls used (browser-esc-window.test.tsx).
 */
import { EventEmitter } from 'events'
import { describe, expect, it, vi } from 'vitest'
import { BrowserHost, isEscape, type BrowserHostDeps } from '../main/helpers/browser-host'

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

function fixture() {
  const views: any[] = []
  const makeView = () => {
    const view: any = { webContents: fakeWc(), setBounds: vi.fn(), setVisible: vi.fn(), setBackgroundColor: vi.fn() }
    view.webContents.capturePage = vi.fn(async () => fakeImage())
    views.push(view)
    return view
  }
  const view = makeView()
  const wc = view.webContents
  Object.assign(wc, {
    frameReply: (): any => frameOf(),
    lastFrame: null as any,
    capturePage: vi.fn(async () => fakeImage()),
    executeJavaScriptInIsolatedWorld: vi.fn(async (_w: number, scripts: { code: string }[]) => {
      const code = scripts[0].code
      if (code.includes('.frame(')) { wc.lastFrame = await wc.frameReply(); return wc.lastFrame }
      if (code.includes('.mark(')) return { docId: wc.lastFrame?.docId, scroll: wc.lastFrame?.scroll, viewport: wc.lastFrame?.viewport }
      return {}
    }),
  })
  let created = 0
  const session: any = new EventEmitter()
  Object.assign(session, { setPermissionRequestHandler: vi.fn(), setPermissionCheckHandler: vi.fn(), webRequest: { onBeforeRequest: vi.fn(), onResponseStarted: vi.fn(), onCompleted: vi.fn() } })
  const win: any = { isDestroyed: () => false, contentView: { addChildView: vi.fn(), removeChildView: vi.fn() }, webContents: { send: vi.fn() } }
  const deps: BrowserHostDeps = {
    window: () => win, createView: () => (++created === 1 ? view : makeView()), fromPartition: () => session,
    http: { request: vi.fn(async () => ({ data: {} })) } as any,
    baseUrl: () => 'http://127.0.0.1:9', appToken: 't', uiSecret: 's', lookup: async () => [{ address: '93.184.216.34', family: 4 }],
    platform: 'win32', now: () => 1000,
    setTimeout: (() => 0) as any, clearTimeout: (() => {}) as any,
  }
  const host = new BrowserHost(deps)
  const loaded = (marker: string) => views.filter((v) => v.webContents.loadURL.mock.calls.some((c: any[]) => decodeURIComponent(String(c[0])).includes(marker)))
  const overlay = () => loaded('İşaretleme · bir öğeye').at(-1)
  const box = () => loaded('Yorum ekle…')[0]
  const tab = () => (host as any).tabs.get(1)
  const fullSends = () => win.webContents.send.mock.calls.filter(([c]: [string]) => c === 'browser-full-toggle').map(([, p]: [string, any]) => p)
  /** Esc (or another key) pressed in a view: whether the host claimed it. */
  const press = (target: any, init: Record<string, unknown> = {}) => {
    const event = { preventDefault: vi.fn() }
    target.emit('before-input-event', event, { type: 'keyDown', key: 'Escape', ...init })
    return event.preventDefault.mock.calls.length > 0
  }
  /** An Esc the main window's renderer did not use (op 'escape'): whether main's rule acted. */
  const pressWindow = async () => ((await host.invoke({ op: 'escape' })) as any).claimed === true
  const annotating = async () => ((await host.invoke({ op: 'panel-status', conversationId: 4 })) as any).annotating === true
  return { host, wc, win, overlay, box, tab, fullSends, press, pressWindow, annotating }
}
type F = ReturnType<typeof fixture>

const settle = async () => { for (let i = 0; i < 6; i++) await new Promise(r => setImmediate(r)) }

async function openPage(f: F) {
  await f.host.invoke({ op: 'set-bounds', conversationId: 4, bounds: { x: 0, y: 0, width: 1400, height: 900 } })
  const reply = f.host.handle({ op: 'navigate', conversation_id: 4, args: { url: 'https://example.com/' } })
  await vi.waitFor(() => expect(f.wc.pending).toBe('https://example.com/'))
  f.wc.pending = undefined
  f.wc.url = 'https://example.com/'
  f.wc.title = 'Example'
  f.wc.emit('did-finish-load')
  await reply
  await f.host.invoke({ op: 'visible', conversationId: 4, visible: true })
}
const annotate = (f: F, on: boolean) => f.host.invoke({ op: 'annotate', conversationId: 4, tabId: 1, on })
const full = (f: F, on: boolean) => f.host.invoke({ op: 'full', on })
async function pick(f: F) {
  f.overlay().webContents.emit('page-title-updated', {}, JSON.stringify({ q: 1, t: 'pick', x: 110, y: 105 }))
  await vi.waitFor(() => expect(f.tab().pick).not.toBeNull())
  await settle()
}

describe('isEscape', () => {
  it('is a plain Esc keyDown only: no modifier, no repeat, no keyUp', () => {
    expect(isEscape({ type: 'keyDown', key: 'Escape' })).toBe(true)
    for (const input of [{ type: 'keyUp', key: 'Escape' }, { type: 'keyDown', key: 'Escape', isAutoRepeat: true },
      { type: 'keyDown', key: 'Escape', shift: true }, { type: 'keyDown', key: 'Escape', control: true },
      { type: 'keyDown', key: 'Escape', meta: true }, { type: 'keyDown', key: 'Escape', alt: true }, { type: 'keyDown', key: 'Enter' }]) {
      expect(isEscape(input)).toBe(false)
    }
  })
})

describe('the Esc rule in main', () => {
  it('with focus inside the page view, Esc leaves annotate mode and is claimed', async () => {
    const f = fixture()
    await openPage(f)
    await annotate(f, true)
    expect(await f.annotating()).toBe(true)
    expect(f.press(f.wc)).toBe(true)
    await vi.waitFor(async () => expect(await f.annotating()).toBe(false))
    expect(f.tab().overlay).toBeNull()
  })

  it('in the overlay, Esc leaves annotate mode', async () => {
    const f = fixture()
    await openPage(f)
    await annotate(f, true)
    expect(f.press(f.overlay().webContents)).toBe(true)
    await vi.waitFor(async () => expect(await f.annotating()).toBe(false))
  })

  it('with focus inside the comment box, Esc drops the open pick and its draft but keeps the mode; the next Esc leaves it', async () => {
    const f = fixture()
    await openPage(f)
    await annotate(f, true)
    await pick(f)
    expect(f.press(f.box().webContents)).toBe(true)
    expect(f.tab().pick).toBeNull()
    expect(f.box().setVisible).toHaveBeenLastCalledWith(false)
    expect(await f.annotating()).toBe(true)
    expect(f.press(f.overlay().webContents)).toBe(true)
    await vi.waitFor(async () => expect(await f.annotating()).toBe(false))
  })

  it('drops a click still waiting on its frame, not the mode', async () => {
    const f = fixture()
    await openPage(f)
    await annotate(f, true)
    let release: (v: unknown) => void = () => {}
    f.wc.frameReply = () => new Promise((r) => { release = r })
    f.overlay().webContents.emit('page-title-updated', {}, JSON.stringify({ q: 1, t: 'pick', x: 110, y: 105 }))
    await settle()
    expect(f.press(f.overlay().webContents)).toBe(true)
    release(frameOf())
    await settle()
    expect(f.tab().pick).toBeNull()
    expect(await f.annotating()).toBe(true)
  })

  it('without annotate mode, Esc in the page ends full screen through the leave message, once', async () => {
    const f = fixture()
    await openPage(f)
    await full(f, true)
    expect(f.press(f.wc)).toBe(true)
    expect(f.fullSends()).toEqual([{ on: false }])
    // Off at once: a second Esc before the renderer's next report is the page's.
    expect(f.press(f.wc)).toBe(false)
    expect(f.fullSends()).toHaveLength(1)
  })

  it('annotate mode comes first: with both on, Esc leaves annotating and full screen stays', async () => {
    const f = fixture()
    await openPage(f)
    await full(f, true)
    await annotate(f, true)
    expect(f.press(f.overlay().webContents)).toBe(true)
    await vi.waitFor(async () => expect(await f.annotating()).toBe(false))
    expect(f.fullSends()).toEqual([])
    expect(f.press(f.wc)).toBe(true)
    expect(f.fullSends()).toEqual([{ on: false }])
  })

  it('with neither on, Esc passes to the page untouched, and a modified or repeated Esc always does', async () => {
    const f = fixture()
    await openPage(f)
    expect(f.press(f.wc)).toBe(false)
    await full(f, true)
    for (const init of [{ shift: true }, { control: true }, { alt: true }, { meta: true }, { isAutoRepeat: true }, { type: 'keyUp' }]) {
      expect(f.press(f.wc, init)).toBe(false)
    }
    expect(f.fullSends()).toEqual([])
  })

  it('in the main window, an unused Esc (op escape) ends full screen once', async () => {
    const f = fixture()
    await openPage(f)
    expect(await f.pressWindow()).toBe(false)
    await full(f, true)
    expect(await f.pressWindow()).toBe(true)
    expect(f.fullSends()).toEqual([{ on: false }])
    expect(await f.pressWindow()).toBe(false)
    expect(f.fullSends()).toHaveLength(1)
  })

  it('in the main window, an unused Esc leaves annotate mode, and never acts for a hidden panel', async () => {
    const f = fixture()
    await openPage(f)
    await annotate(f, true)
    // A dialog over the panel hides the view: the chat is no longer shown, so its tab is not the one Esc is about.
    await f.host.invoke({ op: 'visible', conversationId: 4, visible: false })
    expect(await f.pressWindow()).toBe(false)
    expect(await f.annotating()).toBe(true)
    await f.host.invoke({ op: 'visible', conversationId: 4, visible: true })
    expect(await f.pressWindow()).toBe(true)
    await vi.waitFor(async () => expect(await f.annotating()).toBe(false))
  })

  it('the full op takes only a boolean', async () => {
    const f = fixture()
    expect(await f.host.invoke({ op: 'full', on: 'yes' })).toMatchObject({ error: expect.any(String) })
    expect(await f.host.invoke({ op: 'full', on: true })).toEqual({ full: true })
  })
})
