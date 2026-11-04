import { EventEmitter } from 'events'
import fs from 'fs'
import os from 'os'
import path from 'path'
import { pathToFileURL } from 'url'
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import { BrowserHost, type BrowserHostDeps } from '../main/helpers/browser-host'

// The origin rule of the agent browser: what a request may reach depends on the page the tab shows.
// Public page: nothing local. Loopback page: loopback (not Gamachine's ports) and public hosts. file: page: files in its roots.

const BACKEND_PORT = 9
const HOST_PORT = 41234
const FREE = 53917
const WHISPER = 47123
// What the backend's gateway sends with every command: its gamachine_ports() (defaults, the bridge range, this host's
// port and the whisper server's random one).
const DENIED = [8000, 8080, 9876, 8799, 8888, HOST_PORT, WHISPER, ...Array.from({ length: 100 }, (_, i) => 6400 + i)]

function fakeWebContents() {
  const wc: any = new EventEmitter()
  Object.assign(wc, {
    id: 1, url: '', title: '', loading: false, destroyed: false, pending: undefined as string | undefined,
    isDestroyed: () => wc.destroyed,
    getURL: () => wc.url,
    getTitle: () => wc.title,
    isLoading: () => wc.loading,
    setWindowOpenHandler: vi.fn(),
    loadURL: vi.fn(async (url: string) => { wc.loading = true; wc.pending = url }),
    stop: vi.fn(() => { wc.loading = false }),
    reload: vi.fn(),
    focus: vi.fn(),
    close: vi.fn(() => { wc.destroyed = true }),
    sendInputEvent: vi.fn(),
    navigationHistory: { canGoBack: () => false, canGoForward: () => false, goBack: vi.fn(), goForward: vi.fn() },
    executeJavaScriptInIsolatedWorld: vi.fn(async () => ({})),
    finish(url: string) { wc.url = url; wc.pending = undefined; wc.loading = false; wc.emit('did-finish-load'); wc.emit('did-stop-loading') },
  })
  return wc
}

function fixture(runtime = true) {
  const view: any = { webContents: fakeWebContents(), setBounds: vi.fn(), setVisible: vi.fn() }
  const wc = view.webContents
  const session: any = new EventEmitter()
  Object.assign(session, { setPermissionRequestHandler: vi.fn(), setPermissionCheckHandler: vi.fn(), clearStorageData: vi.fn(), clearCache: vi.fn() })
  session.webRequest = { onBeforeRequest: vi.fn(), onResponseStarted: vi.fn(), onCompleted: vi.fn() }
  const win: any = { isDestroyed: () => false, contentView: { addChildView: vi.fn(), removeChildView: vi.fn() }, webContents: { send: vi.fn() } }
  const http = { request: vi.fn(async () => ({ data: { enabled: true, runtime: { running: false, port: null, token: null } } })) }
  // Names starting with "rebind" resolve to loopback, "internal" to the private network, everything else is public.
  const lookup = vi.fn(async (host: string) => [{ address: host.startsWith('rebind') ? '127.0.0.1' : host.startsWith('internal') ? '10.0.0.8' : '93.184.216.34', family: 4 }])
  const deps: BrowserHostDeps = {
    window: () => win, createView: vi.fn(() => view), fromPartition: vi.fn(() => session), http,
    baseUrl: () => `http://127.0.0.1:${BACKEND_PORT}`, appToken: 'tok', uiSecret: 'secret', lookup, now: () => 1_000,
    fetch: vi.fn(async () => { throw new Error('no network in tests') }),
  }
  const host = new BrowserHost(deps)
  if (runtime) host.setRuntime({ port: HOST_PORT, token: 'rt' })
  return { host, wc, session, win, lookup }
}

type Fixture = ReturnType<typeof fixture>

const send = (f: Fixture, cmd: Record<string, unknown>) => f.host.handle({ ...cmd, denied_ports: DENIED } as never)

/** Runs an agent navigate until the fake page has loaded `url`. */
async function open(f: Fixture, url: string, extra: Record<string, unknown> = {}) {
  const reply = send(f, { op: 'navigate', conversation_id: 4, args: { url, ...extra } })
  await vi.waitFor(() => expect(f.wc.loadURL).toHaveBeenCalledWith(url), { timeout: 2000 })
  f.wc.finish(url)
  return reply
}

/** A request the page shown makes itself (its frame is the page). */
const blocked = (f: Fixture, url: string, type = 'xhr', frame: string | undefined = f.wc.url) => f.host.requestBlocked(url, type, 1, frame)

