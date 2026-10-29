import { describe, expect, it, vi } from 'vitest'
import { openProject, LaunchDeps } from '../main/helpers/unity-launch'

const macRoot = '/Applications/Unity/Hub/Editor'
const exe = (v: string) => `${macRoot}/${v}/Unity.app/Contents/MacOS/Unity`

function setup(over: Partial<LaunchDeps> = {}) {
  const child = { unref: vi.fn(), on: vi.fn() }
  const spawn = vi.fn((..._args: any[]) => child)
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

describe('openProject', () => {
  it('launches the editor matching ProjectVersion.txt, detached, with an argument array', () => {
    const { deps, spawn, child } = setup()
    expect(openProject({ path: '/p/Game' }, deps)).toEqual({ opened: true })
    expect(spawn).toHaveBeenCalledExactlyOnceWith(exe('2022.3.1f1'), ['-projectPath', '/p/Game'], { detached: true, stdio: 'ignore' })
    expect(child.unref).toHaveBeenCalled()
  })

  it('refuses a trusted subfolder that is not a project root, and opens a real project', () => {
    const projects = new Set(['/p/Game'])
    const { deps, spawn } = setup({ isProject: d => projects.has(d) })
    expect(openProject({ path: '/p/Game/Library' }, deps)).toEqual({ opened: false, error: 'Not a Unity project folder.' })
    expect(spawn).not.toHaveBeenCalled()
    expect(openProject({ path: '/p/Game' }, deps)).toEqual({ opened: true })
  })

  it('prefers the version given by the caller', () => {
    const { deps, spawn } = setup()
    openProject({ path: '/p/Game', version: '6000.4.4f1' }, deps)
    expect(spawn.mock.calls[0][0]).toBe(exe('6000.4.4f1'))
  })

  it('refuses untrusted paths without spawning', () => {
    const { deps, spawn } = setup({ isTrusted: () => false })
    const result = openProject({ path: '/etc' }, deps)
    expect(result.opened).toBe(false)
    expect(spawn).not.toHaveBeenCalled()
  })

  it('reports an unknown version and a missing editor', () => {
    const none = setup({ readVersion: () => null })
    expect(openProject({ path: '/p' }, none.deps).opened).toBe(false)
    const missing = setup()
    const result = openProject({ path: '/p', version: '1.0.0f1' }, missing.deps)
    expect(result.opened).toBe(false)
    expect(result.error).toContain('1.0.0f1')
    expect(missing.spawn).not.toHaveBeenCalled()
  })

  it('returns an error when spawn throws', () => {
    const { deps } = setup({ spawn: () => { throw new Error('boom') } })
    expect(openProject({ path: '/p' }, deps)).toEqual({ opened: false, error: 'boom' })
  })
})
