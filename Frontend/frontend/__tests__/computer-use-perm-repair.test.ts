import { describe, expect, it, vi } from 'vitest'
import { ComputerUseHost, type ComputerUseDeps } from '../main/helpers/computer-use'

const screenSettingsUrl = 'x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture'

function fixture() {
  const execFile = vi.fn<NonNullable<ComputerUseDeps['execFile']>>()
    .mockResolvedValue({ stdout: JSON.stringify({ accessibility: false, screen_recording: false }) })
  const accessibility = vi.fn(() => true)
  const screen = vi.fn(() => 'granted')
  const capture = vi.fn(async (): Promise<unknown> => [])
  const openExternal = vi.fn(async () => {})
  const log = vi.fn()
  const deps: ComputerUseDeps = {
    platform: 'darwin', isPackaged: true, execFile, spawn: vi.fn(), connect: vi.fn(),
    fs: { existsSync: vi.fn(() => true), mkdirSync: vi.fn(), chmodSync: vi.fn(),
      mkdtempSync: vi.fn(), unlinkSync: vi.fn(), rmdirSync: vi.fn() },
    http: { request: vi.fn(async () => ({ data: { enabled: true } })) },
    baseUrl: () => 'http://localhost:9', appToken: 'token', uiSecret: 'ui-secret',
    paths: { resourcesPath: 'resources', repoRoot: '.', userData: '.', tmpdir: '.' },
    systemPreferences: { isTrustedAccessibilityClient: accessibility, getMediaAccessStatus: screen },
    requestScreenCapture: capture, shell: { openExternal }, log,
  }
  const host = new ComputerUseHost(deps)
  host.running = true
  host.socketPath = 'cua-test.sock'
  return { host, deps, execFile, accessibility, screen, capture, openExternal, log }
}

describe('computer-use driver permission status', () => {
  it('uses the driver view and passes the exact driver arguments', async () => {
    const f = fixture()
    expect(await f.host.invoke({ op: 'status' })).toMatchObject({
      available: true, running: true, settings: { enabled: true },
      permissions: { accessibility: false, screen: 'denied' },
    })
    expect(f.execFile).toHaveBeenCalledExactlyOnceWith(f.host.driverPath(),
      ['call', 'check_permissions', '{"prompt":false}', '--socket', 'cua-test.sock'], { timeout: 3000 })
    expect(f.accessibility).toHaveBeenCalledExactlyOnceWith(false)
    expect(f.screen).toHaveBeenCalledExactlyOnceWith('screen')
  })

  it.each(['denied', 'not-determined', 'granted'])('reports granted when the driver can record and Electron reports %s', async status => {
    const f = fixture()
    f.screen.mockReturnValue(status)
    f.execFile.mockResolvedValue({ stdout: '{"accessibility":true,"screen_recording":true}' })
    expect(await f.host.invoke({ op: 'status' })).toHaveProperty('permissions', {
      accessibility: true, screen: 'granted',
    })
  })

  it.each(['denied', 'not-determined', 'restricted'])('preserves Electron screen status %s when the driver cannot record', async status => {
    const f = fixture()
    f.screen.mockReturnValue(status)
    expect(await f.host.invoke({ op: 'status' })).toHaveProperty('permissions', {
      accessibility: false, screen: status,
    })
  })

  it.each(['not running', 'missing socket', 'missing execFile'])('falls back without a driver call: %s', async reason => {
    const f = fixture()
    if (reason === 'not running') f.host.running = false
    if (reason === 'missing socket') f.host.socketPath = null
    if (reason === 'missing execFile') delete f.deps.execFile
    expect(await f.host.invoke({ op: 'status' })).toHaveProperty('permissions', {
      accessibility: true, screen: 'granted',
    })
    expect(f.execFile).not.toHaveBeenCalled()
  })

  it('falls back when the driver call rejects', async () => {
    const f = fixture()
    f.execFile.mockRejectedValue(new Error('driver unavailable'))
    expect(await f.host.invoke({ op: 'status' })).toHaveProperty('permissions', {
      accessibility: true, screen: 'granted',
    })
    expect(f.execFile).toHaveBeenCalledTimes(1)
    expect(f.log).toHaveBeenCalledTimes(1)
  })

  it.each(['not JSON', 'null', '{}', '{"accessibility":"true","screen_recording":false}',
    '{"accessibility":false,"screen_recording":1}', '{"accessibility":false}'])('falls back on invalid driver output: %s', async stdout => {
    const f = fixture()
    f.execFile.mockResolvedValue({ stdout })
    expect(await f.host.invoke({ op: 'status' })).toHaveProperty('permissions', {
      accessibility: true, screen: 'granted',
    })
    expect(f.execFile).toHaveBeenCalledTimes(1)
  })
})

