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

  it('a tab that opens the site while the removal awaits is closed and its new login removed', async () => {
    const f = tabFixture()
    f.jar.push({ name: 'login', value: 'old', domain: 'example.com', path: '/', secure: true })
    let release!: () => void
    const gate = new Promise<void>((resolve) => { release = resolve })
    let reached!: () => void
    const started = new Promise<void>((resolve) => { reached = resolve })
    f.session.clearStorageData.mockImplementationOnce(async () => { reached(); await gate })
    const removal = f.host.invoke({ op: 'site-remove', site: 'example.com' })
    await started
    await f.open(2, 'https://example.com/')
    f.jar.push({ name: 'login', value: 'new', domain: 'example.com', path: '/', secure: true })
    release()
    expect(await removal).toEqual({ ok: true, removed: 2 })
    expect(f.views.every((v) => v.webContents.destroyed)).toBe(true)
    expect(f.jar).toEqual([])
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

describe('the removal window and the reopen stack, turn 4', () => {
  const login = (value = 'v') => ({ name: 'login', value, domain: 'example.com', path: '/', secure: true })
  /** Starts site-remove for example.com and holds its first storage clear until `release`. */
  const holdRemoval = async (f: ReturnType<typeof tabFixture>) => {
    let release!: () => void
    const gate = new Promise<void>((resolve) => { release = resolve })
    let reached!: () => void
    const started = new Promise<void>((resolve) => { reached = resolve })
    f.session.clearStorageData.mockImplementationOnce(async () => { reached(); await gate })
    const removal = f.host.invoke({ op: 'site-remove', site: 'example.com' })
    await started
    return { removal, release }
  }
  const origins = (f: ReturnType<typeof tabFixture>) => f.session.clearStorageData.mock.calls.map((c: any) => c[0].origin)

  it('closes a tab whose navigation onto the site starts while the removal runs, before it commits', async () => {
    const f = tabFixture()
    f.jar.push(login())
    await f.open(1, 'https://other.org/')
    const wc = f.views[0].webContents
    const { removal, release } = await holdRemoval(f)
    wc.emit('did-start-navigation', { url: 'https://app.example.com:8443/next', isMainFrame: true })
    expect(wc.destroyed).toBe(true)
    release()
    expect(await removal).toEqual({ ok: true, removed: 1 })
    expect(origins(f)).toContain('https://app.example.com:8443')
  })

  it('closes a tab the host navigates onto the site while the removal runs, though getURL still shows the old page', async () => {
    const f = tabFixture()
    f.jar.push(login())
    const payload = (await f.host.invoke({ op: 'open', conversationId: 1, url: 'https://other.org/' })) as any
    const wc = f.views[0].webContents
    wc.loadURL.mockImplementation(async () => {})
    const { removal, release } = await holdRemoval(f)
    await f.host.invoke({ op: 'navigate', conversationId: 1, tabId: payload.activeTabId, url: 'https://example.com/pending' })
    expect(wc.loadURL).toHaveBeenLastCalledWith('https://example.com/pending')
    expect(wc.destroyed).toBe(true)
    release()
    expect(await removal).toEqual({ ok: true, removed: 1 })
  })

  it('a site tab the user closes during the removal cannot be reopened and its exact origin is cleared', async () => {
    const f = tabFixture()
    f.jar.push(login())
    const payload = (await f.host.invoke({ op: 'open', conversationId: 2, url: 'https://other.org/' })) as any
    const { removal, release } = await holdRemoval(f)
    // Committed without a navigation event the host saw.
    f.views[0].webContents.url = 'https://late.example.com:8443/?token=x'
    await f.host.invoke({ op: 'close-tab', conversationId: 2, tabId: payload.activeTabId })
    release()
    expect(await removal).toEqual({ ok: true, removed: 1 })
    expect(origins(f)).toContain('https://late.example.com:8443')
    expect(await f.host.invoke({ op: 'reopen-tab', conversationId: 2 })).toEqual({ error: 'nothing_to_reopen' })
  })

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
