import React from 'react'
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

function hostFixture(clockNow = 1_000) {
  const cookies: any[] = []
  const session: any = {
    cookies: { get: vi.fn(async () => cookies), remove: vi.fn(async () => {}) },
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
    expect(await f.host.invoke({ op: 'history-remove', url: 'https://a.example/' })).toEqual({ ok: true })
    expect(f.history.list().map((e) => e.url)).toEqual(['https://b.example/'])
    expect(await f.host.invoke({ op: 'history-remove' })).toEqual({ error: 'bad_args' })
    expect(await f.host.invoke({ op: 'history-remove', url: 'https://gone.example/' })).toMatchObject({ error: expect.any(String) })
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
  it('siteOf keeps the last two labels, three under a country second level, and drops leading dots', () => {
    expect(siteOf('.accounts.google.com')).toBe('google.com')
    expect(siteOf('www.example.co.uk')).toBe('example.co.uk')
    expect(siteOf('.shop.example.com.tr')).toBe('example.com.tr')
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
      { site: 'example.com.tr', cookies: 1 },
      { site: 'github.com', cookies: 2, lastExpiry: 300 },
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
    expect(origins).toEqual(['https://github.com', 'http://github.com', 'https://www.github.com', 'http://www.github.com'])
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
})
