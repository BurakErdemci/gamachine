/**
 * Annotate mode on read-only page frames: picks, drafts and notes live only in the host, made from immutable frames,
 * so no page reply or comment-box message can change a pick other than the one it was made for. Covers the frame
 * rule (seq, navigation, the page's place in mark), the pick id on box messages, save and cancel without page calls, the overlay capture,
 * pins and numbering, the hit-test rule, and frame()/mark() run for real in jsdom.
 */
import { EventEmitter } from 'events'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { BrowserHost, NOTE_BOX_HTML, OVERLAY_HTML, PAGE_LIB, areaText, hitElement, type BrowserHostDeps } from '../main/helpers/browser-host'

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

const fakeImage = (bytes: string) => ({
  isEmpty: () => false, getSize: () => ({ width: 800, height: 600 }),
  resize: vi.fn(function (this: any, size: any) { return { ...this, size } }), toJPEG: vi.fn(() => Buffer.from(bytes)),
})
const jpeg = (bytes: string) => `data:image/jpeg;base64,${Buffer.from(bytes).toString('base64')}`

const BODY = { box: { x: 0, y: 0, width: 1000, height: 700 }, id: 'dT-el1', kind: 'element', tag: 'body', role: 'body', name: '', path: 'body', text: 'Satın al Sepet' }
const BUY = { box: { x: 100, y: 100, width: 50, height: 20 }, id: 'dT-el2', kind: 'element', tag: 'button', role: 'button', name: 'Satın al', href: 'https://shop.example/buy', path: 'button#buy', text: 'Satın al' }
const CART = { box: { x: 300, y: 100, width: 60, height: 20 }, id: 'dT-el3', kind: 'element', tag: 'a', role: 'link', name: 'Sepet', href: 'https://shop.example/cart', path: 'a#cart', text: 'Sepet' }
const frameOf = (over: Record<string, unknown> = {}) => ({ docId: 'd1', scroll: [0, 0], viewport: { width: 1000, height: 700 }, elements: [BODY, BUY, CART], ...over })
const markOf = (frame: any) => ({ docId: frame.docId, scroll: frame.scroll, viewport: frame.viewport })
const deferred = <T,>() => {
  let resolve: (v: T) => void = () => {}
  const promise = new Promise<T>((r) => { resolve = r })
  return { promise, resolve }
}

