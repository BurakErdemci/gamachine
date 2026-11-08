import { EventEmitter } from 'node:events'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { openProject, ensureHub, _resetLaunchMemory, LaunchDeps } from '../main/helpers/unity-launch'
import { hubFailureText } from '../renderer/lib/hub'

const winRoot = 'C:\\Program Files\\Unity\\Hub\\Editor'
const macRoot = '/Applications/Unity/Hub/Editor'
const winExe = `${winRoot}\\2022.3.1f1\\Editor\\Unity.exe`
const FLAGS = ['-useHub', '-hubIPC', '-cloudEnvironment', 'production']

function setup(platform: 'win32' | 'darwin', hub: Partial<LaunchDeps> = {}) {
  const spawn = vi.fn((..._args: any[]) => {
    const child = Object.assign(new EventEmitter(), { unref: vi.fn() })
    queueMicrotask(() => child.emit(platform === 'darwin' ? 'exit' : 'spawn', 0))
    return child
  })
  const files: Record<string, string | string[]> = platform === 'win32'
    ? { [winRoot]: ['2022.3.1f1'], [winExe]: '' }
    : { [macRoot]: ['2022.3.1f1'], [`${macRoot}/2022.3.1f1/Unity.app/Contents/MacOS/Unity`]: '' }
  const sleep = vi.fn(async (_ms: number) => {})
  const deps: LaunchDeps = {
    editors: {
      platform, env: {}, homedir: '/h',
      exists: p => p in files,
      readdir: p => { const v = files[p]; if (!Array.isArray(v)) throw new Error('ENOENT'); return v },
      readFile: () => { throw new Error('ENOENT') },
    },
    isTrusted: () => true, isProject: () => true, readVersion: () => '2022.3.1f1',
    isEditorOpen: () => false, now: () => 1_000_000, sleep, spawn,
    ...hub,
  }
  return { deps, spawn, sleep }
}

beforeEach(() => _resetLaunchMemory())

describe('openProject makes sure Unity Hub is up', () => {
  it('spawns with the Hub flags and does not start Hub when it is running and licensing is ready', async () => {
    const startHub = vi.fn(() => true)
    const { deps, spawn } = setup('win32', { isHubRunning: () => true, startHub, isLicensingReady: () => true })
    expect(await openProject({ path: 'C:\\p\\Game' }, deps)).toEqual({ opened: true })
    expect(startHub).not.toHaveBeenCalled()
    expect(spawn.mock.calls[0][1]).toEqual(['-projectPath', 'C:\\p\\Game', ...FLAGS])
  })

  it('starts Hub, waits for licensing, then spawns', async () => {
    let running = false
    let polls = 0
    const startHub = vi.fn(() => { running = true; return true })
    const { deps, spawn, sleep } = setup('win32', {
      isHubRunning: () => running, startHub, isLicensingReady: () => running && ++polls > 3,
    })
    expect((await openProject({ path: 'C:\\p\\Game' }, deps)).opened).toBe(true)
    expect(startHub).toHaveBeenCalledTimes(1)
    expect(sleep).toHaveBeenCalledTimes(3)
    expect(sleep).toHaveBeenCalledWith(500)
    expect(spawn).toHaveBeenCalledTimes(1)
  })

  it('answers hubNotReady without spawning when licensing never comes up, and a retry works', async () => {
    let ready = false
    const { deps, spawn, sleep } = setup('win32', { isHubRunning: () => false, startHub: () => true, isLicensingReady: () => ready })
    const res: any = await openProject({ path: 'C:\\p\\Game' }, deps)
    expect(res).toMatchObject({ opened: false, code: 'hubNotReady' })
    expect(sleep).toHaveBeenCalledTimes(60)
    expect(spawn).not.toHaveBeenCalled()
    ready = true
    expect((await openProject({ path: 'C:\\p\\Game' }, deps)).opened).toBe(true)
    expect(spawn).toHaveBeenCalledTimes(1)
  })

  it('answers hubNotReady when no Hub is installed', async () => {
    const { deps, spawn, sleep } = setup('win32', { isHubRunning: () => false, startHub: () => false, isLicensingReady: () => true })
    expect(await openProject({ path: 'C:\\p\\Game' }, deps)).toMatchObject({ opened: false, code: 'hubNotReady' })
    expect(sleep).not.toHaveBeenCalled()
    expect(spawn).not.toHaveBeenCalled()
  })

  it('launches anyway when a running Hub does not report licensing within 5 seconds', async () => {
    const { deps, spawn, sleep } = setup('win32', { isHubRunning: () => true, startHub: () => true, isLicensingReady: () => false })
    expect((await openProject({ path: 'C:\\p\\Game' }, deps)).opened).toBe(true)
    expect(sleep).toHaveBeenCalledTimes(10)
    expect(spawn).toHaveBeenCalledTimes(1)
  })

  it('puts the Hub flags after --args on macOS', async () => {
    const { deps, spawn } = setup('darwin', { isHubRunning: () => true, startHub: () => true, isLicensingReady: () => true })
    expect((await openProject({ path: '/p/Game' }, deps)).opened).toBe(true)
    expect(spawn.mock.calls[0][1]).toEqual(['-n', '-a', `${macRoot}/2022.3.1f1/Unity.app`, '--args', '-projectPath', '/p/Game', ...FLAGS])
  })

  it('shows the hubNotReady text to the user', () => {
    const t = (key: string) => key
    expect(hubFailureText({ code: 'hubNotReady', error: 'x' }, t as any)).toBe('hub.error.hubNotReady')
  })
})

