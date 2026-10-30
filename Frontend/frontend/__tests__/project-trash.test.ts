import fs from 'fs'
import path from 'path'
import { describe, it, expect, vi } from 'vitest'

import { checkTrashable, trashProject, type TrashDeps } from '../main/helpers/project-trash'
import { ALLOWED_INVOKE_CHANNELS } from '../main/helpers/ipc-whitelist'

// A fake disk: `dirs` exist, `links` map a path to its target.
const fake = (platform: 'win32' | 'darwin' | 'linux', dirs: string[], over: Partial<TrashDeps> = {}, links: Record<string, string> = {}) => {
  const set = new Set(dirs)
  const files = new Set<string>()
  const deps: TrashDeps = {
    platform,
    realpath: (p) => {
      const real = links[p] ?? p
      if (!set.has(real)) throw Object.assign(new Error('ENOENT'), { code: 'ENOENT' })
      return real
    },
    isDirectory: (p) => set.has(p),
    exists: (p) => files.has(p),
    protectedDirs: [],
    appDirs: [],
    openWorkspace: null,
    probeLock: () => 'absent',
    processCommands: async () => [],
    trashItem: vi.fn(async () => {}),
    ...over,
  }
  return { deps, files }
}

const unity = (root: string, sep: string) => [root, `${root}${sep}Assets`, `${root}${sep}ProjectSettings`]
const MAC = '/Users/me/Games/Arena'
const WIN = 'C:\\Users\\me\\Games\\Arena'

