import fs from 'fs'
import os from 'os'
import path from 'path'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ALLOWED_INVOKE_CHANNELS } from '../main/helpers/ipc-whitelist'
import { REMOTE_ROUTES, createRemoteControl, isRelayOrigin } from '../main/helpers/remote-control'

/**
 * Main-process half of remote control: the `remote-control` channel (UI secret
 * only in main, only on the routes that need it) and the keep-awake blocker.
 * The blocker is a fake; nothing here reaches a network or the OS.
 */

const SECRET = 'ui-secret-xyz'
const TOKEN = 'app-token-abc'

const makeBlocker = () => {
  let next = 1
  const live = new Set<number>()
  return {
    live,
    start: vi.fn((_type: string) => { const id = next++; live.add(id); return id }),
    stop: vi.fn((id: number) => { live.delete(id) }),
    isStarted: vi.fn((id: number) => live.has(id)),
  }
}

const setup = (reply: (config: any) => any = () => ({ data: {} })) => {
  const blocker = makeBlocker()
  const request = vi.fn(async (config: any) => reply(config))
  const rc = createRemoteControl({
    baseUrl: () => 'http://127.0.0.1:9',
    appToken: TOKEN, uiSecret: SECRET, http: { request }, blocker,
  })
  return { rc, blocker, request }
}

describe('remote-control · routes', () => {
  it('sends the UI secret on exactly enable, pair-start, pair-approve and set-relay-url', async () => {
    const { rc, request } = setup()
    const withSecret: string[] = []
    const args: Record<string, unknown> = { 'remove-device': 'dev_1', 'set-relay-url': null, 'set-keep-awake': true }
    for (const action of Object.keys(REMOTE_ROUTES)) {
      request.mockClear()
      await rc.invoke(action, args[action])
      const headers = request.mock.calls[0][0].headers
      expect(headers['X-Session-Token']).toBe(TOKEN)
      if (headers['X-Gamachine-UI-Secret'] === SECRET) withSecret.push(action)
    }
    expect(withSecret.sort()).toEqual(['enable', 'pair-approve', 'pair-start', 'set-relay-url'])
  })

  it('maps actions to the backend routes and bodies', async () => {
    const { rc, request } = setup()
    await rc.invoke('remove-device', 'abc-DEF_1')
    expect(request.mock.calls[0][0]).toMatchObject({ method: 'delete', url: 'http://127.0.0.1:9/remote/devices/abc-DEF_1' })
    await rc.invoke('set-relay-url', 'https://my.relay.example')
    expect(request.mock.calls[1][0]).toMatchObject({ method: 'put', url: 'http://127.0.0.1:9/remote/relay-url', data: { url: 'https://my.relay.example' } })
    await rc.invoke('set-keep-awake', false)
    expect(request.mock.calls[2][0]).toMatchObject({ method: 'put', url: 'http://127.0.0.1:9/remote/keep-awake', data: { enabled: false } })
  })

  it('refuses unknown actions and malformed arguments before any request', async () => {
    const { rc, request } = setup()
    await expect(rc.invoke('toString')).rejects.toThrow()
    await expect(rc.invoke('../approval-mode')).rejects.toThrow()
    await expect(rc.invoke('remove-device', '../../approval-mode')).rejects.toThrow()
    await expect(rc.invoke('set-keep-awake', 'yes')).rejects.toThrow()
    await expect(rc.invoke('set-relay-url', 42)).rejects.toThrow()
    expect(request).not.toHaveBeenCalled()
  })

  // One table with the backend (Backend/tests/test_remote_routes.py) so both rules stay the same.
  const RELAY_CASES = JSON.parse(fs.readFileSync(
    path.join(__dirname, '../../../Backend/tests/relay_origin_cases.json'), 'utf8')) as { accept: string[]; reject: string[] }
  const TOO_LONG = `https://${'a'.repeat(505)}.org`

  it('accepts only relay origins, by the table shared with the backend', () => {
    for (const url of RELAY_CASES.accept) expect(isRelayOrigin(url), url).toBe(true)
    for (const url of [...RELAY_CASES.reject, TOO_LONG]) expect(isRelayOrigin(url), url).toBe(false)
  })

  it('refuses a bad relay URL with bad_relay_url before any request; null or empty resets', async () => {
    const { rc, request } = setup()
    for (const url of [...RELAY_CASES.reject, TOO_LONG]) {
      expect(await rc.invoke('set-relay-url', url), url).toEqual({ ok: false, code: 'bad_relay_url' })
    }
    expect(request).not.toHaveBeenCalled()
    await rc.invoke('set-relay-url', null)
    await rc.invoke('set-relay-url', '')
    await rc.invoke('set-relay-url', 'http://127.0.0.1:8799')
    expect(request.mock.calls.map(c => c[0].data)).toEqual([{ url: null }, { url: '' }, { url: 'http://127.0.0.1:8799' }])
  })

  it('returns coded refusals, not exceptions, and never echoes the secret', async () => {
    const { rc } = setup(() => {
      throw { response: { status: 503, data: { detail: { code: 'relay_unreachable' } } } }
    })
    const res = await rc.invoke('pair-start')
    expect(res).toEqual({ ok: false, status: 503, code: 'relay_unreachable', message: undefined })
    expect(JSON.stringify(res)).not.toContain(SECRET)
  })

  it('a network failure is `unreachable`, a missing port is `backend_not_ready`', async () => {
    const { rc } = setup(() => { throw new Error('ECONNREFUSED') })
    expect(await rc.invoke('status')).toEqual({ ok: false, code: 'unreachable' })
    const early = createRemoteControl({
      baseUrl: () => { throw new Error('not ready') }, appToken: TOKEN, uiSecret: SECRET,
      http: { request: vi.fn() }, blocker: makeBlocker(),
    })
    expect(await early.invoke('enable')).toEqual({ ok: false, code: 'backend_not_ready' })
  })
})

