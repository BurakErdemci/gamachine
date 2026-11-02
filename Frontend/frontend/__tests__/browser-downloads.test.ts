import { EventEmitter } from 'events'
import path from 'path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AGENT_BLOCKED_DOWNLOAD_TYPES, BrowserHost, agentBlockedType, downloadName, uniqueDownloadPath, type BrowserHostDeps } from '../main/helpers/browser-host'

// Same fakes as browser-host.test.ts, cut down to what a download needs: a tab made by a real agent command
// and a session that fires will-download.
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
const APPROVAL_MS = 10 * 60_000

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

function fixture(over: Partial<BrowserHostDeps> = {}) {
  const views: any[] = []
  const session: any = new EventEmitter()
  session.setPermissionRequestHandler = vi.fn()
  session.setPermissionCheckHandler = vi.fn()
  session.webRequest = { onBeforeRequest: vi.fn(), onResponseStarted: vi.fn(), onCompleted: vi.fn() }
  const win: any = { isDestroyed: () => false, contentView: { addChildView: vi.fn(), removeChildView: vi.fn() }, webContents: { send: vi.fn() } }
  const clock = { now: 1_000 }
  const existing = new Set<string>()
  // The approval timeout is held for the test to fire; every other timer runs for real.
  const approvals: (() => void)[] = []
  const later = ((fn: () => void, ms: number) => {
    if (ms === APPROVAL_MS) { approvals.push(fn); return 0 as any }
    return setTimeout(fn, ms)
  }) as typeof setTimeout
  const showItemInFolder = vi.fn()
  const deps: BrowserHostDeps = {
    window: () => win,
    createView: vi.fn(() => { const view = fakeView(views.length + 1); views.push(view); return view }),
    fromPartition: () => session,
    http: { request: vi.fn(async () => ({ data: {} })) } as any,
    baseUrl: () => 'http://127.0.0.1:9', appToken: 'tok', uiSecret: 'secret',
    lookup: vi.fn(async () => [{ address: '93.184.216.34', family: 4 }]),
    now: () => clock.now, setTimeout: later, fetch: vi.fn(async () => { throw new Error('no network') }) as any,
    downloadsDir: () => DIR, fileExists: (file) => existing.has(file), showItemInFolder,
    ...over,
  }
  return { host: new BrowserHost(deps), views, session, win, clock, existing, approvals, showItemInFolder }
}

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
  const event = { preventDefault: vi.fn() }
  const item = fakeItem(name)
  f.session.emit('will-download', event, item, wc)
  return { event, item }
}

const list = async (f: ReturnType<typeof fixture>, conversationId = 4) =>
  (await f.host.handle({ op: 'downloads', conversation_id: conversationId, args: {} })).downloads as any[]

describe('download file names', () => {
  it('keeps one file name inside the downloads folder', () => {
    expect(downloadName('../../Windows/System32/evil.dll')).toBe('evil.dll')
    expect(downloadName('..\\..\\a.txt')).toBe('a.txt')
    expect(downloadName('..')).toBe('download')
    expect(downloadName('song\u0000\u202e.mp3')).toBe('song.mp3')
    expect(downloadName('a:b|c?.png')).toBe('abc.png')
    expect(downloadName('setup.exe. . ')).toBe('setup.exe')
    expect(downloadName('CON.txt')).toBe('_CON.txt')
    expect(downloadName(42)).toBe('download')
    for (const raw of ['../x', '..\\..\\y', '/abs/z', 'C:\\w\\q.bin']) {
      expect(path.dirname(path.join(DIR, downloadName(raw)))).toBe(DIR)
    }
  })

  it('names a collision "name (n).ext"', () => {
    const taken = new Set([path.join(DIR, 'a.mp3'), path.join(DIR, 'a (1).mp3')])
    expect(uniqueDownloadPath(DIR, 'a.mp3', (file) => taken.has(file))).toBe(path.join(DIR, 'a (2).mp3'))
    expect(uniqueDownloadPath(DIR, 'b', () => false)).toBe(path.join(DIR, 'b'))
  })

  it('blocks the agent types, double extensions included', () => {
    expect(agentBlockedType('setup.exe')).toBe('exe')
    expect(agentBlockedType('invoice.pdf.EXE')).toBe('exe')
    expect(agentBlockedType('run.ps1')).toBe('ps1')
    expect(agentBlockedType('song.mp3')).toBeNull()
    expect(agentBlockedType('pack.zip')).toBeNull()
    expect(AGENT_BLOCKED_DOWNLOAD_TYPES.has('dll')).toBe(true)
  })
})