function fixture() {
  const views: any[] = []
  const makeView = () => {
    const view: any = { webContents: fakeWc(), setBounds: vi.fn(), setVisible: vi.fn(), setBackgroundColor: vi.fn() }
    view.webContents.capturePage = vi.fn(async () => fakeImage('overlay-bytes'))
    views.push(view)
    return view
  }
  // A tab's page script answers; a test replaces them. mark() follows the last frame() unless a test says otherwise.
  const answers = (wc: any) => Object.assign(wc, {
    frameReply: (): any => frameOf(),
    markReply: (): any => markOf(wc.lastFrame),
    lastFrame: null as any,
    executeJavaScriptInIsolatedWorld: vi.fn(async (_w: number, scripts: { code: string }[]) => {
      const code = scripts[0].code
      if (code.includes('.frame(')) { wc.lastFrame = await wc.frameReply(); return wc.lastFrame }
      if (code.includes('.mark(')) return wc.markReply()
      return {}
    }),
    capturePage: vi.fn(async () => fakeImage('tab-bytes')),
  })
  const view = makeView()
  const wc = answers(view.webContents)
  const created: any[] = []
  const session: any = new EventEmitter()
  Object.assign(session, { setPermissionRequestHandler: vi.fn(), setPermissionCheckHandler: vi.fn(), webRequest: { onBeforeRequest: vi.fn(), onResponseStarted: vi.fn(), onCompleted: vi.fn() } })
  const win: any = { isDestroyed: () => false, contentView: { addChildView: vi.fn(), removeChildView: vi.fn() }, webContents: { send: vi.fn() } }
  const timers: { fn: () => void; ms: number; id: number }[] = []
  let nextTimer = 0
  const deps: BrowserHostDeps = {
    window: () => win, createView: (options: any) => { created.push(options); return created.length === 1 ? view : makeView() }, fromPartition: () => session,
    http: { request: vi.fn(async () => ({ data: {} })) } as any,
    baseUrl: () => 'http://127.0.0.1:9', appToken: 't', uiSecret: 's', lookup: async () => [{ address: '93.184.216.34', family: 4 }],
    platform: 'win32', now: () => 1000,
    setTimeout: ((fn: () => void, ms: number) => { const id = ++nextTimer; timers.push({ fn, ms, id }); return id }) as any,
    clearTimeout: ((id: number) => { const at = timers.findIndex(t => t.id === id); if (at >= 0) timers.splice(at, 1) }) as any,
  }
  const host = new BrowserHost(deps)
  const loaded = (marker: string) => views.filter((v) => v.webContents.loadURL.mock.calls.some((c: any[]) => decodeURIComponent(String(c[0])).includes(marker)))
  const overlays = () => loaded('İşaretleme · bir öğeye')
  const overlay = () => overlays().at(-1)
  const box = () => loaded('Yorum ekle…')[0]
  const tab = () => (host as any).tabs.get(1)
  const pageCalls = (name?: string) => wc.executeJavaScriptInIsolatedWorld.mock.calls.map((c: any) => c[1][0].code as string).filter((code: string) => !name || code.includes(`.${name}(`))
  const overlayCalls = () => (overlay()?.webContents.executeJavaScript.mock.calls ?? []).map((c: any[]) => String(c[0]))
  const boxCalls = () => (box()?.webContents.executeJavaScript.mock.calls ?? []).map((c: any[]) => String(c[0]))
  const sent = (channel: string) => win.webContents.send.mock.calls.filter(([c]: [string]) => c === channel).map(([, p]: [string, any]) => p)
  const fire = async (ms: number) => {
    const at = timers.findIndex(t => t.ms === ms)
    expect(at, `a ${ms} ms timer is due`).toBeGreaterThanOrEqual(0)
    timers.splice(at, 1)[0].fn()
    await settle()
  }
  return { host, wc, view, win, timers, created, overlay, overlays, box, tab, pageCalls, overlayCalls, boxCalls, sent, fire, answers }
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
const annotate = (f: F, on: boolean) => f.host.invoke({ op: 'annotate', conversationId: 4, tabId: 1, on }) as Promise<any>
const overlaySays = (f: F, message: Record<string, unknown>) => f.overlay().webContents.emit('page-title-updated', {}, JSON.stringify({ q: 1, ...message }))
const boxSays = (f: F, message: Record<string, unknown>) => f.box().webContents.emit('page-title-updated', {}, JSON.stringify({ q: 1, p: f.tab().pick?.id ?? null, ...message }))
const setCalls = (f: F) => f.boxCalls().filter((c) => c.startsWith('window.__set('))

/** Annotate mode on and a click on BUY: the pick opens from a fresh frame and the box shows it. */
async function pickBuy(f: F) {
  await openPage(f)
  await annotate(f, true)
  overlaySays(f, { t: 'pick', x: 110, y: 105 })
  await vi.waitFor(() => expect(setCalls(f).length).toBeGreaterThan(0))
  return f.tab().pick
}

describe('frame picks live only in the host', () => {
  it('the overlay claims every browser key the tab does: full screen, tab shortcuts and the chord; other keys stay unclaimed', async () => {
    const f = fixture()
    await openPage(f)
    await annotate(f, true)
    const key = (init: Record<string, unknown>) => {
      const event = { preventDefault: vi.fn() }
      f.overlay().webContents.emit('before-input-event', event, { type: 'keyDown', control: true, ...init })
      return event.preventDefault
    }
    const panel = async () => (await f.host.invoke({ op: 'panel-status', conversationId: 4 })) as any
    expect(key({ key: 'F', shift: true })).toHaveBeenCalled()
    expect(f.sent('browser-full-toggle')).toHaveLength(1)
    expect(key({ key: 'a' })).not.toHaveBeenCalled()
    expect(key({ key: 't' })).toHaveBeenCalled()
    await vi.waitFor(async () => expect((await panel()).tabs).toHaveLength(2))
    expect((await panel()).activeTabId).toBe(2)
    expect(key({ key: '1' })).toHaveBeenCalled()
    await vi.waitFor(async () => expect((await panel()).activeTabId).toBe(1))
    expect(key({ key: 'E', shift: true })).toHaveBeenCalled()
    await vi.waitFor(() => expect(f.tab().annotating).toBe(false))
  })

  it('T1 a box draft message posted for pick A is ignored after pick B opens', async () => {
    const f = fixture()
    const a = await pickBuy(f)
    // Empty draft: a click elsewhere picks again from the same frozen frame.
    const calls = f.pageCalls().length
    overlaySays(f, { t: 'pick', x: 310, y: 105 })
    await vi.waitFor(() => expect(setCalls(f)).toHaveLength(2))
    const b = f.tab().pick
    expect(b.id).not.toBe(a.id)
    expect(b.data.name).toBe('Sepet')
    expect(f.pageCalls()).toHaveLength(calls)
    expect(setCalls(f).at(-1)).toBe(`window.__set("",${JSON.stringify(b.id)})`)
    // A's late messages: a draft, a submit and a cancel all name A and change nothing.
    boxSays(f, { t: 'draft', text: 'A için', h: 56, p: a.id })
    boxSays(f, { t: 'submit', text: 'A için', p: a.id })
    boxSays(f, { t: 'cancel', p: a.id })
    expect(f.tab().pick).toBe(b)
    expect(b.draft).toBe('')
    expect(f.sent('browser-notes')).toHaveLength(0)
    boxSays(f, { t: 'draft', text: 'B için', h: 56, p: b.id })
    expect(b.draft).toBe('B için')
  })

  it('T2 a frame reply whose seq is older than the latest request is dropped; no pick opens from it', async () => {
    const f = fixture()
    await openPage(f)
    await annotate(f, true)
    const first = deferred<any>()
    const second = deferred<any>()
    const replies = [first.promise, second.promise]
    f.wc.frameReply = () => replies.shift() ?? frameOf()
    overlaySays(f, { t: 'pick', x: 110, y: 105 })
    overlaySays(f, { t: 'pick', x: 110, y: 105 })
    await settle()
    second.resolve(frameOf())
    await vi.waitFor(() => expect(setCalls(f)).toHaveLength(1))
    const pick = f.tab().pick
    expect(pick.data.name).toBe('Satın al')
    // The older reply sees another element at the point; it arrives last and must open nothing.
    first.resolve(frameOf({ elements: [BODY, { ...CART, box: BUY.box }] }))
    await settle()
    expect(f.tab().pick).toBe(pick)
    expect(pick.data.name).toBe('Satın al')
    expect(setCalls(f)).toHaveLength(1)
    expect(f.pageCalls('mark')).toHaveLength(1)
  })

  it('T3 a frame whose mark differs from its frame() reply is retried and, after 3 mismatches, opens nothing', async () => {
    const f = fixture()
    await openPage(f)
    await annotate(f, true)
    // The page scrolled between frame() and mark() every time.
    f.wc.markReply = () => ({ ...markOf(f.wc.lastFrame), scroll: [0, 40] })
    overlaySays(f, { t: 'pick', x: 110, y: 105 })
    await settle()
    expect(f.pageCalls('frame')).toHaveLength(3)
    expect(f.pageCalls('mark')).toHaveLength(3)
    expect(f.wc.capturePage).toHaveBeenCalledTimes(3)
    expect(f.tab().pick).toBeNull()
    expect(f.box()).toBeUndefined()
    // Another document or another viewport is a mismatch too; one good read after the bad ones opens the pick.
    let bad = 2
    f.wc.markReply = () => (bad-- > 0 ? { ...markOf(f.wc.lastFrame), ...(bad ? { docId: 'd9' } : { viewport: { width: 1000, height: 650 } }) } : markOf(f.wc.lastFrame))
    overlaySays(f, { t: 'pick', x: 110, y: 105 })
    await vi.waitFor(() => expect(f.tab().pick?.data.name).toBe('Satın al'))
    expect(f.pageCalls('mark')).toHaveLength(6)
  })

  it('T4 navigation completing while a pick with a draft is open keeps the pick and draft; saving emits a note with the frame\'s data', async () => {
    const f = fixture()
    const pick = await pickBuy(f)
    boxSays(f, { t: 'draft', text: 'taslak', h: 56 })
    f.wc.emit('did-start-navigation', { isMainFrame: true })
    f.wc.url = 'https://example.com/next'
    f.wc.emit('did-navigate', {}, 'https://example.com/next')
    f.wc.frameReply = () => frameOf({ docId: 'd2', elements: [BODY] })
    f.wc.emit('did-finish-load')
    await settle()
    expect(f.tab().pick).toBe(pick)
    expect(pick.draft).toBe('taslak')
    expect(f.tab().annotating).toBe(true)
    boxSays(f, { t: 'submit', text: 'taslak' })
    expect(f.sent('browser-notes')).toHaveLength(1)
    const [{ box, ...data }] = [BUY]
    expect(f.sent('browser-notes')[0].notes).toEqual([{ ...data, id: pick.id, comment: 'taslak', n: 1, rect: box }])
    expect(f.tab().pick).toBeNull()
  })

  it('T5 emitNotes returning false keeps the pick with draft and NOTE_SAVE_FAILED; no page call is made during save', async () => {
    const f = fixture()
    const pick = await pickBuy(f)
    boxSays(f, { t: 'draft', text: 'kayıp olmasın', h: 56 })
    f.win.webContents.send.mockImplementation((channel: string) => { if (channel === 'browser-notes') throw new Error('window closing') })
    const calls = f.pageCalls().length
    boxSays(f, { t: 'submit', text: 'kayıp olmasın' })
    expect(f.tab().pick).toBe(pick)
    expect(pick).toMatchObject({ draft: 'kayıp olmasın', error: 'Not kaydedilemedi, tekrar dene' })
    expect(f.boxCalls().at(-1)).toBe(`window.__error("Not kaydedilemedi, tekrar dene",${JSON.stringify(pick.id)})`)
    expect(f.tab().notes).toHaveLength(0)
    expect(f.pageCalls()).toHaveLength(calls)
    // The window answers again: the retry delivers this one note, numbered 1, and still asks nothing of the page.
    f.win.webContents.send.mockImplementation(() => undefined)
    boxSays(f, { t: 'submit', text: 'kayıp olmasın' })
    expect(f.sent('browser-notes').at(-1).notes).toEqual([expect.objectContaining({ id: pick.id, n: 1, comment: 'kayıp olmasın' })])
    expect(f.pageCalls()).toHaveLength(calls)
    expect(f.tab().pick).toBeNull()
  })

  it('T6 after cancel, a frame reply requested before the cancel opens nothing', async () => {
    const f = fixture()
    await openPage(f)
    await annotate(f, true)
    const held = deferred<any>()
    f.wc.frameReply = () => held.promise
    overlaySays(f, { t: 'pick', x: 110, y: 105 })
    await settle()
    f.overlay().webContents.emit('before-input-event', { preventDefault: vi.fn() }, { type: 'keyDown', key: 'Escape' })
    held.resolve(frameOf())
    await settle()
    expect(f.tab().pick).toBeNull()
    expect(f.box()).toBeUndefined()
    // Esc with a click still waiting cancels the click, not the mode.
    expect(f.tab().annotating).toBe(true)
  })

  it('T7 a pick opened from a frame saves successfully no matter how many later frames or clicks occurred while its draft was non-empty', async () => {
    const f = fixture()
    const pick = await pickBuy(f)
    boxSays(f, { t: 'draft', text: 'kalsın', h: 56 })
    for (let i = 0; i < 30; i++) {
      f.wc.frameReply = () => frameOf({ elements: [BODY, CART] })
      overlaySays(f, i % 2 ? { t: 'pick', x: 310, y: 105 } : { t: 'area', rect: { x: 0, y: 0, width: 500, height: 300 } })
      if (i % 10 === 0) { f.wc.emit('did-start-navigation', { isMainFrame: true }); f.wc.emit('did-finish-load') }
      await f.host.invoke({ op: 'visible', conversationId: 4, visible: i % 3 !== 0 })
    }
    await f.host.invoke({ op: 'visible', conversationId: 4, visible: true })
    await settle()
    expect(f.tab().pick).toBe(pick)
    await vi.waitFor(() => expect(setCalls(f).at(-1)).toBe(`window.__set("kalsın",${JSON.stringify(pick.id)})`))
    boxSays(f, { t: 'submit', text: 'kalsın' })
    expect(f.sent('browser-notes')).toHaveLength(1)
    expect(f.sent('browser-notes')[0].notes[0]).toMatchObject({ id: pick.id, name: 'Satın al', comment: 'kalsın', n: 1, rect: BUY.box })
  })

  it('T9 capture-notes returns the overlay capture and restores the overlay\'s prior state (open pick stays frozen; mode off leaves no overlay)', async () => {
    const f = fixture()
    const pick = await pickBuy(f)
    const capture = () => f.host.invoke({ op: 'capture-notes', conversationId: 4, tabId: 1 }) as Promise<any>
    const reply = await capture()
    expect(reply).toEqual({ image: jpeg('overlay-bytes') })
    expect(f.overlay().webContents.capturePage).toHaveBeenCalledTimes(1)
    const calls = f.overlayCalls()
    const shown = calls.lastIndexOf(`window.__freeze(${JSON.stringify(jpeg('tab-bytes'))},null)`)
    expect(shown).toBeGreaterThanOrEqual(0)
    expect(calls.slice(shown + 1)).toContain(`window.__freeze(${JSON.stringify(pick.frame.image)},${JSON.stringify(pick.rect)})`)
    expect(f.tab().pick).toBe(pick)
    expect(f.overlays()).toHaveLength(1)
    // Mode off: the capture makes an overlay of its own and leaves none behind.
    await annotate(f, false)
    expect(f.tab().overlay).toBeNull()
    const off = await capture()
    expect(off).toEqual({ image: jpeg('overlay-bytes') })
    expect(f.overlays()).toHaveLength(2)
    expect(f.overlay().webContents.close).toHaveBeenCalled()
    expect(f.win.contentView.removeChildView).toHaveBeenCalledWith(f.overlay())
    expect(f.tab().overlay).toBeNull()
    // A failed frame read is no picture.
    f.wc.frameReply = () => ({})
    expect(await capture()).toEqual({ image: '' })
  })

  it('T10 clear-notes removes pins; note numbers stay unique per tab across navigation', async () => {
    const f = fixture()
    const first = await pickBuy(f)
    boxSays(f, { t: 'submit', text: 'bir' })
    await settle()
    expect(f.overlayCalls().filter((c) => c.startsWith('window.__pins(')).at(-1)).toBe('window.__pins([{"n":1,"x":100,"y":100}])')
    f.wc.emit('did-start-navigation', { isMainFrame: true })
    f.wc.frameReply = () => frameOf({ docId: 'd2', scroll: [0, 50] })
    f.wc.emit('did-finish-load')
    overlaySays(f, { t: 'pick', x: 110, y: 105 })
    await vi.waitFor(() => expect(f.tab().pick).not.toBeNull())
    const second = f.tab().pick
    boxSays(f, { t: 'submit', text: 'iki' })
    // Each event carries only the note just saved, with its number.
    expect(f.sent('browser-notes').map((e: any) => e.notes.map((n: any) => [n.id, n.n]))).toEqual([[[first.id, 1]], [[second.id, 2]]])
    await settle()
    // Only the second note is of this document: its pin, at its place in the document less the scroll.
    expect(f.overlayCalls().filter((c) => c.startsWith('window.__pins(')).at(-1)).toBe('window.__pins([{"n":2,"x":100,"y":100}])')
    expect(await f.host.invoke({ op: 'clear-notes', conversationId: 4, tabId: 1, ids: [second.id] })).toEqual({ cleared: 1 })
    await settle()
    expect(f.overlayCalls().at(-1)).toBe('window.__pins([])')
    expect(f.tab().notes.map((one: any) => one.note.n)).toEqual([1])
    // A third note after the clear does not reuse 2.
    overlaySays(f, { t: 'pick', x: 110, y: 105 })
    await vi.waitFor(() => expect(f.tab().pick).not.toBeNull())
    boxSays(f, { t: 'submit', text: 'üç' })
    expect(f.sent('browser-notes').at(-1).notes.map((n: any) => n.n)).toEqual([3])
    expect(await f.host.invoke({ op: 'clear-notes', conversationId: 4, tabId: 1 })).toEqual({ cleared: 2 })
    // The page is only ever read: no call but frame() and mark() reached it.
    expect(f.pageCalls().every((code: string) => /\.(frame|mark)\(\{\}\)$/.test(code))).toBe(true)
  })

  it('a note names the page its pick was made on: saved after the tab navigated, it keeps the frame\'s address and title', async () => {
    const f = fixture()
    const pick = await pickBuy(f)
    expect(pick.frame).toMatchObject({ url: 'https://example.com/', title: 'Example' })
    boxSays(f, { t: 'draft', text: 'ilk sayfa', h: 56 })
    f.wc.emit('did-start-navigation', { isMainFrame: true })
    f.wc.url = 'https://other.example/'
    f.wc.title = 'Başka sayfa'
    f.wc.emit('did-navigate', {}, 'https://other.example/')
    f.wc.emit('did-finish-load')
    await settle()
    boxSays(f, { t: 'submit', text: 'ilk sayfa' })
    expect(f.sent('browser-notes')).toHaveLength(1)
    expect(f.sent('browser-notes')[0]).toMatchObject({ url: 'https://example.com/', title: 'Example', tabId: 1, viewport: { width: 1000, height: 700 } })
    expect(f.tab().notes[0]).toMatchObject({ url: 'https://example.com/', title: 'Example' })
  })

  it('a draft typed just before the box was hidden reaches its pick, and showing the box again restores that draft', async () => {
    const f = fixture()
    const pick = await pickBuy(f)
    boxSays(f, { t: 'draft', text: 'önce', h: 56 })
    await f.host.invoke({ op: 'visible', conversationId: 4, visible: false })
    // The box posted this before the hide was processed; it arrives after.
    boxSays(f, { t: 'draft', text: 'önce ve sonra', h: 56, p: pick.id })
    expect(pick.draft).toBe('önce ve sonra')
    await f.host.invoke({ op: 'visible', conversationId: 4, visible: true })
    await vi.waitFor(() => expect(setCalls(f).at(-1)).toBe(`window.__set("önce ve sonra",${JSON.stringify(pick.id)})`))
    expect(f.tab().pick).toBe(pick)
  })

  it('a box message for a pick whose tab was closed changes nothing', async () => {
    const f = fixture()
    const pick = await pickBuy(f)
    boxSays(f, { t: 'draft', text: 'kapanmadan', h: 56 })
    await f.host.invoke({ op: 'close-tab', conversationId: 4, tabId: 1 })
    expect(f.tab()).toBeUndefined()
    for (const t of ['draft', 'submit', 'cancel', 'size']) f.box().webContents.emit('page-title-updated', {}, JSON.stringify({ q: 1, t, text: 'geç', h: 190, p: pick.id }))
    expect(pick.draft).toBe('kapanmadan')
    expect(f.sent('browser-notes')).toHaveLength(0)
  })

  it('the box shows an error only for the pick it is showing', () => {
    const body = NOTE_BOX_HTML.slice(NOTE_BOX_HTML.indexOf('<body>') + 6, NOTE_BOX_HTML.indexOf('<script>'))
    const script = NOTE_BOX_HTML.slice(NOTE_BOX_HTML.indexOf('<script>') + 8, NOTE_BOX_HTML.indexOf('</script>'))
    document.body.innerHTML = body
    const title = Object.getOwnPropertyDescriptor(Document.prototype, 'title')!
    Object.defineProperty(document, 'title', { configurable: true, get: () => '', set: () => {} })
    try {
      ;(0, eval)(`(() => {${script}})()`)
      const w = window as any
      const err = document.getElementById('e') as HTMLElement
      w.__set('', 'n2')
      w.__error('A için hata', 'n1')
      expect(err.hidden).toBe(true)
      w.__error('B için hata', 'n2')
      expect([err.hidden, err.textContent]).toEqual([false, 'B için hata'])
    } finally {
      Object.defineProperty(document, 'title', title)
      document.body.innerHTML = ''
    }
  })

  it('clear-notes takes a removed note\'s pin off the frozen picture of a pick kept across a navigation', async () => {
    const f = fixture()
    const first = await pickBuy(f)
    boxSays(f, { t: 'submit', text: 'bir' })
    await settle()
    overlaySays(f, { t: 'pick', x: 310, y: 105 })
    await vi.waitFor(() => expect(f.tab().pick).not.toBeNull())
    boxSays(f, { t: 'draft', text: 'açık kalsın', h: 56 })
    await settle()
    expect(f.overlayCalls().filter((c) => c.startsWith('window.__pins(')).at(-1)).toBe('window.__pins([{"n":1,"x":100,"y":100}])')
    f.wc.emit('did-start-navigation', { isMainFrame: true })
    f.wc.emit('did-finish-load')
    await settle()
    expect(await f.host.invoke({ op: 'clear-notes', conversationId: 4, tabId: 1, ids: [first.id] })).toEqual({ cleared: 1 })
    await settle()
    expect(f.overlayCalls().filter((c) => c.startsWith('window.__pins(')).at(-1)).toBe('window.__pins([])')
  })

  it('with no pick open, clear-notes still draws the pins of the notes left on the live frame', async () => {
    const f = fixture()
    const first = await pickBuy(f)
    boxSays(f, { t: 'submit', text: 'bir' })
    await settle()
    overlaySays(f, { t: 'pick', x: 310, y: 105 })
    await vi.waitFor(() => expect(f.tab().pick).not.toBeNull())
    boxSays(f, { t: 'submit', text: 'iki' })
    await settle()
    expect(f.tab().pick).toBeNull()
    expect(await f.host.invoke({ op: 'clear-notes', conversationId: 4, tabId: 1, ids: [first.id] })).toEqual({ cleared: 1 })
    await settle()
    expect(f.overlayCalls().at(-1)).toBe('window.__pins([{"n":2,"x":300,"y":100}])')
  })
})

describe('the overlay shows one look at a time', () => {
  const pinCalls = (f: F) => f.overlayCalls().filter((c) => c.startsWith('window.__pins('))
  /** The overlay's next capturePage waits until `release` is called. */
  function holdCapture(f: F) {
    const entered = deferred<void>()
    const gate = deferred<void>()
    f.overlay().webContents.capturePage = vi.fn(async () => { entered.resolve(); await gate.promise; return fakeImage('overlay-bytes') })
    return { entered: entered.promise, release: () => gate.resolve() }
  }

  it('a capture draws only its own frame\'s pins: a note cleared while it is taken does not bring an open pick\'s older document\'s pins onto it', async () => {
    const f = fixture()
    const first = await pickBuy(f)
    boxSays(f, { t: 'submit', text: 'bir' })
    await settle()
    overlaySays(f, { t: 'pick', x: 310, y: 105 })
    await vi.waitFor(() => expect(f.tab().pick).not.toBeNull())
    boxSays(f, { t: 'submit', text: 'iki' })
    await settle()
    overlaySays(f, { t: 'pick', x: 10, y: 400 })
    await vi.waitFor(() => expect(f.tab().pick).not.toBeNull())
    const open = f.tab().pick
    boxSays(f, { t: 'draft', text: 'açık kalsın', h: 56 })
    // The tab moves to another document while the comment stays open on d1's frozen picture.
    f.wc.emit('did-start-navigation', { isMainFrame: true })
    f.wc.frameReply = () => frameOf({ docId: 'd2' })
    f.wc.emit('did-finish-load')
    await settle()
    const held = holdCapture(f)
    const capture = f.host.invoke({ op: 'capture-notes', conversationId: 4, tabId: 1 }) as Promise<any>
    await held.entered
    expect(pinCalls(f).at(-1)).toBe('window.__pins([])')
    expect(await f.host.invoke({ op: 'clear-notes', conversationId: 4, tabId: 1, ids: [first.id] })).toEqual({ cleared: 1 })
    await settle()
    // What the capture is taking is d2 with d2's pins (none), not d1's note 2.
    expect(pinCalls(f).at(-1)).toBe('window.__pins([])')
    held.release()
    expect(await capture).toEqual({ image: jpeg('overlay-bytes') })
    await settle()
    // Afterwards the open pick's frozen d1 picture is back, with d1's remaining pin.
    expect(f.tab().pick).toBe(open)
    expect(pinCalls(f).at(-1)).toBe('window.__pins([{"n":2,"x":300,"y":100}])')
  })

  it('a navigation during a capture leaves the overlay on the live look with nothing drawn: the captured document\'s pins do not come back', async () => {
    const f = fixture()
    await pickBuy(f)
    boxSays(f, { t: 'submit', text: 'bir' })
    await settle()
    expect(pinCalls(f).at(-1)).toBe('window.__pins([{"n":1,"x":100,"y":100}])')
    const held = holdCapture(f)
    const capture = f.host.invoke({ op: 'capture-notes', conversationId: 4, tabId: 1 }) as Promise<any>
    await held.entered
    f.wc.url = 'https://other.example/'
    f.wc.emit('did-start-navigation', { isMainFrame: true })
    await settle()
    held.release()
    expect(await capture).toEqual({ image: jpeg('overlay-bytes') })
    await settle()
    const calls = f.overlayCalls()
    expect(calls.slice(-3)).toEqual(['window.__boxes([],[0,0])', 'window.__pins([])', 'window.__freeze(null,null)'])
    // The next refresh draws the new page.
    f.wc.frameReply = () => frameOf({ docId: 'd2' })
    await f.fire(0)
    expect(f.overlayCalls().at(-1)).toBe('window.__pins([])')
    expect(f.overlayCalls()).toContain(`window.__boxes(${JSON.stringify(frameOf().elements.map(({ box, id, kind }) => ({ ...box, id, kind })))},[0,0])`)
  })

  it('a click read that finishes on a hidden tab opens its pick there and leaves the shown tab\'s comment box alone', async () => {
    const f = fixture()
    await openPage(f)
    await annotate(f, true)
    const read = deferred<any>()
    f.wc.frameReply = () => read.promise
    overlaySays(f, { t: 'pick', x: 110, y: 105 })
    await settle()
    // The user opens a second tab in the same chat (tab 1 is now hidden), annotates it and picks there.
    await f.host.invoke({ op: 'open', conversationId: 4, url: 'https://two.example/' })
    const b = [...(f.host as any).tabs.values()].at(-1)
    expect(b.id).not.toBe(1)
    const bwc = f.answers(b.view.webContents)
    bwc.pending = undefined
    bwc.url = 'https://two.example/'
    bwc.title = 'Two'
    bwc.emit('did-finish-load')
    await settle()
    expect(await f.host.invoke({ op: 'annotate', conversationId: 4, tabId: b.id, on: true })).not.toHaveProperty('error')
    f.overlay().webContents.emit('page-title-updated', {}, JSON.stringify({ q: 1, t: 'pick', x: 110, y: 105 }))
    await vi.waitFor(() => expect(setCalls(f).length).toBeGreaterThan(0))
    const pb = b.pick
    expect((f.host as any).boxTab).toBe(b)
    const hides = f.box().setVisible.mock.calls.filter((c: any[]) => c[0] === false).length
    read.resolve(frameOf())
    await settle()
    await settle()
    expect(f.tab().pick).not.toBeNull()
    expect((f.host as any).boxTab).toBe(b)
    expect(b.pick).toBe(pb)
    expect(f.box().setVisible.mock.calls.filter((c: any[]) => c[0] === false)).toHaveLength(hides)
  })

  /** The overlay's next picture-decoding call (a __freeze with the captured frame) waits until `release` is called. */
  function holdDecode(f: F) {
    const entered = deferred<void>()
    const gate = deferred<void>()
    const run = f.overlay().webContents.executeJavaScript
    let held = false
    f.overlay().webContents.executeJavaScript = vi.fn(async (code: string) => {
      if (!held && code.startsWith('window.__freeze(') && code.includes(JSON.stringify(jpeg('tab-bytes')))) {
        held = true
        entered.resolve()
        await gate.promise
      }
      return run(code)
    })
    return { entered: entered.promise, release: () => gate.resolve() }
  }
  const capture = (f: F) => f.host.invoke({ op: 'capture-notes', conversationId: 4, tabId: 1 }) as Promise<any>

  it('a submit during a capture\'s decode: the capture returns nothing and the live look the submit left stays', async () => {
    const f = fixture()
    await pickBuy(f)
    boxSays(f, { t: 'draft', text: 'bir', h: 56 })
    await settle()
    const held = holdDecode(f)
    const taking = capture(f)
    await held.entered
    boxSays(f, { t: 'submit', text: 'bir' })
    await settle()
    expect(f.tab().pick).toBeNull()
    const look = f.tab().shows
    expect(look?.by).not.toBe('capture')
    const calls = f.overlayCalls().length
    held.release()
    expect(await taking).toEqual({ image: '' })
    await settle()
    expect(f.overlay().webContents.capturePage).not.toHaveBeenCalled()
    expect(f.tab().shows).toBe(look)
    expect(f.overlayCalls().length).toBe(calls)
  })

  it('a click during a capture\'s decode: the capture returns nothing and the new pick\'s look stays', async () => {
    const f = fixture()
    const first = await pickBuy(f)
    const held = holdDecode(f)
    const taking = capture(f)
    await held.entered
    overlaySays(f, { t: 'pick', x: 10, y: 400 })
    await vi.waitFor(() => expect(f.tab().pick).not.toBe(first))
    await settle()
    const look = f.tab().shows
    expect(look?.by).toBe('pick')
    const calls = f.overlayCalls().length
    held.release()
    expect(await taking).toEqual({ image: '' })
    await settle()
    expect(f.overlay().webContents.capturePage).not.toHaveBeenCalled()
    expect(f.tab().shows).toBe(look)
    expect(f.overlayCalls().length).toBe(calls)
  })

  it('two overlapping captures: the one whose look was replaced returns nothing and restores nothing', async () => {
    const f = fixture()
    await openPage(f)
    await annotate(f, true)
    await settle()
    const held = holdDecode(f)
    const first = capture(f)
    await held.entered
    const second = await capture(f)
    expect(second).toEqual({ image: jpeg('overlay-bytes') })
    await settle()
    const look = f.tab().shows
    expect(look?.by).not.toBe('capture')
    const calls = f.overlayCalls().length
    held.release()
    expect(await first).toEqual({ image: '' })
    await settle()
    expect(f.overlay().webContents.capturePage).toHaveBeenCalledTimes(1)
    expect(f.tab().shows).toBe(look)
    expect(f.overlayCalls().length).toBe(calls)
  })

  it('a capture with no navigation puts the open pick\'s frozen picture, outline and pins back as they were', async () => {
    const f = fixture()
    await pickBuy(f)
    boxSays(f, { t: 'submit', text: 'bir' })
    await settle()
    overlaySays(f, { t: 'pick', x: 310, y: 105 })
    await vi.waitFor(() => expect(f.tab().pick).not.toBeNull())
    const pick = f.tab().pick
    boxSays(f, { t: 'draft', text: 'açık kalsın', h: 56 })
    await settle()
    const pickFreeze = `window.__freeze(${JSON.stringify(pick.frame.image)},${JSON.stringify(pick.rect)})`
    const pinsBefore = pinCalls(f).at(-1)
    expect(pinsBefore).toBe('window.__pins([{"n":1,"x":100,"y":100}])')
    expect(await f.host.invoke({ op: 'capture-notes', conversationId: 4, tabId: 1 })).toEqual({ image: jpeg('overlay-bytes') })
    await settle()
    const calls = f.overlayCalls()
    const captured = calls.lastIndexOf(`window.__freeze(${JSON.stringify(jpeg('tab-bytes'))},null)`)
    expect(captured).toBeGreaterThanOrEqual(0)
    expect(calls.slice(captured + 1).filter((c) => c.startsWith('window.__freeze('))).toEqual([pickFreeze])
    expect(pinCalls(f).at(-1)).toBe(pinsBefore)
    expect(f.tab().pick).toBe(pick)
    expect(pick.draft).toBe('açık kalsın')
  })
})

describe('T11 hit-test rule', () => {
  const el = (id: string, x: number, y: number, width: number, height: number, text = id) => ({ id, box: { x, y, width, height }, text })
  const outer = el('outer', 0, 0, 400, 300)
  const inner = el('inner', 50, 50, 100, 40)
  const twinA = el('twinA', 200, 200, 30, 30)
  const twinB = el('twinB', 200, 200, 30, 30)

  it('T11 smallest containing box wins, tie to the later box; area collects only fully contained boxes\' texts', () => {
    expect(hitElement([outer, inner], 60, 60)?.id).toBe('inner')
    expect(hitElement([inner, outer], 60, 60)?.id).toBe('inner')
    expect(hitElement([outer, inner], 10, 10)?.id).toBe('outer')
    expect(hitElement([outer, twinA, twinB], 210, 210)?.id).toBe('twinB')
    expect(hitElement([outer, twinB, twinA], 210, 210)?.id).toBe('twinA')
    // The right and bottom edges are outside the box.
    expect(hitElement([inner], 150, 60)).toBeNull()
    expect(hitElement([], 1, 1)).toBeNull()
    const partly = el('partly', 90, 10, 100, 100)
    expect(areaText([outer, inner, partly, twinA], { x: 40, y: 40, width: 200, height: 200 })).toBe('inner twinA')
    expect(areaText([inner, el('empty', 60, 60, 5, 5, '')], { x: 0, y: 0, width: 500, height: 500 })).toBe('inner')
    expect(areaText([el('long', 0, 0, 1, 1, 'x'.repeat(400)), el('more', 0, 0, 1, 1, 'y'.repeat(400))], { x: 0, y: 0, width: 9, height: 9 })).toHaveLength(500)
  })

  it('T11 the overlay outlines on hover with the same rule, without asking the host', () => {
    const body = OVERLAY_HTML.slice(OVERLAY_HTML.indexOf('<body>') + 6, OVERLAY_HTML.indexOf('<script>'))
    const script = OVERLAY_HTML.slice(OVERLAY_HTML.indexOf('<script>') + 8, OVERLAY_HTML.indexOf('</script>'))
    document.body.innerHTML = body
    const titles: string[] = []
    const title = Object.getOwnPropertyDescriptor(Document.prototype, 'title')!
    Object.defineProperty(document, 'title', { configurable: true, get: () => titles.at(-1) ?? '', set: (v: string) => { titles.push(v) } })
    try {
      ;(0, eval)(`(() => {${script}})()`)
      const w = window as any
      // Two boxes of equal area that both contain (210, 210): the later one is outlined.
      const shifted = el('shifted', 205, 205, 30, 30)
      w.__boxes([outer, inner, twinA, shifted].map(({ id, box }) => ({ ...box, id, kind: 'element' })), [0, 0])
      const hover = document.getElementById('h') as HTMLElement
      const move = (x: number, y: number) => window.dispatchEvent(new MouseEvent('pointermove', { clientX: x, clientY: y }))
      move(60, 60)
      expect([hover.style.left, hover.style.top, hover.style.width]).toEqual(['50px', '50px', '100px'])
      move(210, 210)
      expect([hover.style.left, hover.style.display]).toEqual(['205px', 'block'])
      move(10, 10)
      expect(hover.style.width).toBe('400px')
      expect(titles).toHaveLength(0)
      // A click posts the point; Esc posts nothing (the host claims it before the overlay sees it); pins are drawn with
      // their number as text.
      window.dispatchEvent(new MouseEvent('pointerdown', { clientX: 10, clientY: 10, button: 0 }))
      window.dispatchEvent(new MouseEvent('pointerup', { clientX: 10, clientY: 10, button: 0 }))
      expect(JSON.parse(titles.at(-1)!)).toMatchObject({ t: 'pick', x: 10, y: 10 })
      const posted = titles.length
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
      expect(titles).toHaveLength(posted)
      w.__pins([{ n: '<b>7</b>', x: 40, y: 40 }])
      expect(document.querySelector('.pin')!.textContent).toBe('<b>7</b>')
      expect(document.querySelector('.pin b')).toBeNull()
    } finally {
      Object.defineProperty(document, 'title', title)
      document.body.innerHTML = ''
    }
  })
})

describe('T8 the page script frames', () => {
  beforeEach(() => {
    vi.spyOn(Element.prototype, 'getClientRects').mockImplementation(() => [{}] as unknown as DOMRectList)
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
      const [x, y, w, h] = (this.getAttribute('data-r') || '0,0,0,0').split(',').map(Number)
      return { x, y, left: x, top: y, width: w, height: h, right: x + w, bottom: y + h, toJSON() {} } as DOMRect
    })
    delete (globalThis as any).__gmBrowser
  })
  afterEach(() => {
    vi.restoreAllMocks()
    delete (globalThis as any).__gmBrowser
    document.body.innerHTML = ''
  })

  it('T8 PAGE_LIB frame() and mark() leave document.documentElement.outerHTML unchanged in jsdom, and frame() returns elements with boxes and the expected data', async () => {
    document.body.innerHTML = '<main data-r="0,0,800,600"><a href="https://shop.example/buy"><button id="buy" class="cta" data-r="10,20,100,30">Satın al</button></a>'
      + '<input id="secret" value="hunter2" aria-label="Parola" data-r="10,60,100,20"><p data-r="300,300,50,20">Dışarıda <b>kalın</b></p>'
      + '<p data-r="10,5000,50,20">Aşağıda</p><span data-r="0,0,0,0">Boş</span></main>'
    const before = document.documentElement.outerHTML
    ;(0, eval)(PAGE_LIB)
    const lib = (globalThis as any).__gmBrowser
    const frame = lib.frame()
    const mark = lib.mark()
    await new Promise(r => setTimeout(r, 0))
    expect(lib.mark()).toEqual(mark)
    expect(document.documentElement.outerHTML).toBe(before)
    expect(frame).toMatchObject({ docId: expect.any(String), scroll: [0, 0], viewport: { width: window.innerWidth, height: window.innerHeight } })
    expect(mark).toEqual({ docId: frame.docId, scroll: frame.scroll, viewport: frame.viewport })
    // Shown elements inside the viewport, in document order: no off-screen or empty box.
    expect(frame.elements.map((e: any) => e.tag)).toEqual(['main', 'button', 'input', 'p'])
    expect(frame.elements[3].text).toBe('Dışarıda kalın')
    const buy = frame.elements[1]
    expect(buy).toEqual({
      box: { x: 10, y: 20, width: 100, height: 30 }, id: expect.stringMatching(/^d.+-el\d+$/), tag: 'button', role: 'button',
      name: 'Satın al', href: 'https://shop.example/buy', path: 'button#buy', text: 'Satın al',
    })
    expect(frame.elements[2]).toMatchObject({ tag: 'input', name: 'Parola', text: '' })
    expect(JSON.stringify(frame)).not.toContain('hunter2')
    // The same element keeps its id within the document; a change inside the page (an animation, a clock) leaves the mark
    // as it was, so such a page still lets a pick open.
    expect(lib.frame().elements[1].id).toBe(buy.id)
    document.getElementById('buy')!.textContent = 'Al'
    await new Promise(r => setTimeout(r, 0))
    expect(lib.mark()).toEqual(mark)
    expect(document.documentElement.outerHTML).not.toBe(before)
  })
})

