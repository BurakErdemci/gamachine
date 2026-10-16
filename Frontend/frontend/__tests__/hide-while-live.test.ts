import { EventEmitter } from 'events'
import { describe, expect, it, vi } from 'vitest'
import { ComputerLive, type ComputerLiveDeps } from '../main/helpers/computer-use-live'
import { hideOrMinimize } from '../main/helpers/hide-while-live'

describe('hideOrMinimize', () => {
  it('minimizes only during a live run on macOS', () => {
    const hideApp = vi.fn()
    const minimizeMain = vi.fn(() => true)
    hideOrMinimize({ platform: 'darwin', live: () => true, hideApp, minimizeMain })
    expect(minimizeMain).toHaveBeenCalledOnce()
    expect(hideApp).not.toHaveBeenCalled()
  })

  it.each([
    ['darwin', false],
    ['win32', true],
    ['linux', true],
  ])('hides only on %s with live=%s', (platform, live) => {
    const hideApp = vi.fn()
    const minimizeMain = vi.fn(() => true)
    hideOrMinimize({ platform, live: () => live, hideApp, minimizeMain })
    expect(hideApp).toHaveBeenCalledOnce()
    expect(minimizeMain).not.toHaveBeenCalled()
  })

  it('falls back to hiding when there is no usable main window', () => {
    const hideApp = vi.fn()
    const minimizeMain = vi.fn(() => false)
    hideOrMinimize({ platform: 'darwin', live: () => true, hideApp, minimizeMain })
    expect(minimizeMain).toHaveBeenCalledOnce()
    expect(hideApp).toHaveBeenCalledOnce()
  })

  it('falls back to hiding when minimizing throws during a live macOS run', () => {
    const hideApp = vi.fn()
    const minimizeMain = vi.fn(() => { throw new Error('unavailable') })
    expect(() => hideOrMinimize({
      platform: 'darwin', live: () => true, hideApp, minimizeMain,
    })).not.toThrow()
    expect(minimizeMain).toHaveBeenCalledOnce()
    expect(hideApp).toHaveBeenCalledOnce()
  })

  it('hides when the live check throws', () => {
    const hideApp = vi.fn()
    const minimizeMain = vi.fn(() => true)
    hideOrMinimize({
      platform: 'darwin', live: () => { throw new Error('unavailable') }, hideApp, minimizeMain,
    })
    expect(hideApp).toHaveBeenCalledOnce()
    expect(minimizeMain).not.toHaveBeenCalled()
  })
})

function fixture() {
  const state = { now: 1000, active: true }
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
  const deps: ComputerLiveDeps = {
    host: { running: true, socketPath: null, driverPath: () => 'unused', invoke: vi.fn() },
    http: { request: vi.fn(async () => ({ data: {
      active: state.active, conversation_id: 7, started_at: 1000,
      last: { tool: 'computer_click', at: 1000, decision: 'allow' }, target: null,
    } })) },
    baseUrl: () => 'http://localhost:9', appToken: 'test-token', platform: 'darwin',
    userData: 'user', spawn: vi.fn(), windows: () => [],
    newIndicator: vi.fn(makeIndicator), loadIndicator: vi.fn(), preload: 'preload.js',
    workAreas: () => ({ primary: { x: 0, y: 0, width: 1440, height: 900 }, all: [] }),
    shortcut: { register: vi.fn(() => true), unregister: vi.fn() },
    fs: { readFileSync: vi.fn(() => { throw new Error('ENOENT') }), writeFileSync: vi.fn() },
    focusMain: vi.fn(), now: () => state.now, mainVisible: () => true,
  }
  return { live: new ComputerLive(deps), deps }
}

describe('ComputerLive.isLive', () => {
  it('tracks active activity and shutdown', async () => {
    const { live } = fixture()
    try {
      expect(live.isLive()).toBe(false)
      await live.tick()
      expect(live.isLive()).toBe(true)
    } finally {
      live.shutdown()
    }
    expect(live.isLive()).toBe(false)
  })

  it('becomes idle when the host stops without shutting down', async () => {
    const { live, deps } = fixture()
    try {
      await live.tick()
      expect(live.isLive()).toBe(true)
      deps.host.running = false
      await live.tick()
      expect(live.isLive()).toBe(false)
      deps.host.running = true
      await live.tick()
      expect(live.isLive()).toBe(true)
    } finally {
      live.shutdown()
    }
  })
})