describe('agent navigation to local pages', () => {
  it('opens a loopback page on a free port', async () => {
    const f = fixture()
    expect(await open(f, `http://127.0.0.1:${FREE}/game/index.html`)).toMatchObject({ ok: true })
    expect(f.wc.loadURL).toHaveBeenCalledWith(`http://127.0.0.1:${FREE}/game/index.html`)
  })

  it('refuses Gamachine ports: the backend, the host, Unity MCP and the other defaults', async () => {
    const f = fixture()
    for (const port of [BACKEND_PORT, HOST_PORT, 8000, 8080, 6400, 9876]) {
      for (const host of ['127.0.0.1', 'localhost', '[::1]', '2130706433']) {
        expect(await send(f, { op: 'navigate', conversation_id: 4, args: { url: `http://${host}:${port}/` } }), `${host}:${port}`).toMatchObject({ ok: false, error: 'url_not_allowed' })
      }
    }
    expect(f.wc.loadURL).not.toHaveBeenCalled()
  })

  it('refuses the private network, metadata and a public name that resolves to loopback or private', async () => {
    const f = fixture()
    for (const url of ['http://192.168.1.10/', 'http://10.0.0.1/', 'http://169.254.169.254/latest/meta-data/', 'http://100.64.0.1/', 'https://rebind.example/', 'https://internal.corp/'])
      expect(await send(f, { op: 'navigate', conversation_id: 4, args: { url } }), url).toMatchObject({ ok: false, error: 'url_not_allowed' })
    expect(f.wc.loadURL).not.toHaveBeenCalled()
  })

  it('refuses every loopback address while the host has not been given its own port yet', async () => {
    const f = fixture(false)
    expect(await send(f, { op: 'navigate', conversation_id: 4, args: { url: `http://127.0.0.1:${FREE}/` } })).toMatchObject({ ok: false, error: 'url_not_allowed' })
  })
})

