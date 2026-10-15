import { EventEmitter } from 'events'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ComputerLive, HALT_SHORTCUT, IDLE_MS, pickWindow, type ComputerLiveDeps } from '../main/helpers/computer-use-live'

const READ_ONLY = new Set(['get_window_state', 'list_windows'])
const SOCKET = '/private/cua/d-1.sock'

function activeBody(over: Record<string, unknown> = {}, target: Record<string, unknown> = {}) {
  const at = Date.now()
  return {
    active: true, conversation_id: 7, started_at: at - 5000,
    last: { tool: 'computer_click', bundle_id: 'com.apple.TextEdit', app_name: 'TextEdit', pid: 4242, window_id: 55, at, decision: 'allow' },
    target: { bundle_id: 'com.apple.TextEdit', app_name: 'TextEdit', pid: 4242, window_id: 55, ...target },
    ...over,
  }
}
const INACTIVE = { active: false, conversation_id: null, started_at: null, last: null, target: null }

function fixture(options: { platform?: string; saved?: string; panel?: boolean } = {}) {
  const state = { activity: INACTIVE as any, windows: null as any }
  const rpc: any[] = []
  const children: any[] = []
  const host = { running: true, socketPath: SOCKET as string | null, driverPath: () => '/app/cua/cua-driver',
    invoke: vi.fn(async (_req: unknown) => ({ halted: true })) }
  const http = { request: vi.fn(async (_config: any) => ({ data: state.activity })) }
  const reply = (msg: any) => {
    if (msg.method === 'initialize') return { protocolVersion: '2024-11-05', capabilities: {} }
    if (msg.params?.name === 'list_windows') {
      return state.windows ?? { content: [{ type: 'text', text: JSON.stringify({ windows: [
        { window_id: 3, pid: 4242, z_index: 0, is_on_screen: false },
        { window_id: 9, pid: 4242, z_index: 2, is_on_screen: true },
        { window_id: 8, pid: 4242, z_index: 5, is_on_screen: true },
      ] }) }] }
    }
    return { content: [{ type: 'text', text: 'state' }, { type: 'image', data: 'iVBORw0KGgo=', mimeType: 'image/png' }] }
  }
  const spawn = vi.fn((_command: string, _args: string[], _options: any) => {
    const child: any = new EventEmitter()
    child.stdout = new EventEmitter()
    child.stderr = new EventEmitter()
    child.stdin = Object.assign(new EventEmitter(), {
      write: vi.fn((line: string) => {
        const msg = JSON.parse(line)
        rpc.push(msg)
        if (msg.id !== undefined) {
          queueMicrotask(() => child.stdout.emit('data', Buffer.from(JSON.stringify({ jsonrpc: '2.0', id: msg.id, result: reply(msg) }) + '\n')))
        }
        return true
      }),
    })
    child.kill = vi.fn(() => { queueMicrotask(() => child.emit('exit', null, 'SIGTERM')); return true })
    children.push(child)
    return child
  })
  const main = { isDestroyed: () => false, webContents: { send: vi.fn() } }
  const indicators: any[] = []
  const newIndicator = vi.fn((opts: Record<string, unknown>) => {
    const win: any = new EventEmitter()
    win.options = opts
    win.destroyed = false
    win.isDestroyed = () => win.destroyed
    win.webContents = { send: vi.fn() }
    win.close = vi.fn(() => { win.destroyed = true; win.emit('closed') })
    win.showInactive = vi.fn()
    win.getPosition = () => [400, 300]
    win.setAlwaysOnTop = vi.fn()
    win.setVisibleOnAllWorkspaces = vi.fn()
    indicators.push(win)
    return win
  })
  const shortcut = { register: vi.fn((_accelerator: string, _callback: () => void) => true), unregister: vi.fn() }
  const fs = {
    readFileSync: vi.fn(() => { if (options.saved === undefined) throw new Error('ENOENT'); return options.saved }),
    writeFileSync: vi.fn(),
  }
  const deps: ComputerLiveDeps = {
    host, http, baseUrl: () => 'http://127.0.0.1:9', appToken: 'app-token-secret', platform: options.platform ?? 'darwin',
    userData: '/user', spawn: spawn as any,
    windows: () => [main, ...indicators.filter(w => !w.destroyed)],
    newIndicator, loadIndicator: vi.fn(async () => {}), preload: '/app/preload.js',
    workAreas: () => ({ primary: { x: 0, y: 25, width: 1440, height: 875 }, all: [{ x: 0, y: 25, width: 1440, height: 875 }] }),
    shortcut, fs, focusMain: vi.fn(), now: () => Date.now(), setTimeout, clearTimeout,
  }
  const live = new ComputerLive(deps)
  // Frames are only captured for a visible Computer tab; most cases watch it.
  if (options.panel !== false) void live.invoke({ op: 'panel-visible', visible: true })
  const frames = () => main.webContents.send.mock.calls.filter(([, p]: any) => typeof p.frame === 'string')
  const windowCalls = () => rpc.filter(m => m.method === 'tools/call')
  return { live, deps, state, rpc, children, host, http, spawn, main, indicators, shortcut, fs, frames, windowCalls }
}

