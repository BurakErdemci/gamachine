import React from 'react'
import { EventEmitter } from 'node:events'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { BrowserHistory, type HistoryIo } from '../main/helpers/browser-history'
import { BrowserHost, siteOf, type BrowserHostDeps } from '../main/helpers/browser-host'
import { confirmDialog } from '../renderer/components/ui/ConfirmDialog'
import { BrowserUsePage } from '../renderer/components/home/settings/BrowserUsePage'

vi.mock('../renderer/components/ui/ConfirmDialog', () => ({ confirmDialog: vi.fn() }))
const confirm = confirmDialog as unknown as ReturnType<typeof vi.fn>

function memoryHistory(now: () => number) {
  const state = { file: null as string | null, writes: 0, ok: true }
  const io: HistoryIo = { read: () => state.file, write: (_f, data) => { if (!state.ok) return false; state.file = data; state.writes++ } }
  return { history: new BrowserHistory('history.json', { now, io }), state }
}

afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('BrowserHistory remove and clearSince', () => {
  it('remove drops the exact url and writes at once', async () => {
    const clock = { now: 1_000 }
    const { history, state } = memoryHistory(() => clock.now)
    history.visit('https://a.example/', 'A')
    history.visit('https://b.example/', 'B')
    expect(await history.remove('https://a.example/')).toBe(true)
    expect(history.list().map((e) => e.url)).toEqual(['https://b.example/'])
    expect(state.writes).toBe(1)
    expect(JSON.parse(state.file!).entries).toHaveLength(1)
  })

  it('remove of an unknown url writes nothing and says false', async () => {
    const { history, state } = memoryHistory(() => 1)
    history.visit('https://a.example/', 'A')
    expect(await history.remove('https://other.example/')).toBe(false)
    expect(state.writes).toBe(0)
  })

  it('clearSince removes entries visited at or after the time and keeps older ones', async () => {
    const clock = { now: 100 }
    const { history, state } = memoryHistory(() => clock.now)
    history.visit('https://old.example/')
    clock.now = 500
    history.visit('https://edge.example/')
    clock.now = 900
    history.visit('https://new.example/')
    expect(await history.clearSince(500)).toBe(true)
    expect(history.list().map((e) => e.url)).toEqual(['https://old.example/'])
    expect(JSON.parse(state.file!).entries).toHaveLength(1)
  })

  it('reports a failed write as false so the caller can tell', async () => {
    const { history, state } = memoryHistory(() => 1)
    history.visit('https://a.example/')
    state.ok = false
    expect(await history.clearSince(0)).toBe(false)
    expect(await history.remove('https://nope/')).toBe(false)
  })
})

/** A cookie store whose remove(url, name) deletes the matching cookie, like Electron's. */
function cookieJar() {
  const jar: any[] = []
  const store = {
    get: vi.fn(async () => jar.slice()),
    remove: vi.fn(async (url: string, name: string) => {
      const host = new URL(url).hostname
      const at = jar.findIndex((c) => c.domain.replace(/^\./, '') === host && c.name === name)
      if (at >= 0) jar.splice(at, 1)
    }),
  }
  return { jar, store }
}

function hostFixture(clockNow = 1_000) {
  const { jar: cookies, store } = cookieJar()
  const session: any = {
    cookies: store,
    clearStorageData: vi.fn(async () => {}),
    setPermissionRequestHandler: vi.fn(), setPermissionCheckHandler: vi.fn(),
    on: vi.fn(), webRequest: { onBeforeRequest: vi.fn(), onResponseStarted: vi.fn(), onCompleted: vi.fn() },
  }
  const clock = { now: clockNow }
  const mem = memoryHistory(() => clock.now)
  const deps = {
    window: () => null, createView: vi.fn(), fromPartition: vi.fn(() => session), http: { request: vi.fn(async () => ({ data: {} })) },
    baseUrl: () => 'http://127.0.0.1:9', appToken: 't', uiSecret: 's', now: () => clock.now, history: mem.history,
  } as unknown as BrowserHostDeps
  return { host: new BrowserHost(deps), session, cookies, clock, history: mem.history }
}

describe('browser-live history ops', () => {
  it('filters history by a case-insensitive query on url or title', async () => {
    const f = hostFixture()
    f.history.visit('https://Docs.example/guide', 'Unity Guide')
    f.history.visit('https://shop.example/', 'Cart')
    const byTitle = (await f.host.invoke({ op: 'history', query: 'unity' })) as any
    expect(byTitle.entries.map((e: any) => e.url)).toEqual(['https://docs.example/guide'])
    const byUrl = (await f.host.invoke({ op: 'history', query: 'SHOP.' })) as any
    expect(byUrl.entries).toHaveLength(1)
    expect(((await f.host.invoke({ op: 'history' })) as any).entries).toHaveLength(2)
  })

  it('history-remove deletes one page', async () => {
    const f = hostFixture()
    f.history.visit('https://a.example/', 'A')
    f.history.visit('https://b.example/', 'B')
    expect(await f.host.invoke({ op: 'history-remove', url: 'https://a.example/' })).toEqual({ ok: true, removed: true })
    expect(f.history.list().map((e) => e.url)).toEqual(['https://b.example/'])
    expect(await f.host.invoke({ op: 'history-remove' })).toEqual({ error: 'bad_args' })
    expect(await f.host.invoke({ op: 'history-remove', url: 'https://gone.example/' })).toEqual({ ok: true, removed: false })
  })

  it('history-clear removes the last hour, today (local midnight) or everything by the host clock', async () => {
    const midnight = new Date(2026, 9, 30, 0, 0, 0, 0).getTime()
    const f = hostFixture(midnight + 15 * 3_600_000)
    const visitAt = (url: string, at: number) => { f.clock.now = at; f.history.visit(url) }
    visitAt('https://yesterday.example/', midnight - 3_600_000)
    visitAt('https://morning.example/', midnight + 2 * 3_600_000)
    visitAt('https://recent.example/', midnight + 14.5 * 3_600_000)
    f.clock.now = midnight + 15 * 3_600_000
    expect(await f.host.invoke({ op: 'history-clear', range: 'hour' })).toEqual({ ok: true })
    expect(f.history.list().map((e) => e.url)).toEqual(['https://morning.example/', 'https://yesterday.example/'])
    expect(await f.host.invoke({ op: 'history-clear', range: 'day' })).toEqual({ ok: true })
    expect(f.history.list().map((e) => e.url)).toEqual(['https://yesterday.example/'])
    expect(await f.host.invoke({ op: 'history-clear', range: 'all' })).toEqual({ ok: true })
    expect(f.history.list()).toEqual([])
    expect(await f.host.invoke({ op: 'history-clear', range: 'week' })).toEqual({ error: 'bad_args' })
  })
})