describe('request origin rule', () => {
  it('stops a public page from loading, fetching or framing anything local or private', async () => {
    const f = fixture()
    await open(f, 'https://example.com/')
    for (const [url, type] of [[`http://127.0.0.1:${FREE}/x.js`, 'script'], [`http://localhost:${FREE}/api`, 'xhr'], [`ws://localhost:${FREE}/`, 'webSocket'],
      [`http://[::1]:${FREE}/`, 'image'], [`http://127.0.0.1:${FREE}/`, 'subFrame'], ['http://192.168.1.1/', 'xhr'], ['http://169.254.169.254/', 'xhr'],
      ['file:///C:/Windows/win.ini', 'image'], ['file:///etc/passwd', 'subFrame']])
      expect(await blocked(f, url, type), `${type} ${url}`).toBe(true)
    expect(await blocked(f, 'https://cdn.example/a.png', 'image')).toBe(false)
  })

  it('stops a request with no page behind it from reaching loopback', async () => {
    const f = fixture()
    await open(f, `http://127.0.0.1:${FREE}/`)
    expect(await f.host.requestBlocked(`http://127.0.0.1:${FREE}/worker.js`, 'script')).toBe(true)
    expect(await f.host.requestBlocked(`http://127.0.0.1:${FREE}/worker.js`, 'script', 99)).toBe(true)
    // A tab's request that names no frame is not the page's: it counts as public.
    expect(await f.host.requestBlocked(`http://127.0.0.1:${FREE}/x.js`, 'script', 1)).toBe(true)
  })

  it('lets a loopback page load loopback and public resources, except on Gamachine ports', async () => {
    const f = fixture()
    await open(f, `http://127.0.0.1:${FREE}/`)
    expect(await blocked(f, `http://127.0.0.1:${FREE}/app.js`, 'script')).toBe(false)
    expect(await blocked(f, `http://localhost:${FREE + 1}/api`)).toBe(false)
    expect(await blocked(f, `ws://localhost:${FREE}/socket`, 'webSocket')).toBe(false)
    expect(await blocked(f, 'https://cdn.example/lib.js', 'script')).toBe(false)
    for (const port of [BACKEND_PORT, HOST_PORT, 8000, 8080])
      expect(await blocked(f, `http://127.0.0.1:${port}/`), String(port)).toBe(true)
    for (const url of ['http://192.168.1.1/', 'http://169.254.169.254/', 'https://rebind.example/x.js', 'https://internal.corp/x.js', 'file:///C:/Windows/win.ini'])
      expect(await blocked(f, url, 'script'), url).toBe(true)
  })

  it('stops a public page that the agent opened from being redirected to loopback', async () => {
    const f = fixture()
    await open(f, `http://127.0.0.1:${FREE}/`)
    const reply = send(f, { op: 'navigate', conversation_id: 4, args: { url: 'https://example.com/go' } })
    await vi.waitFor(() => expect(f.wc.pending).toBe('https://example.com/go'))
    // The page shown is still the loopback one, but this load is headed for a public site.
    expect(await blocked(f, `http://127.0.0.1:${FREE}/landing`, 'mainFrame')).toBe(true)
    const redirect = { url: `http://127.0.0.1:${FREE}/landing`, isMainFrame: true, preventDefault: vi.fn() }
    f.wc.emit('will-redirect', redirect)
    expect(redirect.preventDefault).toHaveBeenCalled()
    expect(await reply).toMatchObject({ ok: false, error: 'url_not_allowed' })
  })

  it('stops a public page from navigating to loopback by itself, but lets a loopback page follow its own links', async () => {
    const f = fixture()
    await open(f, 'https://example.com/')
    const toLocal = { url: `http://127.0.0.1:${FREE}/`, isMainFrame: true, preventDefault: vi.fn() }
    f.wc.emit('will-navigate', toLocal)
    expect(toLocal.preventDefault).toHaveBeenCalled()
    await open(f, `http://localhost:${FREE}/`)
    const ownLink = { url: `http://localhost:${FREE}/level2`, isMainFrame: true, preventDefault: vi.fn() }
    const toGamachine = { url: 'http://127.0.0.1:8080/mcp', isMainFrame: true, preventDefault: vi.fn() }
    const toFile = { url: 'file:///C:/Windows/win.ini', isMainFrame: true, preventDefault: vi.fn() }
    for (const event of [ownLink, toGamachine, toFile]) f.wc.emit('will-navigate', event)
    expect(ownLink.preventDefault).not.toHaveBeenCalled()
    expect(toGamachine.preventDefault).toHaveBeenCalled()
    expect(toFile.preventDefault).toHaveBeenCalled()
  })

  it('checks the connected address against the page: loopback is fine for a loopback page only', async () => {
    const f = fixture()
    await open(f, `http://localhost:${FREE}/`)
    const started = f.session.webRequest.onResponseStarted.mock.calls[0][0]
    started({ url: `http://localhost:${FREE}/app.js`, ip: '127.0.0.1', webContentsId: 1, resourceType: 'script', frame: { url: f.wc.url } })
    started({ url: `http://127.0.0.1:${FREE}/app.js`, ip: '127.0.0.1', webContentsId: 1, resourceType: 'script', frame: { url: f.wc.url } })
    expect(f.wc.stop).not.toHaveBeenCalled()
    // A name that is not localhost but connected to loopback is a rebinding, whatever the page.
    started({ url: 'https://rebind.example/x.js', ip: '127.0.0.1', webContentsId: 1, resourceType: 'script', frame: { url: f.wc.url } })
    expect(f.wc.stop).toHaveBeenCalledTimes(1)
    const g = fixture()
    await open(g, 'https://example.com/')
    g.session.webRequest.onResponseStarted.mock.calls[0][0]({ url: `http://localhost:${FREE}/x`, ip: '127.0.0.1', webContentsId: 1, resourceType: 'xhr', frame: { url: g.wc.url } })
    expect(g.wc.stop).toHaveBeenCalled()
    expect(g.win.webContents.send.mock.calls.some(([, payload]: [string, any]) => payload?.error === 'private_address')).toBe(true)
  })

  it('does not block localhost for later loopback pages after a refused connection', async () => {
    const f = fixture()
    await open(f, 'https://example.com/')
    f.session.webRequest.onResponseStarted.mock.calls[0][0]({ url: `http://localhost:${FREE}/x`, ip: '127.0.0.1', webContentsId: 1, resourceType: 'xhr', frame: { url: f.wc.url } })
    expect(await open(f, `http://localhost:${FREE}/`)).toMatchObject({ ok: true })
  })
})