beforeEach(() => { vi.useFakeTimers() })
afterEach(() => { vi.useRealTimers() })

describe('computer-use-live: idle', () => {
  it('polls the activity route with the app token and shows nothing while inactive', async () => {
    const f = fixture()
    f.live.beginPolling()
    await vi.advanceTimersByTimeAsync(3100)
    expect(f.http.request).toHaveBeenCalledTimes(4)
    expect(f.http.request.mock.calls[0][0]).toMatchObject({
      method: 'get', url: 'http://127.0.0.1:9/computer-use/activity', headers: { 'X-Session-Token': 'app-token-secret' },
    })
    expect(f.spawn).not.toHaveBeenCalled()
    expect(f.indicators).toHaveLength(0)
    expect(f.shortcut.register).not.toHaveBeenCalled()
    f.live.shutdown()
  })

  it('does not even ask the backend while the driver is stopped', async () => {
    const f = fixture()
    f.host.running = false
    f.state.activity = activeBody()
    f.live.beginPolling()
    await vi.advanceTimersByTimeAsync(2100)
    expect(f.http.request).not.toHaveBeenCalled()
    expect(f.spawn).not.toHaveBeenCalled()
    f.live.shutdown()
  })

  it('a failing activity request is no news, not a crash', async () => {
    const f = fixture()
    f.http.request.mockRejectedValue(new Error('backend down'))
    f.live.beginPolling()
    await vi.advanceTimersByTimeAsync(2100)
    expect(f.http.request).toHaveBeenCalledTimes(3)
    expect(f.indicators).toHaveLength(0)
    f.live.shutdown()
  })
})