describe('browser-live sites ops', () => {
  it('siteOf is the cookie domain itself without leading dots, never a guessed parent', () => {
    expect(siteOf('.accounts.google.com')).toBe('accounts.google.com')
    expect(siteOf('www.example.co.uk')).toBe('www.example.co.uk')
    expect(siteOf('.Shop.Example.com.tr')).toBe('shop.example.com.tr')
    expect(siteOf('example.com')).toBe('example.com')
    expect(siteOf('localhost')).toBe('localhost')
    expect(siteOf('127.0.0.1')).toBe('127.0.0.1')
  })

  it('groups cookies by site, sorted, with counts and no names or values', async () => {
    const f = hostFixture()
    f.cookies.push(
      { name: 'sid', value: 'SECRET1', domain: '.github.com', path: '/', secure: true, expirationDate: 100 },
      { name: 'tok', value: 'SECRET2', domain: 'gist.github.com', path: '/', secure: true, expirationDate: 300 },
      { name: 'a', value: 'SECRET3', domain: 'example.co.uk', path: '/', secure: false },
      { name: 'b', value: 'SECRET4', domain: '.shop.example.com.tr', path: '/', secure: false },
    )
    const reply = (await f.host.invoke({ op: 'sites' })) as any
    expect(reply.sites).toEqual([
      { site: 'example.co.uk', cookies: 1 },
      { site: 'github.com', cookies: 2, lastExpiry: 300 },
      { site: 'shop.example.com.tr', cookies: 1 },
    ])
    expect(JSON.stringify(reply)).not.toMatch(/SECRET|sid|tok/)
  })

  it('site-remove removes the site cookies only and clears its storage origins', async () => {
    const f = hostFixture()
    f.cookies.push(
      { name: 'sid', value: 'x', domain: '.github.com', path: '/', secure: true },
      { name: 'k', value: 'x', domain: 'gist.github.com', path: '/a', secure: false },
      { name: 'other', value: 'x', domain: '.notgithub.com', path: '/', secure: true },
    )
    expect(await f.host.invoke({ op: 'site-remove', site: 'github.com' })).toEqual({ ok: true, removed: 2 })
    expect(f.session.cookies.remove.mock.calls).toEqual([['https://github.com/', 'sid'], ['http://gist.github.com/a', 'k']])
    const origins = f.session.clearStorageData.mock.calls.map(([o]: [any]) => o.origin)
    expect(origins).toEqual(['https://github.com', 'http://github.com', 'https://www.github.com', 'http://www.github.com', 'https://gist.github.com', 'http://gist.github.com'])
    expect(f.session.clearStorageData.mock.calls[0][0].storages).toEqual(['localstorage', 'indexdb', 'serviceworkers', 'cachestorage', 'websql', 'filesystem'])
  })

  it('site-remove refuses an empty or malformed site', async () => {
    const f = hostFixture()
    for (const site of [undefined, '', 'a b', 'https://x.com', 'x.com/path', '.x.com', 5]) {
      expect(await f.host.invoke({ op: 'site-remove', site })).toEqual({ error: 'bad_args' })
    }
    expect(f.session.cookies.remove).not.toHaveBeenCalled()
    expect(f.session.clearStorageData).not.toHaveBeenCalled()
  })
})

function fakeTabView(id: number) {
  const wc: any = new EventEmitter()
  Object.assign(wc, {
    id, url: '', title: '', destroyed: false,
    isDestroyed: () => wc.destroyed, getURL: () => wc.url, getTitle: () => wc.title, isLoading: () => false,
    setWindowOpenHandler: vi.fn(), loadURL: vi.fn(async (url: string) => { wc.url = url }), stop: vi.fn(), reload: vi.fn(), focus: vi.fn(),
    close: vi.fn(() => { wc.destroyed = true }), sendInputEvent: vi.fn(),
    navigationHistory: { canGoBack: () => false, canGoForward: () => false, goBack: vi.fn(), goForward: vi.fn() },
  })
  return { webContents: wc, setBounds: vi.fn(), setVisible: vi.fn() } as any
}

/** A host with real open tabs (fake views) and a recorder for the backend reports. */
function tabFixture(history?: unknown, now?: () => number) {
  const views: any[] = []
  const { jar, store } = cookieJar()
  const session: any = Object.assign(new EventEmitter(), {
    cookies: store, clearStorageData: vi.fn(async () => {}),
    setPermissionRequestHandler: vi.fn(), setPermissionCheckHandler: vi.fn(), webRequest: { onBeforeRequest: vi.fn(), onResponseStarted: vi.fn(), onCompleted: vi.fn() },
  })
  const win: any = { isDestroyed: () => false, contentView: { addChildView: vi.fn(), removeChildView: vi.fn() }, webContents: { send: vi.fn() } }
  const http = { request: vi.fn(async () => ({ data: {} })) }
  const onHalt = vi.fn()
  const net = { lookup: async (): Promise<{ address: string; family: number }[]> => [{ address: '93.184.216.34', family: 4 }] }
  const host = new BrowserHost({
    window: () => win, createView: vi.fn(() => { const v = fakeTabView(views.length + 1); views.push(v); return v }), fromPartition: () => session,
    http, baseUrl: () => 'http://127.0.0.1:9', appToken: 't', uiSecret: 's', history, onHalt, now,
    lookup: () => net.lookup(),
  } as unknown as BrowserHostDeps)
  const open = async (conversationId: number, url: string) => {
    await host.invoke({ op: 'open', conversationId, url })
    await vi.waitFor(() => expect(views.length).toBeGreaterThan(0))
    views[views.length - 1].webContents.url = url
  }
  return { host, session, views, http, onHalt, open, jar, net }
}

describe('site-remove, turn 2', () => {
  it('clears storage of subdomains, cookie domains and history origins with ports', async () => {
    const history = { list: () => [
      { url: 'http://dev.example.com:3000/x', title: '', favicon: '', lastVisit: 1, visits: 1 },
      { url: 'https://other.org:8443/', title: '', favicon: '', lastVisit: 1, visits: 1 },
      { url: 'https://notexample.com/', title: '', favicon: '', lastVisit: 1, visits: 1 },
    ], clear: async () => true, remove: async () => true, clearSince: async () => true, visit() {}, update() {}, flush: async () => true }
    const f = tabFixture(history)
    f.jar.push({ name: 'sid', value: 'v', domain: '.app.example.com', path: '/', secure: true })
    expect(await f.host.invoke({ op: 'site-remove', site: 'example.com' })).toEqual({ ok: true, removed: 1 })
    const origins = f.session.clearStorageData.mock.calls.map((c: any) => c[0].origin)
    expect(origins).toEqual(expect.arrayContaining(['https://example.com', 'http://www.example.com', 'https://app.example.com', 'http://app.example.com', 'http://dev.example.com:3000']))
    expect(origins).not.toContain('https://other.org:8443')
    expect(origins).not.toContain('https://notexample.com')
    expect(new Set(origins).size).toBe(origins.length)
  })

  it('closes the open tabs of the site (any chat), stops those chats and leaves other tabs', async () => {
    const f = tabFixture()
    await f.open(4, 'https://app.example.com/home')
    await f.open(7, 'https://example.com/')
    await f.open(9, 'https://notexample.com/')
    expect(await f.host.invoke({ op: 'site-remove', site: 'example.com' })).toEqual({ ok: true, removed: 0 })
    expect(f.views[0].webContents.close).toHaveBeenCalled()
    expect(f.views[1].webContents.close).toHaveBeenCalled()
    expect(f.views[2].webContents.close).not.toHaveBeenCalled()
    expect(f.onHalt.mock.calls.map((c) => c[0]).sort()).toEqual([4, 7])
  })

  it('reports to the backend so the grants drop', async () => {
    const f = tabFixture()
    await f.host.invoke({ op: 'site-remove', site: 'example.com' })
    expect(f.http.request).toHaveBeenCalledWith(expect.objectContaining({ url: expect.stringContaining('/browser/runtime'), data: expect.objectContaining({ cleared: true }) }))
  })

  it('keeps going when one cookie removal throws and says partial', async () => {
    const f = tabFixture()
    f.jar.push(
      { name: 'a', value: 'v', domain: 'example.com', path: '/', secure: true },
      { name: 'b', value: 'v', domain: 'example.com', path: '/', secure: true },
    )
    f.session.cookies.remove.mockRejectedValueOnce(new Error('boom'))
    expect(await f.host.invoke({ op: 'site-remove', site: 'example.com' })).toEqual({ ok: false, error: 'site_remove_partial', removed: 1, failed: 1 })
    expect(f.session.cookies.remove).toHaveBeenCalledTimes(2)
    expect(f.session.clearStorageData).toHaveBeenCalled()
  })

  it('refuses a bare public suffix or a value without letters', async () => {
    const f = tabFixture()
    f.jar.push({ name: 'a', value: 'v', domain: 'x.co.uk', path: '/', secure: true })
    for (const site of ['com', 'co.uk', 'com.tr', '-', '--', 'uk']) expect(await f.host.invoke({ op: 'site-remove', site })).toEqual({ error: 'bad_args' })
    expect(f.session.cookies.remove).not.toHaveBeenCalled()
    expect(f.session.clearStorageData).not.toHaveBeenCalled()
    expect(await f.host.invoke({ op: 'site-remove', site: 'localhost' })).toMatchObject({ ok: true })
    expect(await f.host.invoke({ op: 'site-remove', site: 'example.co.uk' })).toMatchObject({ ok: true })
  })
})

