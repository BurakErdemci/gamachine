import fs from 'fs'
import os from 'os'
import path from 'path'
import { describe, it, expect, vi } from 'vitest'

const electron = vi.hoisted(() => ({ userData: '' }))
vi.mock('electron', () => ({ app: { getPath: () => electron.userData }, dialog: {}, BrowserWindow: {} }))

import { checkTrashable, trashProject, trashPrompt, type TrashDeps } from '../main/helpers/project-trash'
import { isRegisteredRoot, registerTrustedRoot } from '../main/helpers/ipc-trust'
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
    openWorkspaces: [],
    identity: (p) => `id:${p}`,
    probeLock: () => 'absent',
    processCommands: async () => [],
    confirm: vi.fn(async () => true),
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
      const { deps } = fake('darwin', dirs, { openWorkspaces: [MAC] })
      expect(await trashProject(dir, deps)).toEqual({ error: 'open-workspace' })
      expect(deps.trashItem).not.toHaveBeenCalled()
    }
    const { deps } = fake('darwin', [...dirs, '/Users/me/Other'], { openWorkspaces: ['/Users/me/Other'] })
    expect(await trashProject(MAC, deps)).toEqual({ ok: true })
  })

  it('compares paths without case on Windows', async () => {
    const { deps } = fake('win32', unity(WIN, '\\'), { openWorkspaces: [WIN.toUpperCase()] }, { [WIN.toUpperCase()]: WIN })
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

    it('treats a differently-cased or relative -projectPath as open (refuse when unsure)', async () => {
      const cases = [
        { cmd: editor(MAC.toLowerCase()), open: platform === 'darwin' },
        { cmd: editor('.'), open: true },
        { cmd: editor('../Arena'), open: true },
        { cmd: editor('"/Users/me/Games/Arena"'), open: true },
        { cmd: editor('/Users/me/Games/Other'), open: false },
      ]
      for (const { cmd, open } of cases) {
        // A case-insensitive disk (macOS) resolves the lowercase spelling to the same folder.
        const { deps, files } = fake(platform, unity(MAC, '/'), { processCommands: async () => [cmd] }, platform === 'darwin' ? { [MAC.toLowerCase()]: MAC } : {})
        files.add(`${MAC}/Temp/UnityLockfile`)
        expect(await trashProject(MAC, deps), cmd).toEqual(open ? { error: 'unity-open' } : { ok: true })
      }
    })

    it('refuses when the editor was started through another absolute alias of the project', async () => {
      const ALIAS = '/Volumes/Alias/Arena'
      for (const arg of [ALIAS, `"${ALIAS}"`, `'${ALIAS}'`]) {
        const { deps, files } = fake(platform, unity(MAC, '/'), { processCommands: async () => [`${editor(arg)} -logFile /tmp/x.log`] }, { [ALIAS]: MAC })
        files.add(`${MAC}/Temp/UnityLockfile`)
        expect(await trashProject(MAC, deps), arg).toEqual({ error: 'unity-open' })
        expect(deps.trashItem).not.toHaveBeenCalled()
      }
    })

    it('refuses an alias that only the inode ties to the project, and finds an unquoted path with spaces', async () => {
      const { deps, files } = fake(platform, [...unity(MAC, '/'), '/Other/Same'], {
        identity: (p) => (p === '/Other/Same' || p === MAC ? '7:7' : `id:${p}`),
        processCommands: async () => [editor('/Other/Same')],
      })
      files.add(`${MAC}/Temp/UnityLockfile`)
      expect(await trashProject(MAC, deps)).toEqual({ error: 'unity-open' })
      const SPACED = '/Users/me/My Games/Arena'
      const spaced = fake(platform, unity(SPACED, '/'), { processCommands: async () => [`${editor(SPACED)} -batchmode`] })
      spaced.files.add(`${SPACED}/Temp/UnityLockfile`)
      expect(await trashProject(SPACED, spaced.deps)).toEqual({ error: 'unity-open' })
    })

    it('finds an unquoted alias whose folder name holds " -x", and picks the longest value that resolves', async () => {
      const ALIAS = '/Volumes/Alias -Design'
      const { deps, files } = fake(platform, unity(MAC, '/'), { processCommands: async () => [`${editor(ALIAS)} -batchmode`] }, { [ALIAS]: MAC })
      files.add(`${MAC}/Temp/UnityLockfile`)
      expect(await trashProject(MAC, deps)).toEqual({ error: 'unity-open' })
      expect(deps.trashItem).not.toHaveBeenCalled()
    })

    it('keeps a trailing space in the -projectPath value (double-quoted, single-quoted and unquoted)', async () => {
      const TRAIL = '/Users/me/Games/Arena '
      for (const arg of [`"${TRAIL}"`, `'${TRAIL}'`, TRAIL]) {
        const { deps, files } = fake(platform, [...unity(TRAIL, '/'), ...unity(MAC, '/')], { processCommands: async () => [`${editor(arg)} -batchmode`] })
        files.add(`${TRAIL}/Temp/UnityLockfile`)
        expect(await trashProject(TRAIL, deps), arg).toEqual({ error: 'unity-open' })
        expect(deps.trashItem).not.toHaveBeenCalled()
      }
    })

    it('cuts an unquoted value at any whitespace, so "-projectPath /w/Game /extra" blocks Game', async () => {
      const GAME = '/w/Game'
      const { deps, files } = fake(platform, unity(GAME, '/'), { processCommands: async () => [editor(`${GAME} /extra`)] })
      files.add(`${GAME}/Temp/UnityLockfile`)
      expect(await trashProject(GAME, deps)).toEqual({ error: 'unity-open' })
      expect(deps.trashItem).not.toHaveBeenCalled()
    })

    it('blocks a folder whose name holds two consecutive spaces when it is open', async () => {
      const DOUBLE = '/Users/me/Games/My  Game'
      for (const arg of [DOUBLE, `"${DOUBLE}"`]) {
        const { deps, files } = fake(platform, [...unity(DOUBLE, '/'), '/Users/me/Games/My'], { processCommands: async () => [`${editor(arg)} -batchmode\r\n`] })
        files.add(`${DOUBLE}/Temp/UnityLockfile`)
        expect(await trashProject(DOUBLE, deps), arg).toEqual({ error: 'unity-open' })
        expect(deps.trashItem).not.toHaveBeenCalled()
      }
    })

    it('does not let an editor for Arena2 block Arena, nor a path only mentioned in the command text', async () => {
      const cases = [editor(`${MAC}2`), `${editor('/Users/me/Games/Other')} -logFile ${MAC}/log.txt`, `/Applications/Unity/Unity ${MAC}`]
      for (const cmd of cases) {
        const { deps, files } = fake(platform, [...unity(MAC, '/'), ...unity(`${MAC}2`, '/')], { processCommands: async () => [cmd] })
        files.add(`${MAC}/Temp/UnityLockfile`)
        expect(await trashProject(MAC, deps), cmd).toEqual({ ok: true })
      }
    })

    it.each(['null', 'throw'])('refuses when the process list cannot be read (%s)', async (how) => {
      const processCommands = how === 'null' ? async () => null : async () => { throw new Error('ps failed') }
      const { deps, files } = fake(platform, unity(MAC, '/'), { processCommands })
      files.add(`${MAC}/Temp/UnityLockfile`)
      expect(await trashProject(MAC, deps)).toEqual({ error: 'unity-open' })
      expect(deps.trashItem).not.toHaveBeenCalled()
    })
  })

  describe('confirmation', () => {
    it('asks about the resolved folder once, after the checks, and trashes only on yes', async () => {
      const { deps } = fake('darwin', unity(MAC, '/'))
      expect(await trashProject(MAC, deps)).toEqual({ ok: true })
      expect(deps.confirm).toHaveBeenCalledTimes(1)
      expect(deps.confirm).toHaveBeenCalledWith(MAC)
    })

    it.each([['no', async () => false], ['throw', async () => { throw new Error('x') }]])('does not trash when the answer is %s', async (_n, confirm) => {
      const { deps } = fake('darwin', unity(MAC, '/'), { confirm })
      expect(await trashProject(MAC, deps)).toEqual({ error: 'cancelled' })
      expect(deps.trashItem).not.toHaveBeenCalled()
    })

    it('never asks when a guard already refuses', async () => {
      const { deps } = fake('darwin', [MAC])
      await trashProject(MAC, deps)
      expect(deps.confirm).not.toHaveBeenCalled()
    })

    it.each([
      ['win32', 'Geri Dönüşüm Kutusu', 'C:\\Users\\me\\Games\\Arena'],
      ['darwin', 'Çöp Kutusu', MAC],
      ['linux', 'Çöp Kutusu', MAC],
    ])('words the box for %s with the name, full path and a default of Vazgeç', (platform, bin, real) => {
      const prompt = trashPrompt(real, platform)
      expect(prompt.message).toContain('"Arena"')
      expect(prompt.detail).toContain(real)
      expect(prompt.detail).toContain(bin)
      expect([prompt.go, prompt.cancel]).toEqual([`${bin}'na taşı`, 'Vazgeç'])
    })
  })

  describe('concurrent and stale requests', () => {
    it('refuses a second request for the same folder while the first waits on its confirmation', async () => {
      let answer: (v: boolean) => void = () => {}
      const { deps } = fake('darwin', unity(MAC, '/'), { confirm: vi.fn(() => new Promise<boolean>(r => { answer = r })) })
      const first = trashProject(MAC, deps)
      expect(await trashProject(MAC, deps)).toEqual({ error: 'busy' })
      for (let i = 0; i < 5; i++) await Promise.resolve()
      expect(deps.confirm).toHaveBeenCalledTimes(1)
      answer(true)
      expect(await first).toEqual({ ok: true })
      expect(deps.trashItem).toHaveBeenCalledTimes(1)
    })

    it('frees the folder after a refusal or a no', async () => {
      const { deps } = fake('darwin', unity(MAC, '/'), { confirm: vi.fn(async () => false) })
      expect(await trashProject(MAC, deps)).toEqual({ error: 'cancelled' })
      expect(await trashProject(MAC, deps)).toEqual({ error: 'cancelled' })
    })

    it('refuses when the folder is gone after the user confirmed', async () => {
      const dirs = new Set(unity(MAC, '/'))
      const { deps } = fake('darwin', [], { confirm: vi.fn(async () => { dirs.delete(MAC); return true }) })
      deps.realpath = (p) => { if (!dirs.has(p)) throw new Error('ENOENT'); return p }
      deps.isDirectory = (p) => dirs.has(p)
      expect(await trashProject(MAC, deps)).toEqual({ error: 'not-found' })
      expect(deps.trashItem).not.toHaveBeenCalled()
    })

    it('refuses when it stopped being a Unity project after the user confirmed', async () => {
      const dirs = new Set(unity(MAC, '/'))
      const { deps } = fake('darwin', [], { confirm: vi.fn(async () => { dirs.delete(`${MAC}/Assets`); return true }) })
      deps.realpath = (p) => p
      deps.isDirectory = (p) => dirs.has(p)
      expect(await trashProject(MAC, deps)).toEqual({ error: 'not-a-unity-project' })
      expect(deps.trashItem).not.toHaveBeenCalled()
    })

    it('refuses when Unity opened it after the user confirmed', async () => {
      const state = { locked: false }
      const { deps } = fake('win32', unity(WIN, '\\'), { probeLock: () => (state.locked ? 'busy' : 'free'), confirm: vi.fn(async () => { state.locked = true; return true }) })
      expect(await trashProject(WIN, deps)).toEqual({ error: 'unity-open' })
      expect(deps.trashItem).not.toHaveBeenCalled()
    })
  })

  describe('Windows path forms', () => {
    const win = (dir: string, extra: string[] = [], over: Partial<TrashDeps> = {}, links: Record<string, string> = {}) =>
      fake('win32', [...unity(WIN, '\\'), ...extra], over, links)

    it.each([
      ['\\\\?\\C:\\Users\\me\\Games\\Arena'],
      ['\\\\.\\C:\\Users\\me\\Games\\Arena'],
      ['C:\\Users\\me\\Games\\Arena.'],
      ['C:\\Users\\me\\Games\\Arena '],
      ['C:\\Users\\me\\Games\\Arena::$DATA'],
      ['C:\\Users\\me\\Games\\Arena:stream'],
    ])('refuses the input form %j', async (dir) => {
      const { deps } = win(dir, [], {}, { [dir]: WIN })
      expect(await trashProject(dir, deps)).toEqual({ error: 'invalid-path' })
      expect(deps.trashItem).not.toHaveBeenCalled()
    })

    it('refuses an 8.3 name that realpath did not expand, but accepts the expanded form', async () => {
      const short = 'C:\\Users\\BURAKE~1\\Games\\Arena'
      const { deps } = fake('win32', unity(short, '\\'), { protectedDirs: ['C:\\Users\\burakemre'] })
      expect(await trashProject(short, deps)).toEqual({ error: 'invalid-path' })
      expect(deps.trashItem).not.toHaveBeenCalled()
      const { deps: expanded } = fake('win32', unity(WIN, '\\'), {}, { [short]: WIN })
      expect(await trashProject(short, expanded)).toEqual({ error: 'invalid-path' })
      const { deps: ok } = fake('win32', unity(WIN, '\\'))
      expect(await trashProject(WIN, ok)).toEqual({ ok: true })
    })

    it('allows a drive letter colon and a dotted folder name inside the path', async () => {
      const dir = 'D:\\My.Games\\Arena'
      const { deps } = fake('win32', unity(dir, '\\'))
      expect(await trashProject(dir, deps)).toEqual({ ok: true })
    })
  })

  describe('identity (dev+ino)', () => {
    const ids: Record<string, string> = { '/a/Docs': '1:1', '/a': '1:2', '/': '1:3', '/b/Alias': '1:1', '/b': '1:4' }
    const identity = (p: string) => ids[p] ?? null

    it('refuses a folder that is the same inode as a protected dir under another name', async () => {
      const { deps } = fake('linux', [...unity('/b/Alias', '/'), '/a/Docs'], { identity: (p) => (p === '/b/Alias' ? '1:1' : identity(p)), protectedDirs: ['/a/Docs'] })
      expect(await trashProject('/b/Alias', deps)).toEqual({ error: 'protected' })
      expect(deps.trashItem).not.toHaveBeenCalled()
    })

    it('refuses a folder that is an ancestor of a protected dir by inode', async () => {
      const { deps } = fake('linux', [...unity('/x/Up', '/'), '/a/Docs'], { identity: (p) => (p === '/x/Up' ? '1:2' : identity(p)), protectedDirs: ['/a/Docs'] })
      expect(await trashProject('/x/Up', deps)).toEqual({ error: 'protected' })
    })

    it('refuses a project that sits inside an app dir by inode', async () => {
      const { deps } = fake('linux', [...unity('/x/Proj', '/'), '/x'], { identity: (p) => ({ '/x/Proj': '9:9', '/x': '1:4', '/y': '1:4' } as Record<string, string>)[p] ?? null, appDirs: ['/y'] })
      expect(await trashProject('/x/Proj', deps)).toEqual({ error: 'protected' })
    })

    it('refuses the open workspace by inode', async () => {
      const { deps } = fake('linux', [...unity('/b/Alias', '/'), '/a/Docs'], { identity: (p) => (p === '/b/Alias' ? '1:1' : identity(p)), openWorkspaces: ['/a/Docs'] })
      expect(await trashProject('/b/Alias', deps)).toEqual({ error: 'open-workspace' })
    })
  })

  describe('identity across the confirmation', () => {
    it('refuses when another directory sits at the same path after the box', async () => {
      let swapped = false
      const { deps } = fake('darwin', unity(MAC, '/'), { identity: (p) => (p === MAC ? (swapped ? '2:2' : '1:1') : `id:${p}`) })
      deps.confirm = vi.fn(async () => { swapped = true; return true })
      expect(await trashProject(MAC, deps)).toEqual({ error: 'not-found' })
      expect(deps.trashItem).not.toHaveBeenCalled()
    })

    it('refuses when no identity can be taken', async () => {
      const { deps } = fake('darwin', unity(MAC, '/'), { identity: () => null })
      expect(await trashProject(MAC, deps)).toEqual({ error: 'not-found' })
      expect(deps.confirm).not.toHaveBeenCalled()
      expect(deps.trashItem).not.toHaveBeenCalled()
    })

    it('refuses when the identity is gone only after the box', async () => {
      let asked = false
      const { deps } = fake('darwin', unity(MAC, '/'), { identity: (p) => (p === MAC && asked ? null : `id:${p}`) })
      deps.confirm = vi.fn(async () => { asked = true; return true })
      expect(await trashProject(MAC, deps)).toEqual({ error: 'not-found' })
      expect(deps.trashItem).not.toHaveBeenCalled()
    })
  })

  describe('identity right before the move', () => {
    it('refuses when the folder is swapped while the second check awaits the process list', async () => {
      const { deps, files } = fake('darwin', unity(MAC, '/'), { })
      files.add(`${MAC}/Temp/UnityLockfile`)
      let id = '1:1', reads = 0
      deps.identity = (p) => (p === MAC ? id : `id:${p}`)
      deps.processCommands = async () => { if (++reads === 2) id = '2:2'; return [] }
      expect(await trashProject(MAC, deps)).toEqual({ error: 'not-found' })
      expect(reads).toBe(2)
      expect(deps.trashItem).not.toHaveBeenCalled()
    })

    it('refuses when the identity cannot be taken at the last moment', async () => {
      const { deps } = fake('darwin', unity(MAC, '/'))
      let calls = 0
      const base = deps.identity!
      deps.identity = (p) => (p === MAC && ++calls > 2 ? null : base(p))
      deps.confirm = vi.fn(async () => true)
      const r = await trashProject(MAC, deps)
      expect(r).toEqual({ error: 'not-found' })
      expect(deps.trashItem).not.toHaveBeenCalled()
    })
  })

  it('reports trash-failed when the OS refuses, and checkTrashable alone never trashes', async () => {
    const { deps } = fake('darwin', unity(MAC, '/'), { trashItem: vi.fn(async () => { throw new Error('denied') }) })
    expect(await trashProject(MAC, deps)).toEqual({ error: 'trash-failed' })
    const { deps: ok } = fake('darwin', unity(MAC, '/'))
    expect(await checkTrashable(MAC, ok)).toEqual({ real: MAC, id: `id:${MAC}` })
    expect(ok.trashItem).not.toHaveBeenCalled()
  })
})

