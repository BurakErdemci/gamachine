import { EventEmitter } from 'events'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { BROWSER_PARTITION, BROWSER_WORLD_ID, BrowserHost, NAVIGATION_TIMEOUT_MS, type BrowserHostDeps } from '../main/helpers/browser-host'

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

function fixture() {
  const wc = fakeWebContents()
  const view: any = { webContents: wc, setBounds: vi.fn(), setVisible: vi.fn() }
  const session: any = new EventEmitter()
  session.setPermissionRequestHandler = vi.fn()
  session.setPermissionCheckHandler = vi.fn()
  session.webRequest = { onBeforeRequest: vi.fn(), onResponseStarted: vi.fn(), onCompleted: vi.fn() }
  const win: any = { isDestroyed: () => false, contentView: { addChildView: vi.fn(), removeChildView: vi.fn() }, webContents: { send: vi.fn() } }
  const http = { request: vi.fn(async (config: any) => {
    if (config.method === 'get') return { data: { enabled: true, runtime: { running: false, port: null, token: null } } }
    if (config.method === 'put') return { data: { enabled: config.data.enabled } }
    return { data: {} }
  }) }
  const lookup = vi.fn(async (host: string) => [{ address: host.startsWith('internal') ? '10.0.0.8' : '93.184.216.34', family: 4 }])
  const createView = vi.fn(() => view)
  const fromPartition = vi.fn(() => session)
  const onHalt = vi.fn()
  const deps: BrowserHostDeps = {
    window: () => win, createView, fromPartition, http, baseUrl: () => 'http://127.0.0.1:9', appToken: 'tok', uiSecret: 'secret',
    lookup, onHalt,
  }
  const host = new BrowserHost(deps)
  return { host, wc, view, session, win, http, lookup, createView, fromPartition, onHalt }
}

async function navigate(f: ReturnType<typeof fixture>, url = 'https://example.com/', html = '') {
  const reply = f.host.handle({ op: 'navigate', conversation_id: 4, args: { url } })
  await vi.waitFor(() => expect(f.wc.loadURL).toHaveBeenCalledWith(url))
  delete (globalThis as any).__gmBrowser
  document.body.innerHTML = html
  f.wc.finish(url)
  return reply
}

describe('BrowserHost view and session', () => {
  it('creates one hardened view on its own partition on the first command', async () => {
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
    const reply = await f.host.handle({ op: 'scroll', args: { direction: 'down' } })
    expect(reply.popup_blocked).toBe('https://ads.example/')
    expect((await f.host.handle({ op: 'scroll', args: { direction: 'down' } })).popup_blocked).toBeUndefined()
  })
})