describe('site identity, origins, races and the reopen stack, turn 3', () => {
  it('lists github.io tenants as two sites and refuses the shared parent', async () => {
    const f = hostFixture()
    f.cookies.push(
      { name: 'login', value: 'a', domain: 'alice.github.io', path: '/', secure: true },
      { name: 'login', value: 'b', domain: 'bob.github.io', path: '/', secure: true },
    )
    expect(((await f.host.invoke({ op: 'sites' })) as any).sites.map((r: any) => r.site)).toEqual(['alice.github.io', 'bob.github.io'])
    expect(await f.host.invoke({ op: 'site-remove', site: 'github.io' })).toEqual({ error: 'bad_args' })
    expect(f.session.cookies.remove).not.toHaveBeenCalled()
    expect(f.session.clearStorageData).not.toHaveBeenCalled()
    expect(await f.host.invoke({ op: 'site-remove', site: 'alice.github.io' })).toEqual({ ok: true, removed: 1 })
    expect(f.cookies.map((c) => c.domain)).toEqual(['bob.github.io'])
    const origins = f.session.clearStorageData.mock.calls.map(([o]: [any]) => o.origin)
    expect(origins.some((o: string) => o.includes('bob.github.io'))).toBe(false)
  })

  it('groups a subdomain cookie under a cookie of its parent domain only', async () => {
    const f = hostFixture()
    f.cookies.push(
      { name: 'a', value: 'x', domain: '.example.com', path: '/', secure: true },
      { name: 'b', value: 'x', domain: 'app.example.com', path: '/', secure: true },
      { name: 'c', value: 'x', domain: 'shop.other.com', path: '/', secure: true },
    )
    expect(((await f.host.invoke({ op: 'sites' })) as any).sites).toEqual([{ site: 'example.com', cookies: 2 }, { site: 'shop.other.com', cookies: 1 }])
  })

  it('clears the storage of an open tab origin that history never saw', async () => {
    const f = tabFixture()
    f.jar.push({ name: 'login', value: 'v', domain: '.example.com', path: '/', secure: true })
    await f.open(1, 'https://app.example.com:8443/?token=x')
    await f.open(2, 'https://notexample.com/')
    expect(await f.host.invoke({ op: 'site-remove', site: 'example.com' })).toEqual({ ok: true, removed: 1 })
    const origins = f.session.clearStorageData.mock.calls.map((c: any) => c[0].origin)
    expect(origins).toContain('https://app.example.com:8443')
    expect(origins).not.toContain('https://notexample.com')
  })

  it('gives up as partial when the site keeps coming back', async () => {
    const f = tabFixture()
    f.jar.push({ name: 'login', value: 'v0', domain: 'example.com', path: '/', secure: true })
    let n = 0
    // A page that writes a fresh login back after every removal.
    f.session.cookies.remove.mockImplementation(async () => { f.jar.splice(0, 1, { name: 'login', value: `v${++n}`, domain: 'example.com', path: '/', secure: true }) })
    expect(await f.host.invoke({ op: 'site-remove', site: 'example.com' })).toMatchObject({ ok: false, error: 'site_remove_partial', removed: 3 })
    expect(f.session.cookies.remove).toHaveBeenCalledTimes(3)
  })

  it('history-clear hour keeps the older visit of a page revisited in the hour', async () => {
    const f = hostFixture(10 * 3_600_000)
    f.clock.now = 7 * 3_600_000
    f.history.visit('https://a.example/', 'A')
    f.clock.now = 10 * 3_600_000 - 60_000
    f.history.visit('https://a.example/', 'A')
    f.clock.now = 10 * 3_600_000
    expect(await f.host.invoke({ op: 'history-clear', range: 'hour' })).toEqual({ ok: true })
    expect(f.history.list()).toMatchObject([{ url: 'https://a.example/', lastVisit: 7 * 3_600_000, visits: 1 }])
  })

  describe('the recently closed tab stack', () => {
    const closeOne = async (f: ReturnType<typeof tabFixture>, conversationId: number, url: string) => {
      const payload = (await f.host.invoke({ op: 'open', conversationId, url })) as any
      await vi.waitFor(() => expect(f.views.length).toBeGreaterThan(0))
      f.views[f.views.length - 1].webContents.url = url
      await f.host.invoke({ op: 'close-tab', conversationId, tabId: payload.activeTabId })
    }
    const reopen = (f: ReturnType<typeof tabFixture>, conversationId: number) => f.host.invoke({ op: 'reopen-tab', conversationId })
    const historyStub = { list: () => [], clear: async () => true, remove: async () => true, clearSince: async () => true, visit() {}, update() {}, flush: async () => true }

    it('history-remove drops the page so reopen cannot bring it back', async () => {
      const f = tabFixture(historyStub)
      await closeOne(f, 1, 'https://example.com/secret#part')
      await f.host.invoke({ op: 'history-remove', url: 'https://example.com/secret' })
      expect(await reopen(f, 1)).toEqual({ error: 'nothing_to_reopen' })
    })

    it('history-clear drops tabs closed inside the range and keeps older ones', async () => {
      const clock = { now: 10 * 3_600_000 }
      const f = tabFixture(historyStub, () => clock.now)
      await closeOne(f, 1, 'https://old.example/')
      clock.now += 2 * 3_600_000
      await closeOne(f, 1, 'https://recent.example/')
      expect(await f.host.invoke({ op: 'history-clear', range: 'hour' })).toEqual({ ok: true })
      const views = f.views.length
      expect(((await reopen(f, 1)) as any).error).toBeUndefined()
      expect(f.views.length).toBe(views + 1)
      expect(f.views[f.views.length - 1].webContents.loadURL).toHaveBeenCalledWith('https://old.example/')
      expect(await reopen(f, 1)).toEqual({ error: 'nothing_to_reopen' })
      await closeOne(f, 1, 'https://again.example/')
      await f.host.invoke({ op: 'history-clear', range: 'all' })
      expect(await reopen(f, 1)).toEqual({ error: 'nothing_to_reopen' })
    })

    it('site-remove drops the site pages from the stack and clears their origins', async () => {
      const f = tabFixture(historyStub)
      await closeOne(f, 1, 'https://other.org/')
      await closeOne(f, 1, 'https://dev.example.com:3000/x')
      await f.host.invoke({ op: 'site-remove', site: 'example.com' })
      expect(f.session.clearStorageData.mock.calls.map((c: any) => c[0].origin)).toContain('https://dev.example.com:3000')
      await reopen(f, 1)
      expect(f.views[f.views.length - 1].webContents.loadURL).toHaveBeenCalledWith('https://other.org/')
      expect(await reopen(f, 1)).toEqual({ error: 'nothing_to_reopen' })
    })
  })
})