describe('remote-control · keep awake', () => {
  it('follows keep_awake_active from any answer: on once, off once', async () => {
    let active = true
    const { rc, blocker } = setup(() => ({ data: { enabled: true, keep_awake: true, keep_awake_active: active } }))
    await rc.invoke('status')
    await rc.invoke('status')
    expect(blocker.start).toHaveBeenCalledTimes(1)
    expect(blocker.start).toHaveBeenCalledWith('prevent-app-suspension')
    expect(rc.keepAwakeActive()).toBe(true)
    active = false
    await rc.invoke('disable')
    expect(blocker.stop).toHaveBeenCalledTimes(1)
    expect(blocker.live.size).toBe(0)
    expect(rc.keepAwakeActive()).toBe(false)
  })

  it('an answer without the field leaves the blocker as it is', async () => {
    let data: any = { keep_awake_active: true }
    const { rc, blocker } = setup(() => ({ data }))
    await rc.invoke('status')
    data = { devices: [] }
    await rc.invoke('devices')
    expect(blocker.start).toHaveBeenCalledTimes(1)
    expect(blocker.stop).not.toHaveBeenCalled()
    expect(rc.keepAwakeActive()).toBe(true)
  })

  it('three failed polls in a row release it; one success resets the count', async () => {
    let fail = false
    const { rc, blocker } = setup(() => {
      if (fail) throw new Error('down')
      return { data: { keep_awake_active: true } }
    })
    await rc.poll()
    fail = true
    await rc.poll(); await rc.poll()
    fail = false
    await rc.poll()
    fail = true
    await rc.poll(); await rc.poll()
    expect(rc.keepAwakeActive()).toBe(true)
    await rc.poll()
    expect(rc.keepAwakeActive()).toBe(false)
    expect(blocker.live.size).toBe(0)
  })

  // Answers held until released, keyed by route, to reorder them at will.
  const deferred = () => {
    const held: Array<{ url: string; resolve: (v: any) => void }> = []
    const { rc, blocker, request } = setup(config => new Promise(resolve => held.push({ url: config.url, resolve })))
    const release = (route: string, data: any, newest = false) => {
      const match = held.map((h, i) => h.url.endsWith(route) ? i : -1).filter(i => i >= 0)
      const i = newest ? match[match.length - 1] : match[0]
      held.splice(i, 1)[0].resolve({ data })
    }
    return { rc, blocker, request, release }
  }
  const flush = async () => { for (let i = 0; i < 5; i++) await Promise.resolve() }

  it.each(['disable', 'forget'])('%s releases it at once, and an older poll cannot restart it', async action => {
    const { rc, blocker, release } = deferred()
    const on = rc.invoke('status'); await flush(); release('/remote/status', { keep_awake_active: true }); await on
    const olderPoll = rc.poll(); await flush()
    const change = rc.invoke(action); await flush()
    expect(blocker.live.size, 'released before the backend answers').toBe(0)
    release(`/remote/${action}`, { keep_awake_active: false }); await change
    release('/remote/status', { keep_awake_active: true }); await olderPoll
    expect(blocker.live.size).toBe(0)
    expect(blocker.start).toHaveBeenCalledTimes(1)
  })

  it('a poll answered while a state change is in flight, or before it settled, is ignored', async () => {
    const { rc, blocker, release } = deferred()
    const change = rc.invoke('set-keep-awake', false); await flush()
    const during = rc.poll(); await flush()
    release('/remote/status', { keep_awake_active: true }); await during
    expect(blocker.live.size).toBe(0)
    release('/remote/keep-awake', { keep_awake_active: false }); await change
    const after = rc.poll(); await flush()
    release('/remote/status', { keep_awake_active: true }); await after
    expect(blocker.live.size, 'a poll started after the change settled counts').toBe(1)
  })

  it('only the newest of overlapping state changes decides; an older poll answer never overrides a newer one', async () => {
    const { rc, blocker, release } = deferred()
    const enable = rc.invoke('enable'); await flush()
    const disable = rc.invoke('disable'); await flush()
    release('/remote/disable', { keep_awake_active: false }); await disable
    release('/remote/enable', { keep_awake_active: true }); await enable
    expect(blocker.live.size, 'the enable answer is older than the disable').toBe(0)
    const first = rc.poll(); await flush()
    const second = rc.poll(); await flush()
    release('/remote/status', { keep_awake_active: true }, true); await second
    expect(blocker.live.size).toBe(1)
    release('/remote/status', { keep_awake_active: false }); await first
    expect(blocker.live.size, 'the first poll answered last but is older').toBe(1)
  })

  it('shutdown (app quit) releases it and stops polling', async () => {
    vi.useFakeTimers()
    try {
      const { rc, blocker, request } = setup(() => ({ data: { keep_awake_active: true } }))
      rc.start()
      await vi.advanceTimersByTimeAsync(0)
      expect(rc.keepAwakeActive()).toBe(true)
      await vi.advanceTimersByTimeAsync(30_000)
      const calls = request.mock.calls.length
      expect(calls).toBe(2)
      rc.shutdown()
      expect(blocker.live.size).toBe(0)
      await vi.advanceTimersByTimeAsync(90_000)
      expect(request.mock.calls.length).toBe(calls)
    } finally {
      vi.useRealTimers()
    }
  })
})