describe('computer-use-live: active', () => {
  it('opens a non-activating always-on-top indicator, registers the shortcut and captures ~8 fps', async () => {
    const f = fixture()
    f.state.activity = activeBody()
    f.live.beginPolling()
    await vi.advanceTimersByTimeAsync(10)
    expect(f.indicators).toHaveLength(1)
    expect(f.indicators[0].options).toMatchObject({
      frame: false, transparent: true, alwaysOnTop: true, skipTaskbar: true, show: false, type: 'panel',
      x: 1440 - 320 - 16, y: 25 + 16, webPreferences: { preload: '/app/preload.js' },
    })
    expect(f.indicators[0].setVisibleOnAllWorkspaces).toHaveBeenCalledWith(true, { visibleOnFullScreen: true, skipTransformProcessType: true })
    f.indicators[0].emit('ready-to-show')
    expect(f.indicators[0].showInactive).toHaveBeenCalled()
    expect(f.deps.loadIndicator).toHaveBeenCalledWith(f.indicators[0])
    expect(f.shortcut.register).toHaveBeenCalledWith(HALT_SHORTCUT, expect.any(Function))
    expect(HALT_SHORTCUT).toBe('CommandOrControl+Shift+Escape')

    const before = f.frames().length
    await vi.advanceTimersByTimeAsync(1000)
    const perSecond = f.frames().length - before
    expect(perSecond).toBeGreaterThanOrEqual(7)
    expect(perSecond).toBeLessThanOrEqual(9)
    const [, payload] = f.frames().at(-1)!
    expect(payload).toMatchObject({ active: true, conversationId: 7, frame: 'data:image/png;base64,iVBORw0KGgo=',
      last: { tool: 'computer_click', app_name: 'TextEdit' } })
    expect(typeof payload.startedAt).toBe('number')
    expect(typeof payload.at).toBe('number')
    // Frames go to the main window only; the indicator gets state updates without pictures.
    for (const [, p] of f.indicators[0].webContents.send.mock.calls) expect(p.frame).toBeNull()
    expect(f.spawn).toHaveBeenCalledTimes(1)
    f.live.shutdown()
  })

  it('spawns one MCP proxy on the host socket with a minimal, secret-free environment', async () => {
    const f = fixture()
    f.state.activity = activeBody()
    f.live.beginPolling()
    await vi.advanceTimersByTimeAsync(500)
    const [command, args, options] = f.spawn.mock.calls[0]
    expect(command).toBe('/app/cua/cua-driver')
    expect(args).toEqual(['mcp', '--embedded', '--socket', SOCKET])
    const env = options.env as Record<string, string>
    for (const key of Object.keys(env)) {
      expect(['PATH', 'HOME', 'TMPDIR'].includes(key) || key.startsWith('CUA_DRIVER_') || key === 'DO_NOT_TRACK').toBe(true)
    }
    expect(JSON.stringify(env)).not.toContain('app-token-secret')
    expect(env.CUA_DRIVER_RS_HOME).toBe('/user/cua/home')
    expect(f.rpc[0]).toMatchObject({ method: 'initialize' })
    expect(f.rpc[1]).toEqual({ jsonrpc: '2.0', method: 'notifications/initialized' })
    f.live.shutdown()
  })

  it('calls only read-only driver tools, without the accessibility tree, at 480 px', async () => {
    const f = fixture()
    f.state.activity = activeBody()
    f.live.beginPolling()
    await vi.advanceTimersByTimeAsync(2000)
    expect(f.windowCalls().length).toBeGreaterThan(5)
    for (const call of f.windowCalls()) expect(READ_ONLY.has(call.params.name)).toBe(true)
    expect(f.windowCalls()[0].params).toEqual({ name: 'get_window_state',
      arguments: { pid: 4242, window_id: 55, include_accessibility_tree: false, max_dimension: 480 } })
    f.live.shutdown()
  })

  it('without a window id, looks the window up once and mirrors the on-screen frontmost one', async () => {
    const f = fixture()
    f.state.activity = activeBody({}, { window_id: null })
    f.live.beginPolling()
    await vi.advanceTimersByTimeAsync(1000)
    const names = f.windowCalls().map(c => c.params.name)
    expect(names[0]).toBe('list_windows')
    expect(names.filter(n => n === 'list_windows')).toHaveLength(1)
    expect(f.windowCalls()[1].params.arguments).toMatchObject({ pid: 4242, window_id: 9 })
    f.live.shutdown()
  })

  it('stops capturing, kills its proxy, closes the indicator and drops the shortcut after 10 s idle', async () => {
    const f = fixture()
    f.state.activity = activeBody()
    f.live.beginPolling()
    await vi.advanceTimersByTimeAsync(1500)
    f.state.activity = INACTIVE
    await vi.advanceTimersByTimeAsync(IDLE_MS - 2000)
    expect(f.children[0].kill).not.toHaveBeenCalled()
    expect(f.indicators[0].close).not.toHaveBeenCalled()
    // Last seen active at the 1 s poll; the first poll more than 10 s later (12 s) goes idle.
    await vi.advanceTimersByTimeAsync(3000)
    expect(f.children[0].kill).toHaveBeenCalledWith('SIGTERM')
    expect(f.indicators[0].close).toHaveBeenCalled()
    expect(f.shortcut.unregister).toHaveBeenCalledWith(HALT_SHORTCUT)
    expect(f.main.webContents.send.mock.calls.at(-1)![1]).toMatchObject({ active: false, frame: null })
    const calls = f.windowCalls().length
    await vi.advanceTimersByTimeAsync(3000)
    expect(f.windowCalls()).toHaveLength(calls)
    f.live.shutdown()
  })

  it('goes idle at once when the driver stops', async () => {
    const f = fixture()
    f.state.activity = activeBody()
    f.live.beginPolling()
    await vi.advanceTimersByTimeAsync(500)
    f.host.running = false
    await vi.advanceTimersByTimeAsync(1000)
    expect(f.children[0].kill).toHaveBeenCalled()
    expect(f.indicators[0].close).toHaveBeenCalled()
    expect(f.shortcut.unregister).toHaveBeenCalled()
    f.live.shutdown()
  })

  it('restarts its proxy when the host socket changes', async () => {
    const f = fixture()
    f.state.activity = activeBody()
    f.live.beginPolling()
    await vi.advanceTimersByTimeAsync(500)
    f.host.socketPath = '/private/cua/d-2.sock'
    await vi.advanceTimersByTimeAsync(500)
    expect(f.children[0].kill).toHaveBeenCalled()
    expect(f.spawn).toHaveBeenCalledTimes(2)
    expect(f.spawn.mock.calls[1][1]).toEqual(['mcp', '--embedded', '--socket', '/private/cua/d-2.sock'])
    f.live.shutdown()
  })

  it('does not capture without a target window pid', async () => {
    const f = fixture()
    f.state.activity = activeBody({ target: null })
    f.live.beginPolling()
    await vi.advanceTimersByTimeAsync(1500)
    expect(f.indicators).toHaveLength(1)
    expect(f.spawn).not.toHaveBeenCalled()
    f.live.shutdown()
  })
})

