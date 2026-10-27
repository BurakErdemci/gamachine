import { EventEmitter } from 'events'
import path from 'path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ComputerUseHost, type ComputerUseDeps } from '../main/helpers/computer-use'
import { ComputerLive, HALT_SHORTCUT, haltShortcut, type ComputerLiveDeps } from '../main/helpers/computer-use-live'

const WIN_ENV = ['PATH', 'SYSTEMROOT', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'ProgramData',
  'ALLUSERSPROFILE', 'TEMP', 'TMP', 'USERNAME', 'HOMEDRIVE', 'HOMEPATH']
const shortUserData = path.join(path.parse(process.cwd()).root, 'gm-d')
const PIPE = /^\\\\\.\\pipe\\gamachine-cua-[0-9a-f]{8}$/

function fixture(platform = 'win32') {
  const children: any[] = []
  const fs = {
    existsSync: vi.fn(() => true), mkdirSync: vi.fn(), chmodSync: vi.fn(), unlinkSync: vi.fn(), rmdirSync: vi.fn(),
    mkdtempSync: vi.fn((prefix: string) => prefix + 'random'),
  }
  const spawn = vi.fn(() => {
    const child: any = new EventEmitter()
    child.stdout = new EventEmitter()
    child.stderr = new EventEmitter()
    child.kill = vi.fn((signal: string) => { queueMicrotask(() => child.emit('exit', 0, signal)); return true })
    children.push(child)
    return child
  })
  const connect = vi.fn((_p: string, cb: () => void) => {
    const socket: any = new EventEmitter()
    socket.destroy = vi.fn()
    queueMicrotask(cb)
    return socket
  })
  const http = { request: vi.fn(async (config: any) => {
    if (config.method === 'get') return { data: { enabled: true, mode: 'auto', runtime: { running: false, driver_path: null, socket_path: null } } }
    return { data: {} }
  }) }
  const host = new ComputerUseHost({
    platform, spawn, connect, fs: fs as unknown as ComputerUseDeps['fs'], http,
    baseUrl: () => 'http://localhost:9', appToken: 'token', uiSecret: 'ui-secret',
    paths: { resourcesPath: undefined, repoRoot: path.resolve('repo'), userData: shortUserData, tmpdir: path.join(path.parse(process.cwd()).root, 'tmp') },
    systemPreferences: { isTrustedAccessibilityClient: vi.fn(() => true), getMediaAccessStatus: vi.fn(() => 'granted') },
    shell: { openExternal: vi.fn(async () => {}) },
    now: Date.now, setTimeout, clearTimeout, log: vi.fn(),
  })
  return { host, fs, spawn, children, connect, http }
}