describe('BrowserHost downloads', () => {
  it('cancels a download from a webContents that is not one of its tabs', async () => {
    const f = fixture()
    await agentTab(f)
    const { event, item } = download(f, { id: 999 }, 'a.mp3')
    expect(event.preventDefault).toHaveBeenCalled()
    expect(item.cancel).toHaveBeenCalled()
    expect(await list(f)).toEqual([])
  })

  it('cancels every download when there is no downloads folder', async () => {
    const f = fixture({ downloadsDir: undefined })
    const wc = await agentTab(f)
    const { event, item } = download(f, wc, 'a.mp3')
    expect(event.preventDefault).toHaveBeenCalled()
    expect(item.cancel).toHaveBeenCalled()
  })

  it('lets a user download proceed to a unique path in the downloads folder, .exe included', async () => {
    const f = fixture()
    await agentTab(f)
    await f.host.invoke({ op: 'open', conversationId: 4, url: 'https://files.example/' })
    const userWc = f.views[1].webContents
    f.clock.now += 10_000
    f.existing.add(path.join(DIR, 'tool.exe'))
    const { event, item } = download(f, userWc, 'tool.exe')
    expect(event.preventDefault).not.toHaveBeenCalled()
    expect(item.setSavePath).toHaveBeenCalledWith(path.join(DIR, 'tool (1).exe'))
    expect(item.pause).not.toHaveBeenCalled()
    expect(item.cancel).not.toHaveBeenCalled()
    const second = download(f, userWc, 'tool.exe')
    // The first is still being written: its path is taken although no file exists yet.
    expect(second.item.setSavePath).toHaveBeenCalledWith(path.join(DIR, 'tool (2).exe'))
    expect((await list(f)).map((d) => d.state)).toEqual(['progressing', 'progressing'])
    // A user download is not reported to the agent.
    expect((await f.host.handle({ op: 'scroll', conversation_id: 4, args: { direction: 'down' } })).download).toBeUndefined()
  })

  it('pauses an agent download and reports it once in the next reply', async () => {
    const f = fixture()
    const wc = await agentTab(f)
    const { event, item } = download(f, wc, 'theme.mp3')
    expect(event.preventDefault).not.toHaveBeenCalled()
    expect(item.setSavePath).toHaveBeenCalledWith(path.join(DIR, 'theme.mp3'))
    expect(item.pause).toHaveBeenCalled()
    const reply = await f.host.handle({ op: 'scroll', conversation_id: 4, args: { direction: 'down' } })
    expect(reply.download).toEqual({ id: expect.stringMatching(/^d[0-9a-f]{12}$/), filename: 'theme.mp3', bytes: 2048,
      host: 'cdn.files.example', state: 'awaiting_approval' })
    expect(JSON.stringify(reply)).not.toContain('Downloads')
    expect((await f.host.handle({ op: 'scroll', conversation_id: 4, args: { direction: 'down' } })).download).toBeUndefined()
  })

  it('keeps the notice out of the gateway\'s describe, for the reply the agent sees', async () => {
    const f = fixture()
    const wc = await agentTab(f)
    document.body.innerHTML = '<button>More</button>'
    const snap = await f.host.handle({ op: 'snapshot', conversation_id: 4, args: {} })
    download(f, wc, 'theme.mp3')
    const described = await f.host.handle({ op: 'describe', conversation_id: 4, args: { snapshot_id: snap.snapshot_id, ref: '1' } })
    expect(described.download).toBeUndefined()
    expect((await f.host.handle({ op: 'scroll', conversation_id: 4, args: { direction: 'down' } })).download).toMatchObject({ filename: 'theme.mp3' })
  })

  it('blocks an agent .exe and .pdf.exe before any card', async () => {
    const f = fixture()
    const wc = await agentTab(f)
    for (const name of ['setup.exe', 'invoice.pdf.exe']) {
      const { event, item } = download(f, wc, name)
      expect(event.preventDefault).toHaveBeenCalled()
      expect(item.cancel).toHaveBeenCalled()
      expect(item.setSavePath).not.toHaveBeenCalled()
      expect(item.pause).not.toHaveBeenCalled()
    }
    const reply = await f.host.handle({ op: 'downloads', conversation_id: 4, args: {} })
    expect(reply.download).toMatchObject({ filename: 'invoice.pdf.exe', state: 'blocked', reason: expect.stringContaining('.exe') })
    expect((reply.downloads as any[]).map((d) => d.state)).toEqual(['blocked', 'blocked'])
  })

  it('decides the origin from tab state: agent command, its 3 s tail, a user gesture, a taken-over tab', async () => {
    const f = fixture()
    const wc = await agentTab(f)
    // The navigate just ended: inside the 3 s tail.
    f.clock.now += 2_500
    expect(download(f, wc, 'a.mp3').item.pause).toHaveBeenCalled()
    // Past the tail the agent tab is still the agent's.
    f.clock.now += 5_000
    expect(download(f, wc, 'b.mp3').item.pause).toHaveBeenCalled()
    // The user's own click a moment ago wins (and takes the tab over).
    wc.emit('input-event', {}, { type: 'mouseDown' })
    f.clock.now += 500
    const user = download(f, wc, 'c.exe')
    expect(user.item.pause).not.toHaveBeenCalled()
    expect(user.item.cancel).not.toHaveBeenCalled()
    // Later, the taken-over tab is still the user's.
    f.clock.now += 5_000
    expect(download(f, wc, 'd.exe').item.cancel).not.toHaveBeenCalled()
  })

  it('treats a download during a running agent command as the agent\'s even on a user gesture-free tab', async () => {
    const f = fixture()
    const wc = await agentTab(f)
    f.clock.now += 60_000
    let release!: () => void
    wc.executeJavaScriptInIsolatedWorld.mockImplementationOnce(() => new Promise((resolve) => { release = () => resolve({}) }))
    const running = f.host.handle({ op: 'scroll', conversation_id: 4, args: { direction: 'down' } })
    await vi.waitFor(() => expect(release).toBeTypeOf('function'))
    const { item } = download(f, wc, 'e.dll')
    expect(item.cancel).toHaveBeenCalled()
    release()
    expect((await running).download).toMatchObject({ filename: 'e.dll', state: 'blocked' })
  })

  it('resumes or cancels only this chat\'s paused downloads', async () => {
    const f = fixture()
    const wc = await agentTab(f)
    const first = download(f, wc, 'one.wav')
    const second = download(f, wc, 'two.wav')
    const [one, two] = await list(f)
    expect(await f.host.handle({ op: 'download_resume', conversation_id: 5, args: { id: one.id } })).toMatchObject({ ok: false, error: 'no_download' })
    expect(first.item.resume).not.toHaveBeenCalled()
    expect(await f.host.handle({ op: 'download_resume', conversation_id: 4, args: { id: one.id } }))
      .toMatchObject({ ok: true, download: { id: one.id, state: 'progressing' } })
    expect(first.item.resume).toHaveBeenCalled()
    // Not paused any more: neither a second resume nor a cancel acts on it.
    expect(await f.host.handle({ op: 'download_cancel', conversation_id: 4, args: { id: one.id } })).toMatchObject({ ok: false, error: 'not_awaiting_approval' })
    expect(first.item.cancel).not.toHaveBeenCalled()
    expect(await f.host.handle({ op: 'download_cancel', conversation_id: 4, args: { id: two.id } }))
      .toMatchObject({ ok: true, download: { state: 'cancelled' } })
    expect(second.item.cancel).toHaveBeenCalled()
  })

  it('cancels a paused agent download nobody answered for 10 minutes', async () => {
    const f = fixture()
    const wc = await agentTab(f)
    const { item } = download(f, wc, 'late.ogg')
    expect(f.approvals).toHaveLength(1)
    f.approvals[0]()
    expect(item.cancel).toHaveBeenCalled()
    expect((await list(f))[0]).toMatchObject({ state: 'cancelled', reason: expect.stringContaining('10 minutes') })
  })

  it('tracks progress and completion, gives the save path only to download_info, and shows it in the folder', async () => {
    const f = fixture()
    const wc = await agentTab(f)
    const { item } = download(f, wc, 'pack.zip')
    const [entry] = await list(f)
    expect(await f.host.handle({ op: 'download_info', conversation_id: 4, args: { id: entry.id } })).toMatchObject({ ok: false })
    await f.host.handle({ op: 'download_resume', conversation_id: 4, args: { id: entry.id } })
    item.received = 1024
    item.emit('updated', {}, 'progressing')
    expect((await list(f))[0]).toMatchObject({ state: 'progressing', received: 1024 })
    item.received = 2048
    item.emit('done', {}, 'completed')
    expect(await f.host.handle({ op: 'downloads', conversation_id: 4, args: { wait_ms: 5000 } }))
      .toMatchObject({ ok: true, downloads: [{ id: entry.id, state: 'completed', received: 2048, bytes: 2048 }] })
    expect(await f.host.handle({ op: 'download_info', conversation_id: 4, args: { id: entry.id } }))
      .toMatchObject({ ok: true, download: { savePath: path.join(DIR, 'pack.zip'), dir: DIR, state: 'completed' } })
    expect(await f.host.handle({ op: 'download_info', conversation_id: 5, args: { id: entry.id } })).toMatchObject({ ok: false, error: 'no_download' })
    const live = f.win.webContents.send.mock.calls.filter(([channel]: [string]) => channel === 'browser-live').map(([, p]: [string, any]) => p)
    expect(live.at(-1).downloads).toEqual([{ id: entry.id, filename: 'pack.zip', bytes: 2048, received: 2048, state: 'completed' }])
    expect(await f.host.invoke({ op: 'download_show', conversationId: 5, id: entry.id })).toEqual({ error: 'no_download' })
    expect(await f.host.invoke({ op: 'download_show', conversationId: 4, id: entry.id })).toEqual({ ok: true })
    expect(f.showItemInFolder).toHaveBeenCalledWith(path.join(DIR, 'pack.zip'))
  })

  it('waits with wait_ms while a download is still progressing', async () => {
    const f = fixture()
    await agentTab(f)
    await f.host.invoke({ op: 'open', conversationId: 4, url: 'https://files.example/' })
    f.clock.now += 10_000
    const { item } = download(f, f.views[1].webContents, 'big.zip')
    setTimeout(() => item.emit('done', {}, 'completed'), 300)
    const reply = await f.host.handle({ op: 'downloads', conversation_id: 4, args: { wait_ms: 5000 } })
    expect((reply.downloads as any[])[0].state).toBe('completed')
  })
})
