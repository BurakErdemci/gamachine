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

/** A host load committing in the fake page the way Electron reports it: started by no frame, committed, finished. */
function commit(f: Fixture, url: string) {
  f.wc.emit('did-start-navigation', { url, isMainFrame: true, isSameDocument: false })
  f.wc.emit('did-navigate', {}, url)
  f.wc.finish(url)
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
  })

  it('refuses loopback to a request of a loopback tab that names no frame', async () => {
    const f = fixture()
    await open(f, `http://127.0.0.1:${FREE}/`)
    // A worker a public iframe starts is frameless too, so no frame is public even in a loopback tab.
    expect(await f.host.requestBlocked(`http://127.0.0.1:${FREE}/x.js`, 'script', 1)).toBe(true)
    expect(await f.host.requestBlocked('https://cdn.example/x.js', 'script', 1)).toBe(false)
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
    const top = { url: f.wc.url, parent: null }
    const ownLink = { url: `http://localhost:${FREE}/level2`, isMainFrame: true, frame: top, preventDefault: vi.fn() }
    const toGamachine = { url: 'http://127.0.0.1:8080/mcp', isMainFrame: true, frame: top, preventDefault: vi.fn() }
    const toFile = { url: 'file:///C:/Windows/win.ini', isMainFrame: true, frame: top, preventDefault: vi.fn() }
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
    const inside = { url: url(path.join(workspace, 'shot.png')), isMainFrame: true, frame: { url: f.wc.url, parent: null }, preventDefault: vi.fn() }
    const out = { url: url(path.join(outside, 'package.json')), isMainFrame: true, frame: inside.frame, preventDefault: vi.fn() }
    const web = { url: 'https://example.com/', isMainFrame: true, frame: inside.frame, preventDefault: vi.fn() }
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
    // A blank top frame is the page the tab shows; no frame at all is public.
    expect(await ask(blank(null))).toBe(false)
    expect(await ask(null)).toBe(true)
    await open(f, 'https://example.com/')
    expect(await ask(blank(null))).toBe(true)
  })

  it('judges about:blank with a fragment, srcdoc, blob: and data: frames by their parent', async () => {
    const f = fixture()
    await open(f, local)
    const ask = (frame: unknown) => {
      const handler = f.session.webRequest.onBeforeRequest.mock.calls[0][0]
      return new Promise<boolean>((resolve) => handler({ url: `http://127.0.0.1:${FREE}/x`, resourceType: 'xhr', webContentsId: 1, frame }, (r: { cancel: boolean }) => resolve(r.cancel)))
    }
    for (const url of ['about:blank#preview', 'about:srcdoc', 'about:srcdoc#x', `blob:${local}0b6e`, 'data:text/html,<p>x</p>']) {
      expect(await ask({ url, parent: { url: local } }), `${url} in a loopback page`).toBe(false)
      expect(await ask({ url, parent: { url: evil, parent: { url: local } } }), `${url} in a public frame`).toBe(true)
    }
    // Inherited all the way up: two blank frames under the loopback page.
    expect(await ask({ url: 'about:blank', parent: { url: 'about:blank#a', parent: { url: local } } })).toBe(false)
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
      const e = { url, isMainFrame: true, frame: { url: f.wc.url, parent: null }, preventDefault: vi.fn() }
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
        goBack: vi.fn(() => commit(f, page)), goForward: vi.fn(),
      }
      expect(await send(f, { op: 'back', conversation_id: 4, args: {} })).toMatchObject({ ok: true })
      expect(f.wc.navigationHistory.goBack).toHaveBeenCalled()
      expect(await blocked(f, shot, 'image')).toBe(false)
    } finally {
      fs.rmSync(work, { recursive: true, force: true })
    }
  })

  it('gives a fragment entry of a file page the page\'s roots', async () => {
    const f = fixture()
    const work = fs.mkdtempSync(path.join(os.tmpdir(), 'gm-frag-'))
    try {
      fs.writeFileSync(path.join(work, 'index.html'), 'x')
      fs.writeFileSync(path.join(work, 'shot.png'), 'x')
      const page = pathToFileURL(path.join(work, 'index.html')).toString()
      const shot = pathToFileURL(path.join(work, 'shot.png')).toString()
      await open(f, page, { file_roots: [work] })
      f.wc.url = `${page}#sec2`
      f.wc.emit('did-navigate-in-page', {}, f.wc.url, true)
      await open(f, 'https://example.com/')
      const entries = [{ url: page }, { url: `${page}#sec2` }, { url: 'https://example.com/' }]
      f.wc.navigationHistory = {
        canGoBack: () => true, canGoForward: () => false, getActiveIndex: () => 2, getEntryAtIndex: (i: number) => entries[i],
        goBack: vi.fn(), goForward: vi.fn(),
      }
      // The load of the entry itself (before it commits), then the page's own image once it has.
      void send(f, { op: 'back', conversation_id: 4, args: {} })
      await vi.waitFor(() => expect(f.wc.navigationHistory.goBack).toHaveBeenCalled())
      expect(await blocked(f, `${page}#sec2`, 'mainFrame', 'https://example.com/')).toBe(false)
      commit(f, `${page}#sec2`)
      expect(await blocked(f, shot, 'image')).toBe(false)
    } finally {
      fs.rmSync(work, { recursive: true, force: true })
    }
  })

  it('keeps the roots of the file page shown when a Back does not happen', async () => {
    const f = fixture()
    const work = fs.mkdtempSync(path.join(os.tmpdir(), 'gm-keep-'))
    try {
      fs.writeFileSync(path.join(work, 'a.html'), 'x')
      fs.writeFileSync(path.join(work, 'i.png'), 'x')
      const a = pathToFileURL(path.join(work, 'a.html')).toString()
      await open(f, 'https://example.com/')
      await open(f, a, { file_roots: [work] })
      const entries = [{ url: 'https://example.com/' }, { url: a }]
      f.wc.navigationHistory = {
        canGoBack: () => true, canGoForward: () => false, getActiveIndex: () => 1, getEntryAtIndex: (i: number) => entries[i],
        goBack: vi.fn(), goForward: vi.fn(),
      }
      // The page cancels the Back (beforeunload): no navigation starts.
      void send(f, { op: 'back', conversation_id: 4, args: {} })
      await vi.waitFor(() => expect(f.wc.navigationHistory.goBack).toHaveBeenCalled())
      expect(await blocked(f, pathToFileURL(path.join(work, 'i.png')).toString(), 'image')).toBe(false)
    } finally {
      fs.rmSync(work, { recursive: true, force: true })
    }
  })
})

