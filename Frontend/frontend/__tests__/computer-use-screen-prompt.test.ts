import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ComputerUseHost, type ComputerUseDeps } from '../main/helpers/computer-use'

const screenSettingsUrl = 'x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture'

function fixture(status = 'not-determined', platform = 'darwin') {
  const requestScreenCapture = vi.fn(async (): Promise<unknown> => [])
  const openExternal = vi.fn(async () => {})
  const getMediaAccessStatus = vi.fn(() => status)
  const log = vi.fn()
  const deps: ComputerUseDeps = {
    platform, spawn: vi.fn(), connect: vi.fn(),
    fs: { existsSync: vi.fn(), mkdirSync: vi.fn(), chmodSync: vi.fn(),
      mkdtempSync: vi.fn(), unlinkSync: vi.fn(), rmdirSync: vi.fn() } as unknown as ComputerUseDeps['fs'],
    http: { request: vi.fn() }, baseUrl: () => 'http://localhost:9',
    appToken: 'token', uiSecret: 'ui-secret',
    paths: { repoRoot: '.', userData: '.', tmpdir: '.' },
    systemPreferences: { isTrustedAccessibilityClient: vi.fn(() => true), getMediaAccessStatus },
    requestScreenCapture, shell: { openExternal }, setTimeout, clearTimeout, log,
  }
  return { host: new ComputerUseHost(deps), deps, requestScreenCapture, openExternal, getMediaAccessStatus, log }
}

describe('computer-use screen recording prompt', () => {
  beforeEach(() => { vi.useFakeTimers() })
  afterEach(() => { vi.useRealTimers() })

  it.each(['not-determined', 'denied'])('attempts capture before opening settings when permission is %s', async status => {
    const f = fixture(status)
    expect(await f.host.invoke({ op: 'open-screen-settings' })).toEqual({ opened: true })
    expect(f.getMediaAccessStatus).toHaveBeenCalledExactlyOnceWith('screen')
    expect(f.requestScreenCapture).toHaveBeenCalledTimes(1)
    expect(f.openExternal).toHaveBeenCalledExactlyOnceWith(screenSettingsUrl)
    expect(f.requestScreenCapture.mock.invocationCallOrder[0]).toBeLessThan(f.openExternal.mock.invocationCallOrder[0])
    expect(vi.getTimerCount()).toBe(0)
  })

  it('waits for capture to finish before opening settings', async () => {
    const f = fixture()
    let finish!: (value: unknown) => void
    f.requestScreenCapture.mockImplementation(() => new Promise(resolve => { finish = resolve }))
    const result = f.host.invoke({ op: 'open-screen-settings' })
    await vi.advanceTimersByTimeAsync(1000)
    expect(f.openExternal).not.toHaveBeenCalled()
    finish([])
    expect(await result).toEqual({ opened: true })
    expect(f.openExternal).toHaveBeenCalledExactlyOnceWith(screenSettingsUrl)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('opens settings without capturing when permission is granted', async () => {
    const f = fixture('granted')
    expect(await f.host.invoke({ op: 'open-screen-settings' })).toEqual({ opened: true })
    expect(f.requestScreenCapture).not.toHaveBeenCalled()
    expect(f.openExternal).toHaveBeenCalledExactlyOnceWith(screenSettingsUrl)
    expect(vi.getTimerCount()).toBe(0)
  })

  it.each(['reject', 'throw'])('opens settings and logs when capture fails by %s', async failure => {
    const f = fixture('denied')
    const error = new Error('capture failed')
    f.requestScreenCapture.mockImplementation(() => {
      if (failure === 'throw') throw error
      return Promise.reject(error)
    })
    expect(await f.host.invoke({ op: 'open-screen-settings' })).toEqual({ opened: true })
    expect(f.requestScreenCapture).toHaveBeenCalledTimes(1)
    expect(f.openExternal).toHaveBeenCalledExactlyOnceWith(screenSettingsUrl)
    expect(f.log).toHaveBeenCalled()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('opens settings after exactly 3000 ms when capture never resolves', async () => {
    const f = fixture()
    f.requestScreenCapture.mockImplementation(() => new Promise(() => {}))
    const result = f.host.invoke({ op: 'open-screen-settings' })
    await vi.advanceTimersByTimeAsync(2999)
    expect(f.openExternal).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1)
    expect(await result).toEqual({ opened: true })
    expect(f.requestScreenCapture).toHaveBeenCalledTimes(1)
    expect(f.openExternal).toHaveBeenCalledExactlyOnceWith(screenSettingsUrl)
    expect(f.log).toHaveBeenCalled()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('opens settings when the optional capture dependency is absent', async () => {
    const f = fixture()
    delete f.deps.requestScreenCapture
    expect(await f.host.invoke({ op: 'open-screen-settings' })).toEqual({ opened: true })
    expect(f.requestScreenCapture).not.toHaveBeenCalled()
    expect(f.openExternal).toHaveBeenCalledExactlyOnceWith(screenSettingsUrl)
  })

  it('does not capture or open settings on win32', async () => {
    const f = fixture('denied', 'win32')
    expect(await f.host.invoke({ op: 'open-screen-settings' })).toEqual({ opened: false })
    expect(f.requestScreenCapture).not.toHaveBeenCalled()
    expect(f.openExternal).not.toHaveBeenCalled()
    expect(f.getMediaAccessStatus).not.toHaveBeenCalled()
  })
})