describe('computer-use packaged permission repair', () => {
  it('waits for the Accessibility reset before prompting', async () => {
    const f = fixture()
    let finish!: (value: { stdout: string }) => void
    f.execFile.mockImplementation(() => new Promise(resolve => { finish = resolve }))
    const result = f.host.invoke({ op: 'request-accessibility' })
    expect(f.execFile).toHaveBeenCalledExactlyOnceWith('tccutil',
      ['reset', 'Accessibility', 'com.gamachine.app'], { timeout: 5000 })
    expect(f.accessibility).not.toHaveBeenCalled()
    finish({ stdout: '' })
    expect(await result).toEqual({ accessibility: true })
    expect(f.accessibility).toHaveBeenCalledExactlyOnceWith(true)
  })

  it('still prompts after a rejected Accessibility reset', async () => {
    const f = fixture()
    f.execFile.mockRejectedValue(new Error('reset failed'))
    expect(await f.host.invoke({ op: 'request-accessibility' })).toEqual({ accessibility: true })
    expect(f.accessibility).toHaveBeenCalledExactlyOnceWith(true)
    expect(f.log).toHaveBeenCalledTimes(1)
  })

  it.each(['development', 'unspecified packaging', 'missing execFile'])('does not reset in %s', async reason => {
    const f = fixture()
    if (reason === 'development') f.deps.isPackaged = false
    if (reason === 'unspecified packaging') delete f.deps.isPackaged
    if (reason === 'missing execFile') delete f.deps.execFile
    expect(await f.host.invoke({ op: 'request-accessibility' })).toEqual({ accessibility: true })
    expect(f.accessibility).toHaveBeenCalledExactlyOnceWith(true)
    expect(await f.host.invoke({ op: 'open-screen-settings' })).toEqual({ opened: true })
    expect(f.execFile).not.toHaveBeenCalled()
    expect(f.capture).not.toHaveBeenCalled()
    expect(f.openExternal).toHaveBeenCalledExactlyOnceWith(screenSettingsUrl)
  })

  it('waits for ScreenCapture reset then captures despite a granted Electron status, before opening settings', async () => {
    const f = fixture()
    let finish!: (value: { stdout: string }) => void
    f.execFile.mockImplementation(() => new Promise(resolve => { finish = resolve }))
    const result = f.host.invoke({ op: 'open-screen-settings' })
    expect(f.execFile).toHaveBeenCalledExactlyOnceWith('tccutil',
      ['reset', 'ScreenCapture', 'com.gamachine.app'], { timeout: 5000 })
    expect(f.capture).not.toHaveBeenCalled()
    expect(f.openExternal).not.toHaveBeenCalled()
    finish({ stdout: '' })
    expect(await result).toEqual({ opened: true })
    expect(f.screen).toHaveBeenCalledExactlyOnceWith('screen')
    expect(f.capture).toHaveBeenCalledTimes(1)
    expect(f.openExternal).toHaveBeenCalledExactlyOnceWith(screenSettingsUrl)
    expect(f.capture.mock.invocationCallOrder[0]).toBeLessThan(f.openExternal.mock.invocationCallOrder[0])
  })

  it('still captures and opens settings after a rejected ScreenCapture reset', async () => {
    const f = fixture()
    f.execFile.mockRejectedValue(new Error('reset failed'))
    expect(await f.host.invoke({ op: 'open-screen-settings' })).toEqual({ opened: true })
    expect(f.capture).toHaveBeenCalledTimes(1)
    expect(f.openExternal).toHaveBeenCalledExactlyOnceWith(screenSettingsUrl)
    expect(f.log).toHaveBeenCalledTimes(1)
  })

  it.each(['win32', 'linux'])('never probes or resets permissions on %s', async platform => {
    const f = fixture()
    f.deps.platform = platform
    expect(await f.host.invoke({ op: 'status' })).toHaveProperty('permissions', {
      accessibility: false, screen: 'not-determined',
    })
    expect(await f.host.invoke({ op: 'request-accessibility' })).toEqual({ accessibility: false })
    expect(await f.host.invoke({ op: 'open-screen-settings' })).toEqual({ opened: false })
    expect(f.execFile).not.toHaveBeenCalled()
    expect(f.accessibility).not.toHaveBeenCalled()
    expect(f.screen).not.toHaveBeenCalled()
    expect(f.capture).not.toHaveBeenCalled()
    expect(f.openExternal).not.toHaveBeenCalled()
  })
})