describe('ComputerUseHost on win32', () => {
  beforeEach(() => { vi.useFakeTimers() })
  afterEach(() => { vi.useRealTimers(); vi.unstubAllEnvs() })

  it('is available with the .exe driver, from the same directories', () => {
    const f = fixture()
    expect(f.host.available).toBe(true)
    expect(f.host.driverPath()).toBe(path.join(path.resolve('repo'), 'Backend', 'vendor', 'cua', 'cua-driver.exe'))
    expect(fixture('darwin').host.driverPath()).toBe(path.join(path.resolve('repo'), 'Backend', 'vendor', 'cua', 'cua-driver'))
  })

  it('stays unavailable on win32 without the driver file and on other platforms', () => {
    const f = fixture()
    f.fs.existsSync.mockReturnValue(false)
    expect(f.host.available).toBe(false)
    expect(fixture('linux').host.available).toBe(false)
  })

  it('spawns serve on a named pipe with the exact Windows environment and no mac flags', async () => {
    for (const key of [...WIN_ENV, 'HOME', 'TMPDIR']) vi.stubEnv(key, `value-of-${key}`)
    vi.stubEnv('UNRELATED_SECRET', 'must-not-leak')
    const f = fixture()
    await f.host.start()
    const [binary, args, options] = f.spawn.mock.calls[0] as any
    const pipe = f.host.socketPath!
    expect(pipe).toMatch(PIPE)
    expect(binary).toBe(f.host.driverPath())
    expect(args).toEqual(['serve', '--embedded', '--socket', pipe, '--no-overlay'])
    const home = path.join(shortUserData, 'cua', 'home')
    expect(options.env).toEqual({
      ...Object.fromEntries(WIN_ENV.map(k => [k, `value-of-${k}`])),
      CUA_DRIVER_EMBEDDED: '1', DO_NOT_TRACK: '1', CUA_DRIVER_RS_TELEMETRY_ENABLED: '0',
      CUA_DRIVER_RS_UPDATE_CHECK: '0', CUA_DRIVER_DISABLE_RUN_SCRIPT: '1',
      CUA_DRIVER_RS_HOME: home, CUA_DRIVER_TELEMETRY_HOME: home,
    })
    expect(options.env).not.toHaveProperty('CUA_DRIVER_HOST_BUNDLE_ID')
    expect(f.connect).toHaveBeenCalledWith(pipe, expect.any(Function))
    expect(f.fs.mkdirSync).toHaveBeenCalledWith(home, { recursive: true, mode: 0o700 })
    expect(f.fs.mkdtempSync).not.toHaveBeenCalled()
    expect(f.http.request).toHaveBeenLastCalledWith(expect.objectContaining({
      url: 'http://localhost:9/computer-use/runtime', data: { running: true, driver_path: binary, socket_path: pipe },
    }))
  })

  it('leaves out Windows variables that are not set', async () => {
    for (const key of WIN_ENV) vi.stubEnv(key, undefined as unknown as string)
    vi.stubEnv('PATH', 'only-path')
    const f = fixture()
    await f.host.start()
    const env = (f.spawn.mock.calls[0] as any)[2].env
    expect(env.PATH).toBe('only-path')
    for (const key of WIN_ENV.filter(k => k !== 'PATH')) expect(env).not.toHaveProperty(key)
  })

  it('never unlinks or removes anything for a pipe, and clears the path on stop and on a crash', async () => {
    const f = fixture()
    await f.host.start()
    await f.host.stop()
    expect(f.host.socketPath).toBeNull()
    await f.host.start()
    expect(f.host.socketPath).toMatch(PIPE)
    f.children[1].emit('exit', 1)
    await vi.advanceTimersByTimeAsync(0)
    expect(f.host.socketPath).toBeNull()
    expect(f.fs.unlinkSync).not.toHaveBeenCalled()
    expect(f.fs.rmdirSync).not.toHaveBeenCalled()
  })

  it('still unlinks the socket file on darwin', async () => {
    const f = fixture('darwin')
    await f.host.start()
    const socket = f.host.socketPath!
    await f.host.stop()
    expect(f.fs.unlinkSync).toHaveBeenCalledWith(socket)
  })

  it.each([['win32', 'win32'], ['darwin', 'darwin']])('reports the platform in the status answer (%s)', async (platform, expected) => {
    const f = fixture(platform)
    const status: any = await f.host.invoke({ op: 'status' })
    expect(status.platform).toBe(expected)
    expect(status.available).toBe(true)
  })

  it('remove-grant accepts Windows app ids on win32 and rejects malformed ones', async () => {
    const f = fixture()
    const ok = ['win:c:\\program files\\x\\y.exe', 'win:clipboard', 'com.example.app']
    for (const id of ok) {
      f.http.request.mockClear()
      await f.host.invoke({ op: 'remove-grant', bundleId: id })
      expect(f.http.request).toHaveBeenCalledWith(expect.objectContaining({
        method: 'delete', url: `http://localhost:9/computer-use/grants/${encodeURIComponent(id)}`,
      }))
    }
    for (const id of ['win:', 'win:\u0000x', 'win:a\nb', `win:${'a'.repeat(1021)}`, 'WIN:x', 'c:\\x.exe', 7, undefined]) {
      f.http.request.mockClear()
      expect(await f.host.invoke({ op: 'remove-grant', bundleId: id })).toEqual({ error: 'Unknown operation or malformed computer-use arguments.' })
      expect(f.http.request).not.toHaveBeenCalled()
    }
  })

  it('remove-grant does not accept a Windows id on darwin', async () => {
    const f = fixture('darwin')
    expect(await f.host.invoke({ op: 'remove-grant', bundleId: 'win:c:\\x\\y.exe' }))
      .toEqual({ error: 'Unknown operation or malformed computer-use arguments.' })
    expect(f.http.request).not.toHaveBeenCalled()
  })
})

