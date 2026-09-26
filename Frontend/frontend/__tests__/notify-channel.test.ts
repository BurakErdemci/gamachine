import fs from 'fs'
import os from 'os'
import { EventEmitter } from 'events'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ALLOWED_INVOKE_CHANNELS } from '../main/helpers/ipc-whitelist'
import {
  NOTIFY_BODY_MAX,
  NOTIFY_LIVE_MAX,
  NOTIFY_TITLE_MAX,
  createNotifier,
  parseNotifyPayload,
} from '../main/helpers/notify'

/**
 * The `notify` invoke channel (desktop notifications for background chats).
 *
 * The handler is taken from the REAL registration in `background.ts`, with
 * electron mocked the way the other main-process tests do it, so the sender
 * check (`handleSecure`) and the whitelist are part of what is exercised. No
 * OS notification is raised: `Notification` is a fake that records itself.
 */

const handlers = new Map<string, (...args: any[]) => any>()

class FakeNotification extends EventEmitter {
  static supported = true
  static created: FakeNotification[] = []
  static isSupported = () => FakeNotification.supported
  shown = 0
  constructor(public options: { title: string; body: string }) {
    super()
    FakeNotification.created.push(this)
  }
  show() { this.shown += 1 }
}

const makeWindow = () => ({
  isDestroyed: vi.fn(() => false),
  isMinimized: vi.fn(() => true),
  restore: vi.fn(),
  show: vi.fn(),
  focus: vi.fn(),
  webContents: { send: vi.fn() },
})
let senderWindow: ReturnType<typeof makeWindow> | null = null

vi.mock('electron', () => ({
  app: {
    getPath: vi.fn(() => os.tmpdir()), setPath: vi.fn(),
    requestSingleInstanceLock: vi.fn(() => false), quit: vi.fn(), on: vi.fn(),
  },
  ipcMain: { handle: vi.fn((channel: string, listener: (...args: any[]) => any) => handlers.set(channel, listener)) },
  dialog: {}, shell: {},
  BrowserWindow: { getAllWindows: vi.fn(() => []), fromWebContents: vi.fn(() => senderWindow) },
  Notification: FakeNotification,
}))
vi.mock('electron-serve', () => ({ default: vi.fn() }))
vi.mock('electron-updater', () => ({ autoUpdater: {} }))
vi.mock('node-pty', () => ({ spawn: vi.fn() }))
vi.mock('../main/helpers', () => ({ createWindow: vi.fn() }))
vi.mock('../main/helpers/ipc-trust', () => ({
  confirmLegacyRoot: vi.fn(() => false),
  isOwnFrame: vi.fn((event: any) => event?.senderFrame?.url === 'app://./home'),
  isTrustedRoot: vi.fn(() => true), registerTrustedRoot: vi.fn(),
}))
vi.mock('../main/helpers/csp', () => ({ applyContentSecurityPolicy: vi.fn() }))

const own = { senderFrame: { url: 'app://./home' }, sender: {} }
const foreign = { senderFrame: { url: 'https://evil.example/' }, sender: {} }

async function notifyHandler() {
  const originalAppend = fs.appendFileSync.bind(fs)
  vi.spyOn(fs, 'appendFileSync').mockImplementation(((file: fs.PathOrFileDescriptor, data: any, options?: any) => {
    if (typeof file === 'string' && file.endsWith('gamachine.log')) return
    return originalAppend(file, data, options)
  }) as typeof fs.appendFileSync)
  await import('../main/background')
  const listener = handlers.get('notify')
  expect(listener).toBeTypeOf('function')
  return listener!
}

beforeEach(() => {
  FakeNotification.created = []
  FakeNotification.supported = true
  senderWindow = makeWindow()
})