describe('computer-use-live: halt and ops', () => {
  it("'halt' forwards to the host's halt op for the active conversation and goes idle", async () => {
    const f = fixture()
    f.state.activity = activeBody()
    f.live.beginPolling()
    await vi.advanceTimersByTimeAsync(500)
    expect(await f.live.invoke({ op: 'halt' })).toEqual({ halted: true })
    expect(f.host.invoke).toHaveBeenCalledWith({ op: 'halt', conversationId: 7 })
    expect(f.indicators[0].close).toHaveBeenCalled()
    expect(f.children[0].kill).toHaveBeenCalled()
    // The backend still reports the pre-halt action for a while: it must not bring the indicator back.
    await vi.advanceTimersByTimeAsync(3000)
    expect(f.indicators).toHaveLength(1)
    // A newer action (the user's next turn) does.
    f.state.activity = activeBody()
    f.state.activity.last.at = Date.now() + 1
    await vi.advanceTimersByTimeAsync(1000)
    expect(f.indicators).toHaveLength(2)
    f.live.shutdown()
  })

  it('the global shortcut calls the same halt', async () => {
    const f = fixture()
    f.state.activity = activeBody()
    f.live.beginPolling()
    await vi.advanceTimersByTimeAsync(500)
    const callback = f.shortcut.register.mock.calls[0][1]
    callback()
    await vi.advanceTimersByTimeAsync(0)
    expect(f.host.invoke).toHaveBeenCalledWith({ op: 'halt', conversationId: 7 })
    expect(f.shortcut.unregister).toHaveBeenCalledWith(HALT_SHORTCUT)
    f.live.shutdown()
  })

  it('halt with nothing active asks nobody', async () => {
    const f = fixture()
    expect(await f.live.invoke({ op: 'halt' })).toEqual({ error: 'Computer use is not active.' })
    expect(f.host.invoke).not.toHaveBeenCalled()
  })

  it("'halted' only forgets the run it names and never calls the host", async () => {
    const f = fixture()
    f.state.activity = activeBody()
    f.live.beginPolling()
    await vi.advanceTimersByTimeAsync(500)
    expect(await f.live.invoke({ op: 'halted', conversationId: 8 })).toEqual({ halted: true })
    expect(f.indicators[0].close).not.toHaveBeenCalled()
    expect(await f.live.invoke({ op: 'halted', conversationId: 7 })).toEqual({ halted: true })
    expect(f.indicators[0].close).toHaveBeenCalled()
    expect(f.host.invoke).not.toHaveBeenCalled()
    expect(await f.live.invoke({ op: 'halted', conversationId: '7' })).toHaveProperty('error')
    f.live.shutdown()
  })

  it('status, panel-visible, open-panel and malformed requests', async () => {
    const f = fixture({ panel: false })
    expect(await f.live.invoke({ op: 'status' })).toMatchObject({ active: false, running: true, conversationId: null, panelVisible: false })
    expect(await f.live.invoke({ op: 'panel-visible', visible: true })).toEqual({ panelVisible: true })
    expect(await f.live.invoke({ op: 'panel-visible', visible: 'yes' })).toHaveProperty('error')
    expect(await f.live.invoke({ op: 'open-panel' })).toEqual({ opened: true })
    expect(f.deps.focusMain).toHaveBeenCalled()
    for (const bad of [null, 'status', [], { op: 'click' }]) expect(await f.live.invoke(bad)).toHaveProperty('error')
    expect(f.spawn).not.toHaveBeenCalled()
  })

  it('shutdown kills the proxy, closes the indicator, drops the shortcut and stops polling', async () => {
    const f = fixture()
    f.state.activity = activeBody()
    f.live.beginPolling()
    await vi.advanceTimersByTimeAsync(500)
    f.live.shutdown(true)
    expect(f.children[0].kill).toHaveBeenCalledWith('SIGKILL')
    expect(f.indicators[0].close).toHaveBeenCalled()
    expect(f.shortcut.unregister).toHaveBeenCalledWith(HALT_SHORTCUT)
    const polls = f.http.request.mock.calls.length
    const calls = f.windowCalls().length
    await vi.advanceTimersByTimeAsync(5000)
    expect(f.http.request.mock.calls.length).toBe(polls)
    expect(f.windowCalls()).toHaveLength(calls)
  })
})