describe('site-remove passes and the reopen stack, turn 4', () => {
  const login = (value = 'v') => ({ name: 'login', value, domain: 'example.com', path: '/', secure: true })

  it('removes an identical cookie written back during the removal', async () => {
    const f = tabFixture()
    f.jar.push(login('same'))
    f.session.clearStorageData.mockImplementationOnce(async () => { f.jar.push(login('same')) })
    expect(await f.host.invoke({ op: 'site-remove', site: 'example.com' })).toEqual({ ok: true, removed: 2 })
    expect(f.jar).toEqual([])
  })

  it('reports partial when a cookie of the site is still in the session after every pass', async () => {
    const f = tabFixture()
    f.jar.push(login())
    f.session.cookies.remove.mockImplementation(async () => {})
    expect(await f.host.invoke({ op: 'site-remove', site: 'example.com' })).toMatchObject({ ok: false, error: 'site_remove_partial', removed: 3 })
  })

  describe('a reopen whose address check fails', () => {
    const pendingReopen = async (f: ReturnType<typeof tabFixture>) => {
      const payload = (await f.host.invoke({ op: 'open', conversationId: 1, url: 'https://example.com/secret' })) as any
      await f.host.invoke({ op: 'close-tab', conversationId: 1, tabId: payload.activeTabId })
      let answer!: (addresses: { address: string; family: number }[]) => void
      f.net.lookup = () => new Promise((resolve) => { answer = resolve })
      const reopening = f.host.invoke({ op: 'reopen-tab', conversationId: 1 })
      await vi.waitFor(() => expect(answer).toBeDefined())
      return { reopening, refuse: () => answer([{ address: '10.0.0.8', family: 4 }]) }
    }

    for (const [name, request] of [
      ['history-remove', { op: 'history-remove', url: 'https://example.com/secret' }],
      ['history-clear', { op: 'history-clear', range: 'all' }],
      ['site-remove', { op: 'site-remove', site: 'example.com' }],
    ] as const) {
      it(`does not put the page back after ${name} forgot it meanwhile`, async () => {
        const mem = memoryHistory(() => 1_000)
        const f = tabFixture(mem.history)
        const { reopening, refuse } = await pendingReopen(f)
        expect(await f.host.invoke(request)).toMatchObject({ ok: true })
        refuse()
        expect(await reopening).toEqual({ error: 'url_not_allowed' })
        expect(await f.host.invoke({ op: 'reopen-tab', conversationId: 1 })).toEqual({ error: 'nothing_to_reopen' })
      })
    }

    it('puts the page back when nothing was forgotten meanwhile', async () => {
      const f = tabFixture(memoryHistory(() => 1_000).history)
      const { reopening, refuse } = await pendingReopen(f)
      refuse()
      expect(await reopening).toEqual({ error: 'url_not_allowed' })
      expect(await f.host.invoke({ op: 'reopen-tab', conversationId: 1 })).not.toEqual({ error: 'nothing_to_reopen' })
    })
  })
})

describe('site-remove targets and failed reopens, turn 5', () => {
  const login = () => ({ name: 'login', value: 'v', domain: 'example.com', path: '/', secure: true })
  const origins = (f: ReturnType<typeof tabFixture>) => f.session.clearStorageData.mock.calls.map((c: any) => c[0].origin)
  const addresses = async () => [{ address: '93.184.216.34', family: 4 }]
  /** Opens and closes `url` in chat 1, then starts its reopen with the address check held until `refuse`. */
  const pendingReopen = async (f: ReturnType<typeof tabFixture>, url: string) => {
    const payload = (await f.host.invoke({ op: 'open', conversationId: 1, url })) as any
    f.views[f.views.length - 1].webContents.url = url
    await f.host.invoke({ op: 'close-tab', conversationId: 1, tabId: payload.activeTabId })
    let answer!: (addresses: { address: string; family: number }[]) => void
    f.net.lookup = () => new Promise((resolve) => { answer = resolve })
    const reopening = f.host.invoke({ op: 'reopen-tab', conversationId: 1 })
    await vi.waitFor(() => expect(answer).toBeDefined())
    return {
      refused: async () => {
        answer([{ address: '10.0.0.8', family: 4 }])
        expect(await reopening).toEqual({ error: 'url_not_allowed' })
        f.net.lookup = addresses
        return f.host.invoke({ op: 'reopen-tab', conversationId: 1 })
      },
    }
  }

  it('a refused site-remove closes its window, so the site loads normally afterwards', async () => {
    const f = tabFixture()
    f.jar.push({ ...login(), domain: 'alice.github.io' }, { ...login(), domain: 'bob.github.io' })
    expect(await f.host.invoke({ op: 'site-remove', site: 'github.io' })).toEqual({ error: 'bad_args' })
    await f.open(1, 'https://alice.github.io/')
    expect(f.views[0].webContents.destroyed).toBe(false)
  })

  it('clears the origin of a closed tab whose reopen is still checking its address', async () => {
    const f = tabFixture()
    f.jar.push(login())
    const { refused } = await pendingReopen(f, 'https://hidden.example.com:8443/?token=x')
    expect(await f.host.invoke({ op: 'site-remove', site: 'example.com' })).toEqual({ ok: true, removed: 1 })
    expect(origins(f)).toContain('https://hidden.example.com:8443')
    expect(await refused()).toEqual({ error: 'nothing_to_reopen' })
  })

  for (const ending of ['did-fail-load', 'did-stop-loading'] as const) {
    it(`a main-frame navigation that ends in ${ending} without a commit no longer makes its tab a site tab`, async () => {
      const f = tabFixture()
      f.jar.push(login())
      const payload = (await f.host.invoke({ op: 'open', conversationId: 1, url: 'https://unrelated.example/keep' })) as any
      const wc = f.views[0].webContents
      wc.loadURL.mockImplementation(async () => {})
      await f.host.invoke({ op: 'navigate', conversationId: 1, tabId: payload.activeTabId, url: 'https://example.com/unreachable' })
      if (ending === 'did-fail-load') wc.emit('did-fail-load', {}, -105, 'ERR_NAME_NOT_RESOLVED', 'https://example.com/unreachable', true)
      else wc.emit('did-stop-loading')
      expect(await f.host.invoke({ op: 'site-remove', site: 'example.com' })).toEqual({ ok: true, removed: 1 })
      expect(wc.destroyed).toBe(false)
    })
  }

  it('an aborted navigation (-3) that another one replaced keeps the new target', async () => {
    const f = tabFixture()
    f.jar.push(login())
    await f.open(1, 'https://other.org/')
    const wc = f.views[0].webContents
    wc.emit('did-start-navigation', { url: 'https://example.com/next', isMainFrame: true })
    wc.emit('did-fail-load', {}, -3, 'ERR_ABORTED', 'https://other.org/old', true)
    expect(await f.host.invoke({ op: 'site-remove', site: 'example.com' })).toEqual({ ok: true, removed: 1 })
    expect(wc.destroyed).toBe(true)
  })

  describe('a failed reopen and a forget that ran while it was in flight', () => {
    const clock = { now: 10_000_000 }
    const reopenAfter = async (request: Record<string, unknown> | null, laterBy = 0) => {
      const f = tabFixture(memoryHistory(() => clock.now).history, () => clock.now)
      f.jar.push(login())
      const { refused } = await pendingReopen(f, 'https://unrelated.example/keep')
      clock.now += laterBy
      if (request) expect(await f.host.invoke(request)).toMatchObject({ ok: true })
      return refused()
    }

    for (const [name, request, laterBy] of [
      ['an unrelated site-remove', { op: 'site-remove', site: 'example.com' }, 0],
      ['a history-remove of another url', { op: 'history-remove', url: 'https://absent.example/deleted' }, 0],
      ['a history-clear hour that does not reach back to the close', { op: 'history-clear', range: 'hour' }, 3_600_001],
    ] as const) {
      it(`keeps the page after ${name}`, async () => {
        expect(await reopenAfter(request, laterBy)).not.toEqual({ error: 'nothing_to_reopen' })
      })
    }

    for (const [name, request] of [
      ['a history-remove of its url', { op: 'history-remove', url: 'https://unrelated.example/keep' }],
      ['a history-clear hour that covers the close', { op: 'history-clear', range: 'hour' }],
      ['a site-remove of its site', { op: 'site-remove', site: 'unrelated.example' }],
    ] as const) {
      it(`drops the page after ${name}`, async () => {
        expect(await reopenAfter(request)).toEqual({ error: 'nothing_to_reopen' })
      })
    }
  })
})