describe('notify · registration', () => {
  it('the channel is on the invoke whitelist', () => {
    expect(ALLOWED_INVOKE_CHANNELS.has('notify')).toBe(true)
  })

  it('a frame that is not the app is refused before anything is shown', async () => {
    const listener = await notifyHandler()
    expect(() => listener(foreign, { title: 'Gamachine', body: 'x', conversationId: 1 })).toThrow(/IPC reddedildi/)
    expect(FakeNotification.created).toHaveLength(0)
  })

  it.each([
    ['nothing', undefined],
    ['a string', 'Gamachine'],
    ['an array', ['Gamachine', 'x']],
    ['a missing body', { title: 'Gamachine' }],
    ['a non-string title', { title: 7, body: 'x' }],
    ['an empty title', { title: '   ', body: 'x' }],
    ['an extra key', { title: 'Gamachine', body: 'x', icon: 'C:/evil.png' }],
    ['a zero id', { title: 'Gamachine', body: 'x', conversationId: 0 }],
    ['a negative id', { title: 'Gamachine', body: 'x', conversationId: -3 }],
    ['a fractional id', { title: 'Gamachine', body: 'x', conversationId: 1.5 }],
    ['a string id', { title: 'Gamachine', body: 'x', conversationId: '4' }],
    ['a class instance', new (class { title = 'Gamachine'; body = 'x' })()],
  ])('refuses %s and shows nothing', async (_label, payload) => {
    const listener = await notifyHandler()
    expect(() => listener(own, payload)).toThrow(/Invalid notification payload/)
    expect(FakeNotification.created).toHaveLength(0)
  })

  it('shows a valid request, with title and body capped', async () => {
    const listener = await notifyHandler()
    const out = listener(own, { title: 'T'.repeat(500), body: 'B'.repeat(5000), conversationId: 7 })
    expect(out).toEqual({ shown: true })
    expect(FakeNotification.created).toHaveLength(1)
    const n = FakeNotification.created[0]
    expect(Array.from(n.options.title)).toHaveLength(NOTIFY_TITLE_MAX)
    expect(Array.from(n.options.body)).toHaveLength(NOTIFY_BODY_MAX)
    expect(n.shown).toBe(1)
  })

  it('shows nothing when the platform has no notifications', async () => {
    const listener = await notifyHandler()
    FakeNotification.supported = false
    expect(listener(own, { title: 'Gamachine', body: 'x' })).toEqual({ shown: false })
    expect(FakeNotification.created).toHaveLength(0)
  })
})

describe('notify · click', () => {
  it('restores and focuses the asking window and sends open-conversation with the id', async () => {
    const listener = await notifyHandler()
    listener(own, { title: 'Gamachine', body: 'Alpha: approval needed', conversationId: 7 })
    const win = senderWindow!
    expect(win.webContents.send).not.toHaveBeenCalled()

    FakeNotification.created[0].emit('click')

    expect(win.restore).toHaveBeenCalledTimes(1)
    expect(win.show).toHaveBeenCalledTimes(1)
    expect(win.focus).toHaveBeenCalledTimes(1)
    expect(win.webContents.send).toHaveBeenCalledTimes(1)
    expect(win.webContents.send).toHaveBeenCalledWith('open-conversation', 7)
  })

  it('without an id the click only focuses the window', async () => {
    const listener = await notifyHandler()
    listener(own, { title: 'Gamachine', body: 'tray' })
    FakeNotification.created[0].emit('click')
    expect(senderWindow!.focus).toHaveBeenCalledTimes(1)
    expect(senderWindow!.webContents.send).not.toHaveBeenCalled()
  })

  it('a click after the window is gone touches nothing', async () => {
    const listener = await notifyHandler()
    listener(own, { title: 'Gamachine', body: 'x', conversationId: 2 })
    senderWindow!.isDestroyed.mockReturnValue(true)
    FakeNotification.created[0].emit('click')
    expect(senderWindow!.focus).not.toHaveBeenCalled()
    expect(senderWindow!.webContents.send).not.toHaveBeenCalled()
  })
})

describe('notify · payload and references', () => {
  it('strips control and bidi characters from the text', () => {
    const req = parseNotifyPayload({ title: 'Game\u202Emachine', body: 'a\nb\u0000c', conversationId: 3 })
    expect(req).toEqual({ title: 'Game machine', body: 'a b c', conversationId: 3 })
  })

  it('never cuts a surrogate pair in half', () => {
    const req = parseNotifyPayload({ title: '😀'.repeat(NOTIFY_TITLE_MAX + 5), body: '' })
    expect(req!.title.endsWith('…')).toBe(true)
    expect(req!.title).not.toMatch(/[\uD800-\uDBFF](?![\uDC00-\uDFFF])/)
  })

  it('keeps each notification referenced until it is clicked, within a bound', () => {
    const made: FakeNotification[] = []
    const notifier = createNotifier({
      isSupported: () => true,
      create: (o) => { const n = new FakeNotification(o); made.push(n); return n },
    })
    notifier.show({ title: 'a', body: 'b' }, null)
    notifier.show({ title: 'a', body: 'b' }, null)
    expect(notifier.liveCount()).toBe(2)
    // 'close' alone does not release: on Windows it also fires on timeout.
    made[0].emit('close')
    expect(notifier.liveCount()).toBe(2)
    made[0].emit('click')
    made[1].emit('failed', {}, 'error')
    expect(notifier.liveCount()).toBe(0)

    for (let i = 0; i < NOTIFY_LIVE_MAX + 10; i++) notifier.show({ title: 'a', body: 'b' }, null)
    expect(notifier.liveCount()).toBe(NOTIFY_LIVE_MAX)
  })
})