describe('computer-use-live: indicator position', () => {
  it('reuses a saved spot that is still on a display and saves where it is moved', async () => {
    const f = fixture({ saved: JSON.stringify({ x: 100, y: 200 }) })
    f.state.activity = activeBody()
    f.live.beginPolling()
    await vi.advanceTimersByTimeAsync(10)
    expect(f.indicators[0].options).toMatchObject({ x: 100, y: 200 })
    f.indicators[0].emit('moved')
    expect(f.fs.writeFileSync).toHaveBeenCalledWith('/user/computer-live-indicator.json', JSON.stringify({ x: 400, y: 300 }), { mode: 0o600 })
    f.live.shutdown()
  })

  it('falls back to the top-right corner when the saved spot is off every display', async () => {
    const f = fixture({ saved: JSON.stringify({ x: 5000, y: 200 }), platform: 'win32' })
    f.state.activity = activeBody()
    f.live.beginPolling()
    await vi.advanceTimersByTimeAsync(10)
    expect(f.indicators[0].options).toMatchObject({ x: 1104, y: 41 })
    expect(f.indicators[0].options).not.toHaveProperty('type')
    f.live.shutdown()
  })
})

describe('pickWindow', () => {
  it('prefers on-screen windows, then the lowest z_index, and reads structured content too', () => {
    expect(pickWindow({ structuredContent: { windows: [
      { window_id: 1, pid: 5, z_index: 0, is_on_screen: false }, { window_id: 2, pid: 5, z_index: 4 }, { window_id: 3, pid: 5, z_index: 1 },
    ] } }, 5)).toBe(3)
    expect(pickWindow({ content: [{ type: 'text', text: '[{"window_id": 4, "pid": 6}]' }] }, 5)).toBeNull()
    expect(pickWindow({ content: [{ type: 'text', text: 'not json' }] }, 5)).toBeNull()
    expect(pickWindow(null, 5)).toBeNull()
  })
})

describe('frame capture follows the Computer tab', () => {
  it('captures nothing while live with only the indicator showing, then starts with the tab', async () => {
    const f = fixture({ panel: false })
    f.state.activity = activeBody()
    f.live.beginPolling()
    await vi.advanceTimersByTimeAsync(10)
    expect(f.indicators).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(1000)
    expect(f.frames()).toHaveLength(0)
    expect(f.spawn).not.toHaveBeenCalled()
    await f.live.invoke({ op: 'panel-visible', visible: true })
    await vi.advanceTimersByTimeAsync(1000)
    expect(f.frames().length).toBeGreaterThanOrEqual(7)
  })
})