describe('workspace-trash channel', () => {
  const read = (rel: string) => fs.readFileSync(path.join(__dirname, '..', rel), 'utf-8')

  it('is whitelisted for the desktop renderer and absent from the web remote shim', () => {
    expect(ALLOWED_INVOKE_CHANNELS.has('workspace-trash')).toBe(true)
    expect(read('renderer/lib/remote/ipcShim.ts')).not.toContain('workspace-trash')
  })

  it('authorizes the root and confirms natively in main before trashing', () => {
    const src = read('main/background.ts')
    const handler = src.slice(src.indexOf("handleSecure('workspace-trash'"), src.indexOf("handleSecure('register-dropped-folder'"))
    expect(handler.indexOf('isRegisteredRoot(hostPath)')).toBeGreaterThan(0)
    expect(handler).not.toContain('untrustedWorkspace')
    expect(handler).toContain('dialog.showMessageBox')
    expect(handler).toContain('defaultId: 1')
    expect(handler).toContain('cancelId: 1')
    expect(handler).toContain('realpathSync.native')
  })

  it('is registered through handleSecure and the helper never deletes permanently', () => {
    expect(read('main/background.ts')).toContain("handleSecure('workspace-trash'")
    expect(read('main/helpers/project-trash.ts')).not.toMatch(/\b(rmSync|rmdirSync|rimraf|unlinkSync)\b|fs\.rm\(/)
  })
})

describe('exact registered root', () => {
  it('accepts a registered root itself and refuses a folder inside it', () => {
    electron.userData = fs.mkdtempSync(path.join(os.tmpdir(), 'trash-trust-'))
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'trash-root-'))
    const inner = path.join(root, 'Inner')
    fs.mkdirSync(inner)
    try {
      registerTrustedRoot(root)
      expect(isRegisteredRoot(root)).toBe(true)
      expect(isRegisteredRoot(inner)).toBe(false)
      expect(isRegisteredRoot(path.dirname(root))).toBe(false)
    } finally {
      fs.rmSync(root, { recursive: true, force: true })
      fs.rmSync(electron.userData, { recursive: true, force: true })
    }
  })
})

describe('registered roots are compared as stored', () => {
  it('does not grant a folder a retargeted saved path now points at', () => {
    electron.userData = fs.mkdtempSync(path.join(os.tmpdir(), 'trash-trust-'))
    const box = fs.mkdtempSync(path.join(os.tmpdir(), 'trash-retarget-'))
    const saved = path.join(box, 'Saved'), other = path.join(box, 'Other')
    fs.mkdirSync(saved); fs.mkdirSync(other)
    try {
      registerTrustedRoot(saved)
      fs.renameSync(saved, path.join(box, 'Moved'))
      fs.symlinkSync(other, saved, 'dir')
      expect(isRegisteredRoot(other)).toBe(false)
    } finally {
      fs.rmSync(box, { recursive: true, force: true })
      fs.rmSync(electron.userData, { recursive: true, force: true })
    }
  })
})
