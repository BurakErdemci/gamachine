import { EventEmitter } from 'node:events'
import { describe, expect, it, vi } from 'vitest'
import { openProject, bundleOf, LaunchDeps } from '../main/helpers/unity-launch'

const macRoot = '/Applications/Unity/Hub/Editor'
const exe = (v: string) => `${macRoot}/${v}/Unity.app/Contents/MacOS/Unity`

function setup(over: Partial<LaunchDeps> = {}) {
  const child = Object.assign(new EventEmitter(), { unref: vi.fn() })
  // A real child emits 'spawn' once the process started.
  const spawn = vi.fn((..._args: any[]) => { queueMicrotask(() => child.emit('spawn')); return child })
  const files: Record<string, string | string[]> = {
    [macRoot]: ['6000.4.4f1', '2022.3.1f1'],
    [exe('6000.4.4f1')]: '',
    [exe('2022.3.1f1')]: '',
  }
  const deps: LaunchDeps = {
    editors: {
      platform: 'darwin', env: {}, homedir: '/Users/t',
      exists: p => p in files,
      readdir: p => { const v = files[p]; if (!Array.isArray(v)) throw new Error('ENOENT'); return v },
      readFile: () => { throw new Error('ENOENT') },
    },
    isTrusted: () => true,
    isProject: () => true,
    readVersion: () => '2022.3.1f1',
    spawn,
    ...over,
  }
  return { deps, spawn, child }
}

// open spawns, then exits at once with its status.
const openSpawn = (child: EventEmitter & { unref: () => void }, code: number) => vi.fn((..._args: any[]) => {
  queueMicrotask(() => { child.emit('spawn'); child.emit('exit', code) })
  return child
})

const winRoot = 'C:\\Program Files\\Unity\\Hub\\Editor'
const winExe = (v: string) => `${winRoot}\\${v}\\Editor\\Unity.exe`