// Shared by the turn 6 and lock tests.
const login = () => ({ name: 'login', value: 'v', domain: 'example.com', path: '/', secure: true })
const origins = (f: ReturnType<typeof tabFixture>) => f.session.clearStorageData.mock.calls.map((c: any) => c[0].origin)
const addresses = async () => [{ address: '93.184.216.34', family: 4 }]
const loads = (f: ReturnType<typeof tabFixture>) => f.views.flatMap((v: any) => v.webContents.loadURL.mock.calls.map((c: any) => c[0]))
/** Holds the first cookie read of the next site-remove until the returned release is called. */
const holdFirstRead = (f: ReturnType<typeof tabFixture>) => {
  let release!: () => void
  const gate = new Promise<void>((resolve) => { release = resolve })
  const read = f.session.cookies.get.getMockImplementation()
  f.session.cookies.get.mockImplementationOnce(async () => { await gate; return read() })
  return release
}
/** Opens and closes `url` in chat 1, then starts its reopen with the address check held. */
const pendingReopen = async (f: ReturnType<typeof tabFixture>, url: string) => {
  const payload = (await f.host.invoke({ op: 'open', conversationId: 1, url })) as any
  f.views[f.views.length - 1].webContents.url = url
  await f.host.invoke({ op: 'close-tab', conversationId: 1, tabId: payload.activeTabId })
  let answer!: (addresses: { address: string; family: number }[]) => void
  f.net.lookup = () => new Promise((resolve) => { answer = resolve })
  const reopening = f.host.invoke({ op: 'reopen-tab', conversationId: 1 })
  await vi.waitFor(() => expect(answer).toBeDefined())
  const viewsBefore = f.views.length
  const settle = async (ok: boolean) => {
    answer([{ address: ok ? '93.184.216.34' : '10.0.0.8', family: 4 }])
    const result = await reopening
    f.net.lookup = addresses
    return result
  }
  return { settle, opened: () => f.views.slice(viewsBefore), reopenLoads: () => f.views.slice(viewsBefore).flatMap((v: any) => v.webContents.loadURL.mock.calls.map((c: any) => c[0])) }
}

describe('turn 6', () => {
  describe('a removal refused by its checks', () => {
    it('a refused parent stops a tenant navigation that starts during its first read and keeps the tab on its page', async () => {
      const f = tabFixture()
      f.jar.push({ ...login(), domain: 'alice.github.io' }, { ...login(), domain: 'bob.github.io' })
      await f.open(1, 'https://other.org/')
      const wc = f.views[0].webContents
      const release = holdFirstRead(f)
      const removal = f.host.invoke({ op: 'site-remove', site: 'github.io' })
      wc.emit('did-start-navigation', { url: 'https://alice.github.io/', isMainFrame: true })
      expect(wc.stop).toHaveBeenCalled()
      release()
      expect(await removal).toEqual({ error: 'bad_args' })
      expect(wc.destroyed).toBe(false)
      expect(wc.getURL()).toBe('https://other.org/')
    })
  })

  describe('a reopen in flight that a removal or forget matched', () => {
    for (const [name, forget] of [
      ['a site-remove of its site that finished', { op: 'site-remove', site: 'example.com' }],
      ['a history-remove of its url', { op: 'history-remove', url: 'https://hidden.example.com/p' }],
    ] as const) {
      it(`is not opened when its address check passes after ${name}`, async () => {
        const f = tabFixture(memoryHistory(() => 1_000).history)
        f.jar.push(login())
        const reopen = await pendingReopen(f, 'https://hidden.example.com/p')
        expect(await f.host.invoke(forget)).toMatchObject({ ok: true })
        expect(await reopen.settle(true)).toEqual({ error: 'nothing_to_reopen' })
        expect(reopen.opened()).toEqual([])
        expect(await f.host.invoke({ op: 'reopen-tab', conversationId: 1 })).toEqual({ error: 'nothing_to_reopen' })
      })
    }

    it('is not opened after shutdown, and reopen and open refuse once the host stopped', async () => {
      const f = tabFixture(memoryHistory(() => 1_000).history)
      const reopen = await pendingReopen(f, 'https://unrelated.example/keep')
      await f.host.shutdown()
      expect(await reopen.settle(true)).toEqual({ error: 'closed' })
      expect(reopen.opened()).toEqual([])
      expect(await f.host.invoke({ op: 'reopen-tab', conversationId: 1 })).toEqual({ error: 'closed' })
      expect(await f.host.invoke({ op: 'open', conversationId: 1, url: 'https://unrelated.example/' })).toEqual({ error: 'closed' })
      expect(f.views.every((v: any) => v.webContents.destroyed)).toBe(true)
    })
  })

  describe('a reopen in flight that nothing matched', () => {
    it('is still put back when its address check fails', async () => {
      const f = tabFixture(memoryHistory(() => 1_000).history)
      const reopen = await pendingReopen(f, 'https://unrelated.example/keep')
      expect(await reopen.settle(false)).toEqual({ error: 'url_not_allowed' })
      // The refused address stays cached as blocked, so the stack itself is what shows the page came back.
      expect((f.host as any).closed.get(1).map((tab: any) => tab.url)).toEqual(['https://unrelated.example/keep'])
    })
  })
})

