import { EventEmitter } from 'events'
import path from 'path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ComputerUseHost, type ComputerUseDeps } from '../main/helpers/computer-use'

const shortUserData = path.join(path.parse(process.cwd()).root, 'gm-d')

function fixture(options: { platform?: string; userData?: string; available?: boolean } = {}) {
  const children: any[] = []
  const sockets: any[] = []
  let ready = true
  let settings = { enabled: true, mode: 'auto' }
  const fs = {
    existsSync: vi.fn(() => options.available !== false),
    mkdirSync: vi.fn(), chmodSync: vi.fn(), unlinkSync: vi.fn(), rmdirSync: vi.fn(),
    mkdtempSync: vi.fn((prefix: string) => prefix + 'random'),
  }
  const spawn = vi.fn(() => {
    const child: any = new EventEmitter()
    child.stdout = new EventEmitter()
    child.stderr = new EventEmitter()
    child.exitCode = null
    child.signalCode = null
    child.kill = vi.fn((signal: string) => {
      if (signal === 'SIGKILL') child.emit('exit', null, signal)
      return true
    })
    children.push(child)
    return child
  })
  const connect = vi.fn((_p: string, cb: () => void) => {
    const socket: any = new EventEmitter()
    socket.destroy = vi.fn()
    sockets.push(socket)
    queueMicrotask(() => ready ? cb() : socket.emit('error', new Error('not ready')))
    return socket
  })
  const http = { request: vi.fn(async (config: any) => ({ data: config.method === 'get' ? settings : {} })) }
  const log = vi.fn()
  const host = new ComputerUseHost({
    platform: options.platform ?? 'darwin', spawn, connect, fs: fs as unknown as ComputerUseDeps['fs'], http,
    baseUrl: () => 'http://localhost:9', appToken: 'token', uiSecret: 'ui-secret',
    paths: { resourcesPath: undefined, repoRoot: path.resolve('repo'),
      userData: options.userData ?? shortUserData, tmpdir: path.join(path.parse(process.cwd()).root, 'tmp') },
    systemPreferences: { isTrustedAccessibilityClient: vi.fn(() => true), getMediaAccessStatus: vi.fn(() => 'granted') },
    shell: { openExternal: vi.fn(async () => {}) },
    now: Date.now, setTimeout, clearTimeout, log,
  })
  return { host, fs, spawn, children, connect, sockets, http, log,
    ready: (value: boolean) => { ready = value }, settings: (value: typeof settings) => { settings = value } }
}