describe('openProject', () => {
  it('on macOS launches the editor through open -n -a with the bundle and project path', async () => {
    const { deps, child } = setup()
    const spawn = openSpawn(child, 0)
    expect(await openProject({ path: '/p/Game' }, { ...deps, spawn })).toEqual({ opened: true })
    expect(spawn).toHaveBeenCalledExactlyOnceWith(
      '/usr/bin/open',
      ['-n', '-a', `${macRoot}/2022.3.1f1/Unity.app`, '--args', '-projectPath', '/p/Game'],
      { detached: true, stdio: 'ignore' },
    )
    expect(child.unref).toHaveBeenCalled()
  })

  it('passes project paths with spaces and Turkish characters as single arguments', async () => {
    const { deps, child } = setup()
    const spawn = openSpawn(child, 0)
    const path = '/Users/Burak Emre/Belgeler/Oyun Çalışması ğüşiöç'
    await openProject({ path }, { ...deps, spawn })
    expect(spawn.mock.calls[0][1]).toEqual(['-n', '-a', `${macRoot}/2022.3.1f1/Unity.app`, '--args', '-projectPath', path])
  })

  it('launches from a secondary install root whose path contains spaces', async () => {
    const root = '/Volumes/Big Disk/Unity Editors'
    const path = `${root}/2022.3.1f1/Unity.app/Contents/MacOS/Unity`
    const files: Record<string, string | string[]> = {
      '/Users/t/Library/Application Support/UnityHub/secondaryInstallPath.json': JSON.stringify(root),
      [root]: ['2022.3.1f1'],
      [path]: '',
    }
    const { deps, child } = setup()
    const spawn = openSpawn(child, 0)
    const editors = { ...deps.editors, exists: (p: string) => p in files, readdir: (p: string) => { const v = files[p]; if (!Array.isArray(v)) throw new Error('ENOENT'); return v }, readFile: (p: string) => files[p] as string }
    await openProject({ path: '/p/Game' }, { ...deps, editors, spawn })
    expect(spawn.mock.calls[0][1][2]).toBe(`${root}/2022.3.1f1/Unity.app`)
  })

  it('derives the bundle from the editor binary and rejects paths outside a bundle', () => {
    expect(bundleOf('/Applications/Unity Hub/Editor/6000.0.1f1/Unity.app/Contents/MacOS/Unity')).toBe('/Applications/Unity Hub/Editor/6000.0.1f1/Unity.app')
    expect(bundleOf('/opt/unity/2022.3.1f1/Unity')).toBeNull()
    expect(bundleOf('C:\\Program Files\\Unity\\Editor\\Unity.exe')).toBeNull()
  })

  it('reports a non-zero exit of open as a launch failure', async () => {
    const { deps, child } = setup()
    const spawn = openSpawn(child, 1)
    expect(await openProject({ path: '/p/Game' }, { ...deps, spawn })).toEqual({ opened: false, error: 'Could not start Unity: open exited with code 1' })
  })

  it('on Windows keeps the direct detached spawn', async () => {
    const files: Record<string, string | string[]> = { [winRoot]: ['2022.3.1f1'], [winExe('2022.3.1f1')]: '' }
    const { deps, spawn, child } = setup()
    const editors = {
      ...deps.editors, platform: 'win32' as const, env: { ProgramFiles: 'C:\\Program Files' },
      exists: (p: string) => p in files, readdir: (p: string) => { const v = files[p]; if (!Array.isArray(v)) throw new Error('ENOENT'); return v },
    }
    expect(await openProject({ path: 'C:\\p\\Game' }, { ...deps, editors })).toEqual({ opened: true })
    expect(spawn).toHaveBeenCalledExactlyOnceWith(winExe('2022.3.1f1'), ['-projectPath', 'C:\\p\\Game'], { detached: true, stdio: 'ignore' })
    expect(child.unref).toHaveBeenCalled()
  })

  it('refuses a trusted subfolder that is not a project root, and opens a real project', async () => {
    const projects = new Set(['/p/Game'])
    const { deps, spawn, child } = setup({ isProject: d => projects.has(d) })
    spawn.mockImplementation(() => { queueMicrotask(() => { child.emit('spawn'); child.emit('exit', 0) }); return child })
    expect(await openProject({ path: '/p/Game/Library' }, deps)).toEqual({ opened: false, error: 'Not a Unity project folder.' })
    expect(spawn).not.toHaveBeenCalled()
    expect(await openProject({ path: '/p/Game' }, deps)).toEqual({ opened: true })
  })

  it('prefers the version given by the caller', async () => {
    const { deps, child } = setup()
    const spawn = openSpawn(child, 0)
    await openProject({ path: '/p/Game', version: '6000.4.4f1' }, { ...deps, spawn })
    expect(spawn.mock.calls[0][1][2]).toBe(`${macRoot}/6000.4.4f1/Unity.app`)
  })

  it('refuses untrusted paths without spawning', async () => {
    const { deps, spawn } = setup({ isTrusted: () => false })
    const result = await openProject({ path: '/etc' }, deps)
    expect(result.opened).toBe(false)
    expect(spawn).not.toHaveBeenCalled()
  })

  it('reports an unknown version and a missing editor', async () => {
    const none = setup({ readVersion: () => null })
    expect((await openProject({ path: '/p' }, none.deps)).opened).toBe(false)
    const missing = setup()
    const result = await openProject({ path: '/p', version: '1.0.0f1' }, missing.deps)
    expect(result.opened).toBe(false)
    expect(result.error).toContain('1.0.0f1')
    expect(missing.spawn).not.toHaveBeenCalled()
  })

  it('returns an error when spawn throws', async () => {
    const { deps } = setup({ spawn: () => { throw new Error('boom') } })
    expect(await openProject({ path: '/p' }, deps)).toEqual({ opened: false, error: 'boom' })
  })

  it('reports an async spawn failure instead of claiming success', async () => {
    const { deps, child } = setup({
      spawn: () => { queueMicrotask(() => child.emit('error', new Error('spawn EACCES'))); return child },
    })
    expect(await openProject({ path: '/p/Game' }, deps)).toEqual({ opened: false, error: 'Could not start Unity: spawn EACCES' })
    expect(() => child.emit('error', new Error('late'))).not.toThrow()
    expect(child.unref).not.toHaveBeenCalled()
  })
})