describe('the hops of every frame', () => {
  const local = `http://127.0.0.1:${FREE}/`
  const hop = (f: Fixture, kind: string, url: string, frame: unknown, isMainFrame = false) => {
    const e = { url, isMainFrame, frame, preventDefault: vi.fn() }
    f.wc.emit(kind, e)
    return e.preventDefault.mock.calls.length > 0
  }
  const request = (f: Fixture, url: string, resourceType: string, frame: unknown) => {
    const handler = f.session.webRequest.onBeforeRequest.mock.calls[0][0]
    return new Promise<boolean>((resolve) => handler({ url, resourceType, webContentsId: 1, frame }, (r: { cancel: boolean }) => resolve(r.cancel)))
  }

  it('stops a new iframe of a loopback page that a public site redirects to loopback', async () => {
    const f = fixture()
    await open(f, local)
    const fresh = { url: 'about:blank', parent: { url: local } }
    expect(hop(f, 'will-frame-navigate', 'https://evil.example/r', fresh)).toBe(false)
    expect(await request(f, 'https://evil.example/r', 'subFrame', fresh)).toBe(false)
    expect(hop(f, 'will-redirect', `${local}delete?id=1`, fresh)).toBe(true)
    expect(await request(f, `${local}delete?id=1`, 'subFrame', fresh)).toBe(true)
  })

  it('lets a new iframe of a loopback page open loopback and follow its loopback redirects', async () => {
    const f = fixture()
    await open(f, local)
    const fresh = { url: 'about:srcdoc', parent: { url: local } }
    expect(hop(f, 'will-frame-navigate', `${local}a`, fresh)).toBe(false)
    expect(await request(f, `${local}a`, 'subFrame', fresh)).toBe(false)
    expect(hop(f, 'will-redirect', `http://localhost:${FREE + 1}/b`, fresh)).toBe(false)
    expect(await request(f, `http://localhost:${FREE + 1}/b`, 'subFrame', fresh)).toBe(false)
  })

  it('stops a new iframe inside a public frame from opening loopback', async () => {
    const f = fixture()
    await open(f, local)
    const fresh = { url: 'about:blank', parent: { url: 'https://evil.example/', parent: { url: local } } }
    expect(hop(f, 'will-frame-navigate', `${local}a`, fresh)).toBe(true)
    expect(await request(f, `${local}a`, 'subFrame', fresh)).toBe(true)
  })

  it('judges the redirect of a page-started history navigation by the hop before it', async () => {
    const f = fixture()
    await open(f, local)
    f.wc.emit('did-stop-loading')
    // history.back() to a public entry skips will-navigate: the request itself is the first hop.
    const top = { url: local, parent: null }
    expect(await request(f, 'https://example.com/back', 'mainFrame', top)).toBe(false)
    expect(hop(f, 'will-redirect', `${local}in`, top, true)).toBe(true)
  })

  it('forgets the hops when loading stops', async () => {
    const f = fixture()
    await open(f, local)
    const fresh = { url: 'about:blank', parent: { url: local } }
    expect(hop(f, 'will-frame-navigate', 'https://evil.example/r', fresh)).toBe(false)
    expect(hop(f, 'will-redirect', `${local}x`, fresh)).toBe(true)
    f.wc.emit('did-stop-loading')
    // The frame is still blank (its navigation never committed): a new one starts from the loopback page.
    expect(hop(f, 'will-frame-navigate', `${local}x`, fresh)).toBe(false)
  })
})

