import { EventEmitter } from 'events'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { BROWSER_PARTITION, BROWSER_WORLD_ID, BrowserHost, MAX_CHAT_TABS, MAX_TABS, NAVIGATION_TIMEOUT_MS, type BrowserHostDeps } from '../main/helpers/browser-host'

// The isolated-world script runs for real against jsdom. jsdom lays nothing out and
// has no innerText or scrollBy, so every element reports one client rect, innerText
// reads textContent and scrolling is a no-op.
beforeEach(() => {
  vi.spyOn(Element.prototype, 'getClientRects').mockImplementation(() => [{}] as unknown as DOMRectList)
  Element.prototype.scrollIntoView = vi.fn()
  Object.defineProperty(HTMLElement.prototype, 'innerText', { configurable: true, get() { return this.textContent } })
  window.scrollBy = vi.fn() as typeof window.scrollBy
})
afterEach(() => {
  vi.restoreAllMocks()
  delete (globalThis as any).__gmBrowser
  document.body.innerHTML = ''
})

function fakeWebContents() {
  const wc: any = new EventEmitter()
  Object.assign(wc, {
    url: '', title: '', loading: false, destroyed: false, openHandler: null as any,
    isDestroyed: () => wc.destroyed,
    getURL: () => wc.url,
    getTitle: () => wc.title,
    isLoading: () => wc.loading,
    setWindowOpenHandler: (h: any) => { wc.openHandler = h },
    loadURL: vi.fn(async (url: string) => { wc.loading = true; wc.pending = url }),
    stop: vi.fn(() => { wc.loading = false }),
    reload: vi.fn(),
    focus: vi.fn(),
    close: vi.fn(() => { wc.destroyed = true }),
    sendInputEvent: vi.fn(),
    capturePage: vi.fn(),
    navigationHistory: { canGoBack: () => false, canGoForward: () => false, goBack: vi.fn(), goForward: vi.fn() },
    executeJavaScriptInIsolatedWorld: vi.fn(async (_world: number, scripts: { code: string }[]) => (0, eval)(scripts[0].code)),
    finish(url: string, title = 'Page') { wc.url = url; wc.title = title; wc.loading = false; wc.emit('did-finish-load') },
  })
  return wc
}

function fakeView(id: number) {
  const wc = fakeWebContents()
  wc.id = id
  return { webContents: wc, setBounds: vi.fn(), setVisible: vi.fn() } as any
}

function fixture(platform?: string) {
  // Each createView call hands out the next view; `view`/`wc` are the first tab's.
  const views: any[] = [fakeView(1)]
  const view = views[0]
  const wc = view.webContents
  const session: any = new EventEmitter()
  session.setPermissionRequestHandler = vi.fn()
  session.setPermissionCheckHandler = vi.fn()
  session.clearStorageData = vi.fn(async () => {})
  session.clearCache = vi.fn(async () => {})
  session.webRequest = { onBeforeRequest: vi.fn(), onResponseStarted: vi.fn(), onCompleted: vi.fn() }
  const win: any = { isDestroyed: () => false, contentView: { addChildView: vi.fn(), removeChildView: vi.fn() }, webContents: { send: vi.fn() } }
  const http = { request: vi.fn(async (config: any) => {
    if (config.method === 'get') return { data: { enabled: true, runtime: { running: false, port: null, token: null } } }
    if (config.method === 'put') return { data: { enabled: config.data.enabled } }
    return { data: {} }
  }) }
  const lookup = vi.fn(async (host: string) => [{ address: host.startsWith('internal') ? '10.0.0.8' : '93.184.216.34', family: 4 }])
  let made = 0
  const createView = vi.fn(() => {
    if (!views[made]) views.push(fakeView(made + 1))
    return views[made++]
  })
  const fromPartition = vi.fn(() => session)
  const onHalt = vi.fn()
  const clock = { now: 1_000 }
  const fetch = vi.fn(async (_url: string, _init: unknown): Promise<Response> => { throw new Error('no network in tests') })
  const history = { visit: vi.fn(), update: vi.fn(), list: vi.fn(() => [{ url: 'https://a.example/', title: 'A', favicon: '', lastVisit: 1, visits: 1 }]), clear: vi.fn(), flush: vi.fn() }
  const deps: BrowserHostDeps = {
    window: () => win, createView, fromPartition, http, baseUrl: () => 'http://127.0.0.1:9', appToken: 'tok', uiSecret: 'secret',
    lookup, onHalt, now: () => clock.now, history, platform, fetch,
  }
  const host = new BrowserHost(deps)
  return { host, wc, view, views, session, win, http, lookup, createView, fromPartition, onHalt, clock, history, fetch }
}

async function navigate(f: ReturnType<typeof fixture>, url = 'https://example.com/', html = '', conversationId = 4) {
  const tab = f.views.length
  const reply = f.host.handle({ op: 'navigate', conversation_id: conversationId, args: { url } })
  let wc: any
  await vi.waitFor(() => {
    wc = f.views.find((v: any) => v.webContents.loadURL.mock.calls.some(([u]: [string]) => u === url) && v.webContents.pending === url)?.webContents
    expect(wc, `tab ${tab} loading ${url}`).toBeTruthy()
  })
  delete (globalThis as any).__gmBrowser
  document.body.innerHTML = html
  wc.pending = undefined
  wc.finish(url)
  return reply
}

const livePayloads = (f: ReturnType<typeof fixture>) =>
  f.win.webContents.send.mock.calls.filter(([channel]: [string]) => channel === 'browser-live').map(([, p]: [string, any]) => p)

describe('BrowserHost view and session', () => {
  it('creates one hardened view on the persistent shared partition on the first command', async () => {
    expect(BROWSER_PARTITION).toBe('persist:gamachine-agent-browser')
    const f = fixture()
    expect(f.createView).not.toHaveBeenCalled()
    expect(await navigate(f)).toEqual({ ok: true, url: 'https://example.com/', title: 'Page' })
    await navigate(f, 'https://example.org/')
    expect(f.fromPartition).toHaveBeenCalledWith(BROWSER_PARTITION)
    expect(f.createView).toHaveBeenCalledTimes(1)
    const prefs = (f.createView.mock.calls[0] as any)[0].webPreferences
    expect(prefs).toMatchObject({ session: f.session, nodeIntegration: false, contextIsolation: true, sandbox: true })
    expect(Object.keys(prefs).some((k) => /load/i.test(k))).toBe(false)
    expect(f.win.contentView.addChildView).toHaveBeenCalledWith(f.view)
  })

  it('denies every permission request and check', async () => {
    const f = fixture()
    await navigate(f)
    const request = f.session.setPermissionRequestHandler.mock.calls[0][0]
    const callback = vi.fn()
    for (const permission of ['media', 'geolocation', 'notifications', 'clipboard-read']) request(f.wc, permission, callback, {})
    expect(callback.mock.calls.every(([granted]: [boolean]) => granted === false)).toBe(true)
    expect(callback).toHaveBeenCalledTimes(4)
    const check = f.session.setPermissionCheckHandler.mock.calls[0][0]
    expect(check(f.wc, 'media', 'https://example.com', {})).toBe(false)
  })

  it('cancels every download', async () => {
    const f = fixture()
    await navigate(f)
    const event = { preventDefault: vi.fn() }
    const item = { cancel: vi.fn() }
    f.session.emit('will-download', event, item, f.wc)
    expect(event.preventDefault).toHaveBeenCalled()
    expect(item.cancel).toHaveBeenCalled()
  })

  it('denies pop-ups and hands their URL back in the next reply', async () => {
    const f = fixture()
    await navigate(f)
    expect(f.wc.openHandler({ url: 'https://ads.example/' })).toEqual({ action: 'deny' })
    const reply = await f.host.handle({ op: 'scroll', conversation_id: 4, args: { direction: 'down' } })
    expect(reply.popup_blocked).toBe('https://ads.example/')
    expect((await f.host.handle({ op: 'scroll', conversation_id: 4, args: { direction: 'down' } })).popup_blocked).toBeUndefined()
  })
})