describe('the comment box opens at its measured height', () => {
  it('stays hidden until it has measured itself at the width it will have, then shows at that height', async () => {
    const f = fixture()
    await openPage(f)
    await annotate(f, true)
    // The box answers __set with its height, as the real page does.
    const order: string[] = []
    const answering = (wc: any) => wc.executeJavaScript.mockImplementation(async (code: string) => {
      order.push(code.startsWith('window.__width(') ? 'width' : code.startsWith('window.__set(') ? 'set' : 'other')
      return code.startsWith('window.__set(') ? 90 : undefined
    })
    // The next view made is the box: it answers from the start.
    const deps = (f.host as any).deps
    const make = deps.createView
    deps.createView = (options: any) => { const v = make(options); answering(v.webContents); return v }
    overlaySays(f, { t: 'pick', x: 110, y: 105 })
    await vi.waitFor(() => expect(f.box()).toBeDefined())
    await vi.waitFor(() => expect(f.box().setVisible).toHaveBeenLastCalledWith(true))
    expect(f.boxCalls()).toContain('window.__width(320)')
    expect(order.indexOf('width')).toBeLessThan(order.indexOf('set'))
    // Shown once, after the measured bounds were set: never at the minimum first.
    const shows = f.box().setVisible.mock.calls.map((c: any[]) => c[0])
    expect(shows.filter((v: boolean) => v)).toHaveLength(1)
    expect(f.box().setBounds.mock.calls.at(-1)[0].height).toBe(90)
  })

  it('the box page measures at the width it is given, returns its height, saves on Enter and leaves Esc to the host', () => {
    expect(NOTE_BOX_HTML).not.toContain('M8 13V3M3.5 7.5 8 3l4.5 4.5')
    expect(NOTE_BOX_HTML).not.toMatch(/border-radius:50%/)
    const body = NOTE_BOX_HTML.slice(NOTE_BOX_HTML.indexOf('<body>') + 6, NOTE_BOX_HTML.indexOf('<script>'))
    const script = NOTE_BOX_HTML.slice(NOTE_BOX_HTML.indexOf('<script>') + 8, NOTE_BOX_HTML.indexOf('</script>'))
    document.body.innerHTML = body
    const titles: string[] = []
    const title = Object.getOwnPropertyDescriptor(Document.prototype, 'title')!
    Object.defineProperty(document, 'title', { configurable: true, get: () => titles.at(-1) ?? '', set: (v: string) => { titles.push(v) } })
    try {
      ;(0, eval)(`(() => {${script}})()`)
      const w = window as any
      w.__width(320)
      expect((document.getElementById('w') as HTMLElement).style.width).toBe('308px')
      expect(typeof w.__set('ilk', 'n1')).toBe('number')
      expect(JSON.parse(titles.at(-1)!)).toMatchObject({ t: 'size', p: 'n1', text: 'ilk' })
      const ta = document.getElementById('t') as HTMLTextAreaElement
      const posted = titles.length
      ta.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
      expect(titles).toHaveLength(posted)
      ta.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', shiftKey: true, bubbles: true }))
      expect(titles).toHaveLength(posted)
      ta.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
      expect(JSON.parse(titles.at(-1)!)).toMatchObject({ t: 'submit', text: 'ilk', p: 'n1' })
      // An error is drawn as text only.
      w.__error('<b>kayıt</b>', 'n1')
      expect(document.getElementById('e')!.textContent).toBe('<b>kayıt</b>')
      expect(document.querySelector('#e b')).toBeNull()
    } finally {
      Object.defineProperty(document, 'title', title)
      document.body.innerHTML = ''
    }
  })
})
