import { EventEmitter } from 'events'
import path from 'path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { BrowserHost, type BrowserHostDeps } from '../main/helpers/browser-host'

// Same fakes as browser-downloads.test.ts, plus a settings reply the test controls.
beforeEach(() => {
  vi.spyOn(Element.prototype, 'getClientRects').mockImplementation(() => [{}] as unknown as DOMRectList)
  Object.defineProperty(HTMLElement.prototype, 'innerText', { configurable: true, get() { return this.textContent } })
  window.scrollBy = vi.fn() as typeof window.scrollBy
})
afterEach(() => {
  vi.restoreAllMocks()
  delete (globalThis as any).__gmBrowser
  document.body.innerHTML = ''
})

const DIR = path.join('C:', 'Users', 'u', 'Downloads')
const APPROVAL_MS = 3 * 60_000

function fakeView(id: number) {
  const wc: any = new EventEmitter()
  Object.assign(wc, {
    id, url: '', title: '', loading: false, destroyed: false,
    isDestroyed: () => wc.destroyed, getURL: () => wc.url, getTitle: () => wc.title, isLoading: () => wc.loading,
    setWindowOpenHandler: vi.fn(),
    loadURL: vi.fn(async (url: string) => { wc.loading = true; wc.pending = url }),
    stop: vi.fn(), reload: vi.fn(), focus: vi.fn(), close: vi.fn(() => { wc.destroyed = true }), sendInputEvent: vi.fn(),
    navigationHistory: { canGoBack: () => false, canGoForward: () => false, goBack: vi.fn(), goForward: vi.fn() },
    executeJavaScriptInIsolatedWorld: vi.fn(async (_world: number, scripts: { code: string }[]) => (0, eval)(scripts[0].code)),
    finish(url: string) { wc.url = url; wc.title = 'Page'; wc.loading = false; wc.emit('did-finish-load') },
  })
  return { webContents: wc, setBounds: vi.fn(), setVisible: vi.fn() } as any
}

// Each GET /browser/settings is held until the test answers it with reply() or fail().
function fixture() {
  const views: any[] = []
  const session: any = new EventEmitter()
  session.setPermissionRequestHandler = vi.fn()
  session.setPermissionCheckHandler = vi.fn()
  session.webRequest = { onBeforeRequest: vi.fn(), onResponseStarted: vi.fn(), onCompleted: vi.fn() }
  const win: any = { isDestroyed: () => false, contentView: { addChildView: vi.fn(), removeChildView: vi.fn() }, webContents: { send: vi.fn() } }
  const clock = { now: 1_000 }
  const later = ((fn: () => void, ms: number) => (ms === APPROVAL_MS ? (0 as any) : setTimeout(fn, ms))) as typeof setTimeout
  const settings: { resolve: (value: unknown) => void, reject: (error: Error) => void }[] = []
  const request = vi.fn((config: { method: string, url: string }) => {
    if (config.url.endsWith('/browser/settings')) return new Promise((resolve, reject) => { settings.push({ resolve, reject }) })
    return Promise.resolve({ data: {} })
  })
  const deps: BrowserHostDeps = {
    window: () => win,
    createView: vi.fn(() => { const view = fakeView(views.length + 1); views.push(view); return view }),
    fromPartition: () => session,
    http: { request } as any,
    baseUrl: () => 'http://127.0.0.1:9', appToken: 'tok', uiSecret: 'secret',
    lookup: vi.fn(async () => [{ address: '93.184.216.34', family: 4 }]),
    now: () => clock.now, setTimeout: later, fetch: vi.fn(async () => { throw new Error('no network') }) as any,
    downloadsDir: () => DIR, fileExists: () => false, showItemInFolder: vi.fn(),
  }
  const reply = async (index: number, cardsOff: boolean, extra: Record<string, unknown> = {}) => {
    settings[index].resolve({ data: { enabled: true, cards_off: cardsOff, halted: [], ...extra } })
    await flush()
  }
  const fail = async (index: number) => { settings[index].reject(new Error('backend down')); await flush() }
  return { host: new BrowserHost(deps), views, session, clock, settings, request, reply, fail }
}

const flush = () => new Promise<void>((resolve) => setTimeout(resolve, 0))

async function agentTab(f: ReturnType<typeof fixture>, conversationId = 4) {
  const url = 'https://files.example/'
  const reply = f.host.handle({ op: 'navigate', conversation_id: conversationId, args: { url } })
  let wc: any
  await vi.waitFor(() => {
    wc = f.views.find((v) => v.webContents.pending === url)?.webContents
    expect(wc).toBeTruthy()
  })
  wc.pending = undefined
  wc.finish(url)
  await reply
  return wc
}