describe('the host\'s own load', () => {
  const local = `http://127.0.0.1:${FREE}/`
  const backFromPublic = async (f: Fixture) => {
    await open(f, local)
    await open(f, 'https://example.com/')
    const entries = [{ url: local }, { url: 'https://example.com/' }]
    f.wc.navigationHistory = { canGoBack: () => true, canGoForward: () => false, getActiveIndex: () => 1, getEntryAtIndex: (i: number) => entries[i], goBack: vi.fn(), goForward: vi.fn() }
    void send(f, { op: 'back', conversation_id: 4, args: {} })
    await vi.waitFor(() => expect(f.wc.navigationHistory.goBack).toHaveBeenCalled())
  }

  it('gives the public page shown no way to loopback when a Back never starts', async () => {
    const f = fixture()
    await backFromPublic(f)
    const e = { url: `${local}x`, isMainFrame: true, frame: { url: 'https://example.com/', parent: null }, preventDefault: vi.fn() }
    f.wc.emit('will-navigate', e)
    expect(e.preventDefault).toHaveBeenCalled()
  })

  it('drops a Back that has not started within 10 s', async () => {
    const f = fixture()
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      await backFromPublic(f)
      expect(await blocked(f, `${local}x`, 'mainFrame')).toBe(false)
      vi.advanceTimersByTime(10_001)
      expect(await blocked(f, `${local}x`, 'mainFrame')).toBe(true)
    } finally {
      vi.useRealTimers()
    }
  })

  it('does not lend a waiting Back to a history navigation the page starts itself', async () => {
    const f = fixture()
    await backFromPublic(f)
    f.wc.emit('did-start-navigation', { url: local, isMainFrame: true, isSameDocument: false, frame: { url: 'https://example.com/' }, initiator: { url: 'https://example.com/' } })
    expect(await blocked(f, `${local}x`, 'mainFrame')).toBe(true)
  })

  it('ignores the end of an older load while its own has not started, and ends with its own', async () => {
    const f = fixture()
    await open(f, local)
    void send(f, { op: 'navigate', conversation_id: 4, args: { url: 'https://example.com/go' } })
    await vi.waitFor(() => expect(f.wc.pending).toBe('https://example.com/go'))
    // The loopback page's late finish and stop belong to the load before.
    f.wc.loading = false
    f.wc.emit('did-finish-load')
    f.wc.emit('did-stop-loading')
    expect(await blocked(f, `${local}landing`, 'mainFrame')).toBe(true)
    f.wc.emit('did-start-navigation', { url: 'https://example.com/go', isMainFrame: true, isSameDocument: false })
    f.wc.emit('did-fail-load', {}, -105, 'NAME_NOT_RESOLVED', 'https://example.com/go', true)
    // The public load is over: the loopback page still shown judges its own next load.
    expect(await blocked(f, `${local}landing`, 'mainFrame')).toBe(false)
  })
})

describe('Gamachine ports that start later', () => {
  it('takes a list the backend pushes with no agent command', async () => {
    const f = fixture()
    await open(f, `http://127.0.0.1:${FREE}/`)
    expect(await blocked(f, 'http://127.0.0.1:52000/')).toBe(false)
    expect(await f.host.handle({ op: 'ports', denied_ports: [...DENIED, 52000] } as never)).toMatchObject({ ok: true })
    expect(await blocked(f, 'http://127.0.0.1:52000/')).toBe(true)
    expect(await blocked(f, `http://127.0.0.1:${FREE}/`)).toBe(false)
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
