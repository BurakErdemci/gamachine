import { EventEmitter } from 'events'
import { describe, expect, it, vi } from 'vitest'
import { ComputerLive, HALT_SHORTCUT, IDLE_MS, type ComputerLiveDeps } from '../main/helpers/computer-use-live'

function fixture(mainVisible?: () => boolean) {
  const state = { now: 1000, active: true }
  const indicators: ReturnType<typeof makeIndicator>[] = []
  function makeIndicator() {
    let destroyed = false
    const win = Object.assign(new EventEmitter(), {
      isDestroyed: () => destroyed,
      webContents: { send: vi.fn() },
      close: vi.fn(() => { destroyed = true; win.emit('closed') }),
      showInactive: vi.fn(),
      getPosition: () => [400, 300],
      setAlwaysOnTop: vi.fn(),
      setVisibleOnAllWorkspaces: vi.fn(),
    })
    return win
  }
  const shortcut = { register: vi.fn(() => true), unregister: vi.fn() }
  const deps: ComputerLiveDeps = {
    host: { running: true, socketPath: null, driverPath: () => 'unused', invoke: vi.fn() },
    http: { request: vi.fn(async () => ({ data: {
      active: state.active, conversation_id: 7, started_at: 1000,
      last: { tool: 'computer_click', at: 1000, decision: 'allow' }, target: null,
    } })) },
    baseUrl: () => 'http://localhost:9', appToken: 'test-token', platform: 'darwin',
    userData: 'user', spawn: vi.fn(), windows: () => [],
    newIndicator: vi.fn(() => {
      const win = makeIndicator()
      indicators.push(win)
      return win
    }),
    loadIndicator: vi.fn(), preload: 'preload.js',
    workAreas: () => ({ primary: { x: 0, y: 0, width: 1440, height: 900 }, all: [] }),
    shortcut,
    fs: { readFileSync: vi.fn(() => { throw new Error('ENOENT') }), writeFileSync: vi.fn() },
    focusMain: vi.fn(), now: () => state.now,
    ...(mainVisible ? { mainVisible } : {}),
  }
  return { live: new ComputerLive(deps), state, deps, indicators, shortcut }
}

describe('computer-use-live: main window visibility', () => {
  it('keeps the shortcut while visible and opens and closes one indicator with visibility changes', async () => {
    let visible = true
    const f = fixture(() => visible)
    await f.live.tick()
    expect(f.live.status().active).toBe(true)
    expect(f.indicators).toHaveLength(0)
    expect(f.shortcut.register).toHaveBeenCalledWith(HALT_SHORTCUT, expect.any(Function))

    visible = false
    f.live.mainVisibilityChanged()
    f.live.mainVisibilityChanged()
    expect(f.indicators).toHaveLength(1)
    f.indicators[0].emit('ready-to-show')
    expect(f.indicators[0].showInactive).toHaveBeenCalledOnce()

    visible = true
    f.live.mainVisibilityChanged()
    expect(f.indicators[0].close).toHaveBeenCalledOnce()
    expect(f.shortcut.unregister).not.toHaveBeenCalled()
    expect(f.shortcut.register).toHaveBeenCalledOnce()
    expect(f.live.status().active).toBe(true)
    f.live.shutdown()
  })

  it('treats a throwing visibility check as hidden', async () => {
    const f = fixture(() => { throw new Error('window closing') })
    await f.live.tick()
    expect(f.indicators).toHaveLength(1)
    f.live.shutdown()
  })

  it('closes the indicator after inactive activity passes the idle timeout', async () => {
    const f = fixture(() => false)
    await f.live.tick()
    f.state.active = false
    f.state.now += IDLE_MS
    await f.live.tick()
    expect(f.indicators[0].close).not.toHaveBeenCalled()
    f.state.now++
    await f.live.tick()
    expect(f.indicators[0].close).toHaveBeenCalledOnce()
    expect(f.live.status().active).toBe(false)
    expect(f.shortcut.unregister).toHaveBeenCalledWith(HALT_SHORTCUT)
    f.live.mainVisibilityChanged()
    expect(f.indicators).toHaveLength(1)
    f.live.shutdown()
  })

  it('opens the indicator when no visibility dependency is supplied', async () => {
    const f = fixture()
    expect(f.deps).not.toHaveProperty('mainVisible')
    await f.live.tick()
    expect(f.indicators).toHaveLength(1)
    f.live.shutdown()
  })

  it('ignores visibility notifications while idle or stopped', async () => {
    const mainVisible = vi.fn(() => false)
    const f = fixture(mainVisible)
    f.live.mainVisibilityChanged()
    expect(f.indicators).toHaveLength(0)
    await f.live.tick()
    expect(f.indicators).toHaveLength(1)
    f.live.shutdown()
    mainVisible.mockClear()
    f.live.mainVisibilityChanged()
    expect(mainVisible).not.toHaveBeenCalled()
    expect(f.indicators).toHaveLength(1)
    expect(f.indicators[0].close).toHaveBeenCalledOnce()
  })
})