describe('one Hub start shared by every caller', () => {
  it('two projects pressed while Hub starts call startHub once and both open', async () => {
    let running = false
    let polls = 0
    const hub = {
      isHubRunning: () => running,
      startHub: vi.fn(async () => { await new Promise(r => setTimeout(r, 5)); running = true; return true }),
      isLicensingReady: () => running && ++polls > 3,
    }
    const a = setup('win32', hub)
    const b = setup('win32', hub)
    const [ra, rb] = await Promise.all([openProject({ path: 'C:\p\A' }, a.deps), openProject({ path: 'C:\p\B' }, b.deps)])
    expect(ra).toEqual({ opened: true })
    expect(rb).toEqual({ opened: true })
    expect(hub.startHub).toHaveBeenCalledTimes(1)
  })

  it('a later start is a new one once the first has finished', async () => {
    let running = false
    const hub = { isHubRunning: () => running, startHub: vi.fn(() => { running = true; return true }), isLicensingReady: () => running }
    await ensureHub({ ...hub, sleep: async () => {} })
    running = false
    await ensureHub({ ...hub, sleep: async () => {} })
    expect(hub.startHub).toHaveBeenCalledTimes(2)
  })
})

describe('a Hub helper that throws', () => {
  it('answers hubNotReady and lets a retry spawn', async () => {
    const bad = setup('win32', { isHubRunning: () => { throw new Error('boom') }, startHub: () => true, isLicensingReady: () => true })
    expect(await openProject({ path: 'C:\p\Game' }, bad.deps)).toMatchObject({ opened: false, code: 'hubNotReady' })
    expect(bad.spawn).not.toHaveBeenCalled()
    const good = setup('win32', { isHubRunning: () => true, startHub: () => true, isLicensingReady: () => true })
    expect(await openProject({ path: 'C:\p\Game' }, good.deps)).toEqual({ opened: true })
    expect(good.spawn).toHaveBeenCalledTimes(1)
  })

  it('also covers a startHub that rejects', async () => {
    const bad = setup('win32', { isHubRunning: () => false, startHub: async () => { throw new Error('boom') }, isLicensingReady: () => false })
    expect(await openProject({ path: 'C:\p\Game' }, bad.deps)).toMatchObject({ opened: false, code: 'hubNotReady' })
  })
})