describe('BrowserHost navigation refusal', () => {
  it('refuses disallowed URLs before loading and hosts that resolve privately', async () => {
    const f = fixture()
    for (const url of ['file:///etc/passwd', 'http://127.0.0.1:8000/', 'http://localhost/', 'javascript:alert(1)', 'https://internal.corp/']) {
      expect(await f.host.handle({ op: 'navigate', args: { url } })).toMatchObject({ ok: false, error: 'url_not_allowed' })
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
    const reply = f.host.handle({ op: 'navigate', args: { url: 'https://example.com/' } })
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
    expect(await f.host.handle({ op: 'navigate', args: { url: 'https://rebind.example/again' } })).toMatchObject({ ok: false, error: 'url_not_allowed' })
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
    const pending = f.host.handle({ op: 'navigate', args: { url: 'https://rebind.example/' } })
    await vi.waitFor(() => expect(f.wc.loadURL).toHaveBeenCalledWith('https://rebind.example/'))
    started({ url: 'https://rebind.example/', ip: '127.0.0.1' })
    expect(await pending).toMatchObject({ ok: false, error: 'private_address' })
    expect(f.wc.stop).toHaveBeenCalled()
    expect(f.wc.loadURL).toHaveBeenLastCalledWith('about:blank')
    const payloads = f.win.webContents.send.mock.calls.filter(([channel]: [string]) => channel === 'browser-live').map(([, p]: [string, any]) => p)
    expect(payloads.some((p: any) => p.error === 'private_address')).toBe(true)
    // The host is now blocked even though its lookup answers publicly again.
    expect(await f.host.handle({ op: 'navigate', args: { url: 'https://rebind.example/' } })).toMatchObject({ ok: false, error: 'url_not_allowed' })
    f.wc.loadURL.mockClear()
    completed({ url: 'https://other.example/', ip: '[::1]' })
    expect(f.wc.loadURL).toHaveBeenCalledWith('about:blank')
  })

  it('reports a navigation that never finishes as a timeout', async () => {
    vi.useFakeTimers()
    try {
      const f = fixture()
      const reply = f.host.handle({ op: 'navigate', args: { url: 'https://slow.example/' } })
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
    const snap = await f.host.handle({ op: 'snapshot', args: {} })
    expect(snap.ok).toBe(true)
    expect(f.wc.executeJavaScriptInIsolatedWorld.mock.calls[0][0]).toBe(BROWSER_WORLD_ID)
    const elements = snap.elements as { ref: string; name: string; kind: string; role: string; value?: string }[]
    const byName = (name: string) => elements.find((e) => e.name === name)!
    expect(byName('Walk cycle')).toMatchObject({ role: 'link', kind: 'link', href: 'https://example.com/walk' })
    expect(byName('Password').value).toBeUndefined()
    const clicked = vi.fn()
    document.getElementById('b')!.addEventListener('click', clicked)
    expect(await f.host.handle({ op: 'click', args: { snapshot_id: snap.snapshot_id, ref: byName('Search').ref } })).toMatchObject({ ok: true })
    expect(clicked).toHaveBeenCalled()
    expect(f.wc.executeJavaScriptInIsolatedWorld.mock.calls.at(-1)[2]).toBe(true)
    await f.host.handle({ op: 'fill', args: { snapshot_id: snap.snapshot_id, ref: byName('User').ref, value: 'walker' } })
    expect((document.getElementById('u') as HTMLInputElement).value).toBe('walker')
    await f.host.handle({ op: 'select', args: { snapshot_id: snap.snapshot_id, ref: byName('License').ref, option: 'CC-BY' } })
    expect((document.getElementById('s') as HTMLSelectElement).value).toBe('by')
    const described = await f.host.handle({ op: 'describe', args: { snapshot_id: snap.snapshot_id, ref: byName('Sign in').ref } })
    expect(described.element).toMatchObject({ submits_form: true, form_method: 'post', form_has_password: true, is_password: false })
  })

  it('describes the form of an input, so Enter in it is classified', async () => {
    const f = fixture()
    await navigate(f, 'https://example.com/', PAGE +
      '<form method="GET" action="/s"><input aria-label="Query"></form>' +
      '<form action="/two"><input aria-label="First"><input aria-label="Second"></form>' +
      '<form method="post" action="/two-btn"><input aria-label="Name"><input aria-label="Tick" type="checkbox"><button>Go</button></form>' +
      '<input aria-label="Loose">')
    const snap = await f.host.handle({ op: 'snapshot', args: {} })
    const refOf = (name: string) => (snap.elements as { ref: string; name: string }[]).find((e) => e.name === name)!.ref
    const describe = async (name: string) => (await f.host.handle({ op: 'describe', args: { snapshot_id: snap.snapshot_id, ref: refOf(name) } })).element
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
    const first = await f.host.handle({ op: 'snapshot', args: {} })
    const ref = (first.elements as { ref: string }[])[0].ref
    const second = await f.host.handle({ op: 'snapshot', args: {} })
    expect(await f.host.handle({ op: 'click', args: { snapshot_id: first.snapshot_id, ref } })).toMatchObject({ ok: false, error: 'stale_snapshot' })
    await navigate(f, 'https://example.org/', PAGE)
    expect(await f.host.handle({ op: 'click', args: { snapshot_id: second.snapshot_id, ref } })).toMatchObject({ ok: false, error: 'stale_snapshot' })
  })
})

describe('BrowserHost halt and renderer channel', () => {
  it('halt stops loading, wakes the waiting navigation and drops queued commands', async () => {
    const f = fixture()
    await navigate(f)
    const pending = f.host.handle({ op: 'navigate', args: { url: 'https://slow.example/' } })
    await vi.waitFor(() => expect(f.wc.loadURL).toHaveBeenCalledTimes(2))
    expect(await f.host.invoke({ op: 'halt' })).toEqual({ halted: true })
    expect(f.http.request).toHaveBeenCalledWith(expect.objectContaining({ method: 'post', url: 'http://127.0.0.1:9/computer-use/halt/4',
      headers: { 'X-Session-Token': 'tok', 'X-Gamachine-UI-Secret': 'secret' } }))
    expect(await pending).toMatchObject({ ok: false, error: 'halted' })
    expect(f.wc.stop).toHaveBeenCalled()
    expect(f.onHalt).toHaveBeenCalled()
    expect(await f.host.handle({ op: 'halt', args: {} })).toMatchObject({ ok: true })
    expect(f.onHalt).toHaveBeenCalledTimes(2)
  })

  it('halt with no active conversation does not call the backend', async () => {
    const f = fixture()
    expect(await f.host.invoke({ op: 'halt' })).toEqual({ halted: true })
    expect(f.http.request).not.toHaveBeenCalled()
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
    const payloads = f.win.webContents.send.mock.calls.filter(([channel]: [string]) => channel === 'browser-live').map(([, p]: [string, unknown]) => p)
    expect(payloads.at(-1)).toEqual({ active: true, conversationId: 4, url: 'https://example.com/', title: 'Page', loading: false })
  })

  it('validates bounds and visibility before touching the view', async () => {
    const f = fixture()
    expect(await f.host.invoke({ op: 'set-bounds', bounds: { x: 1, y: 2, width: 300.4, height: 200 } })).toEqual({ bounds: { x: 1, y: 2, width: 300, height: 200 } })
    for (const bounds of [{ x: -1, y: 0, width: 1, height: 1 }, { x: 0, y: 0, width: Infinity, height: 1 }, { x: 0, y: 0, width: '5', height: 1 }, null])
      expect(await f.host.invoke({ op: 'set-bounds', bounds })).toHaveProperty('error')
    expect(await f.host.invoke({ op: 'visible', visible: 'yes' })).toHaveProperty('error')
    expect(await f.host.invoke({ op: 'visible', visible: true })).toEqual({ visible: true })
    await navigate(f)
    expect(f.view.setBounds).toHaveBeenCalledWith({ x: 1, y: 2, width: 300, height: 200 })
    expect(f.view.setVisible).toHaveBeenCalledWith(true)
    await f.host.invoke({ op: 'visible', visible: false })
    expect(f.view.setVisible).toHaveBeenLastCalledWith(false)
    expect(await f.host.invoke({ op: 'status' })).toMatchObject({ active: true, visible: false })
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