describe('halt shortcut', () => {
  beforeEach(() => { vi.useFakeTimers() })
  afterEach(() => { vi.useRealTimers() })

  it('keeps the exported value and picks Ctrl+Alt+Esc only on win32', () => {
    expect(HALT_SHORTCUT).toBe('CommandOrControl+Shift+Escape')
    expect(haltShortcut('darwin')).toBe('CommandOrControl+Shift+Escape')
    expect(haltShortcut('linux')).toBe('CommandOrControl+Shift+Escape')
    expect(haltShortcut('win32')).toBe('Control+Alt+Escape')
  })

  it.each([['win32', 'Control+Alt+Escape'], ['darwin', 'CommandOrControl+Shift+Escape']])(
    'registers and unregisters the %s chord', async (platform, accelerator) => {
      const now = Date.now()
      const activity = { active: true, conversation_id: 7, started_at: now - 5000,
        last: { tool: 'computer_click', bundle_id: 'a', app_name: 'A', pid: 4242, window_id: 55, at: now, decision: 'allow' },
        target: { bundle_id: 'a', app_name: 'A', pid: 4242, window_id: 55 } }
      const spawn = vi.fn(() => {
        const child: any = new EventEmitter()
        child.stdout = new EventEmitter()
        child.stderr = new EventEmitter()
        child.stdin = Object.assign(new EventEmitter(), { write: vi.fn((line: string) => {
          const msg = JSON.parse(line)
          if (msg.id !== undefined) queueMicrotask(() => child.stdout.emit('data', Buffer.from(JSON.stringify({
            jsonrpc: '2.0', id: msg.id, result: { content: [{ type: 'text', text: '{"windows":[]}' }] } }) + '\n')))
          return true
        }) })
        child.kill = vi.fn(() => { queueMicrotask(() => child.emit('exit', null, 'SIGTERM')); return true })
        return child
      })
      const indicator = () => {
        const win: any = new EventEmitter()
        Object.assign(win, { isDestroyed: () => false, webContents: { send: vi.fn() }, close: vi.fn(), showInactive: vi.fn(),
          getPosition: () => [0, 0], setAlwaysOnTop: vi.fn(), setVisibleOnAllWorkspaces: vi.fn() })
        return win
      }
      const shortcut = { register: vi.fn(() => true), unregister: vi.fn() }
      const area = { x: 0, y: 0, width: 1440, height: 900 }
      const deps: ComputerLiveDeps = {
        host: { running: true, socketPath: platform === 'win32' ? '\\\\.\\pipe\\gamachine-cua-1' : '/p/d.sock',
          driverPath: () => '/app/cua/cua-driver', invoke: vi.fn(async () => ({ halted: true })) } as any,
        http: { request: vi.fn(async () => ({ data: activity })) } as any, baseUrl: () => 'http://127.0.0.1:9',
        appToken: 't', platform, userData: '/user', spawn: spawn as any,
        windows: () => [], newIndicator: indicator as any, loadIndicator: vi.fn(async () => {}), preload: '/p.js',
        workAreas: () => ({ primary: area, all: [area] }), shortcut,
        fs: { readFileSync: vi.fn(() => { throw new Error('ENOENT') }), writeFileSync: vi.fn() },
        focusMain: vi.fn(), mainVisible: () => false, now: () => Date.now(), setTimeout, clearTimeout, log: vi.fn(),
      } as unknown as ComputerLiveDeps
      const live = new ComputerLive(deps)
      live.beginPolling()
      await vi.advanceTimersByTimeAsync(10)
      expect(shortcut.register).toHaveBeenCalledWith(accelerator, expect.any(Function))
      live.shutdown()
      await vi.advanceTimersByTimeAsync(10)
      expect(shortcut.unregister).toHaveBeenCalledWith(accelerator)
    })
})