describe('BrowserHost navigation refusal', () => {
  it('refuses disallowed URLs before loading and hosts that resolve privately', async () => {
    const f = fixture()
    for (const url of ['file:///etc/passwd', 'http://127.0.0.1:8000/', 'http://localhost/', 'javascript:alert(1)', 'https://internal.corp/']) {
      expect(await f.host.handle({ op: 'navigate', conversation_id: 4, args: { url } })).toMatchObject({ ok: false, error: 'url_not_allowed' })
    }
    expect(f.wc.loadURL).not.toHaveBeenCalled()
  })

  it('prevents page-initiated navigations, redirects and frame navigations to refused URLs', async () => {
    const f = fixture()
    await navigate(f)
    for (const name of ['will-navigate', 'will-redirect', 'will-frame-navigate']) {
      const bad = { url: 'http://192.168.1.1/admin', isMainFrame: false, preventDefault: vi.fn() }
      const good = { url: 'https://example.net/', isMainFrame: false, preventDefault: vi.fn() }
      f.wc.emit(name, bad)
      f.wc.emit(name, good)
      expect(bad.preventDefault, name).toHaveBeenCalled()
      expect(good.preventDefault, name).not.toHaveBeenCalled()
    }
  })

  it('ends a navigation whose redirect is refused instead of waiting for the timeout', async () => {
    const f = fixture()
    const reply = f.host.handle({ op: 'navigate', conversation_id: 4, args: { url: 'https://example.com/' } })
    await vi.waitFor(() => expect(f.wc.loadURL).toHaveBeenCalled())
    f.wc.emit('will-redirect', { url: 'http://127.0.0.1:8000/', isMainFrame: true, preventDefault: vi.fn() })
    expect(await reply).toMatchObject({ ok: false, error: 'url_not_allowed' })
  })

  it('cancels sub-resource requests to private hosts, caching only the blocked lookups', async () => {
    const f = fixture()
    await navigate(f)
    const gate = f.session.webRequest.onBeforeRequest.mock.calls[0][0]
    const ask = (url: string, resourceType = 'xhr') => new Promise((resolve) => gate({ url, resourceType }, resolve))
    expect(await ask('http://127.0.0.1:8000/api')).toEqual({ cancel: true })
    expect(await ask('ws://localhost:9000/')).toEqual({ cancel: true })
    expect(await ask('https://internal.corp/x.js', 'script')).toEqual({ cancel: true })
    expect(await ask('file:///etc/passwd', 'image')).toEqual({ cancel: true })
    expect(await ask('https://cdn.example/a.png', 'image')).toEqual({ cancel: false })
    expect(await ask('https://cdn.example/b.png', 'image')).toEqual({ cancel: false })
    expect(await ask('data:image/png;base64,AAAA', 'image')).toEqual({ cancel: false })
    expect(await ask('data:text/html,hi', 'subFrame')).toEqual({ cancel: true })
    // An allowed verdict is looked up again every time, so a rebinding name is caught on its next request.
    expect(f.lookup.mock.calls.filter(([h]: [string]) => h === 'cdn.example')).toHaveLength(2)
    expect(await ask('https://internal.corp/y.js', 'script')).toEqual({ cancel: true })
    expect(f.lookup.mock.calls.filter(([h]: [string]) => h === 'internal.corp')).toHaveLength(1)
  })

  it('re-resolves an allowed host on the next navigation and blocks it once it rebinds', async () => {
    const f = fixture()
    await navigate(f, 'https://rebind.example/')
    f.lookup.mockImplementation(async () => [{ address: '127.0.0.1', family: 4 }])
    expect(await f.host.handle({ op: 'navigate', conversation_id: 4, args: { url: 'https://rebind.example/again' } })).toMatchObject({ ok: false, error: 'url_not_allowed' })
  })

  it('aborts the page and reports private_address when the connected IP is private', async () => {
    const f = fixture()
    await navigate(f)
    const started = f.session.webRequest.onResponseStarted.mock.calls[0][0]
    const completed = f.session.webRequest.onCompleted.mock.calls[0][0]
    f.win.webContents.send.mockClear()
    started({ url: 'https://example.com/ok.js', ip: '93.184.216.34' })
    started({ url: 'https://example.com/cached.js' })
    started({ url: 'http://93.184.216.34/literal', ip: '10.0.0.1' })
    expect(f.wc.stop).not.toHaveBeenCalled()
    const pending = f.host.handle({ op: 'navigate', conversation_id: 4, args: { url: 'https://rebind.example/' } })
    await vi.waitFor(() => expect(f.wc.loadURL).toHaveBeenCalledWith('https://rebind.example/'))
    started({ url: 'https://rebind.example/', ip: '127.0.0.1' })
    expect(await pending).toMatchObject({ ok: false, error: 'private_address' })
    expect(f.wc.stop).toHaveBeenCalled()
    expect(f.wc.loadURL).toHaveBeenLastCalledWith('about:blank')
    const payloads = f.win.webContents.send.mock.calls.filter(([channel]: [string]) => channel === 'browser-live').map(([, p]: [string, any]) => p)
    expect(payloads.some((p: any) => p.error === 'private_address')).toBe(true)
    // The host is now blocked even though its lookup answers publicly again.
    expect(await f.host.handle({ op: 'navigate', conversation_id: 4, args: { url: 'https://rebind.example/' } })).toMatchObject({ ok: false, error: 'url_not_allowed' })
    f.wc.loadURL.mockClear()
    completed({ url: 'https://other.example/', ip: '[::1]' })
    expect(f.wc.loadURL).toHaveBeenCalledWith('about:blank')
  })

  it('reports a navigation that never finishes as a timeout', async () => {
    vi.useFakeTimers()
    try {
      const f = fixture()
      const reply = f.host.handle({ op: 'navigate', conversation_id: 4, args: { url: 'https://slow.example/' } })
      await vi.waitFor(() => expect(f.wc.loadURL).toHaveBeenCalled())
      await vi.advanceTimersByTimeAsync(NAVIGATION_TIMEOUT_MS)
      expect(await reply).toMatchObject({ ok: false, error: 'navigation_timeout' })
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('BrowserHost page scripts', () => {
  const PAGE = '<a href="https://example.com/walk">Walk cycle</a><button id="b">Search</button>' +
    '<form method="post" action="/login"><input id="u" placeholder="User"><input type="password" id="p" aria-label="Password"><button>Sign in</button></form>' +
    '<select id="s" aria-label="License"><option value="cc0">CC0</option><option value="by">CC-BY</option></select>'

  it('snapshots in the isolated world and clicks, fills and selects by ref', async () => {
    const f = fixture()
    await navigate(f, 'https://example.com/', PAGE)
    const snap = await f.host.handle({ op: 'snapshot', conversation_id: 4, args: {} })
    expect(snap.ok).toBe(true)
    expect(f.wc.executeJavaScriptInIsolatedWorld.mock.calls[0][0]).toBe(BROWSER_WORLD_ID)
    const elements = snap.elements as { ref: string; name: string; kind: string; role: string; value?: string }[]
    const byName = (name: string) => elements.find((e) => e.name === name)!
    expect(byName('Walk cycle')).toMatchObject({ role: 'link', kind: 'link', href: 'https://example.com/walk' })
    expect(byName('Password').value).toBeUndefined()
    const clicked = vi.fn()
    document.getElementById('b')!.addEventListener('click', clicked)
    expect(await f.host.handle({ op: 'click', conversation_id: 4, args: { snapshot_id: snap.snapshot_id, ref: byName('Search').ref } })).toMatchObject({ ok: true })
    expect(clicked).toHaveBeenCalled()
    expect(f.wc.executeJavaScriptInIsolatedWorld.mock.calls.at(-1)[2]).toBe(true)
    await f.host.handle({ op: 'fill', conversation_id: 4, args: { snapshot_id: snap.snapshot_id, ref: byName('User').ref, value: 'walker' } })
    expect((document.getElementById('u') as HTMLInputElement).value).toBe('walker')
    await f.host.handle({ op: 'select', conversation_id: 4, args: { snapshot_id: snap.snapshot_id, ref: byName('License').ref, option: 'CC-BY' } })
    expect((document.getElementById('s') as HTMLSelectElement).value).toBe('by')
    const described = await f.host.handle({ op: 'describe', conversation_id: 4, args: { snapshot_id: snap.snapshot_id, ref: byName('Sign in').ref } })
    expect(described.element).toMatchObject({ submits_form: true, form_method: 'post', form_has_password: true, is_password: false })
  })

  it('describes the form of an input, so Enter in it is classified', async () => {
    const f = fixture()
    await navigate(f, 'https://example.com/', PAGE +
      '<form method="GET" action="/s"><input aria-label="Query"></form>' +
      '<form action="/two"><input aria-label="First"><input aria-label="Second"></form>' +
      '<form method="post" action="/two-btn"><input aria-label="Name"><input aria-label="Tick" type="checkbox"><button>Go</button></form>' +
      '<input aria-label="Loose">')
    const snap = await f.host.handle({ op: 'snapshot', conversation_id: 4, args: {} })
    const refOf = (name: string) => (snap.elements as { ref: string; name: string }[]).find((e) => e.name === name)!.ref
    const describe = async (name: string) => (await f.host.handle({ op: 'describe', conversation_id: 4, args: { snapshot_id: snap.snapshot_id, ref: refOf(name) } })).element
    expect(await describe('User')).toMatchObject({ tag: 'input', submits_form: true, form_method: 'post', form_has_password: true })
    expect(await describe('Password')).toMatchObject({ is_password: true, submits_form: true, form_has_password: true })
    expect(await describe('Query')).toMatchObject({ submits_form: true, form_method: 'get', form_has_password: false })
    expect(await describe('First')).toMatchObject({ submits_form: false, form_method: 'get' })
    expect(await describe('Name')).toMatchObject({ submits_form: true, form_method: 'post' })
    expect(await describe('Tick')).toMatchObject({ submits_form: false, form_method: 'post' })
    expect(await describe('License')).toMatchObject({ tag: 'select', submits_form: false, form_has_password: false })
    const loose = await describe('Loose') as Record<string, unknown>
    expect(loose).toMatchObject({ submits_form: false, form_has_password: false })
    expect(loose).not.toHaveProperty('form_method')
  })

  it('returns stale_snapshot for an old snapshot id and after the page changed', async () => {
    const f = fixture()
    await navigate(f, 'https://example.com/', PAGE)
    const first = await f.host.handle({ op: 'snapshot', conversation_id: 4, args: {} })
    const ref = (first.elements as { ref: string }[])[0].ref
    const second = await f.host.handle({ op: 'snapshot', conversation_id: 4, args: {} })
    expect(await f.host.handle({ op: 'click', conversation_id: 4, args: { snapshot_id: first.snapshot_id, ref } })).toMatchObject({ ok: false, error: 'stale_snapshot' })
    await navigate(f, 'https://example.org/', PAGE)
    expect(await f.host.handle({ op: 'click', conversation_id: 4, args: { snapshot_id: second.snapshot_id, ref } })).toMatchObject({ ok: false, error: 'stale_snapshot' })
  })
})

describe('BrowserHost halt and renderer channel', () => {
  it('halt stops loading, wakes the waiting navigation and drops queued commands', async () => {
    const f = fixture()
    await navigate(f)
    const pending = f.host.handle({ op: 'navigate', conversation_id: 4, args: { url: 'https://slow.example/' } })
    await vi.waitFor(() => expect(f.wc.loadURL).toHaveBeenCalledTimes(2))
    expect(await f.host.invoke({ op: 'halt', conversationId: 4 })).toEqual({ halted: true })
    expect(f.http.request).toHaveBeenCalledWith(expect.objectContaining({ method: 'post', url: 'http://127.0.0.1:9/computer-use/halt/4',
      headers: { 'X-Session-Token': 'tok', 'X-Gamachine-UI-Secret': 'secret' } }))
    expect(await pending).toMatchObject({ ok: false, error: 'halted' })
    expect(f.wc.stop).toHaveBeenCalled()
    expect(f.onHalt).toHaveBeenCalledWith(4)
    expect(await f.host.handle({ op: 'halt', conversation_id: 4, args: {} })).toMatchObject({ ok: true })
    expect(f.onHalt).toHaveBeenCalledTimes(2)
  })

  it('halt for a chat without a tab still posts the backend halt; an id-less halt with no acted chat does not', async () => {
    const f = fixture()
    expect(await f.host.invoke({ op: 'halt' })).toEqual({ halted: true })
    expect(f.http.request).not.toHaveBeenCalled()
    expect(await f.host.invoke({ op: 'halt', conversationId: 4 })).toEqual({ halted: true })
    expect(f.http.request).toHaveBeenCalledTimes(1)
    expect(f.http.request).toHaveBeenCalledWith(expect.objectContaining({ url: 'http://127.0.0.1:9/computer-use/halt/4' }))
    for (const conversationId of [0, -1, 1.5, '4'])
      expect(await f.host.invoke({ op: 'halt', conversationId })).toHaveProperty('error')
  })

  it('answer-card posts the decision with the UI secret and returns the backend reply', async () => {
    const f = fixture()
    f.http.request.mockResolvedValueOnce({ data: { status: 'ok' } } as any)
    expect(await f.host.invoke({ op: 'answer-card', gateId: 'b-12_x', approved: true })).toEqual({ status: 'ok' })
    expect(f.http.request).toHaveBeenCalledWith(expect.objectContaining({ method: 'post', url: 'http://127.0.0.1:9/mcp-approval-respond/b-12_x',
      data: { approved: true }, headers: { 'X-Session-Token': 'tok', 'X-Gamachine-UI-Secret': 'secret' } }))
    f.http.request.mockClear()
    for (const bad of [{ gateId: '../x', approved: true }, { gateId: '', approved: true }, { gateId: 'a'.repeat(129), approved: true },
      { gateId: 'ok', approved: 'yes' }, { gateId: 5, approved: true }])
      expect(await f.host.invoke({ op: 'answer-card', ...bad })).toHaveProperty('error')
    expect(f.http.request).not.toHaveBeenCalled()
    f.http.request.mockRejectedValueOnce(Object.assign(new Error('500'), { config: { headers: { 'X-Gamachine-UI-Secret': 'secret' } } }))
    const failed = await f.host.invoke({ op: 'answer-card', gateId: 'g1', approved: false })
    expect(failed).toEqual({ error: 'Approval request failed.' })
  })

  it('emits browser-live to the main window while commands run', async () => {
    const f = fixture()
    await navigate(f)
    expect(livePayloads(f).at(-1)).toEqual({ active: true, conversationId: 4, url: 'https://example.com/', title: 'Page', loading: false, userControl: false,
      tabs: [{ id: 1, url: 'https://example.com/', title: 'Page', loading: false, favicon: '', agent: true }], activeTabId: 1 })
  })

  it('validates bounds and visibility before touching the view', async () => {
    const f = fixture()
    expect(await f.host.invoke({ op: 'set-bounds', conversationId: 4, bounds: { x: 1, y: 2, width: 300.4, height: 200 } })).toEqual({ bounds: { x: 1, y: 2, width: 300, height: 200 } })
    for (const bounds of [{ x: -1, y: 0, width: 1, height: 1 }, { x: 0, y: 0, width: Infinity, height: 1 }, { x: 0, y: 0, width: '5', height: 1 }, null])
      expect(await f.host.invoke({ op: 'set-bounds', conversationId: 4, bounds })).toHaveProperty('error')
    expect(await f.host.invoke({ op: 'set-bounds', bounds: { x: 1, y: 2, width: 3, height: 4 } })).toHaveProperty('error')
    expect(await f.host.invoke({ op: 'visible', conversationId: 4, visible: 'yes' })).toHaveProperty('error')
    expect(await f.host.invoke({ op: 'visible', visible: true })).toHaveProperty('error')
    expect(await f.host.invoke({ op: 'visible', conversationId: 4, visible: true })).toEqual({ visible: true })
    await navigate(f)
    expect(f.view.setBounds).toHaveBeenCalledWith({ x: 1, y: 2, width: 300, height: 200 })
    expect(f.view.setVisible).toHaveBeenCalledWith(true)
    await f.host.invoke({ op: 'visible', conversationId: 4, visible: false })
    expect(f.view.setVisible).toHaveBeenLastCalledWith(false)
    expect(await f.host.invoke({ op: 'status', conversationId: 4 })).toMatchObject({ active: true, conversationId: 4, visible: false, userControl: false })
    expect(await f.host.invoke({ op: 'status', conversationId: 5 })).toMatchObject({ active: false, conversationId: 5 })
    for (const conversationId of [undefined, 0, -3, 2.5, '4'])
      expect(await f.host.invoke({ op: 'status', conversationId })).toHaveProperty('error')
    expect(await f.host.invoke('halt')).toHaveProperty('error')
  })

  it('settings reads with the session token; enable and disable also send the UI secret, which never reaches the renderer', async () => {
    const f = fixture()
    expect(await f.host.invoke({ op: 'settings' })).toEqual({ enabled: true, running: false })
    expect(f.http.request.mock.calls[0][0]).toMatchObject({ method: 'get', url: 'http://127.0.0.1:9/browser/settings', headers: { 'X-Session-Token': 'tok' } })
    expect(f.http.request.mock.calls[0][0].headers).not.toHaveProperty('X-Gamachine-UI-Secret')
    const enabled = await f.host.invoke({ op: 'enable' })
    expect(enabled).toEqual({ enabled: true })
    expect(f.http.request).toHaveBeenCalledWith(expect.objectContaining({ method: 'put', url: 'http://127.0.0.1:9/browser/settings', data: { enabled: true },
      headers: { 'X-Session-Token': 'tok', 'X-Gamachine-UI-Secret': 'secret' } }))
    expect(await f.host.invoke({ op: 'disable' })).toEqual({ enabled: false })
    f.http.request.mockRejectedValueOnce(Object.assign(new Error('500'), { config: { headers: { 'X-Gamachine-UI-Secret': 'secret' } } }))
    const failed = await f.host.invoke({ op: 'enable' })
    expect(JSON.stringify(failed)).not.toContain('secret')
    expect(failed).toHaveProperty('error')
  })
})

describe('BrowserHost runtime report', () => {
  it('reports its listener when the backend does not know it and running=false on shutdown', async () => {
    const f = fixture()
    f.host.setRuntime({ port: 4321, token: 'tkn' })
    await f.host.reconcile()
    expect(f.http.request).toHaveBeenCalledWith(expect.objectContaining({ method: 'post', url: 'http://127.0.0.1:9/browser/runtime',
      data: { running: true, port: 4321, token: 'tkn' }, headers: { 'X-Session-Token': 'tok', 'X-Gamachine-UI-Secret': 'secret' } }))
    f.http.request.mockClear()
    f.http.request.mockResolvedValueOnce({ data: { enabled: true, runtime: { running: true, port: 4321, token: 'tkn' } } })
    await f.host.reconcile()
    expect(f.http.request).toHaveBeenCalledTimes(1)
    await navigate(f)
    await f.host.shutdown()
    expect(f.http.request).toHaveBeenLastCalledWith(expect.objectContaining({ method: 'post', url: 'http://127.0.0.1:9/browser/runtime', data: { running: false, port: null, token: null } }))
    expect(f.wc.close).toHaveBeenCalled()
    expect(f.win.contentView.removeChildView).toHaveBeenCalledWith(f.view)
  })
})

describe('BrowserHost tabs per chat', () => {
  it('gives two chats two views on one session, each with its own snapshot ids and visibility', async () => {
    const f = fixture()
    await navigate(f, 'https://a.example/', '<button>A</button>', 4)
    await navigate(f, 'https://b.example/', '<button>B</button>', 7)
    expect(f.createView).toHaveBeenCalledTimes(2)
    expect(new Set((f.fromPartition.mock.calls as unknown as [string][]).map(([p]) => p))).toEqual(new Set(['persist:gamachine-agent-browser']))
    expect((f.createView.mock.calls[1] as any)[0].webPreferences.session).toBe(f.session)
    // Hardening is session-wide and runs once.
    expect(f.session.setPermissionRequestHandler).toHaveBeenCalledTimes(1)
    const [a, b] = f.views
    expect(a.webContents.loadURL).toHaveBeenCalledWith('https://a.example/')
    expect(a.webContents.loadURL).not.toHaveBeenCalledWith('https://b.example/')
    expect((await f.host.handle({ op: 'snapshot', conversation_id: 4, args: {} })).snapshot_id).toBe('s1')
    expect((await f.host.handle({ op: 'snapshot', conversation_id: 7, args: {} })).snapshot_id).toBe('s1')
    expect(await f.host.invoke({ op: 'visible', conversationId: 7, visible: true })).toEqual({ visible: true })
    expect(b.setVisible).toHaveBeenLastCalledWith(true)
    expect(a.setVisible).toHaveBeenLastCalledWith(false)
    await f.host.invoke({ op: 'visible', conversationId: 4, visible: true })
    expect(a.setVisible).toHaveBeenLastCalledWith(true)
    expect(b.setVisible).toHaveBeenLastCalledWith(false)
    expect(await f.host.invoke({ op: 'status', conversationId: 7 })).toMatchObject({ active: true, conversationId: 7, url: 'https://b.example/', visible: false })
  })

  it('halting chat A leaves chat B loading and posts only A to the backend', async () => {
    const f = fixture()
    await navigate(f, 'https://a.example/', '', 4)
    await navigate(f, 'https://b.example/', '', 7)
    const [a, b] = f.views
    const pendingA = f.host.handle({ op: 'navigate', conversation_id: 4, args: { url: 'https://slow-a.example/' } })
    const pendingB = f.host.handle({ op: 'navigate', conversation_id: 7, args: { url: 'https://slow-b.example/' } })
    await vi.waitFor(() => expect(b.webContents.loadURL).toHaveBeenCalledWith('https://slow-b.example/'))
    await vi.waitFor(() => expect(a.webContents.loadURL).toHaveBeenCalledWith('https://slow-a.example/'))
    f.http.request.mockClear()
    expect(await f.host.invoke({ op: 'halt', conversationId: 4 })).toEqual({ halted: true })
    expect(await pendingA).toMatchObject({ ok: false, error: 'halted' })
    expect(f.onHalt).toHaveBeenLastCalledWith(4)
    expect(a.webContents.stop).toHaveBeenCalled()
    expect(b.webContents.stop).not.toHaveBeenCalled()
    expect(f.http.request.mock.calls.map(([c]: [any]) => c.url)).toEqual(['http://127.0.0.1:9/computer-use/halt/4'])
    b.webContents.finish('https://slow-b.example/')
    expect(await pendingB).toMatchObject({ ok: true, url: 'https://slow-b.example/' })
  })

  it('host.halt() without an id stops every tab the agent used', async () => {
    const f = fixture()
    await navigate(f, 'https://a.example/', '', 4)
    await navigate(f, 'https://b.example/', '', 7)
    await f.host.invoke({ op: 'open', conversationId: 9 })
    f.http.request.mockClear()
    f.host.halt()
    await vi.waitFor(() => expect(f.http.request).toHaveBeenCalledTimes(2))
    expect(f.http.request.mock.calls.map(([c]: [any]) => c.url).sort()).toEqual(['http://127.0.0.1:9/computer-use/halt/4', 'http://127.0.0.1:9/computer-use/halt/7'])
    expect(f.onHalt).toHaveBeenLastCalledWith(null)
  })

  it('caps live tabs, destroying the least recently used hidden one', async () => {
    const f = fixture()
    expect(MAX_TABS).toBe(8)
    await f.host.invoke({ op: 'open', conversationId: 1 })
    // Chat 1 stays shown and is the oldest; agent commands do not change what is shown.
    for (let id = 2; id <= 9; id++) await f.host.handle({ op: 'scroll', conversation_id: id, args: { direction: 'down' } })
    expect(f.createView).toHaveBeenCalledTimes(9)
    const two = f.views[1]
    expect(two.webContents.close).toHaveBeenCalled()
    expect(f.win.contentView.removeChildView).toHaveBeenCalledWith(two)
    expect(livePayloads(f)).toContainEqual(expect.objectContaining({ active: false, conversationId: 2 }))
    expect(f.views[0].webContents.close).not.toHaveBeenCalled()
    expect(await f.host.invoke({ op: 'panel-status', conversationId: 1 })).toMatchObject({ active: true, visible: true })
    expect(await f.host.invoke({ op: 'panel-status', conversationId: 2 })).toMatchObject({ active: false, conversationId: 2 })
    for (let id = 3; id <= 9; id++) expect((await f.host.invoke({ op: 'panel-status', conversationId: id }) as any).active).toBe(true)
  })

  it('never evicts a tab under user control, even when the cap is exceeded', async () => {
    const f = fixture()
    for (let id = 1; id <= 8; id++) await f.host.handle({ op: 'scroll', conversation_id: id, args: { direction: 'down' } })
    await f.host.invoke({ op: 'take-over', conversationId: 1 })
    await f.host.handle({ op: 'scroll', conversation_id: 9, args: { direction: 'down' } })
    expect(f.views[0].webContents.close).not.toHaveBeenCalled()
    expect(f.views[1].webContents.close).toHaveBeenCalled()
    for (let id = 1; id <= 9; id++) await f.host.invoke({ op: 'take-over', conversationId: id })
    await f.host.handle({ op: 'scroll', conversation_id: 10, args: { direction: 'down' } }).catch(() => {})
    f.views.forEach((v: any, i: number) => { if (i !== 1) expect(v.webContents.close).not.toHaveBeenCalled() })
  })

  it('close forgets a deleted chat for the global halt; evict keeps it', async () => {
    const f = fixture()
    await navigate(f, 'https://a.example/', '', 4)
    await navigate(f, 'https://b.example/', '', 7)
    await f.host.invoke({ op: 'close', conversationId: 4 })
    f.http.request.mockClear()
    f.host.halt()
    await vi.waitFor(() => expect(f.http.request).toHaveBeenCalledTimes(1))
    expect(f.http.request.mock.calls.map(([c]: [any]) => c.url)).toEqual(['http://127.0.0.1:9/computer-use/halt/7'])
  })
})

describe('BrowserHost user operations', () => {
  it('open creates and shows the chat tab and emits it as active; close emits it inactive', async () => {
    const f = fixture()
    expect(await f.host.invoke({ op: 'open', conversationId: 3 })).toMatchObject({ active: true, conversationId: 3, userControl: false })
    expect(f.createView).toHaveBeenCalledTimes(1)
    expect(f.view.setVisible).toHaveBeenLastCalledWith(true)
    expect(f.wc.loadURL).not.toHaveBeenCalled()
    expect(livePayloads(f).at(-1)).toMatchObject({ active: true, conversationId: 3 })
    // Every open is a new tab of the user's own.
    expect(await f.host.invoke({ op: 'open', conversationId: 3, url: 'https://example.com/' })).toMatchObject({ active: true })
    expect(f.createView).toHaveBeenCalledTimes(2)
    expect(f.views[1].webContents.loadURL).toHaveBeenCalledWith('https://example.com/')
    expect(await f.host.invoke({ op: 'close', conversationId: 3 })).toMatchObject({ active: false, conversationId: 3 })
    expect(f.wc.close).toHaveBeenCalled()
    expect(f.views[1].webContents.close).toHaveBeenCalled()
    expect(livePayloads(f).at(-1)).toEqual({ active: false, conversationId: 3, url: '', title: '', loading: false, userControl: false, tabs: [], activeTabId: null })
  })

  it('navigate applies the agent URL policy and needs no card', async () => {
    const f = fixture()
    for (const url of ['file:///etc/passwd', 'http://127.0.0.1:8000/', 'javascript:alert(1)', 'https://internal.corp/', '', 5]) {
      expect(await f.host.invoke({ op: 'navigate', conversationId: 3, url })).toEqual({ error: 'url_not_allowed' })
      expect(await f.host.invoke({ op: 'open', conversationId: 3, url })).toEqual({ error: 'url_not_allowed' })
    }
    expect(f.createView).not.toHaveBeenCalled()
    expect(await f.host.invoke({ op: 'navigate', conversationId: 3, url: 'https://example.com/' })).toMatchObject({ active: true, conversationId: 3 })
    expect(f.wc.loadURL).toHaveBeenCalledWith('https://example.com/')
    expect(livePayloads(f).at(-1)).toMatchObject({ active: true, conversationId: 3 })
    expect(f.http.request).not.toHaveBeenCalled()
    expect(await f.host.invoke({ op: 'navigate', url: 'https://example.com/' })).toHaveProperty('error')
  })

  it('back, forward and reload act on the chat tab only', async () => {
    const f = fixture()
    expect(await f.host.invoke({ op: 'back', conversationId: 3 })).toEqual({ error: 'no_tab' })
    await f.host.invoke({ op: 'open', conversationId: 3 })
    expect(await f.host.invoke({ op: 'back', conversationId: 3 })).toEqual({ error: 'no_history' })
    f.wc.navigationHistory.canGoBack = () => true
    expect(await f.host.invoke({ op: 'back', conversationId: 3 })).toMatchObject({ active: true })
    expect(f.wc.navigationHistory.goBack).toHaveBeenCalled()
    expect(await f.host.invoke({ op: 'forward', conversationId: 3 })).toEqual({ error: 'no_history' })
    await f.host.invoke({ op: 'reload', conversationId: 3 })
    expect(f.wc.reload).toHaveBeenCalled()
  })

  it('user input takes the tab over and agent commands get user_control until release', async () => {
    const f = fixture()
    await navigate(f)
    f.wc.emit('input-event', {}, { type: 'mouseMove' })
    expect((await f.host.invoke({ op: 'status', conversationId: 4 }) as any).userControl).toBe(false)
    f.wc.emit('input-event', {}, { type: 'mouseDown' })
    expect(livePayloads(f).at(-1)).toMatchObject({ conversationId: 4, userControl: true })
    f.wc.loadURL.mockClear()
    f.wc.executeJavaScriptInIsolatedWorld.mockClear()
    expect(await f.host.handle({ op: 'navigate', conversation_id: 4, args: { url: 'https://example.org/' } }))
      .toEqual({ ok: false, error: 'user_control', url: 'https://example.com/', title: 'Page' })
    expect(await f.host.handle({ op: 'snapshot', conversation_id: 4, args: {} })).toMatchObject({ ok: false, error: 'user_control' })
    expect(f.host.userControlReply({ op: 'click', conversation_id: 4 })).toMatchObject({ ok: false, error: 'user_control' })
    expect(f.host.userControlReply({ op: 'halt', conversation_id: 4 })).toBeNull()
    expect(f.host.userControlReply({ op: 'click', conversation_id: 7 })).toBeNull()
    expect(f.wc.loadURL).not.toHaveBeenCalled()
    expect(f.wc.executeJavaScriptInIsolatedWorld).not.toHaveBeenCalled()
    expect(await f.host.invoke({ op: 'release', conversationId: 4 })).toMatchObject({ userControl: false })
    expect(livePayloads(f).at(-1)).toMatchObject({ conversationId: 4, userControl: false })
    expect(await f.host.handle({ op: 'scroll', conversation_id: 4, args: { direction: 'down' } })).toMatchObject({ ok: true })
    f.wc.emit('before-input-event', {}, { type: 'keyDown' })
    expect((await f.host.invoke({ op: 'status', conversationId: 4 }) as any).userControl).toBe(true)
    await f.host.invoke({ op: 'release', conversationId: 4 })
    expect(await f.host.invoke({ op: 'take-over', conversationId: 4 })).toMatchObject({ userControl: true })
    expect(await f.host.invoke({ op: 'take-over', conversationId: 5 })).toEqual({ error: 'no_tab' })
  })

  it("the agent's own key presses do not count as the user taking over", async () => {
    const f = fixture()
    await navigate(f)
    f.wc.sendInputEvent.mockImplementation((e: { type: string }) => {
      f.wc.emit('before-input-event', {}, { type: e.type })
      f.wc.emit('input-event', {}, { type: e.type === 'keyDown' ? 'rawKeyDown' : e.type })
    })
    expect(await f.host.handle({ op: 'press', conversation_id: 4, args: { key: 'Enter' } })).toMatchObject({ ok: true })
    expect(f.wc.focus).toHaveBeenCalled()
    expect((await f.host.invoke({ op: 'status', conversationId: 4 }) as any).userControl).toBe(false)
    f.clock.now += 5_000
    f.wc.emit('before-input-event', {}, { type: 'keyDown' })
    expect((await f.host.invoke({ op: 'status', conversationId: 4 }) as any).userControl).toBe(true)
    f.wc.focus.mockClear()
    expect(await f.host.handle({ op: 'press', conversation_id: 4, args: { key: 'Tab' } })).toMatchObject({ ok: false, error: 'user_control' })
    expect(f.wc.focus).not.toHaveBeenCalled()
  })

  it('clear-browsing-data clears the session, closes every tab and reports a runtime change', async () => {
    const f = fixture()
    f.host.setRuntime({ port: 4321, token: 'tkn' })
    await navigate(f, 'https://a.example/', '', 4)
    await f.host.invoke({ op: 'open', conversationId: 7 })
    f.http.request.mockClear()
    expect(await f.host.invoke({ op: 'clear-browsing-data' })).toEqual({ cleared: true })
    expect(f.session.clearStorageData).toHaveBeenCalled()
    expect(f.session.clearCache).toHaveBeenCalled()
    expect(f.views[0].webContents.close).toHaveBeenCalled()
    expect(f.views[1].webContents.close).toHaveBeenCalled()
    expect(livePayloads(f)).toContainEqual(expect.objectContaining({ active: false, conversationId: 4 }))
    expect(livePayloads(f)).toContainEqual(expect.objectContaining({ active: false, conversationId: 7 }))
    expect(await f.host.invoke({ op: 'status', conversationId: 4 })).toMatchObject({ active: false })
    const posts = f.http.request.mock.calls.map(([c]: [any]) => c).filter((c: any) => c.url.endsWith('/browser/runtime'))
    expect(posts.map((c: any) => c.data)).toEqual([{ running: true, port: 4321, token: 'tkn', cleared: true }])
    expect(posts[0].headers).toEqual({ 'X-Session-Token': 'tok', 'X-Gamachine-UI-Secret': 'secret' })
    f.session.clearCache.mockRejectedValueOnce(new Error('disk'))
    expect(await f.host.invoke({ op: 'clear-browsing-data' })).toEqual({ error: 'Clearing browsing data failed.' })
  })

  it('refuses open, navigate and agent commands while a clear runs and leaves no tab afterwards', async () => {
    const f = fixture()
    let release: () => void = () => {}
    f.session.clearStorageData.mockImplementationOnce(() => new Promise<void>((resolve) => { release = resolve }))
    const clearing = f.host.invoke({ op: 'clear-browsing-data' })
    await vi.waitFor(() => expect(f.session.clearStorageData).toHaveBeenCalled())
    expect(await f.host.invoke({ op: 'open', conversationId: 5 })).toEqual({ error: 'clearing' })
    expect(await f.host.invoke({ op: 'navigate', conversationId: 5, url: 'https://example.com/' })).toEqual({ error: 'clearing' })
    expect(await f.host.handle({ op: 'scroll', conversation_id: 5, args: { direction: 'down' } })).toMatchObject({ ok: false, error: 'clearing' })
    release()
    expect(await clearing).toEqual({ cleared: true })
    expect(f.createView).not.toHaveBeenCalled()
    expect(await f.host.invoke({ op: 'status', conversationId: 5 })).toMatchObject({ active: false })
    expect(await f.host.invoke({ op: 'open', conversationId: 5 })).toMatchObject({ active: true })
  })

  it('does not create a tab for an open whose address lookup was still running when a clear began', async () => {
    const f = fixture()
    let resolveLookup: (v: any) => void = () => {}
    f.lookup.mockImplementationOnce(() => new Promise((resolve) => { resolveLookup = resolve }))
    const opening = f.host.invoke({ op: 'open', conversationId: 5, url: 'https://slow.example/' })
    await vi.waitFor(() => expect(f.lookup).toHaveBeenCalled())
    let release: () => void = () => {}
    f.session.clearStorageData.mockImplementationOnce(() => new Promise<void>((resolve) => { release = resolve }))
    const clearing = f.host.invoke({ op: 'clear-browsing-data' })
    await vi.waitFor(() => expect(f.session.clearStorageData).toHaveBeenCalled())
    resolveLookup([{ address: '93.184.216.34', family: 4 }])
    expect(await opening).toEqual({ error: 'clearing' })
    release()
    await clearing
    expect(f.createView).not.toHaveBeenCalled()
  })

  it('reports a failed clear as cleared, since the logins may already be gone', async () => {
    const f = fixture()
    f.session.clearCache.mockRejectedValueOnce(new Error('disk'))
    expect(await f.host.invoke({ op: 'clear-browsing-data' })).toEqual({ error: 'Clearing browsing data failed.' })
    const posts = f.http.request.mock.calls.map(([c]: [any]) => c).filter((c: any) => c.url.endsWith('/browser/runtime'))
    expect(posts.map((c: any) => c.data.cleared)).toEqual([true])
  })
})


const key = (k: string, mods: Record<string, boolean> = {}, extra: Record<string, unknown> = {}) => ({ type: 'keyDown', key: k, ...mods, ...extra })
const press = (wc: any, input: any) => {
  const event = { preventDefault: vi.fn() }
  wc.emit('before-input-event', event, input)
  return event
}
const status = async (f: ReturnType<typeof fixture>, conversationId = 4) => (await f.host.invoke({ op: 'panel-status', conversationId })) as any

describe('BrowserHost agent tab and user tabs', () => {
  async function withUserTab() {
    const f = fixture('win32')
    await f.host.invoke({ op: 'open', conversationId: 4, url: 'https://mine.example/' })
    const user = f.views[0].webContents
    user.finish('https://mine.example/', 'Mine')
    return { f, user }
  }

  it('gives the agent its own tab and never touches the one the user opened', async () => {
    const { f, user } = await withUserTab()
    await navigate(f, 'https://agent.example/', '<button>Go</button>', 4)
    const agent = f.views[1].webContents
    expect(f.createView).toHaveBeenCalledTimes(2)
    user.loadURL.mockClear()
    for (const op of ['snapshot', 'scroll', 'screenshot'])
      await f.host.handle({ op, conversation_id: 4, args: { direction: 'down', snapshot_id: 's1', ref: 'e1' } })
    // A tab id smuggled into the command changes nothing.
    const smuggled = f.host.handle({ op: 'navigate', conversation_id: 4, tab_id: 1, tabId: 1, args: { url: 'https://other.example/', tab_id: 1, tabId: 1 } } as any)
    await vi.waitFor(() => expect(agent.loadURL).toHaveBeenLastCalledWith('https://other.example/'))
    agent.finish('https://other.example/')
    await smuggled
    expect(user.loadURL).not.toHaveBeenCalled()
    expect(user.executeJavaScriptInIsolatedWorld).not.toHaveBeenCalled()
    expect(user.capturePage).not.toHaveBeenCalled()
    expect(user.sendInputEvent).not.toHaveBeenCalled()
    const live = await status(f)
    expect(live.tabs.map((t: any) => [t.agent, t.url])).toEqual([[false, 'https://mine.example/'], [true, 'https://other.example/']])
    expect(live.activeTabId).toBe(live.tabs[0].id)
  })

  it('does not select the agent tab over the tab the user is looking at, and shows only the selected tab', async () => {
    const { f } = await withUserTab()
    await f.host.invoke({ op: 'visible', conversationId: 4, visible: true })
    await navigate(f, 'https://agent.example/', '', 4)
    expect(f.views[0].setVisible).toHaveBeenLastCalledWith(true)
    expect(f.views[1].setVisible).toHaveBeenLastCalledWith(false)
    const ids = (await status(f)).tabs.map((t: any) => t.id)
    await f.host.invoke({ op: 'select', conversationId: 4, tabId: ids[1] })
    expect(f.views[1].setVisible).toHaveBeenLastCalledWith(true)
    expect(f.views[0].setVisible).toHaveBeenLastCalledWith(false)
    expect(await f.host.invoke({ op: 'select', conversationId: 4, tabId: 999 })).toEqual({ error: 'no_tab' })
    expect(await f.host.invoke({ op: 'select', conversationId: 7, tabId: ids[0] })).toEqual({ error: 'no_tab' })
  })

  it('user input in a user tab does not take the agent tab over; take-over only reaches the agent tab', async () => {
    const { f, user } = await withUserTab()
    expect(await f.host.invoke({ op: 'take-over', conversationId: 4 })).toEqual({ error: 'no_tab' })
    user.emit('input-event', {}, { type: 'mouseDown' })
    user.emit('before-input-event', {}, key('a'))
    await navigate(f, 'https://agent.example/', '', 4)
    expect((await status(f)).userControl).toBe(false)
    expect(f.host.userControlReply({ op: 'click', conversation_id: 4 })).toBeNull()
    expect(await f.host.handle({ op: 'scroll', conversation_id: 4, args: { direction: 'down' } })).toMatchObject({ ok: true })
    f.views[1].webContents.emit('input-event', {}, { type: 'mouseDown' })
    expect((await status(f)).userControl).toBe(true)
    // The user keeps working in their own tab while the agent is refused.
    expect(await f.host.handle({ op: 'scroll', conversation_id: 4, args: { direction: 'down' } })).toMatchObject({ ok: false, error: 'user_control' })
    expect(await f.host.invoke({ op: 'navigate', conversationId: 4, tabId: (await status(f)).tabs[0].id, url: 'https://next.example/' })).toMatchObject({ active: true })
    expect(user.loadURL).toHaveBeenLastCalledWith('https://next.example/')
  })

  it('applies the same navigation policy to user tabs, opens and reopens', async () => {
    const { f, user } = await withUserTab()
    for (const url of ['file:///etc/passwd', 'http://127.0.0.1:8000/', 'https://internal.corp/'])
      expect(await f.host.invoke({ op: 'open', conversationId: 4, url })).toEqual({ error: 'url_not_allowed' })
    const bad = { url: 'http://192.168.1.1/admin', isMainFrame: true, preventDefault: vi.fn() }
    user.emit('will-navigate', bad)
    expect(bad.preventDefault).toHaveBeenCalled()
    expect(user.setWindowOpenHandler).toBeDefined()
    expect(user.openHandler({ url: 'https://x.example/', disposition: 'new-window' })).toEqual({ action: 'deny' })
    expect(f.createView).toHaveBeenCalledTimes(1)
    // A link opened in a new tab becomes a user tab, after the same check, when the user just clicked in the visible chat.
    await f.host.invoke({ op: 'visible', conversationId: 4, visible: true })
    user.emit('input-event', {}, { type: 'mouseDown' })
    user.openHandler({ url: 'https://link.example/', disposition: 'foreground-tab' })
    await vi.waitFor(() => expect(f.createView).toHaveBeenCalledTimes(2))
    user.openHandler({ url: 'http://127.0.0.1/', disposition: 'foreground-tab' })
    await new Promise((r) => setTimeout(r, 10))
    expect(f.createView).toHaveBeenCalledTimes(2)
    // The agent's pop-up is only reported.
    await navigate(f, 'https://agent.example/', '', 4)
    expect(f.views[2].webContents.openHandler({ url: 'https://ads.example/', disposition: 'foreground-tab' })).toEqual({ action: 'deny' })
    expect(f.createView).toHaveBeenCalledTimes(3)
  })

  it('Ctrl+W on the agent tab closes it, wakes its load, and the next agent command makes a fresh agent tab', async () => {
    const f = fixture('win32')
    await navigate(f, 'https://agent.example/', '', 4)
    const first = f.views[0].webContents
    const pending = f.host.handle({ op: 'navigate', conversation_id: 4, args: { url: 'https://slow.example/' } })
    await vi.waitFor(() => expect(first.loadURL).toHaveBeenCalledTimes(2))
    const event = press(first, key('w', { control: true }))
    expect(event.preventDefault).toHaveBeenCalled()
    expect(await pending).toMatchObject({ ok: false, error: 'halted' })
    expect(first.close).toHaveBeenCalled()
    expect((await status(f)).active).toBe(false)
    await navigate(f, 'https://again.example/', '', 4)
    expect(f.createView).toHaveBeenCalledTimes(2)
    const live = await status(f)
    expect(live.tabs).toHaveLength(1)
    expect(live.tabs[0]).toMatchObject({ agent: true, url: 'https://again.example/' })
    // Reopening the closed agent page yields a tab of the user's, not a second agent tab.
    press(f.views[1].webContents, key('t', { control: true, shift: true }))
    await vi.waitFor(() => expect(f.createView).toHaveBeenCalledTimes(3))
    expect(f.views[2].webContents.loadURL).toHaveBeenCalledWith('https://agent.example/')
    expect((await status(f)).tabs.map((t: any) => t.agent)).toEqual([true, false])
    // Agent commands still land on the agent tab.
    await f.host.handle({ op: 'scroll', conversation_id: 4, args: { direction: 'down' } })
    expect(f.views[2].webContents.executeJavaScriptInIsolatedWorld).not.toHaveBeenCalled()
  })

  it('claims Ctrl+T/W/Shift+T/1-9 on win32 and leaves Cmd and clipboard keys alone', async () => {
    const f = fixture('win32')
    await navigate(f, 'https://a.example/', '', 4)
    const wc = f.views[0].webContents
    // Claimed keys are not the user taking the agent tab over.
    expect(press(wc, key('t', { control: true })).preventDefault).toHaveBeenCalled()
    await vi.waitFor(async () => expect((await status(f)).tabs).toHaveLength(2))
    expect((await status(f)).userControl).toBe(false)
    expect(press(wc, key('t', { control: true })).preventDefault).toHaveBeenCalled()
    await vi.waitFor(async () => expect((await status(f)).tabs).toHaveLength(3))
    const ids = (await status(f)).tabs.map((t: any) => t.id)
    expect((await status(f)).activeTabId).toBe(ids[2])
    press(wc, key('1', { control: true }))
    expect((await status(f)).activeTabId).toBe(ids[0])
    press(wc, key('2', { control: true }))
    expect((await status(f)).activeTabId).toBe(ids[1])
    press(wc, key('9', { control: true }))
    expect((await status(f)).activeTabId).toBe(ids[2])
    press(wc, key('7', { control: true }))
    expect((await status(f)).activeTabId).toBe(ids[2])
    // Ctrl+W in a user tab closes that tab, and Ctrl+Shift+T brings it back.
    press(f.views[2].webContents, key('w', { control: true }))
    expect((await status(f)).tabs).toHaveLength(2)
    expect((await status(f)).activeTabId).toBe(ids[1])
    // Everything else is typing in the page: never intercepted (the agent tab counts it as the user's).
    for (const k of ['c', 'v', 'x', 'a', 'z', 'f', 'l']) expect(press(wc, key(k, { control: true })).preventDefault).not.toHaveBeenCalled()
    expect(press(wc, key('t', { meta: true })).preventDefault).not.toHaveBeenCalled()
    expect(press(wc, key('w', { control: true, alt: true })).preventDefault).not.toHaveBeenCalled()
    expect(press(wc, key('w', { control: true, shift: true })).preventDefault).not.toHaveBeenCalled()
    expect(press(wc, key('w', { control: true }, { isAutoRepeat: true })).preventDefault).not.toHaveBeenCalled()
    expect(press(wc, { type: 'keyUp', key: 'w', control: true }).preventDefault).not.toHaveBeenCalled()
    expect((await status(f)).tabs).toHaveLength(2)
  })

  it('claims Cmd on darwin and ignores Ctrl there', async () => {
    const f = fixture('darwin')
    await navigate(f, 'https://a.example/', '', 4)
    const wc = f.views[0].webContents
    expect(press(wc, key('t', { control: true })).preventDefault).not.toHaveBeenCalled()
    expect(press(wc, key('c', { meta: true })).preventDefault).not.toHaveBeenCalled()
    expect(press(wc, key('t', { meta: true })).preventDefault).toHaveBeenCalled()
    await vi.waitFor(async () => expect((await status(f)).tabs).toHaveLength(2))
    press(wc, key('1', { meta: true }))
    expect((await status(f)).activeTabId).toBe((await status(f)).tabs[0].id)
    press(f.views[1].webContents, key('w', { meta: true }))
    expect((await status(f)).tabs).toHaveLength(1)
    press(wc, key('t', { meta: true, shift: true }))
    await vi.waitFor(async () => expect((await status(f)).tabs).toHaveLength(1))
    // Nothing was closed after loading (the new tab had no page), so there is nothing to reopen.
    expect(f.createView).toHaveBeenCalledTimes(2)
  })

  it('reopens the last closed tab, newest first, and drops the stack when the chat is deleted', async () => {
    const f = fixture('win32')
    await f.host.invoke({ op: 'open', conversationId: 4, url: 'https://one.example/' })
    f.views[0].webContents.finish('https://one.example/')
    await f.host.invoke({ op: 'open', conversationId: 4, url: 'https://two.example/' })
    f.views[1].webContents.finish('https://two.example/')
    const ids = (await status(f)).tabs.map((t: any) => t.id)
    await f.host.invoke({ op: 'close-tab', conversationId: 4, tabId: ids[0] })
    await f.host.invoke({ op: 'close-tab', conversationId: 4, tabId: ids[1] })
    expect(await f.host.invoke({ op: 'close-tab', conversationId: 4, tabId: ids[1] })).toEqual({ error: 'no_tab' })
    expect(await f.host.invoke({ op: 'reopen-tab', conversationId: 4 })).toMatchObject({ active: true })
    expect(f.views[2].webContents.loadURL).toHaveBeenCalledWith('https://two.example/')
    await f.host.invoke({ op: 'close', conversationId: 4 })
    expect(await f.host.invoke({ op: 'reopen-tab', conversationId: 4 })).toEqual({ error: 'nothing_to_reopen' })
  })

  it('caps tabs per chat', async () => {
    const f = fixture('win32')
    for (let i = 0; i < MAX_CHAT_TABS; i++) await f.host.invoke({ op: 'open', conversationId: 4 })
    expect(await f.host.invoke({ op: 'open', conversationId: 4 })).toEqual({ error: 'too_many_tabs' })
  })
})

describe('BrowserHost eviction and clearing with several tabs', () => {
  it('evicts the least recently used hidden tab, user tabs included, but never the shown chat, a controlled tab or one mid-command', async () => {
    const f = fixture('win32')
    await f.host.invoke({ op: 'open', conversationId: 1 })
    await f.host.invoke({ op: 'open', conversationId: 1 })
    for (let id = 2; id <= 4; id++) await f.host.invoke({ op: 'open', conversationId: id })
    for (let id = 5; id <= 6; id++) await f.host.handle({ op: 'scroll', conversation_id: id, args: { direction: 'down' } })
    await f.host.invoke({ op: 'visible', conversationId: 1, visible: true })
    expect(f.createView).toHaveBeenCalledTimes(7)
    // Chat 2's user tab is the oldest hidden one.
    await f.host.invoke({ op: 'take-over', conversationId: 5 })
    // open shows the chat it opens in, so the panel of chat 1 is put back on screen after each one.
    const openElsewhere = async (id: number) => {
      await f.host.invoke({ op: 'open', conversationId: id })
      await f.host.invoke({ op: 'visible', conversationId: 1, visible: true })
    }
    await openElsewhere(7)
    await openElsewhere(7)
    expect(f.views[2].webContents.close).toHaveBeenCalled()
    expect(f.views[3].webContents.close).not.toHaveBeenCalled()
    expect(f.views[0].webContents.close).not.toHaveBeenCalled()
    expect(f.views[1].webContents.close).not.toHaveBeenCalled()
    expect(f.views[5].webContents.close).not.toHaveBeenCalled()
    expect((await status(f, 2)).active).toBe(false)
    // An agent tab with a command in flight is skipped too.
    const pending = f.host.handle({ op: 'navigate', conversation_id: 6, args: { url: 'https://busy.example/' } })
    await vi.waitFor(() => expect(f.views[6].webContents.loadURL).toHaveBeenCalled())
    for (let id = 8; id <= 12; id++) await openElsewhere(id)
    expect(f.views[3].webContents.close).toHaveBeenCalled()
    expect(f.views[4].webContents.close).toHaveBeenCalled()
    expect(f.views[5].webContents.close).not.toHaveBeenCalled()
    expect(f.views[6].webContents.close).not.toHaveBeenCalled()
    f.views[6].webContents.finish('https://busy.example/')
    await pending
  })

  it('clear-browsing-data closes agent and user tabs of every chat, history and the reopen stacks', async () => {
    const f = fixture('win32')
    await navigate(f, 'https://a.example/', '', 4)
    await f.host.invoke({ op: 'open', conversationId: 4, url: 'https://mine.example/' })
    await f.host.invoke({ op: 'open', conversationId: 7 })
    const ids = (await status(f)).tabs.map((t: any) => t.id)
    f.views[1].webContents.finish('https://mine.example/')
    await f.host.invoke({ op: 'close-tab', conversationId: 4, tabId: ids[1] })
    expect(await f.host.invoke({ op: 'clear-browsing-data' })).toEqual({ cleared: true })
    for (const v of f.views) expect(v.webContents.close).toHaveBeenCalled()
    expect(f.history.clear).toHaveBeenCalled()
    expect((await status(f, 4)).tabs).toEqual([])
    expect((await status(f, 7)).tabs).toEqual([])
    expect(await f.host.invoke({ op: 'reopen-tab', conversationId: 4 })).toEqual({ error: 'nothing_to_reopen' })
  })

  it('records visits and serves the history through the history op', async () => {
    const f = fixture('win32')
    await navigate(f, 'https://a.example/', '', 4)
    f.wc.emit('did-navigate', {}, 'https://a.example/')
    expect(f.history.visit).toHaveBeenCalledWith('https://a.example/', 'Page')
    f.fetch.mockResolvedValue(new Response(new Uint8Array([1, 2, 3]), { headers: { 'content-type': 'image/png' } }))
    f.wc.emit('page-favicon-updated', {}, ['data:image/png;base64,AA', 'https://a.example/fav.ico'])
    await vi.waitFor(() => expect(f.history.update).toHaveBeenCalledWith('https://a.example/', { favicon: 'data:image/png;base64,AQID' }))
    expect((await status(f)).tabs[0].favicon).toBe('data:image/png;base64,AQID')
    expect(await f.host.invoke({ op: 'history' })).toEqual({ entries: [expect.objectContaining({ url: 'https://a.example/' })] })
  })
})

describe('BrowserHost halt during the address lookup and synthetic keys', () => {
  it('a navigate awaiting its hostname check does not load after its chat was halted, with several tabs open', async () => {
    const f = fixture('win32')
    await f.host.invoke({ op: 'open', conversationId: 1 })
    const user = f.views[0].webContents
    let release: (v: any) => void = () => {}
    f.lookup.mockImplementationOnce(() => new Promise((resolve) => { release = resolve }))
    const pending = f.host.handle({ op: 'navigate', conversation_id: 1, args: { url: 'https://slow.example/' } })
    await vi.waitFor(() => expect(f.lookup).toHaveBeenCalled())
    await f.host.invoke({ op: 'halt', conversationId: 1 })
    release([{ address: '93.184.216.34', family: 4 }])
    expect(await pending).toMatchObject({ ok: false, error: 'halted' })
    expect(f.views[1].webContents.loadURL).not.toHaveBeenCalled()
    // The user's tab is not the agent's and is left alone by the halt.
    expect(user.stop).not.toHaveBeenCalled()
    expect(f.views[1].webContents.stop).toHaveBeenCalled()
  })

  it('a global halt also stops a navigate that is still in its lookup', async () => {
    const f = fixture('win32')
    let release: (v: any) => void = () => {}
    f.lookup.mockImplementationOnce(() => new Promise((resolve) => { release = resolve }))
    const pending = f.host.handle({ op: 'navigate', conversation_id: 2, args: { url: 'https://slow.example/' } })
    await vi.waitFor(() => expect(f.lookup).toHaveBeenCalled())
    f.host.halt()
    release([{ address: '93.184.216.34', family: 4 }])
    expect(await pending).toMatchObject({ ok: false, error: 'halted' })
    expect(f.views[0].webContents.loadURL).not.toHaveBeenCalled()
  })

  it('a real key right after the agent key takes the tab over; the host\'s own echo does not', async () => {
    const f = fixture('win32')
    await f.host.invoke({ op: 'open', conversationId: 4 })
    await navigate(f, 'https://a.example/', '', 4)
    const agent = f.views[1].webContents
    agent.sendInputEvent.mockImplementation((e: { type: string; keyCode: string }) => {
      agent.emit('before-input-event', { preventDefault: vi.fn() }, { type: e.type, key: e.keyCode })
      agent.emit('input-event', {}, { type: e.type === 'keyDown' ? 'rawKeyDown' : e.type })
    })
    expect(await f.host.handle({ op: 'press', conversation_id: 4, args: { key: 'Tab' } })).toMatchObject({ ok: true })
    expect((await status(f)).userControl).toBe(false)
    f.clock.now += 100
    agent.emit('before-input-event', { preventDefault: vi.fn() }, { type: 'keyDown', key: 'a' })
    expect((await status(f)).userControl).toBe(true)
    expect(await f.host.handle({ op: 'scroll', conversation_id: 4, args: { direction: 'down' } })).toMatchObject({ ok: false, error: 'user_control' })
  })

  it('an echo is consumed once: a second identical key in the window is the user\'s, and a lost echo expires', async () => {
    const f = fixture('win32')
    await navigate(f, 'https://a.example/', '', 4)
    const wc = f.wc
    wc.sendInputEvent.mockImplementation(() => {})
    await f.host.handle({ op: 'press', conversation_id: 4, args: { key: 'Tab' } })
    f.clock.now += 50
    wc.emit('before-input-event', { preventDefault: vi.fn() }, { type: 'keyDown', key: 'Tab' })
    wc.emit('input-event', {}, { type: 'rawKeyDown' })
    expect((await status(f)).userControl).toBe(false)
    wc.emit('before-input-event', { preventDefault: vi.fn() }, { type: 'keyDown', key: 'Tab' })
    expect((await status(f)).userControl).toBe(true)
    await f.host.invoke({ op: 'release', conversationId: 4 })
    await f.host.handle({ op: 'press', conversation_id: 4, args: { key: 'Tab' } })
    f.clock.now += 1_500
    wc.emit('input-event', {}, { type: 'rawKeyDown' })
    expect((await status(f)).userControl).toBe(true)
  })
})

const later = () => new Promise<void>((resolve) => setTimeout(resolve, 5))
const pendingLookup = (f: ReturnType<typeof fixture>) => {
  let release: (v: any) => void = () => {}
  f.lookup.mockImplementationOnce(() => new Promise((resolve) => { release = resolve }))
  return () => release([{ address: '93.184.216.34', family: 4 }])
}

describe('BrowserHost tab caps', () => {
  it('keeps the per-chat cap within the global one and refuses opens past it', async () => {
    const f = fixture('win32')
    expect(MAX_CHAT_TABS).toBeLessThanOrEqual(MAX_TABS)
    for (let n = 0; n < MAX_CHAT_TABS; n++) expect(await f.host.invoke({ op: 'open', conversationId: 4 })).not.toHaveProperty('error')
    expect(await f.host.invoke({ op: 'open', conversationId: 4 })).toEqual({ error: 'too_many_tabs' })
    expect(f.views.filter((v: any) => !v.webContents.destroyed)).toHaveLength(MAX_CHAT_TABS)
  })

  it('refuses an agent tab when the visible chat fills the cap, instead of exceeding it', async () => {
    const f = fixture('win32')
    for (let n = 0; n < MAX_TABS; n++) await f.host.invoke({ op: 'open', conversationId: 4 })
    expect(await f.host.handle({ op: 'press', conversation_id: 4, args: { key: 'Tab' } })).toMatchObject({ ok: false, error: 'too_many_tabs' })
    // Another chat has no tab to give up either: every tab belongs to the chat on screen.
    expect(await f.host.handle({ op: 'press', conversation_id: 5, args: { key: 'Tab' } })).toMatchObject({ ok: false, error: 'too_many_tabs' })
    expect(f.views.filter((v: any) => !v.webContents.destroyed)).toHaveLength(MAX_TABS)
  })
})

describe('BrowserHost work that outlives its chat or a clear', () => {
  it.each(['close', 'clear-browsing-data'])('an open still in its lookup makes no tab after %s', async (op) => {
    const f = fixture('win32')
    const finish = pendingLookup(f)
    const pending = f.host.invoke({ op: 'open', conversationId: 4, url: 'https://old.example/' })
    await vi.waitFor(() => expect(f.lookup).toHaveBeenCalled())
    await f.host.invoke({ op, conversationId: 4 })
    finish()
    expect(await pending).toHaveProperty('error')
    expect(f.createView).not.toHaveBeenCalled()
    expect(((await f.host.invoke({ op: 'panel-status', conversationId: 4 })) as any).tabs).toEqual([])
  })

  it('a user navigate still in its lookup does not load after the chat was closed', async () => {
    const f = fixture('win32')
    await f.host.invoke({ op: 'open', conversationId: 4 })
    const finish = pendingLookup(f)
    const pending = f.host.invoke({ op: 'navigate', conversationId: 4, url: 'https://old.example/' })
    await vi.waitFor(() => expect(f.lookup).toHaveBeenCalled())
    await f.host.invoke({ op: 'close', conversationId: 4 })
    finish()
    expect(await pending).toEqual({ error: 'chat_closed' })
    expect(f.views[0].webContents.loadURL).not.toHaveBeenCalled()
  })

  it('closing a chat ends its running command as halted and tells the command server to drop the queue', async () => {
    const f = fixture('win32')
    const running = f.host.handle({ op: 'navigate', conversation_id: 4, args: { url: 'https://busy.example/' } })
    await vi.waitFor(() => expect(f.views[0]?.webContents.loadURL).toHaveBeenCalled())
    await f.host.invoke({ op: 'close', conversationId: 4 })
    expect(f.onHalt).toHaveBeenCalledWith(4)
    expect(await running).toMatchObject({ ok: false, error: 'halted' })
    expect(((await f.host.invoke({ op: 'panel-status', conversationId: 4 })) as any).tabs).toEqual([])
  })

  it('closing the agent tab settles a navigate that is still in its lookup as halted, at once', async () => {
    const f = fixture('win32')
    const finish = pendingLookup(f)
    let settled = false
    const pending = f.host.handle({ op: 'navigate', conversation_id: 4, args: { url: 'https://slow.example/' } }).then((r) => { settled = true; return r })
    await vi.waitFor(() => expect(f.lookup).toHaveBeenCalled())
    const id = (await status(f)).tabs[0].id
    await f.host.invoke({ op: 'close-tab', conversationId: 4, tabId: id })
    await later()
    expect(settled).toBe(true)
    finish()
    expect(await pending).toMatchObject({ ok: false, error: 'halted' })
    expect(f.views[0].webContents.loadURL).not.toHaveBeenCalled()
  })

  it('an agent command that awaited a script answers halted when Stop came meanwhile', async () => {
    const f = fixture('win32')
    await f.host.handle({ op: 'scroll', conversation_id: 4, args: { direction: 'down' } })
    const wc = f.views[0].webContents
    let finish: (v: unknown) => void = () => {}
    wc.executeJavaScriptInIsolatedWorld.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
    const pending = f.host.handle({ op: 'snapshot', conversation_id: 4, args: {} })
    await vi.waitFor(() => expect(wc.executeJavaScriptInIsolatedWorld).toHaveBeenCalledTimes(2))
    await f.host.invoke({ op: 'halt', conversationId: 4 })
    finish({ text: 'late', elements: [] })
    expect(await pending).toMatchObject({ ok: false, error: 'halted' })
  })

  it('a clear that fails to erase the history file reports an error, not cleared', async () => {
    const f = fixture('win32')
    f.history.clear.mockResolvedValue(false)
    expect(await f.host.invoke({ op: 'clear-browsing-data' })).toEqual({ error: 'Clearing browsing data failed.' })
    expect(f.session.clearStorageData).toHaveBeenCalled()
    f.history.clear.mockResolvedValue(true)
    expect(await f.host.invoke({ op: 'clear-browsing-data' })).toEqual({ cleared: true })
  })

  it('reopening puts the entry back when the tab cannot be opened', async () => {
    const f = fixture('win32')
    await f.host.invoke({ op: 'open', conversationId: 4, url: 'https://gone.example/' })
    f.views[0].webContents.finish('https://gone.example/')
    await f.host.invoke({ op: 'close-tab', conversationId: 4, tabId: 1 })
    for (let n = 0; n < MAX_CHAT_TABS; n++) await f.host.invoke({ op: 'open', conversationId: 4 })
    expect(await f.host.invoke({ op: 'reopen-tab', conversationId: 4 })).toEqual({ error: 'too_many_tabs' })
    const last = (await status(f)).tabs.at(-1)
    await f.host.invoke({ op: 'close-tab', conversationId: 4, tabId: last.id })
    expect(await f.host.invoke({ op: 'reopen-tab', conversationId: 4 })).not.toHaveProperty('error')
    expect(f.views.at(-1).webContents.loadURL).toHaveBeenCalledWith('https://gone.example/')
    expect(await f.host.invoke({ op: 'reopen-tab', conversationId: 4 })).toEqual({ error: 'nothing_to_reopen' })
  })
})

describe('BrowserHost tabs a page opens', () => {
  async function withPage() {
    const f = fixture('win32')
    await f.host.invoke({ op: 'open', conversationId: 4, url: 'https://page.example/' })
    const page = f.views[0].webContents
    page.finish('https://page.example/')
    await f.host.invoke({ op: 'visible', conversationId: 4, visible: true })
    const popup = (url = 'https://link.example/') => page.openHandler({ url, disposition: 'foreground-tab' })
    const count = () => f.createView.mock.calls.length
    return { f, page, popup, count }
  }

  it('is denied without a recent key or mouse press in that page', async () => {
    const { f, page, popup, count } = await withPage()
    expect(popup()).toEqual({ action: 'deny' })
    await later()
    expect(count()).toBe(1)
    page.emit('input-event', {}, { type: 'mouseDown' })
    f.clock.now += 1_001
    popup()
    await later()
    expect(count()).toBe(1)
    page.emit('before-input-event', { preventDefault: vi.fn() }, { type: 'keyDown', key: 'a' })
    f.clock.now += 900
    popup()
    await vi.waitFor(() => expect(count()).toBe(2))
  })

  it('is denied while its chat is not the one on screen', async () => {
    const { f, page, popup, count } = await withPage()
    await f.host.invoke({ op: 'visible', conversationId: 4, visible: false })
    page.emit('input-event', {}, { type: 'mouseDown' })
    popup()
    await later()
    expect(count()).toBe(1)
    await f.host.invoke({ op: 'visible', conversationId: 9, visible: true })
    popup()
    await later()
    expect(count()).toBe(1)
  })

  it('does not bring its chat forward when another chat came on screen during the check', async () => {
    const { f, page, popup, count } = await withPage()
    page.emit('input-event', {}, { type: 'mouseDown' })
    const finish = pendingLookup(f)
    popup()
    await vi.waitFor(() => expect(f.lookup).toHaveBeenCalled())
    await f.host.invoke({ op: 'visible', conversationId: 9, visible: true })
    finish()
    await vi.waitFor(() => expect(count()).toBe(2))
    expect(((await f.host.invoke({ op: 'panel-status', conversationId: 9 })) as any).visible).toBe(true)
    expect(((await f.host.invoke({ op: 'panel-status', conversationId: 4 })) as any).visible).toBe(false)
  })

  it('is limited to three per ten seconds in a chat', async () => {
    const { f, page, popup, count } = await withPage()
    for (let n = 0; n < 5; n++) {
      page.emit('input-event', {}, { type: 'mouseDown' })
      popup()
      await later()
    }
    expect(count()).toBe(4)
    f.clock.now += 10_001
    page.emit('input-event', {}, { type: 'mouseDown' })
    popup()
    await vi.waitFor(() => expect(count()).toBe(5))
  })

  it('is only a gesture when it was not the host\'s own key echo', async () => {
    const { f, popup, count } = await withPage()
    await f.host.handle({ op: 'scroll', conversation_id: 4, args: { direction: 'down' } })
    const agent = f.views[1].webContents
    agent.sendInputEvent.mockImplementation((e: { type: string; keyCode: string }) => {
      agent.emit('before-input-event', { preventDefault: vi.fn() }, { type: e.type, key: e.keyCode })
    })
    await f.host.handle({ op: 'press', conversation_id: 4, args: { key: 'Tab' } })
    expect(agent.openHandler({ url: 'https://x.example/', disposition: 'foreground-tab' })).toEqual({ action: 'deny' })
    await later()
    expect(count()).toBe(2)
    expect(popup()).toEqual({ action: 'deny' })
  })
})

describe('BrowserHost favicons', () => {
  const png = (bytes = 3, type = 'image/png') => new Response(new Uint8Array(bytes).fill(7), { headers: { 'content-type': type } })
  async function withPage() {
    const f = fixture('win32')
    await navigate(f, 'https://a.example/', '', 4)
    f.wc.emit('did-navigate', {}, 'https://a.example/')
    const icons = async (...hrefs: string[]) => {
      f.wc.emit('page-favicon-updated', {}, hrefs)
      await later()
      return ((await status(f)).tabs[0].favicon) as string
    }
    return { f, icons }
  }

  it('is fetched without credentials through the session, as a small data: image', async () => {
    const { f, icons } = await withPage()
    f.fetch.mockResolvedValue(png())
    expect(await icons('https://a.example/f.png')).toBe('data:image/png;base64,BwcH')
    expect(f.fetch).toHaveBeenCalledWith('https://a.example/f.png', expect.objectContaining({ credentials: 'omit', redirect: 'manual' }))
  })

  it.each([
    ['an address with credentials', 'https://u:p@a.example/f.png', png()],
    ['a private address', 'https://internal.corp/f.png', png()],
    ['a non-image type', 'https://a.example/f.png', png(3, 'text/html')],
    ['a large image', 'https://a.example/f.png', png(32 * 1024 + 1)],
    ['an empty body', 'https://a.example/f.png', png(0)],
  ])('is not stored for %s', async (_name, href, response) => {
    const { f, icons } = await withPage()
    f.fetch.mockResolvedValue(response)
    expect(await icons(href)).toBe('')
    expect(f.history.update).not.toHaveBeenCalled()
  })

  it('follows a redirect only to an address that passes the same check', async () => {
    const { f, icons } = await withPage()
    f.fetch.mockResolvedValueOnce(new Response(null, { status: 302, headers: { location: 'https://internal.corp/f.png' } }))
    expect(await icons('https://a.example/f.png')).toBe('')
    expect(f.fetch).toHaveBeenCalledTimes(1)
    f.fetch.mockResolvedValueOnce(new Response(null, { status: 302, headers: { location: '/real.png' } })).mockResolvedValueOnce(png())
    expect(await icons('https://a.example/f.png')).toBe('data:image/png;base64,BwcH')
    expect(f.fetch).toHaveBeenLastCalledWith('https://a.example/real.png', expect.anything())
  })

  it('is dropped when the page navigated away while it loaded', async () => {
    const { f, icons } = await withPage()
    let finish: (r: Response) => void = () => {}
    f.fetch.mockImplementation(() => new Promise<Response>((resolve) => { finish = resolve }))
    const pending = icons('https://a.example/f.png')
    await vi.waitFor(() => expect(f.fetch).toHaveBeenCalled())
    f.wc.emit('did-navigate', {}, 'https://a.example/next')
    finish(png())
    expect(await pending).toBe('')
  })
})

describe('BrowserHost status for a paired phone and the agent tab after a message', () => {
  it('status carries only the agent tab\'s page, panel-status every tab', async () => {
    const f = fixture('win32')
    await f.host.invoke({ op: 'open', conversationId: 4, url: 'https://private-account.example/' })
    f.views[0].webContents.finish('https://private-account.example/', 'Account')
    await navigate(f, 'https://agent.example/', '', 4)
    const remote = (await f.host.invoke({ op: 'status', conversationId: 4 })) as any
    expect(remote).toMatchObject({ active: true, url: 'https://agent.example/', title: 'Page', loading: false, userControl: false })
    expect(remote).not.toHaveProperty('tabs')
    expect(JSON.stringify(remote)).not.toContain('private-account')
    expect(((await f.host.invoke({ op: 'panel-status', conversationId: 4 })) as any).tabs).toHaveLength(2)
    expect(((await f.host.invoke({ op: 'status', conversationId: 6 })) as any)).toMatchObject({ active: false, url: '' })
  })

  async function twoTabs() {
    const f = fixture('win32')
    await navigate(f, 'https://agent.example/', '', 4)
    await f.host.invoke({ op: 'open', conversationId: 4, url: 'https://mine.example/' })
    const agent = f.views[0].webContents
    const mine = f.views[1].webContents
    mine.finish('https://mine.example/')
    return { f, agent, mine }
  }

  it('the tab selected when the user sends becomes the agent tab, and the old one the user\'s', async () => {
    const { f, agent, mine } = await twoTabs()
    expect((await status(f)).tabs.map((t: any) => t.agent)).toEqual([true, false])
    await f.host.invoke({ op: 'release', conversationId: 4 })
    expect((await status(f)).tabs.map((t: any) => t.agent)).toEqual([false, true])
    agent.loadURL.mockClear()
    await f.host.handle({ op: 'scroll', conversation_id: 4, args: { direction: 'down' } })
    expect(mine.executeJavaScriptInIsolatedWorld).toHaveBeenCalled()
    expect((await f.host.handle({ op: 'snapshot', conversation_id: 4 })).url).toBe('https://mine.example/')
    expect(agent.loadURL).not.toHaveBeenCalled()
  })

  it('switching tabs while the agent works does not move it; the next message does', async () => {
    const { f, agent, mine } = await twoTabs()
    await f.host.invoke({ op: 'release', conversationId: 4 })
    const [first, second] = (await status(f)).tabs
    await f.host.invoke({ op: 'select', conversationId: 4, tabId: first.id })
    const before = mine.executeJavaScriptInIsolatedWorld.mock.calls.length
    const oldAgentBefore = agent.executeJavaScriptInIsolatedWorld.mock.calls.length
    await f.host.handle({ op: 'scroll', conversation_id: 4, args: { direction: 'down' } })
    expect(mine.executeJavaScriptInIsolatedWorld.mock.calls.length).toBeGreaterThan(before)
    expect(agent.executeJavaScriptInIsolatedWorld.mock.calls.length).toBe(oldAgentBefore)
    expect((await status(f)).tabs.find((t: any) => t.agent).id).toBe(second.id)
    await f.host.invoke({ op: 'release', conversationId: 4 })
    expect((await status(f)).tabs.find((t: any) => t.agent).id).toBe(first.id)
  })

  it('user input on the adopted tab takes it over', async () => {
    const { f, mine } = await twoTabs()
    await f.host.invoke({ op: 'release', conversationId: 4 })
    mine.emit('before-input-event', { preventDefault: vi.fn() }, { type: 'keyDown', key: 'a' })
    expect((await status(f)).userControl).toBe(true)
    expect(await f.host.handle({ op: 'scroll', conversation_id: 4, args: { direction: 'down' } })).toMatchObject({ error: 'user_control' })
  })

  it('adopts the selected tab even when the user had taken the old agent tab over', async () => {
    const { f, agent, mine } = await twoTabs()
    agent.emit('before-input-event', { preventDefault: vi.fn() }, { type: 'keyDown', key: 'a' })
    expect((await status(f)).userControl).toBe(true)
    await f.host.invoke({ op: 'release', conversationId: 4 })
    const live = await status(f)
    expect(live.userControl).toBe(false)
    expect(live.tabs.map((t: any) => t.agent)).toEqual([false, true])
    const before = agent.executeJavaScriptInIsolatedWorld.mock.calls.length
    await f.host.handle({ op: 'scroll', conversation_id: 4, args: { direction: 'down' } })
    expect(mine.executeJavaScriptInIsolatedWorld).toHaveBeenCalled()
    expect(agent.executeJavaScriptInIsolatedWorld.mock.calls.length).toBe(before)
  })

  it('does not adopt while a command runs on the old agent tab, even after user control', async () => {
    const { f, agent } = await twoTabs()
    const running = f.host.handle({ op: 'navigate', conversation_id: 4, args: { url: 'https://busy.example/' } })
    await vi.waitFor(() => expect(agent.loadURL).toHaveBeenLastCalledWith('https://busy.example/'))
    await f.host.invoke({ op: 'take-over', conversationId: 4 })
    await f.host.invoke({ op: 'release', conversationId: 4 })
    expect((await status(f)).tabs.map((t: any) => t.agent)).toEqual([true, false])
    agent.finish('https://busy.example/')
    await running
  })

  it('a chat without an agent tab yet gets the selected one; a running command keeps its tab', async () => {
    const f = fixture('win32')
    await f.host.invoke({ op: 'open', conversationId: 4, url: 'https://mine.example/' })
    f.views[0].webContents.finish('https://mine.example/')
    await f.host.invoke({ op: 'release', conversationId: 4 })
    expect((await status(f)).tabs.map((t: any) => t.agent)).toEqual([true])
    await f.host.invoke({ op: 'open', conversationId: 4, url: 'https://other.example/' })
    const running = f.host.handle({ op: 'navigate', conversation_id: 4, args: { url: 'https://busy.example/' } })
    await vi.waitFor(() => expect(f.views[0].webContents.loadURL).toHaveBeenLastCalledWith('https://busy.example/'))
    await f.host.invoke({ op: 'release', conversationId: 4 })
    expect((await status(f)).tabs.map((t: any) => t.agent)).toEqual([true, false])
    f.views[0].webContents.finish('https://busy.example/')
    await running
  })
})