describe('site removal lock', () => {
  const tenants = () => [{ ...login(), domain: 'alice.github.io' }, { ...login(), domain: 'bob.github.io' }]
  const held = (f: ReturnType<typeof tabFixture>) => (f.host as any).clearing as boolean
  const stack = (f: ReturnType<typeof tabFixture>, conversationId: number) => ((f.host as any).closed.get(conversationId) ?? []).map((tab: any) => tab.url)
  /** Opens `url` in a user tab of the chat, marks it committed and closes it, so it sits on the chat's reopen stack. */
  const closeOne = async (f: ReturnType<typeof tabFixture>, conversationId: number, url: string) => {
    const payload = (await f.host.invoke({ op: 'open', conversationId, url })) as any
    f.views[f.views.length - 1].webContents.url = url
    await f.host.invoke({ op: 'close-tab', conversationId, tabId: payload.activeTabId })
  }
  /** Gives chat 4 an agent tab on other.org; resolves once that first load finished. */
  const agentOn = async (f: ReturnType<typeof tabFixture>) => {
    const first = f.host.handle({ op: 'navigate', conversation_id: 4, args: { url: 'https://other.org/' } })
    await vi.waitFor(() => expect(f.views[0]?.webContents.loadURL).toHaveBeenCalled())
    f.views[0].webContents.emit('did-finish-load')
    await first
  }
  const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve() }

  it('is taken at the call, before the first await, and released when the removal ends', async () => {
    const f = tabFixture()
    f.jar.push(login())
    const removal = f.host.invoke({ op: 'site-remove', site: 'example.com' })
    expect(held(f)).toBe(true)
    const open = f.host.invoke({ op: 'open', conversationId: 1, url: 'https://other.org/' })
    expect(await removal).toEqual({ ok: true, removed: 1 })
    expect(await open).toEqual({ error: 'clearing' })
    expect(held(f)).toBe(false)
    expect(f.views).toEqual([])
  })

  it('refuses every user op and agent command with clearing while held, and allows them once released', async () => {
    const f = tabFixture(memoryHistory(() => 1_000).history)
    f.jar.push(login())
    await agentOn(f)
    const tab = (await f.host.invoke({ op: 'open', conversationId: 1, url: 'https://other.org/' })) as any
    await closeOne(f, 2, 'https://other.org/closed')
    const release = holdFirstRead(f)
    const removal = f.host.invoke({ op: 'site-remove', site: 'example.com' })
    const before = { views: f.views.length, loads: loads(f).length }
    const ops = [
      { op: 'open', conversationId: 1, url: 'https://other.org/new' },
      { op: 'navigate', conversationId: 1, tabId: tab.activeTabId, url: 'https://other.org/next' },
      { op: 'back', conversationId: 1, tabId: tab.activeTabId },
      { op: 'forward', conversationId: 1, tabId: tab.activeTabId },
      { op: 'reload', conversationId: 1, tabId: tab.activeTabId },
      { op: 'reopen-tab', conversationId: 2 },
    ]
    for (const request of ops) expect(await f.host.invoke(request)).toEqual({ error: 'clearing' })
    expect(await f.host.handle({ op: 'navigate', conversation_id: 4, args: { url: 'https://other.org/agent' } })).toMatchObject({ ok: false, error: 'clearing' })
    expect(await f.host.handle({ op: 'snapshot', conversation_id: 4, args: {} })).toMatchObject({ ok: false, error: 'clearing' })
    expect({ views: f.views.length, loads: loads(f).length, reloads: f.views[1].webContents.reload.mock.calls.length }).toEqual({ ...before, reloads: 0 })
    expect(stack(f, 2)).toEqual(['https://other.org/closed'])
    release()
    expect(await removal).toEqual({ ok: true, removed: 1 })
    expect((await f.host.invoke(ops[0]) as any).error).toBeUndefined()
    expect((await f.host.invoke(ops[1]) as any).error).toBeUndefined()
    expect(await f.host.invoke(ops[2])).toEqual({ error: 'no_history' })
    expect((await f.host.invoke(ops[4]) as any).error).toBeUndefined()
    expect((await f.host.invoke(ops[5]) as any).error).toBeUndefined()
    expect(loads(f)).toEqual(expect.arrayContaining(['https://other.org/new', 'https://other.org/next', 'https://other.org/closed']))
    const agent = f.host.handle({ op: 'navigate', conversation_id: 4, args: { url: 'https://other.org/agent' } })
    await vi.waitFor(() => expect(f.views[0].webContents.loadURL).toHaveBeenCalledWith('https://other.org/agent'))
    f.views[0].webContents.emit('did-finish-load')
    expect(await agent).toMatchObject({ ok: true })
  })

  it('a refused removal changes nothing and releases the lock, also when its first read fails', async () => {
    const f = tabFixture()
    f.jar.push(...tenants())
    await f.open(1, 'https://alice.github.io/')
    await closeOne(f, 2, 'https://bob.github.io/page')
    expect(await f.host.invoke({ op: 'site-remove', site: 'github.io' })).toEqual({ error: 'bad_args' })
    expect(held(f)).toBe(false)
    f.session.cookies.get.mockRejectedValueOnce(new Error('read failed'))
    expect(await f.host.invoke({ op: 'site-remove', site: 'alice.github.io' })).toEqual({ error: 'site_remove_failed' })
    expect(held(f)).toBe(false)
    expect(f.views[0].webContents.destroyed).toBe(false)
    expect(stack(f, 2)).toEqual(['https://bob.github.io/page'])
    expect(f.session.cookies.remove).not.toHaveBeenCalled()
    expect(f.session.clearStorageData).not.toHaveBeenCalled()
    expect(f.jar).toHaveLength(2)
    expect((await f.host.invoke({ op: 'open', conversationId: 3, url: 'https://alice.github.io/x' }) as any).error).toBeUndefined()
  })

  it('an accepted removal closes the site tabs, committed or pending, drops its stack entries and clears their exact origins', async () => {
    const f = tabFixture()
    f.jar.push(login())
    await f.open(1, 'https://app.example.com:8443/home')
    const pending = (await f.host.invoke({ op: 'open', conversationId: 2, url: 'https://other.org/' })) as any
    f.views[1].webContents.loadURL.mockImplementation(async () => {})
    await f.host.invoke({ op: 'navigate', conversationId: 2, tabId: pending.activeTabId, url: 'https://late.example.com:9443/x' })
    await f.open(3, 'https://other.org/keep')
    await closeOne(f, 3, 'https://closed.example.com:7443/p')
    await closeOne(f, 3, 'https://other.org/stacked')
    expect(await f.host.invoke({ op: 'site-remove', site: 'example.com' })).toEqual({ ok: true, removed: 1 })
    expect(f.views.map((v: any) => v.webContents.destroyed)).toEqual([true, true, false, true, true])
    expect(origins(f)).toEqual(expect.arrayContaining(['https://app.example.com:8443', 'https://late.example.com:9443', 'https://closed.example.com:7443']))
    expect(origins(f).some((origin: string) => origin.includes('other.org'))).toBe(false)
    expect(stack(f, 3)).toEqual(['https://other.org/stacked'])
    expect(f.onHalt.mock.calls.map((c) => c[0]).sort()).toEqual([1, 2])
  })

  for (const when of ['during', 'after'] as const) {
    it(`an in-flight reopen of the site that settles ${when} the removal is neither opened nor put back, and its origin is cleared`, async () => {
      const f = tabFixture(memoryHistory(() => 1_000).history)
      f.jar.push(login())
      const reopen = await pendingReopen(f, 'https://hidden.example.com:8443/p')
      const release = holdFirstRead(f)
      const removal = f.host.invoke({ op: 'site-remove', site: 'example.com' })
      if (when === 'during') expect(await reopen.settle(true)).toEqual({ error: 'clearing' })
      release()
      expect(await removal).toEqual({ ok: true, removed: 1 })
      if (when === 'after') expect(await reopen.settle(true)).toEqual({ error: 'nothing_to_reopen' })
      expect(reopen.opened()).toEqual([])
      expect(origins(f)).toContain('https://hidden.example.com:8443')
      expect(await f.host.invoke({ op: 'reopen-tab', conversationId: 1 })).toEqual({ error: 'nothing_to_reopen' })
    })

    it(`an in-flight reopen of another site that settles ${when} the removal is refused and put back`, async () => {
      const f = tabFixture(memoryHistory(() => 1_000).history)
      f.jar.push(login())
      const reopen = await pendingReopen(f, 'https://unrelated.example/keep')
      const release = holdFirstRead(f)
      const removal = f.host.invoke({ op: 'site-remove', site: 'example.com' })
      if (when === 'during') expect(await reopen.settle(true)).toEqual({ error: 'clearing' })
      release()
      expect(await removal).toEqual({ ok: true, removed: 1 })
      // The clearGen bump of the lock voids it like any open that awaited across a clear.
      if (when === 'after') expect(await reopen.settle(true)).toEqual({ error: 'chat_closed' })
      expect(reopen.opened()).toEqual([])
      expect(stack(f, 1)).toEqual(['https://unrelated.example/keep'])
      expect((await f.host.invoke({ op: 'reopen-tab', conversationId: 1 }) as any).error).toBeUndefined()
      expect(reopen.reopenLoads()).toEqual(['https://unrelated.example/keep'])
    })
  }

  it('drops a page navigation onto the site while held and lets one to another site through', async () => {
    const f = tabFixture()
    f.jar.push(login())
    await f.open(1, 'https://other.org/')
    const wc = f.views[0].webContents
    const navigate = (url: string) => {
      const event = { url, isMainFrame: true, preventDefault: vi.fn() }
      wc.emit('will-navigate', event)
      return event.preventDefault
    }
    const release = holdFirstRead(f)
    const removal = f.host.invoke({ op: 'site-remove', site: 'example.com' })
    expect(navigate('https://app.example.com/login')).toHaveBeenCalled()
    // A history navigation the page starts itself skips will-navigate.
    wc.emit('did-start-navigation', { url: 'https://example.com/back', isMainFrame: true })
    expect(wc.stop).toHaveBeenCalledTimes(1)
    expect(navigate('https://other.org/next')).not.toHaveBeenCalled()
    wc.emit('did-start-navigation', { url: 'https://other.org/next', isMainFrame: true })
    expect(wc.stop).toHaveBeenCalledTimes(1)
    release()
    expect(await removal).toEqual({ ok: true, removed: 1 })
    expect({ destroyed: wc.destroyed, url: wc.getURL() }).toEqual({ destroyed: false, url: 'https://other.org/' })
    expect(navigate('https://example.com/')).not.toHaveBeenCalled()
  })

  it('refuses a second removal and a clear of browsing data while held', async () => {
    const f = tabFixture()
    f.jar.push(login(), { ...login(), domain: 'other.org' })
    const release = holdFirstRead(f)
    const removal = f.host.invoke({ op: 'site-remove', site: 'example.com' })
    expect(await f.host.invoke({ op: 'site-remove', site: 'other.org' })).toEqual({ error: 'clearing' })
    expect(await f.host.invoke({ op: 'clear-browsing-data' })).toEqual({ error: 'clearing' })
    release()
    expect(await removal).toEqual({ ok: true, removed: 1 })
    expect(f.jar.map((c) => c.domain)).toEqual(['other.org'])
    expect(await f.host.invoke({ op: 'site-remove', site: 'other.org' })).toEqual({ ok: true, removed: 1 })
  })

  for (const [name, fail, reply] of [
    ['a cookie removal throws', (f: ReturnType<typeof tabFixture>) => { f.session.cookies.remove.mockRejectedValueOnce(new Error('boom')) },
      { ok: false, error: 'site_remove_partial', removed: 0, failed: 1 }],
    ['the second cookie read throws', (f: ReturnType<typeof tabFixture>) => {
      const read = f.session.cookies.get.getMockImplementation()
      let n = 0
      f.session.cookies.get.mockImplementation(async () => { if (++n === 2) throw new Error('read failed'); return read() })
    }, { error: 'site_remove_failed' }],
  ] as const) {
    it(`keeps the tabs it closed closed and releases the lock when ${name}`, async () => {
      const f = tabFixture()
      f.jar.push(login())
      await f.open(1, 'https://example.com/')
      fail(f)
      expect(await f.host.invoke({ op: 'site-remove', site: 'example.com' })).toEqual(reply)
      expect(f.views[0].webContents.destroyed).toBe(true)
      expect(held(f)).toBe(false)
      expect(await f.host.invoke({ op: 'reopen-tab', conversationId: 1 })).toEqual({ error: 'nothing_to_reopen' })
      expect((await f.host.invoke({ op: 'open', conversationId: 1, url: 'https://other.org/' }) as any).error).toBeUndefined()
    })
  }

  it('no agent command waits for its own timeout: one sent while held is refused at once, one loading the site ends with its tab', async () => {
    const f = tabFixture()
    f.jar.push(login())
    await agentOn(f)
    const wc = f.views[0].webContents
    wc.loadURL.mockImplementation(async () => {})
    let loading: any = null
    const slow = f.host.handle({ op: 'navigate', conversation_id: 4, args: { url: 'https://example.com/slow' } }).then((r) => { loading = r })
    await vi.waitFor(() => expect(wc.loadURL).toHaveBeenCalledWith('https://example.com/slow'))
    const release = holdFirstRead(f)
    const removal = f.host.invoke({ op: 'site-remove', site: 'example.com' })
    let sent: any = null
    void f.host.handle({ op: 'navigate', conversation_id: 4, args: { url: 'https://other.org/again' } }).then((r) => { sent = r })
    await flush()
    expect(sent).toMatchObject({ ok: false, error: 'clearing' })
    expect(loading).toBeNull()
    release()
    expect(await removal).toEqual({ ok: true, removed: 1 })
    await slow
    expect(loading).toMatchObject({ ok: false, error: 'halted' })
    expect(wc.destroyed).toBe(true)
  })
})