describe('file: pages', () => {
  let base: string
  let workspace: string
  let outside: string
  beforeAll(() => {
    base = fs.mkdtempSync(path.join(os.tmpdir(), 'gm-file-origin-'))
    workspace = path.join(base, 'workspace')
    // Outside the project and outside the OS temp dir, which a file: page may always read from.
    outside = process.cwd()
    fs.mkdirSync(workspace)
    fs.writeFileSync(path.join(workspace, 'index.html'), '<img src="shot.png">')
    fs.writeFileSync(path.join(workspace, 'shot.png'), 'x')
  })
  afterAll(() => { fs.rmSync(base, { recursive: true, force: true }) })
  const url = (p: string) => pathToFileURL(p).toString()

  it('opens a file in the roots the backend named, and refuses everything else', async () => {
    const f = fixture()
    const page = url(path.join(workspace, 'index.html'))
    expect(await send(f, { op: 'navigate', conversation_id: 4, args: { url: page } })).toMatchObject({ ok: false, error: 'url_not_allowed' })
    expect(await send(f, { op: 'navigate', conversation_id: 4, args: { url: page, file_roots: [] } })).toMatchObject({ ok: false, error: 'url_not_allowed' })
    expect(await send(f, { op: 'navigate', conversation_id: 4, args: { url: url(path.join(outside, 'package.json')), file_roots: [workspace] } })).toMatchObject({ ok: false, error: 'url_not_allowed' })
    expect(await send(f, { op: 'navigate', conversation_id: 4, args: { url: 'file://server/share/x.html', file_roots: [workspace] } })).toMatchObject({ ok: false, error: 'url_not_allowed' })
    expect(f.wc.loadURL).not.toHaveBeenCalled()
    expect(await open(f, page, { file_roots: [workspace] })).toMatchObject({ ok: true })
    expect(f.wc.loadURL).toHaveBeenCalledWith(page)
  })

  it('opens a file in the OS temp dir whenever it is given any root, except Gamachine\'s own', async () => {
    const f = fixture()
    const shot = url(path.join(os.tmpdir(), 'shot.png'))
    expect(await open(f, shot, { file_roots: [workspace] })).toMatchObject({ ok: true })
    for (const own of ['gamachine-shot.png', 'gamachine.log', path.join('Gamachine-cache', 'a.png')])
      expect(await send(f, { op: 'navigate', conversation_id: 4, args: { url: url(path.join(os.tmpdir(), own)), file_roots: [workspace] } }), own).toMatchObject({ ok: false, error: 'url_not_allowed' })
  })

  it('lets a file: page load files in its roots, and nothing over http(s), ws(s) or from loopback', async () => {
    const f = fixture()
    await open(f, url(path.join(workspace, 'index.html')), { file_roots: [workspace] })
    expect(await blocked(f, url(path.join(workspace, 'shot.png')), 'image')).toBe(false)
    expect(await blocked(f, url(path.join(outside, 'package.json')), 'xhr')).toBe(true)
    for (const [target, type] of [['https://example.com/a.js', 'script'], ['http://example.com/', 'subFrame'], ['ws://example.com/', 'webSocket'],
      [`http://127.0.0.1:${FREE}/`, 'xhr'], [`http://localhost:${FREE}/`, 'image']])
      expect(await blocked(f, target, type), target).toBe(true)
    expect(await blocked(f, 'data:image/png;base64,AAAA', 'image')).toBe(false)
  })

  it('keeps a file: page to links inside its roots', async () => {
    const f = fixture()
    await open(f, url(path.join(workspace, 'index.html')), { file_roots: [workspace] })
    const inside = { url: url(path.join(workspace, 'shot.png')), isMainFrame: true, preventDefault: vi.fn() }
    const out = { url: url(path.join(outside, 'package.json')), isMainFrame: true, preventDefault: vi.fn() }
    const web = { url: 'https://example.com/', isMainFrame: true, preventDefault: vi.fn() }
    for (const event of [inside, out, web]) f.wc.emit('will-navigate', event)
    expect(inside.preventDefault).not.toHaveBeenCalled()
    expect(out.preventDefault).toHaveBeenCalled()
    expect(web.preventDefault).toHaveBeenCalled()
  })

  it('stops public and loopback pages from loading or opening a file:', async () => {
    const f = fixture()
    const target = url(path.join(workspace, 'shot.png'))
    await open(f, 'https://example.com/')
    expect(await blocked(f, target, 'image')).toBe(true)
    await open(f, `http://127.0.0.1:${FREE}/`)
    expect(await blocked(f, target, 'image')).toBe(true)
    const link = { url: target, isMainFrame: true, preventDefault: vi.fn() }
    f.wc.emit('will-navigate', link)
    expect(link.preventDefault).toHaveBeenCalled()
  })

  it('forgets the roots when the tab goes to a page that is not a file', async () => {
    const f = fixture()
    await open(f, url(path.join(workspace, 'index.html')), { file_roots: [workspace] })
    await open(f, 'https://example.com/')
    expect(await blocked(f, url(path.join(workspace, 'shot.png')), 'image')).toBe(true)
  })
})

