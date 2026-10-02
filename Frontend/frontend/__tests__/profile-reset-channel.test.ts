import fs from 'fs'
import os from 'os'
import { describe, expect, it, vi } from 'vitest'
import { ALLOWED_INVOKE_CHANNELS } from '../main/helpers/ipc-whitelist'
import { createProfileReset } from '../main/helpers/profile-reset'

/**
 * The `profile-reset` invoke channel (Settings > Account > Reset statistics).
 *
 * `POST /profile/reset` refuses a call without the UI secret, and the renderer never holds
 * that secret. The request itself is tested on the helper with injected deps; the REAL
 * registration in `background.ts` (electron mocked as in notify-channel.test.ts) is tested for
 * the sender check and for taking nothing from the renderer.
 */

const handlers = new Map<string, (...args: any[]) => any>()
const post = vi.fn()

vi.mock('axios', () => {
  const instance = { post: (...a: any[]) => post(...a), get: vi.fn(), defaults: { headers: { common: {} } } }
  return { default: instance, ...instance }
})
vi.mock('electron', () => ({
  app: {
    getPath: vi.fn(() => os.tmpdir()), setPath: vi.fn(),
    requestSingleInstanceLock: vi.fn(() => false), quit: vi.fn(), on: vi.fn(),
  },
  ipcMain: { handle: vi.fn((channel: string, listener: (...args: any[]) => any) => handlers.set(channel, listener)) },
  dialog: {}, shell: {},
  BrowserWindow: { getAllWindows: vi.fn(() => []), fromWebContents: vi.fn(() => null) },
  Notification: class { static isSupported = () => false },
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

async function resetHandler() {
  const originalAppend = fs.appendFileSync.bind(fs)
  vi.spyOn(fs, 'appendFileSync').mockImplementation(((file: fs.PathOrFileDescriptor, data: any, options?: any) => {
    if (typeof file === 'string' && file.endsWith('gamachine.log')) return
    return originalAppend(file, data, options)
  }) as typeof fs.appendFileSync)
  await import('../main/background')
  const listener = handlers.get('profile-reset')
  expect(listener).toBeTypeOf('function')
  return listener!
}

const fakeHttp = () => ({ post: vi.fn() })

describe('profile-reset · the request (helpers/profile-reset.ts)', () => {
  it('posts to /profile/reset with the session token and the UI secret', async () => {
    const http = fakeHttp()
    http.post.mockResolvedValue({ data: { cleared: 42 } })
    const reset = createProfileReset({ baseUrl: () => 'http://127.0.0.1:9', appToken: 'tok', uiSecret: 'sec', http })
    await expect(reset()).resolves.toEqual({ cleared: 42 })
    expect(http.post).toHaveBeenCalledTimes(1)
    const [url, body, config] = http.post.mock.calls[0]
    expect(url).toBe('http://127.0.0.1:9/profile/reset')
    expect(body).toEqual({})
    expect(config.headers).toEqual({ 'X-Session-Token': 'tok', 'X-Gamachine-UI-Secret': 'sec' })
  })

  it('a refused reset rejects with the backend detail', async () => {
    const http = fakeHttp()
    http.post.mockRejectedValue({ response: { data: { detail: 'Profile reset requires the app UI' } } })
    const reset = createProfileReset({ baseUrl: () => 'http://x', appToken: 't', uiSecret: 's', http })
    await expect(reset()).rejects.toThrow('Profile reset requires the app UI')
  })
})

describe('profile-reset · the registered channel (background.ts)', () => {
  it('is on the invoke whitelist', () => {
    expect(ALLOWED_INVOKE_CHANNELS.has('profile-reset')).toBe(true)
  })

  it('a frame that is not the app is refused before any request', async () => {
    post.mockReset()
    const listener = await resetHandler()
    expect(() => listener(foreign)).toThrow(/IPC reddedildi/)
    expect(post).not.toHaveBeenCalled()
  })

  it('takes nothing from the renderer and sends nothing before the backend is up', async () => {
    post.mockReset()
    const listener = await resetHandler()
    // No backend port in the test process: the real handler rejects without a request, whatever
    // the renderer passes.
    await expect(listener(own, { path: '/remote/enable' }, 'secret')).rejects.toThrow()
    expect(post).not.toHaveBeenCalled()
  })
})