describe('after shutdown', () => {
  it('an agent command makes no view', async () => {
    const f = tabFixture()
    await f.host.shutdown()
    expect(await f.host.handle({ op: 'navigate', conversation_id: 4, args: { url: 'https://other.org/' } })).toMatchObject({ ok: false, error: 'closed' })
    expect(f.views).toEqual([])
  })
})

describe('BrowserUsePage sites and history', () => {
  let invoke: ReturnType<typeof vi.fn>
  const calls = (op: string) => invoke.mock.calls.filter((c) => c[1]?.op === op).map((c) => c[1])
  const rows = (n: number) => Array.from({ length: n }, (_, i) => ({ url: `https://p${i}.example/`, title: `Page ${i}`, favicon: '', lastVisit: 1_000 + i, visits: 1 }))

  beforeEach(() => {
    confirm.mockReset()
    let sites = [{ site: 'github.com', cookies: 3 }, { site: 'itch.io', cookies: 1 }]
    let entries = rows(250)
    invoke = vi.fn(async (_c: string, req: any) => {
      switch (req.op) {
        case 'settings': return { enabled: true, running: false }
        case 'sites': return { sites }
        case 'site-remove': sites = sites.filter((s) => s.site !== req.site); return { ok: true, removed: 1 }
        case 'history': return { entries: req.query ? entries.filter((e) => e.title.toLowerCase().includes(req.query.toLowerCase())) : entries }
        case 'history-remove': entries = entries.filter((e) => e.url !== req.url); return { ok: true }
        case 'history-clear': entries = []; return { ok: true }
        default: return { ok: true }
      }
    })
    vi.stubGlobal('ipc', { invoke })
  })
  afterEach(() => { vi.unstubAllGlobals() })

  it('lists the sites and removes one only after the confirm', async () => {
    render(<BrowserUsePage saved={vi.fn()} />)
    expect(await screen.findByTestId('bp-site-github.com')).toBeTruthy()
    expect(screen.getByTestId('bp-site-itch.io')).toBeTruthy()
    confirm.mockResolvedValueOnce(false)
    fireEvent.click(screen.getByTestId('bp-site-remove-github.com'))
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1))
    expect(calls('site-remove')).toHaveLength(0)
    confirm.mockResolvedValueOnce(true)
    fireEvent.click(screen.getByTestId('bp-site-remove-github.com'))
    await waitFor(() => expect(calls('site-remove')).toEqual([{ op: 'site-remove', site: 'github.com' }]))
    await waitFor(() => expect(screen.queryByTestId('bp-site-github.com')).toBeNull())
    expect(screen.getByTestId('bp-site-itch.io')).toBeTruthy()
  })

  it('shows the empty state when no site has cookies', async () => {
    invoke.mockImplementation(async (_c: string, req: any) => (req.op === 'sites' ? { sites: [] } : req.op === 'history' ? { entries: [] } : { enabled: true }))
    render(<BrowserUsePage saved={vi.fn()} />)
    expect(await screen.findByTestId('bp-sites-empty')).toBeTruthy()
    expect(await screen.findByTestId('bp-hist-empty')).toBeTruthy()
  })

  it('shows the newest 100 rows and adds 100 per "more"', async () => {
    render(<BrowserUsePage saved={vi.fn()} />)
    await waitFor(() => expect(screen.getAllByTestId('bp-hist-row')).toHaveLength(100))
    fireEvent.click(screen.getByTestId('bp-hist-more'))
    expect(screen.getAllByTestId('bp-hist-row')).toHaveLength(200)
    fireEvent.click(screen.getByTestId('bp-hist-more'))
    expect(screen.getAllByTestId('bp-hist-row')).toHaveLength(250)
    expect(screen.queryByTestId('bp-hist-more')).toBeNull()
  })

  it('searches through the history op after a short pause', async () => {
    render(<BrowserUsePage saved={vi.fn()} />)
    await waitFor(() => expect(screen.getAllByTestId('bp-hist-row').length).toBeGreaterThan(0))
    fireEvent.change(screen.getByTestId('bp-hist-search'), { target: { value: 'Page 24' } })
    await waitFor(() => expect(calls('history').some((r) => r.query === 'Page 24')).toBe(true))
    await waitFor(() => expect(screen.getAllByTestId('bp-hist-row')).toHaveLength(11))
  })

  it('removes one history row through history-remove', async () => {
    render(<BrowserUsePage saved={vi.fn()} />)
    const buttons = await screen.findAllByTestId('bp-hist-remove')
    fireEvent.click(buttons[0])
    await waitFor(() => expect(calls('history-remove')).toEqual([{ op: 'history-remove', url: 'https://p0.example/' }]))
    await waitFor(() => expect(screen.getAllByTestId('bp-hist-row')[0].textContent).toContain('Page 1'))
  })

  it('clears a range only after the confirm', async () => {
    render(<BrowserUsePage saved={vi.fn()} />)
    await screen.findAllByTestId('bp-hist-row')
    confirm.mockResolvedValueOnce(false)
    fireEvent.click(screen.getByTestId('bp-hist-clear-hour'))
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1))
    expect(calls('history-clear')).toHaveLength(0)
    for (const range of ['hour', 'day', 'all']) {
      confirm.mockResolvedValueOnce(true)
      fireEvent.click(screen.getByTestId(`bp-hist-clear-${range}`))
      await waitFor(() => expect(calls('history-clear').map((r) => r.range)).toContain(range))
    }
  })

  it('shows an error message when a removal fails', async () => {
    invoke.mockImplementation(async (_c: string, req: any) => {
      if (req.op === 'sites') return { sites: [{ site: 'github.com', cookies: 1 }] }
      if (req.op === 'site-remove') return { error: 'site_remove_failed' }
      return req.op === 'history' ? { entries: [] } : { enabled: true }
    })
    const toast = vi.fn()
    render(<BrowserUsePage saved={vi.fn()} showToast={toast} />)
    confirm.mockResolvedValueOnce(true)
    fireEvent.click(await screen.findByTestId('bp-site-remove-github.com'))
    await waitFor(() => expect(toast).toHaveBeenCalledWith(expect.any(String), 'error'))
    expect((await screen.findByTestId('bp-msg')).textContent).toBeTruthy()
  })

  it('reloads the sites and history after a successful clear of browsing data', async () => {
    render(<BrowserUsePage saved={vi.fn()} />)
    await screen.findByTestId('bp-site-github.com')
    const before = { sites: calls('sites').length, history: calls('history').length }
    confirm.mockResolvedValueOnce(true)
    fireEvent.click(screen.getByTestId('bp-clear'))
    await waitFor(() => expect(calls('sites').length).toBe(before.sites + 1))
    expect(calls('history').length).toBe(before.history + 1)
  })

  it('does not show the empty state when the sites list fails to load', async () => {
    invoke.mockImplementation(async (_c: string, req: any) => (req.op === 'sites' ? { error: 'sites_failed' } : req.op === 'history' ? { entries: [] } : { enabled: true }))
    render(<BrowserUsePage saved={vi.fn()} />)
    expect((await screen.findByTestId('bp-msg')).textContent).toBeTruthy()
    expect(screen.queryByTestId('bp-sites-empty')).toBeNull()
  })

  it('ignores a history reply that belongs to an older search', async () => {
    const pending: Record<string, (v: unknown) => void> = {}
    invoke.mockImplementation(async (_c: string, req: any) => {
      if (req.op === 'history') return new Promise((resolve) => { pending[req.query ?? ''] = resolve })
      return req.op === 'sites' ? { sites: [] } : { enabled: true }
    })
    render(<BrowserUsePage saved={vi.fn()} />)
    await waitFor(() => expect(pending['']).toBeTruthy())
    fireEvent.change(screen.getByTestId('bp-hist-search'), { target: { value: 'new' } })
    await waitFor(() => expect(pending['new']).toBeTruthy())
    pending['new']({ entries: [{ url: 'https://new.example/', title: 'New', favicon: '', lastVisit: 2, visits: 1 }] })
    await waitFor(() => expect(screen.getAllByTestId('bp-hist-row')).toHaveLength(1))
    pending['']({ entries: rows(3) })
    await new Promise((r) => setTimeout(r, 20))
    expect(screen.getAllByTestId('bp-hist-row')).toHaveLength(1)
    expect(screen.getByTestId('bp-hist-row').textContent).toContain('New')
  })
})