describe('the requesting frame decides, not the tab', () => {
  const evil = 'https://evil.example/frame'
  const local = `http://127.0.0.1:${FREE}/`

  it('stops a public child frame inside a loopback page from reaching loopback', async () => {
    const f = fixture()
    await open(f, local)
    expect(await blocked(f, `http://127.0.0.1:${FREE}/api`, 'xhr', evil)).toBe(true)
    expect(await blocked(f, `http://localhost:${FREE}/api`, 'subFrame', evil)).toBe(true)
    expect(await blocked(f, `http://127.0.0.1:${FREE}/api`, 'xhr', local)).toBe(false)
    expect(await blocked(f, 'https://cdn.example/lib.js', 'script', evil)).toBe(false)
  })

  it('judges a blank frame by its parent', async () => {
    const f = fixture()
    await open(f, local)
    const blank = (parent: { url: string } | null) => ({ url: 'about:blank', parent })
    const ask = (frame: unknown) => {
      const handler = f.session.webRequest.onBeforeRequest.mock.calls[0][0]
      return new Promise<boolean>((resolve) => handler({ url: `http://127.0.0.1:${FREE}/x`, resourceType: 'xhr', webContentsId: 1, frame }, (r: { cancel: boolean }) => resolve(r.cancel)))
    }
    expect(await ask(blank({ url: local }))).toBe(false)
    expect(await ask(blank({ url: evil }))).toBe(true)
    expect(await ask(blank(null))).toBe(true)
    expect(await ask(null)).toBe(true)
  })

  it('refuses a subframe navigation to loopback from a public frame, and a redirect of it', async () => {
    const f = fixture()
    await open(f, local)
    const event = (frame: unknown, kind: string) => {
      const e = { url: `http://127.0.0.1:${FREE}/inner`, isMainFrame: false, frame, preventDefault: vi.fn() }
      f.wc.emit(kind, e)
      return e.preventDefault.mock.calls.length > 0
    }
    for (const kind of ['will-frame-navigate', 'will-redirect']) {
      expect(event({ url: evil }, kind), kind).toBe(true)
      expect(event({ url: local }, kind), kind).toBe(false)
      expect(event(undefined, kind), kind).toBe(true)
    }
  })
})

describe('hops of one navigation', () => {
  it('judges a redirect by the hop before it, so a page-started trip through a public site cannot land on loopback', async () => {
    const f = fixture()
    await open(f, `http://127.0.0.1:${FREE}/`)
    const hop = (url: string, kind = 'will-navigate') => {
      const e = { url, isMainFrame: true, preventDefault: vi.fn() }
      f.wc.emit(kind, e)
      return e.preventDefault.mock.calls.length > 0
    }
    expect(hop('https://example.com/r')).toBe(false)
    expect(await blocked(f, `http://127.0.0.1:${FREE}/in`, 'mainFrame')).toBe(true)
    expect(hop(`http://127.0.0.1:${FREE}/in`, 'will-redirect')).toBe(true)
    // Loopback to loopback stays open.
    f.wc.finish('https://example.com/r')
    f.wc.url = `http://127.0.0.1:${FREE}/`
    expect(hop(`http://127.0.0.1:${FREE}/next`)).toBe(false)
    expect(hop(`http://127.0.0.1:${FREE}/after`, 'will-redirect')).toBe(false)
  })

  it('is not cleared by a stop that belongs to the navigation a newer one replaced', async () => {
    const f = fixture()
    await open(f, `http://127.0.0.1:${FREE}/`)
    const reply = send(f, { op: 'navigate', conversation_id: 4, args: { url: 'https://example.com/go' } })
    await vi.waitFor(() => expect(f.wc.pending).toBe('https://example.com/go'))
    // The earlier load's stop arrives while the newer one is still loading.
    f.wc.emit('did-stop-loading')
    const redirect = { url: `http://127.0.0.1:${FREE}/landing`, isMainFrame: true, preventDefault: vi.fn() }
    f.wc.emit('will-redirect', redirect)
    expect(redirect.preventDefault).toHaveBeenCalled()
    expect(await blocked(f, `http://127.0.0.1:${FREE}/landing`, 'mainFrame')).toBe(true)
    expect(await reply).toMatchObject({ ok: false, error: 'url_not_allowed' })
  })

  it('clears the hop when loading really stops', async () => {
    const f = fixture()
    await open(f, 'https://example.com/')
    expect(await blocked(f, `http://127.0.0.1:${FREE}/landing`, 'mainFrame')).toBe(true)
    await open(f, `http://127.0.0.1:${FREE}/`)
    expect(await blocked(f, `http://127.0.0.1:${FREE}/other`, 'mainFrame')).toBe(false)
  })
})