// ── wiring in background.ts ───────────────────────────────────────────────
const handlers = new Map<string, (...args: any[]) => any>()
vi.mock('electron', () => ({
  app: {
    getPath: vi.fn(() => os.tmpdir()), setPath: vi.fn(),
    requestSingleInstanceLock: vi.fn(() => false), quit: vi.fn(), on: vi.fn(),
  },
  ipcMain: { handle: vi.fn((channel: string, listener: (...args: any[]) => any) => handlers.set(channel, listener)) },
  dialog: {}, shell: {},
  BrowserWindow: { getAllWindows: vi.fn(() => []) },
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

async function remoteHandler() {
  const originalAppend = fs.appendFileSync.bind(fs)
  vi.spyOn(fs, 'appendFileSync').mockImplementation(((file: fs.PathOrFileDescriptor, data: any, options?: any) => {
    if (typeof file === 'string' && file.endsWith('gamachine.log')) return
    return originalAppend(file, data, options)
  }) as typeof fs.appendFileSync)
  await import('../main/background')
  const listener = handlers.get('remote-control')
  expect(listener).toBeTypeOf('function')
  return listener!
}

describe('remote-control · registration', () => {
  beforeEach(() => { vi.useRealTimers() })

  it('is on the whitelist and registered by the main process', async () => {
    expect(ALLOWED_INVOKE_CHANNELS.has('remote-control')).toBe(true)
    await remoteHandler()
  })

  it('a frame that is not the app is refused', async () => {
    const listener = await remoteHandler()
    expect(() => listener(foreign, 'enable')).toThrow(/IPC reddedildi/)
  })

  it('the app frame reaches the helper (no backend in tests: backend_not_ready)', async () => {
    const listener = await remoteHandler()
    await expect(listener(own, 'enable')).resolves.toEqual({ ok: false, code: 'backend_not_ready' })
    await expect(listener(own, 'nope')).rejects.toThrow()
  })
})