describe('ComputerUseHost', () => {
  beforeEach(() => { vi.useFakeTimers() })
  afterEach(() => { vi.useRealTimers(); vi.unstubAllEnvs() })

  it('spawns the embedded binary with only the approved environment and reports after readiness', async () => {
    vi.stubEnv('UNRELATED_SECRET', 'must-not-leak')
    const f = fixture()
    await f.host.start()
    const [binary, args, options] = f.spawn.mock.calls[0] as any
    expect(binary).toBe(f.host.driverPath())
    expect(args).toEqual(['serve', '--embedded', '--socket', f.host.socketPath,
      '--permission-mode', 'standard', '--host-bundle-id', 'com.gamachine.app', '--no-permissions-gate'])
    expect(options.stdio).toEqual(['ignore', 'pipe', 'pipe'])
    expect(options.env).toEqual({
      ...Object.fromEntries(['PATH', 'HOME', 'TMPDIR'].filter(k => process.env[k] !== undefined).map(k => [k, process.env[k]])),
      CUA_DRIVER_EMBEDDED: '1', DO_NOT_TRACK: '1', CUA_DRIVER_RS_TELEMETRY_ENABLED: '0',
      CUA_DRIVER_RS_UPDATE_CHECK: '0', CUA_DRIVER_DISABLE_RUN_SCRIPT: '1',
      CUA_DRIVER_HOST_BUNDLE_ID: 'com.gamachine.app',
      CUA_DRIVER_RS_HOME: path.join(shortUserData, 'cua', 'home'),
      CUA_DRIVER_TELEMETRY_HOME: path.join(shortUserData, 'cua', 'home'),
    })
    expect(f.host.running).toBe(true)
    expect(f.http.request).toHaveBeenLastCalledWith(expect.objectContaining({
      url: 'http://localhost:9/computer-use/runtime', method: 'post',
      headers: { 'X-Session-Token': 'token', 'X-Gamachine-UI-Secret': 'ui-secret' },
      data: { running: true, driver_path: binary, socket_path: f.host.socketPath },
    }))
    f.children[0].stdout.emit('data', Buffer.from('hello'))
    expect(f.log).toHaveBeenCalledWith('[cua] hello')
  })

  it('waits for socket acceptance, not just the spawn, and destroys every probe', async () => {
    const f = fixture()
    f.ready(false)
    const start = f.host.start()
    await vi.advanceTimersByTimeAsync(500)
    expect(f.host.running).toBe(false)
    expect(f.http.request).not.toHaveBeenCalled()
    f.ready(true)
    await vi.advanceTimersByTimeAsync(100)
    await start
    expect(f.host.running).toBe(true)
    expect(f.sockets.every(s => s.destroy.mock.calls.length === 1)).toBe(true)
  })

  it.each([shortUserData, path.resolve('é'.repeat(100))])('uses private directories and short byte-length sockets for %s', async userData => {
    const f = fixture({ userData })
    await f.host.start()
    expect(Buffer.byteLength(f.host.socketPath!)).toBeLessThan(100)
    expect(f.host.socketPath).toMatch(/d-[a-f0-9]{8}\.sock$/)
    expect(f.fs.mkdirSync).toHaveBeenCalledWith(path.join(userData, 'cua'), { recursive: true, mode: 0o700 })
    expect(f.fs.chmodSync).toHaveBeenCalledWith(path.join(userData, 'cua'), 0o700)
    expect(f.fs.chmodSync).toHaveBeenCalledWith(path.join(userData, 'cua', 'home'), 0o700)
    if (userData.includes('é')) {
      expect(f.fs.mkdtempSync).toHaveBeenCalled()
      expect(f.fs.chmodSync).toHaveBeenCalledWith(expect.stringContaining('cua-'), 0o700)
    } else expect(f.fs.mkdtempSync).not.toHaveBeenCalled()
  })

  it('marks unexpected exits stopped, reports them, and limits automatic restarts', async () => {
    const f = fixture()
    await f.host.reconcile()
    f.children[0].emit('exit', 1)
    expect(f.host.running).toBe(false)
    await f.host.reconcile()
    expect(f.spawn).toHaveBeenCalledTimes(2)
    f.children[1].emit('exit', 1)
    await f.host.reconcile()
    expect(f.spawn).toHaveBeenCalledTimes(2)
    expect(f.http.request.mock.calls.filter(([c]) => c.data?.running === false)).toHaveLength(2)
    await vi.advanceTimersByTimeAsync(60_000)
    await f.host.reconcile()
    expect(f.spawn).toHaveBeenCalledTimes(3)
  })

  it('stops idempotently, waits two seconds, kills and removes the socket', async () => {
    const f = fixture()
    await f.host.start()
    const socket = f.host.socketPath
    const stopping = f.host.stop()
    await vi.advanceTimersByTimeAsync(1999)
    expect(f.children[0].kill.mock.calls).toEqual([['SIGTERM']])
    await vi.advanceTimersByTimeAsync(1)
    await stopping
    await f.host.stop()
    expect(f.children[0].kill.mock.calls).toEqual([['SIGTERM'], ['SIGKILL']])
    expect(f.fs.unlinkSync).toHaveBeenCalledWith(socket)
    expect(f.host.running).toBe(false)
    expect(f.http.request).toHaveBeenLastCalledWith(expect.objectContaining({ data: { running: false, driver_path: null, socket_path: null } }))
  })

  it('does not SIGKILL a child that exited after SIGTERM', async () => {
    const f = fixture()
    await f.host.start()
    const stopping = f.host.stop()
    await vi.advanceTimersByTimeAsync(0)
    f.children[0].emit('exit', 0)
    await stopping
    await vi.advanceTimersByTimeAsync(3000)
    expect(f.children[0].kill.mock.calls).toEqual([['SIGTERM']])
  })

  it.each([
    ['darwin', true, true, 'auto', 1], ['darwin', false, true, 'auto', 0],
    ['darwin', true, false, 'auto', 0], ['darwin', true, true, 'balanced', 0],
    ['darwin', true, true, 'step', 0], ['win32', true, true, 'auto', 0],
  ])('reconciles %s available=%s enabled=%s mode=%s', async (platform, available, enabled, mode, count) => {
    const f = fixture({ platform: platform as string, available: available as boolean })
    f.settings({ enabled: enabled as boolean, mode: mode as string })
    await Promise.all([f.host.reconcile(), f.host.reconcile()])
    expect(f.spawn).toHaveBeenCalledTimes(count as number)
    if (platform !== 'darwin') { await f.host.start(); expect(f.spawn).not.toHaveBeenCalled() }
  })

  it('stops an existing daemon on mode change and shuts down polling', async () => {
    const f = fixture()
    f.host.beginPolling()
    await vi.advanceTimersByTimeAsync(0)
    expect(f.host.running).toBe(true)
    f.settings({ enabled: true, mode: 'step' })
    await vi.advanceTimersByTimeAsync(7000)
    expect(f.host.running).toBe(false)
    const shutdown = f.host.shutdown()
    await vi.advanceTimersByTimeAsync(2000)
    await shutdown
    const calls = f.http.request.mock.calls.length
    await vi.advanceTimersByTimeAsync(20_000)
    expect(f.http.request).toHaveBeenCalledTimes(calls)
    await f.host.start()
    expect(f.spawn).toHaveBeenCalledTimes(1)
  })

  it('bounds a socket probe that never replies and kills a failed start', async () => {
    const f = fixture()
    f.connect.mockImplementation(() => {
      const socket: any = new EventEmitter()
      socket.destroy = vi.fn()
      return socket
    })
    const start = f.host.start()
    const assertion = expect(start).rejects.toThrow(/socket/)
    await vi.advanceTimersByTimeAsync(12_000)
    await assertion
    expect(f.host.running).toBe(false)
    expect(f.children[0].kill.mock.calls).toEqual([['SIGTERM'], ['SIGKILL']])
  })
})