describe('the ports the backend names', () => {
  it('keeps every loopback address closed until the backend has sent its list', async () => {
    const f = fixture()
    expect(await f.host.handle({ op: 'navigate', conversation_id: 4, args: { url: `http://127.0.0.1:${FREE}/` } })).toMatchObject({ ok: false, error: 'url_not_allowed' })
    expect(await open(f, `http://127.0.0.1:${FREE}/`)).toMatchObject({ ok: true })
  })

  it('refuses the whisper port, the Unity bridge fallback range and what the environment names', async () => {
    const f = fixture()
    vi.stubEnv('UNITY_MCP_HTTP_URL', 'http://localhost:9191')
    try {
      for (const port of [WHISPER, 6401, 6450, 6499, 9191])
        expect(await send(f, { op: 'navigate', conversation_id: 4, args: { url: `http://localhost:${port}/` } }), String(port)).toMatchObject({ ok: false, error: 'url_not_allowed' })
      expect(f.wc.loadURL).not.toHaveBeenCalled()
      expect(await open(f, `http://localhost:${FREE}/`)).toMatchObject({ ok: true })
    } finally {
      vi.unstubAllEnvs()
    }
  })

  it('keeps the latest list and drops it with the runtime', async () => {
    const f = fixture()
    await open(f, `http://127.0.0.1:${FREE}/`)
    expect(await blocked(f, `http://127.0.0.1:${FREE + 1}/`)).toBe(false)
    await f.host.handle({ op: 'tabs', conversation_id: 4, denied_ports: [FREE + 1] } as never)
    expect(await blocked(f, `http://127.0.0.1:${FREE + 1}/`)).toBe(true)
    f.host.setRuntime({ port: HOST_PORT + 1, token: 'other' })
    expect(await blocked(f, `http://127.0.0.1:${FREE}/`)).toBe(true)
  })
})

describe('file roots across history', () => {
  it('gives a file: entry its roots again after a web page and Back', async () => {
    const f = fixture()
    const work = fs.mkdtempSync(path.join(os.tmpdir(), 'gm-hist-'))
    try {
      fs.writeFileSync(path.join(work, 'index.html'), 'x')
      fs.writeFileSync(path.join(work, 'shot.png'), 'x')
      const page = pathToFileURL(path.join(work, 'index.html')).toString()
      const shot = pathToFileURL(path.join(work, 'shot.png')).toString()
      const roots = [work]
      await open(f, page, { file_roots: roots })
      await open(f, 'https://example.com/')
      const entries = [{ url: page }, { url: 'https://example.com/' }]
      f.wc.navigationHistory = {
        canGoBack: () => true, canGoForward: () => false, getActiveIndex: () => 1, getEntryAtIndex: (i: number) => entries[i],
        goBack: vi.fn(() => f.wc.finish(page)), goForward: vi.fn(),
      }
      expect(await send(f, { op: 'back', conversation_id: 4, args: {} })).toMatchObject({ ok: true })
      expect(f.wc.navigationHistory.goBack).toHaveBeenCalled()
      expect(await blocked(f, shot, 'image')).toBe(false)
    } finally {
      fs.rmSync(work, { recursive: true, force: true })
    }
  })
})

describe('the record of denied names', () => {
  it('holds at most 1000 names and drops expired ones first', async () => {
    let now = 1_000
    const f = fixture()
    ;(f.host as any).now = () => now
    const cache: Map<string, unknown> = (f.host as any).hostCache
    await open(f, 'https://example.com/')
    for (let i = 0; i < 1500; i++) await f.host.requestBlocked(`https://rebind${i}.example/x`, 'script', 1, 'https://example.com/')
    expect(cache.size).toBe(1000)
    expect(cache.has('rebind1499.example')).toBe(true)
    expect(cache.has('rebind0.example')).toBe(false)
    now += 61_000
    await f.host.requestBlocked('https://rebind-new.example/x', 'script', 1, 'https://example.com/')
    expect(cache.size).toBe(1)
  })
})