function fakeItem(name: string, total = 2048) {
  const item: any = new EventEmitter()
  Object.assign(item, {
    received: 0,
    getFilename: () => name, getURL: () => 'https://cdn.files.example/get?id=1', getMimeType: () => 'audio/mpeg',
    getTotalBytes: () => total, getReceivedBytes: () => item.received,
    setSavePath: vi.fn(), pause: vi.fn(), resume: vi.fn(), cancel: vi.fn(),
  })
  return item
}

function download(f: ReturnType<typeof fixture>, wc: any, name: string) {
  const item = fakeItem(name)
  f.session.emit('will-download', { preventDefault: vi.fn() }, item, wc)
  return { item }
}

const list = async (f: ReturnType<typeof fixture>, conversationId = 4) =>
  (await f.host.handle({ op: 'downloads', conversation_id: conversationId, args: {} })).downloads as any[]

const settingsCalls = (f: ReturnType<typeof fixture>) => f.request.mock.calls.filter(([config]) => config.url.endsWith('/browser/settings'))

describe('agent download with browser cards off', () => {
  it('resumes at once when the settings say cards_off', async () => {
    const f = fixture()
    const wc = await agentTab(f)
    const { item } = download(f, wc, 'theme.mp3')
    expect(item.pause).toHaveBeenCalled()
    expect((await list(f))[0].state).toBe('awaiting_approval')
    await f.reply(0, true)
    expect(item.resume).toHaveBeenCalledTimes(1)
    expect((await list(f))[0].state).toBe('progressing')
    expect(settingsCalls(f)[0][0]).toMatchObject({ method: 'get' })
  })

  it('stays paused when cards are on', async () => {
    const f = fixture()
    const wc = await agentTab(f)
    const { item } = download(f, wc, 'theme.mp3')
    await f.reply(0, false)
    expect(item.resume).not.toHaveBeenCalled()
    expect((await list(f))[0].state).toBe('awaiting_approval')
  })

  it('stays paused when the settings request fails', async () => {
    const f = fixture()
    const wc = await agentTab(f)
    const { item } = download(f, wc, 'theme.mp3')
    await f.fail(0)
    expect(item.resume).not.toHaveBeenCalled()
    expect((await list(f))[0].state).toBe('awaiting_approval')
  })

  it('ignores a late settings reply for a download that was cancelled or resumed meanwhile', async () => {
    const f = fixture()
    const wc = await agentTab(f)
    const cancelled = download(f, wc, 'one.mp3')
    const resumed = download(f, wc, 'two.mp3')
    const [one, two] = await list(f)
    await f.host.handle({ op: 'download_cancel', conversation_id: 4, args: { id: one.id } })
    await f.host.handle({ op: 'download_resume', conversation_id: 4, args: { id: two.id } })
    expect(resumed.item.resume).toHaveBeenCalledTimes(1)
    await f.reply(0, true)
    await f.reply(1, true)
    expect(cancelled.item.resume).not.toHaveBeenCalled()
    expect(resumed.item.resume).toHaveBeenCalledTimes(1)
    expect((await list(f)).map((d) => d.state)).toEqual(['cancelled', 'progressing'])
  })

  it('makes no settings request for a user download', async () => {
    const f = fixture()
    await agentTab(f)
    await f.host.invoke({ op: 'open', conversationId: 4, url: 'https://files.example/' })
    f.clock.now += 10_000
    const { item } = download(f, f.views[1].webContents, 'tool.zip')
    await flush()
    expect(item.pause).not.toHaveBeenCalled()
    expect(settingsCalls(f)).toHaveLength(0)
  })

  it('stays paused when the user stopped that chat', async () => {
    const f = fixture()
    const wc = await agentTab(f)
    const { item } = download(f, wc, 'theme.mp3')
    await f.reply(0, true, { halted: [9, 4] })
    expect(item.resume).not.toHaveBeenCalled()
    expect((await list(f))[0].state).toBe('awaiting_approval')
  })

  it('stays paused when the browser is disabled', async () => {
    const f = fixture()
    const wc = await agentTab(f)
    const { item } = download(f, wc, 'theme.mp3')
    await f.reply(0, true, { enabled: false })
    expect(item.resume).not.toHaveBeenCalled()
  })

  it('does not resume when the host shut down before the settings reply', async () => {
    const f = fixture()
    const wc = await agentTab(f)
    const { item } = download(f, wc, 'theme.mp3')
    await f.host.shutdown()
    await f.reply(0, true)
    expect(item.resume).not.toHaveBeenCalled()
  })
})