describe('project-trash guards', () => {
  it('trashes a closed Unity project exactly once and returns ok', async () => {
    const { deps } = fake('darwin', unity(MAC, '/'))
    expect(await trashProject(MAC, deps)).toEqual({ ok: true })
    expect(deps.trashItem).toHaveBeenCalledTimes(1)
    expect(deps.trashItem).toHaveBeenCalledWith(MAC)
  })

  it.each([undefined, null, 42, '', 'relative/Arena', 'Arena'])('refuses a non-absolute or non-string path %j', async (arg) => {
    const { deps } = fake('darwin', unity(MAC, '/'))
    expect(await trashProject(arg, deps)).toEqual({ error: 'invalid-path' })
    expect(deps.trashItem).not.toHaveBeenCalled()
  })

  it('refuses a folder that does not exist', async () => {
    const { deps } = fake('darwin', [])
    expect(await trashProject(MAC, deps)).toEqual({ error: 'not-found' })
    expect(deps.trashItem).not.toHaveBeenCalled()
  })

  it('refuses a folder that lacks Assets or ProjectSettings', async () => {
    for (const dirs of [[MAC], [MAC, `${MAC}/Assets`], [MAC, `${MAC}/ProjectSettings`]]) {
      const { deps } = fake('darwin', dirs)
      expect(await trashProject(MAC, deps)).toEqual({ error: 'not-a-unity-project' })
      expect(deps.trashItem).not.toHaveBeenCalled()
    }
  })

  it.each([
    ['posix root', 'linux', '/'],
    ['drive root', 'win32', 'C:\\'],
    ['UNC share root', 'win32', '\\\\srv\\share\\'],
  ] as const)('refuses a filesystem root: %s', async (_n, platform, root) => {
    const sep = platform === 'win32' ? '\\' : '/'
    const { deps } = fake(platform, [root, `${root}Assets`, `${root}ProjectSettings`, ...unity(root.replace(/[\\/]+$/, ''), sep)])
    expect(await trashProject(root, deps)).toEqual({ error: 'protected' })
    expect(deps.trashItem).not.toHaveBeenCalled()
  })

  it('refuses home, Desktop, Documents and Downloads even when they hold Assets and ProjectSettings', async () => {
    const protectedDirs = ['/Users/me', '/Users/me/Desktop', '/Users/me/Documents', '/Users/me/Downloads']
    const dirs = protectedDirs.flatMap(d => unity(d, '/'))
    for (const dir of protectedDirs) {
      const { deps } = fake('darwin', dirs, { protectedDirs })
      expect(await trashProject(dir, deps)).toEqual({ error: 'protected' })
      expect(deps.trashItem).not.toHaveBeenCalled()
    }
  })

  it('refuses a folder that contains a protected folder', async () => {
    const { deps } = fake('darwin', [...unity('/Users', '/'), '/Users/me'], { protectedDirs: ['/Users/me'] })
    expect(await trashProject('/Users', deps)).toEqual({ error: 'protected' })
  })

  it('allows a project inside Documents', async () => {
    const dir = '/Users/me/Documents/Arena'
    const { deps } = fake('darwin', [...unity(dir, '/'), '/Users/me/Documents'], { protectedDirs: ['/Users/me/Documents'] })
    expect(await trashProject(dir, deps)).toEqual({ ok: true })
  })

  it('refuses the app userData and install dirs, what is inside them and what holds them', async () => {
    const appDirs = ['/Users/me/Library/Application Support/Gamachine', '/Applications/Gamachine.app']
    const inside = '/Applications/Gamachine.app/Contents/Proj'
    const dirs = [...appDirs.flatMap(d => unity(d, '/')), ...unity(inside, '/'), ...unity('/Applications', '/')]
    for (const dir of [...appDirs, inside, '/Applications']) {
      const { deps } = fake('darwin', dirs, { appDirs })
      expect(await trashProject(dir, deps)).toEqual({ error: 'protected' })
      expect(deps.trashItem).not.toHaveBeenCalled()
    }
  })

  it('refuses a symlink that points at a protected folder, and trashes nothing', async () => {
    const link = '/Users/me/Games/Shortcut'
    const { deps } = fake('darwin', unity('/Users/me', '/'), { protectedDirs: ['/Users/me'] }, { [link]: '/Users/me' })
    expect(await trashProject(link, deps)).toEqual({ error: 'protected' })
    expect(deps.trashItem).not.toHaveBeenCalled()
  })

  it('judges a protected dir through its own link too, and trashes the resolved folder', async () => {
    const link = '/Users/me/Games/Shortcut'
    const { deps } = fake('darwin', unity(MAC, '/'), {}, { [link]: MAC })
    expect(await trashProject(link, deps)).toEqual({ ok: true })
    expect(deps.trashItem).toHaveBeenCalledWith(MAC)

    const { deps: other } = fake('darwin', [...unity(MAC, '/'), '/Users/me/Games'], { protectedDirs: ['/Users/alias'] }, { '/Users/alias': '/Users/me/Games' })
    expect(await trashProject('/Users/me/Games', other)).toEqual({ error: 'protected' })
  })

  it('refuses the open workspace and any folder that contains it', async () => {
    const dirs = [...unity(MAC, '/'), ...unity('/Users/me/Games', '/')]
    for (const dir of [MAC, '/Users/me/Games']) {
      const { deps } = fake('darwin', dirs, { openWorkspace: MAC })
      expect(await trashProject(dir, deps)).toEqual({ error: 'open-workspace' })
      expect(deps.trashItem).not.toHaveBeenCalled()
    }
    const { deps } = fake('darwin', [...dirs, '/Users/me/Other'], { openWorkspace: '/Users/me/Other' })
    expect(await trashProject(MAC, deps)).toEqual({ ok: true })
  })

  it('compares paths without case on Windows', async () => {
    const { deps } = fake('win32', unity(WIN, '\\'), { openWorkspace: WIN.toUpperCase() }, { [WIN.toUpperCase()]: WIN })
    expect(await trashProject(WIN, deps)).toEqual({ error: 'open-workspace' })
  })

  describe('Windows lock file', () => {
    it('probes Temp\\UnityLockfile and refuses while it is busy', async () => {
      const probeLock = vi.fn(() => 'busy' as const)
      const { deps } = fake('win32', unity(WIN, '\\'), { probeLock })
      expect(await trashProject(WIN, deps)).toEqual({ error: 'unity-open' })
      expect(probeLock).toHaveBeenCalledWith(`${WIN}\\Temp\\UnityLockfile`)
      expect(deps.trashItem).not.toHaveBeenCalled()
    })

    it.each(['free', 'absent'] as const)('allows the trash when the lock is %s', async (probe) => {
      const { deps } = fake('win32', unity(WIN, '\\'), { probeLock: () => probe })
      expect(await trashProject(WIN, deps)).toEqual({ ok: true })
      expect(deps.trashItem).toHaveBeenCalledWith(WIN)
    })

    it('never lists processes on Windows', async () => {
      const processCommands = vi.fn(async () => [])
      const { deps } = fake('win32', unity(WIN, '\\'), { processCommands })
      await trashProject(WIN, deps)
      expect(processCommands).not.toHaveBeenCalled()
    })
  })

  describe.each(['darwin', 'linux'] as const)('%s Unity process check', (platform) => {
    const editor = (p: string) => `/Applications/Unity/Hub/Editor/6000.2.4f1/Unity.app/Contents/MacOS/Unity -projectPath ${p}`

    it('allows a project without a lock file and never lists processes', async () => {
      const processCommands = vi.fn(async () => [editor(MAC)])
      const { deps } = fake(platform, unity(MAC, '/'), { processCommands })
      expect(await trashProject(MAC, deps)).toEqual({ ok: true })
      expect(processCommands).not.toHaveBeenCalled()
    })

    it('refuses when the lock file exists and an editor names the project', async () => {
      const { deps, files } = fake(platform, unity(MAC, '/'), { processCommands: async () => ['/bin/zsh', editor(MAC)] })
      files.add(`${MAC}/Temp/UnityLockfile`)
      expect(await trashProject(MAC, deps)).toEqual({ error: 'unity-open' })
      expect(deps.trashItem).not.toHaveBeenCalled()
    })

    it('allows a stale lock file when no editor names the project', async () => {
      const { deps, files } = fake(platform, unity(MAC, '/'), { processCommands: async () => [editor('/Users/me/Games/Other'), '/usr/bin/vim Arena.txt'] })
      files.add(`${MAC}/Temp/UnityLockfile`)
      expect(await trashProject(MAC, deps)).toEqual({ ok: true })
    })

    it.each(['null', 'throw'])('refuses when the process list cannot be read (%s)', async (how) => {
      const processCommands = how === 'null' ? async () => null : async () => { throw new Error('ps failed') }
      const { deps, files } = fake(platform, unity(MAC, '/'), { processCommands })
      files.add(`${MAC}/Temp/UnityLockfile`)
      expect(await trashProject(MAC, deps)).toEqual({ error: 'unity-open' })
      expect(deps.trashItem).not.toHaveBeenCalled()
    })
  })

  it('reports trash-failed when the OS refuses, and checkTrashable alone never trashes', async () => {
    const { deps } = fake('darwin', unity(MAC, '/'), { trashItem: vi.fn(async () => { throw new Error('denied') }) })
    expect(await trashProject(MAC, deps)).toEqual({ error: 'trash-failed' })
    const { deps: ok } = fake('darwin', unity(MAC, '/'))
    expect(await checkTrashable(MAC, ok)).toEqual({ real: MAC })
    expect(ok.trashItem).not.toHaveBeenCalled()
  })
})

describe('workspace-trash channel', () => {
  const read = (rel: string) => fs.readFileSync(path.join(__dirname, '..', rel), 'utf-8')

  it('is whitelisted for the desktop renderer and absent from the web remote shim', () => {
    expect(ALLOWED_INVOKE_CHANNELS.has('workspace-trash')).toBe(true)
    expect(read('renderer/lib/remote/ipcShim.ts')).not.toContain('workspace-trash')
  })

  it('is registered through handleSecure and the helper never deletes permanently', () => {
    expect(read('main/background.ts')).toContain("handleSecure('workspace-trash'")
    expect(read('main/helpers/project-trash.ts')).not.toMatch(/\b(rmSync|rmdirSync|rimraf|unlinkSync)\b|fs\.rm\(/)
  })
})
