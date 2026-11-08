import { EventEmitter } from 'node:events'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { openProject, _resetLaunchMemory, LaunchDeps } from '../main/helpers/unity-launch'
import { isEditorOpen } from '../main/helpers/unity-project'

const winRoot = 'C:\\Program Files\\Unity\\Hub\\Editor'
const winExe = (v: string) => `${winRoot}\\${v}\\Editor\\Unity.exe`

function setup(over: Partial<LaunchDeps> = {}) {
  let clock = 1_000_000
  // Each spawn answers 'spawn' on its own, like a real child.
  const spawn = vi.fn((..._args: any[]) => {
    const child = Object.assign(new EventEmitter(), { unref: vi.fn() })
    queueMicrotask(() => child.emit('spawn'))
    return child
  })
  const files: Record<string, string | string[]> = { [winRoot]: ['2022.3.1f1'], [winExe('2022.3.1f1')]: '' }
  const deps: LaunchDeps = {
    editors: {
      platform: 'win32', env: {}, homedir: 'C:\\Users\\t',
      exists: p => p in files,
      readdir: p => { const v = files[p]; if (!Array.isArray(v)) throw new Error('ENOENT'); return v },
      readFile: () => { throw new Error('ENOENT') },
    },
    isTrusted: () => true,
    isProject: () => true,
    readVersion: () => '2022.3.1f1',
    isEditorOpen: () => false,
    now: () => clock,
    spawn,
    ...over,
  }
  return { deps, spawn, advance: (ms: number) => { clock += ms } }
}

beforeEach(() => _resetLaunchMemory())

describe('openProject launches once', () => {
  it('answers alreadyOpen without spawning while the editor is open', async () => {
    const { deps, spawn } = setup({ isEditorOpen: () => true })
    expect(await openProject({ path: 'C:\\p\\Game' }, deps)).toEqual({ opened: true, alreadyOpen: true })
    expect(spawn).not.toHaveBeenCalled()
  })

  it('spawns once for two concurrent calls and answers the second alreadyOpen', async () => {
    const { deps, spawn } = setup()
    const [first, second] = await Promise.all([
      openProject({ path: 'C:\\p\\Game' }, deps),
      openProject({ path: 'C:\\p\\Game' }, deps),
    ])
    expect(first).toEqual({ opened: true })
    expect(second).toEqual({ opened: true, alreadyOpen: true })
    expect(spawn).toHaveBeenCalledTimes(1)
  })

  it('remembers a launch for 30 s', async () => {
    const { deps, spawn, advance } = setup()
    expect(await openProject({ path: 'C:\\p\\Game' }, deps)).toEqual({ opened: true })
    advance(10_000)
    expect(await openProject({ path: 'C:\\p\\Game' }, deps)).toEqual({ opened: true, alreadyOpen: true })
    expect(spawn).toHaveBeenCalledTimes(1)
    advance(21_000)
    expect(await openProject({ path: 'C:\\p\\Game' }, deps)).toEqual({ opened: true })
    expect(spawn).toHaveBeenCalledTimes(2)
  })

  it('lets a retry through after a failed spawn', async () => {
    const failing = vi.fn((..._args: any[]) => {
      const child = Object.assign(new EventEmitter(), { unref: vi.fn() })
      queueMicrotask(() => child.emit('error', new Error('EACCES')))
      return child
    })
    const { deps, spawn } = setup({ spawn: failing })
    const res = await openProject({ path: 'C:\\p\\Game' }, deps)
    expect(res).toMatchObject({ opened: false, code: 'spawnFailed' })
    const retry = await openProject({ path: 'C:\\p\\Game' }, { ...deps, spawn })
    expect(retry).toEqual({ opened: true })
    expect(spawn).toHaveBeenCalledTimes(1)
  })

  it('treats another case or a trailing slash on Windows as the same project', async () => {
    const { deps, spawn } = setup()
    expect(await openProject({ path: 'C:\\p\\Game' }, deps)).toEqual({ opened: true })
    expect(await openProject({ path: 'c:\\P\\game\\' }, deps)).toEqual({ opened: true, alreadyOpen: true })
    expect(spawn).toHaveBeenCalledTimes(1)
  })
})

describe('isEditorOpen', () => {
  const project = (content?: string) => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'editor-open-'))
    if (content !== undefined) {
      fs.mkdirSync(path.join(dir, 'Library'))
      fs.writeFileSync(path.join(dir, 'Library', 'EditorInstance.json'), content)
    }
    return dir
  }
  const withPid = (pid: unknown) => project(JSON.stringify({ process_id: pid }))

  it('is true for a live process', () => {
    expect(isEditorOpen(withPid(process.pid))).toBe(true)
  })

  it('is false for a dead process', () => {
    expect(isEditorOpen(withPid(2 ** 31 - 2))).toBe(false)
  })

  it('is false for a missing or garbage file', () => {
    expect(isEditorOpen(project())).toBe(false)
    expect(isEditorOpen(project('{not json'))).toBe(false)
    expect(isEditorOpen(project('{"process_id":"abc"}'))).toBe(false)
  })

  it('is false for pid 0 and 1', () => {
    expect(isEditorOpen(withPid(0))).toBe(false)
    expect(isEditorOpen(withPid(1))).toBe(false)
  })
})
