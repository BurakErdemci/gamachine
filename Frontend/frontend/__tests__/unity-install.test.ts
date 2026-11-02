import { EventEmitter } from 'events'
import fs from 'fs'
import path from 'path'
import { describe, expect, it } from 'vitest'
import {
  InstallChild, InstallDeps, InstallProgress, cancelInstall, cliState, findUnityCli, loadCatalog, planInstall, startInstall,
} from '../main/helpers/unity-install'

const FIX = path.join(__dirname, 'fixtures', 'unity-install')
const fixture = (name: string) => fs.readFileSync(path.join(FIX, name), 'utf-8')
const releaseFixture = () => JSON.parse(fixture('release-api-latest-lts-windows.json'))
const VERSION = '6000.3.27f2'
const GB = 1024 ** 3
const CLI = 'C:\\Users\\u\\AppData\\Local\\Unity\\bin\\unity.exe'
const ROOT = 'C:\\Program Files\\Unity\\Hub\\Editor'

class FakeChild extends EventEmitter implements InstallChild {
  pid = 4242
  stdout = new EventEmitter()
  stderr = new EventEmitter()
  killed = false
  kill() { this.killed = true; return true }
  out(text: string) { this.stdout.emit('data', Buffer.from(text)) }
  lines(lines: string[]) { for (const l of lines) this.out(l + '\n') }
  close(code: number | null) { this.emit('close', code, null) }
}

const flush = () => new Promise<void>(resolve => setImmediate(resolve))
async function until(cond: () => boolean) {
  for (let i = 0; i < 200 && !cond(); i++) await flush()
  expect(cond()).toBe(true)
}

interface Call { command: string; args: string[]; options: any; child: FakeChild }

function setup(over: Partial<InstallDeps> = {}) {
  const files = new Map<string, string>()
  const present = new Set<string>(['C:\\Program Files'])
  const clock = { t: 1_000_000 }
  const timers = new Map<number, () => void>()
  let nextTimer = 1
  const calls: Call[] = []
  let installPath: string | null = ROOT
  const releases = new Map<string, any>()
  const state = {
    editors: [] as { version: string; path: string }[],
    hub: 'C:\\Program Files\\Unity Hub\\Unity Hub.exe' as string | null,
    fetchUrls: [] as string[],
    fetchImpl: undefined as undefined | ((url: string) => Promise<any>),
  }
  const installCalls = () => calls.filter(c => !c.args.includes('install-path'))
  const deps: InstallDeps = {
    fetch: async (url) => {
      state.fetchUrls.push(url)
      if (state.fetchImpl) return state.fetchImpl(url)
      const u = new URL(url)
      const wanted = u.searchParams.get('version')
      const body = wanted
        ? { results: releases.has(wanted) ? [releases.get(wanted)] : [] }
        : u.searchParams.get('stream') === 'LTS' ? releaseFixture() : { results: [] }
      return { ok: true, status: 200, json: async () => body }
    },
    spawn: (command, args, options) => {
      const child = new FakeChild()
      calls.push({ command, args, options, child })
      if (args.includes('install-path')) {
        Promise.resolve().then(() => {
          if (installPath) child.lines([JSON.stringify({ type: 'result', success: true, command: 'install-path', data: { path: installPath }, errors: [], warnings: [] })])
          child.close(installPath ? 0 : 1)
        })
      }
      return child
    },
    exists: p => present.has(p),
    readFile: p => { if (!files.has(p)) throw new Error('ENOENT'); return files.get(p)! },
    statfs: async () => ({ bavail: 500 * GB, bsize: 1 }),
    platform: 'win32',
    arch: 'x64',
    env: { APPDATA: 'C:\\Users\\u\\AppData\\Roaming', LOCALAPPDATA: 'C:\\Users\\u\\AppData\\Local', ProgramFiles: 'C:\\Program Files' },
    homedir: 'C:\\Users\\u',
    now: () => clock.t,
    setInterval: fn => { const id = nextTimer++; timers.set(id, fn); return id },
    clearInterval: id => { timers.delete(id) },
    killTree: pid => { calls.find(c => c.child.pid === pid && !c.args.includes('install-path'))?.child.close(1) },
    editors: () => state.editors,
    findHub: () => state.hub,
    ...over,
  }
  const tick = (ms: number) => {
    for (let spent = 0; spent < ms; spent += 1000) {
      clock.t += 1000
      for (const fn of [...timers.values()]) fn()
    }
  }
  return { deps, files, present, calls, tick, state, releases, installCalls, setInstallPath: (p: string | null) => { installPath = p } }
}
type Ctx = ReturnType<typeof setup>

const withCli = (s: Ctx) => { s.present.add(CLI); return s }
const installed = (s: Ctx) => {
  s.state.editors = [{ version: VERSION, path: `${ROOT}\\${VERSION}\\Editor\\Unity.exe` }]
}

async function withCatalog(s: Ctx = setup()) {
  const cat = await loadCatalog(s.deps)
  expect(cat.ok).toBe(true)
  return s
}

async function runInstall(s: Ctx, req: any) {
  const events: InstallProgress[] = []
  const before = s.installCalls().length
  const promise = startInstall(req, p => events.push(p), s.deps)
  await until(() => s.installCalls().length > before)
  return { promise, child: s.installCalls().at(-1)!.child, call: s.installCalls().at(-1)!, events }
}

describe('findUnityCli / cliState', () => {
  it('finds the CLI through cli-install.json when the target exists', () => {
    const s = setup()
    s.files.set('C:\\Users\\u\\AppData\\Roaming\\UnityHub\\cli-install.json', JSON.stringify({ path: 'D:\\tools\\unity.exe' }))
    s.present.add('C:\\Users\\u\\AppData\\Roaming\\UnityHub\\cli-install.json')
    s.present.add('D:\\tools\\unity.exe')
    expect(findUnityCli(s.deps)).toBe('D:\\tools\\unity.exe')
    expect(cliState(s.deps)).toEqual({ available: true })
  })

  it('falls back to the default path when the manifest target is gone', () => {
    const s = setup()
    s.files.set('C:\\Users\\u\\AppData\\Roaming\\UnityHub\\cli-install.json', JSON.stringify({ path: 'D:\\gone\\unity.exe' }))
    s.present.add('C:\\Users\\u\\AppData\\Roaming\\UnityHub\\cli-install.json')
    s.present.add(CLI)
    expect(findUnityCli(s.deps)).toBe(CLI)
  })

  it('uses the macOS default location', () => {
    const s = setup({ platform: 'darwin', homedir: '/Users/u', env: {} })
    s.present.add('/Users/u/.unity/bin/unity')
    expect(findUnityCli(s.deps)).toBe('/Users/u/.unity/bin/unity')
  })

  it('ignores a manifest target that is not the CLI binary, but accepts UNITY.EXE in any case on Windows', () => {
    const s = setup()
    const manifest = 'C:\\Users\\u\\AppData\\Roaming\\UnityHub\\cli-install.json'
    s.present.add(manifest)
    s.present.add('D:\\tools\\evil.exe')
    s.files.set(manifest, JSON.stringify({ path: 'D:\\tools\\evil.exe' }))
    expect(findUnityCli(s.deps)).toBeNull()
    s.present.add('D:\\tools\\UNITY.EXE')
    s.files.set(manifest, JSON.stringify({ path: 'D:\\tools\\UNITY.EXE' }))
    expect(findUnityCli(s.deps)).toBe('D:\\tools\\UNITY.EXE')
    const mac = setup({ platform: 'darwin', homedir: '/Users/u', env: {} })
    const macManifest = '/Users/u/Library/Application Support/UnityHub/cli-install.json'
    mac.present.add(macManifest)
    mac.present.add('/opt/Unity')
    mac.files.set(macManifest, JSON.stringify({ path: '/opt/Unity' }))
    expect(findUnityCli(mac.deps)).toBeNull()
  })

  describe('MSIX Hub', () => {
    const CACHE = 'C:\\Users\\u\\AppData\\Local\\Packages\\UnityTechnologies.UnityHub_2vrhnee42bhxm\\LocalCache'
    const MSIX_MANIFEST = `${CACHE}\\Roaming\\UnityHub\\cli-install.json`
    const MSIX_CLI = `${CACHE}\\Local\\Unity\\bin\\unity.exe`
    const CLASSIC_MANIFEST = 'C:\\Users\\u\\AppData\\Roaming\\UnityHub\\cli-install.json'
    const msixManifest = (s: Ctx, target: unknown) => {
      s.present.add(MSIX_MANIFEST)
      s.files.set(MSIX_MANIFEST, JSON.stringify({ path: target }))
    }

    it('maps the virtualized LOCALAPPDATA target into LocalCache', () => {
      const s = setup()
      msixManifest(s, CLI)
      s.present.add(MSIX_CLI)
      expect(findUnityCli(s.deps)).toBe(MSIX_CLI)
      expect(cliState(s.deps)).toEqual({ available: true })
    })

    it('maps a virtualized APPDATA target into LocalCache Roaming, ignoring case', () => {
      const s = setup()
      const roamingCli = `${CACHE}\\Roaming\\Tools\\unity.exe`
      msixManifest(s, 'c:\\users\\u\\appdata\\roaming\\Tools\\unity.exe')
      s.present.add(roamingCli)
      expect(findUnityCli(s.deps)).toBe(roamingCli)
    })

    it('uses a manifest target that exists as-is', () => {
      const s = setup()
      msixManifest(s, 'D:\\tools\\unity.exe')
      s.present.add('D:\\tools\\unity.exe')
      expect(findUnityCli(s.deps)).toBe('D:\\tools\\unity.exe')
    })

    it('rejects a manifest target that is not the CLI binary', () => {
      const s = setup()
      msixManifest(s, 'C:\\Users\\u\\AppData\\Local\\Unity\\bin\\evil.exe')
      s.present.add(`${CACHE}\\Local\\Unity\\bin\\evil.exe`)
      expect(findUnityCli(s.deps)).toBeNull()
    })

    it('does not map a path that merely shares the LOCALAPPDATA prefix', () => {
      const s = setup()
      msixManifest(s, 'C:\\Users\\u\\AppData\\LocalLow\\unity.exe')
      s.present.add(`${CACHE}\\LocalLow\\unity.exe`)
      expect(findUnityCli(s.deps)).toBeNull()
    })

    it('finds the CLI by the LocalCache fallback when no manifest exists', () => {
      const s = setup()
      s.present.add(MSIX_CLI)
      expect(findUnityCli(s.deps)).toBe(MSIX_CLI)
    })

    it('lets the classic manifest win over the MSIX one', () => {
      const s = setup()
      s.present.add(CLASSIC_MANIFEST)
      s.files.set(CLASSIC_MANIFEST, JSON.stringify({ path: 'D:\\tools\\unity.exe' }))
      s.present.add('D:\\tools\\unity.exe')
      msixManifest(s, CLI)
      s.present.add(MSIX_CLI)
      expect(findUnityCli(s.deps)).toBe('D:\\tools\\unity.exe')
    })

    it('falls through an invalid MSIX manifest to the fallbacks', () => {
      const s = setup()
      s.present.add(MSIX_MANIFEST)
      s.files.set(MSIX_MANIFEST, '{not json')
      s.present.add(MSIX_CLI)
      expect(findUnityCli(s.deps)).toBe(MSIX_CLI)
    })
  })

  it('reports noHub or openHubOnce when the CLI is absent', () => {
    const s = setup()
    expect(findUnityCli(s.deps)).toBeNull()
    expect(cliState(s.deps)).toEqual({ available: false, reason: 'openHubOnce' })
    s.state.hub = null
    expect(cliState(s.deps)).toEqual({ available: false, reason: 'noHub' })
  })
})

describe('loadCatalog', () => {
  it('maps the real Release API response', async () => {
    const s = setup()
    const cat = await loadCatalog(s.deps)
    if (!cat.ok) throw new Error('catalog failed')
    expect(cat.releases).toHaveLength(1)
    const r = cat.releases[0]
    expect(r).toMatchObject({ version: VERSION, changeset: '22188f149405', stream: 'LTS', recommended: false, installed: false,
      releaseDate: '2026-10-30T16:57:42.531Z', downloadBytes: 4236703648, installedBytes: 8466397074 })
    const ids = r.modules.map(m => m.id)
    expect(ids).not.toContain('visualstudioprofessionalunityworkload')
    expect(ids).toContain('android')
    expect(r.modules.find(m => m.id === 'visualstudio')).toMatchObject({ preSelected: true, category: 'DEV_TOOL', downloadBytes: 1699733504 })
    const android = r.modules.find(m => m.id === 'android')!
    expect(android.parts[0].id).toBe('android-open-jdk-17.0.18+8')
    expect(android.parts.map(p => p.id)).toContain('android-ndk-r27c')
    expect(android.parts.length).toBeGreaterThan(2)
    expect(android.downloadBytes).toBeGreaterThan(1549421200)
    expect(android.eulas.length).toBeGreaterThan(0)
    expect(new Set(android.eulas.map(e => e.url)).size).toBe(android.eulas.length)
    expect(r.modules.find(m => m.id === 'webgl')!.eulas).toEqual([])
    expect(s.state.fetchUrls).toHaveLength(2)
    expect(s.state.fetchUrls[0]).toContain('stream=LTS')
    expect(s.state.fetchUrls[0]).toContain('platform=WINDOWS&architecture=X86_64')
    expect(s.state.fetchUrls[0]).toContain('limit=10&order=RELEASE_DATE_DESC')
    expect(s.state.fetchUrls[1]).toContain('stream=SUPPORTED')
  })

  it('merges both streams, dedupes by version and sorts newest first', async () => {
    const s = setup()
    const newer = releaseFixture().results[0]
    newer.version = '6000.6.7f1'; newer.stream = 'SUPPORTED'; newer.recommended = true; newer.releaseDate = '2026-11-01T00:00:00.000Z'
    s.state.fetchImpl = async url => ({ ok: true, status: 200, json: async () =>
      ({ results: url.includes('stream=SUPPORTED') ? [releaseFixture().results[0], newer] : releaseFixture().results }) })
    const cat = await loadCatalog(s.deps)
    if (!cat.ok) throw new Error('catalog failed')
    expect(cat.releases.map(r => r.version)).toEqual(['6000.6.7f1', VERSION])
    expect(cat.releases[0]).toMatchObject({ stream: 'SUPPORTED', recommended: true })
  })

  it('uses ARM64 on arm64 and fails on Linux without fetching', async () => {
    const s = setup({ arch: 'arm64', platform: 'darwin', homedir: '/Users/u' })
    await loadCatalog(s.deps)
    expect(s.state.fetchUrls[0]).toContain('platform=MAC_OS&architecture=ARM64')
    const linux = setup({ platform: 'linux' })
    expect(await loadCatalog(linux.deps)).toMatchObject({ ok: false, code: 'catalogFailed' })
    expect(linux.state.fetchUrls).toHaveLength(0)
  })

  it('flags installed editors and their installed modules, and fetches missing installed versions', async () => {
    const s = setup()
    installed(s)
    s.files.set(`${ROOT}\\${VERSION}\\modules.json`, JSON.stringify([{ id: 'webgl', selected: true }, { id: 'ios', selected: false }, { id: 'android' }]))
    s.state.editors.push({ version: '2022.3.1f1', path: `${ROOT}\\2022.3.1f1\\Editor\\Unity.exe` })
    const old = releaseFixture().results[0]
    old.version = '2022.3.1f1'; old.releaseDate = '2023-01-01T00:00:00.000Z'
    s.releases.set('2022.3.1f1', old)
    const cat = await loadCatalog(s.deps)
    if (!cat.ok) throw new Error('catalog failed')
    expect(cat.releases.map(r => r.version)).toEqual([VERSION, '2022.3.1f1'])
    const r = cat.releases[0]
    expect(r.installed).toBe(true)
    expect(r.modules.find(m => m.id === 'webgl')!.installed).toBe(true)
    expect(r.modules.find(m => m.id === 'ios')!.installed).toBe(false)
    expect(r.modules.find(m => m.id === 'android')!.installed).toBe(false)
    expect(s.state.fetchUrls.filter(u => u.includes('version='))).toHaveLength(1)
  })

  it('drops releases with a bad version, changeset or size and modules with a bad id or size', async () => {
    const s = setup()
    const base = releaseFixture().results[0]
    const variant = (edit: (r: any) => void, version: string) => {
      const r = JSON.parse(JSON.stringify(base))
      r.version = version
      edit(r)
      return r
    }
    const mods = (r: any) => r.downloads[0].modules
    const results = [
      variant(() => {}, '6000.3.27f2'),
      variant(() => {}, '6000.3.26f1; rm'),
      variant(r => { r.shortRevision = 'ZZZ' }, '6000.3.25f1'),
      variant(r => { r.shortRevision = '--help' }, '6000.3.24f1'),
      variant(r => { r.downloads[0].downloadSize.value = -1 }, '6000.3.23f1'),
      variant(r => { r.downloads[0].installedSize.value = Number.POSITIVE_INFINITY }, '6000.3.22f1'),
      variant(r => { r.downloads[0].downloadSize = { unit: 'BYTE' } }, '6000.3.21f1'),
      variant(r => {
        mods(r).find((m: any) => m.id === 'webgl').id = '--evil'
        mods(r).find((m: any) => m.id === 'ios').downloadSize.value = -5
        mods(r).find((m: any) => m.id === 'android').subModules[0].installedSize.value = Number.NaN
      }, '6000.3.20f1'),
    ]
    s.state.fetchImpl = async url => ({ ok: true, status: 200, json: async () =>
      ({ results: url.includes('stream=LTS') ? results : [] }) })
    const cat = await loadCatalog(s.deps)
    if (!cat.ok) throw new Error('catalog failed')
    expect(cat.releases.map(r => r.version).sort()).toEqual(['6000.3.20f1', '6000.3.27f2'])
    const kept = cat.releases.find(r => r.version === '6000.3.20f1')!.modules.map(m => m.id)
    expect(kept).not.toContain('--evil')
    expect(kept).not.toContain('ios')
    expect(kept).not.toContain('android')
    expect(kept).toContain('visualstudio')
  })

  it('returns offline on a network error or timeout and catalogFailed on HTTP or JSON errors', async () => {
    const s = setup()
    s.state.fetchImpl = async () => { throw new TypeError('fetch failed') }
    expect(await loadCatalog(s.deps)).toMatchObject({ ok: false, code: 'offline', cli: { available: false } })
    s.state.fetchImpl = async () => { const e = new Error('timeout'); e.name = 'TimeoutError'; throw e }
    expect(await loadCatalog(s.deps)).toMatchObject({ ok: false, code: 'offline' })
    s.state.fetchImpl = async () => ({ ok: false, status: 503, json: async () => ({}) })
    expect(await loadCatalog(s.deps)).toMatchObject({ ok: false, code: 'catalogFailed' })
    s.state.fetchImpl = async () => ({ ok: true, status: 200, json: async () => { throw new SyntaxError('bad json') } })
    expect(await loadCatalog(s.deps)).toMatchObject({ ok: false, code: 'catalogFailed' })
  })
})

describe('planInstall', () => {
  it('counts the editor and the module bytes for a new editor', async () => {
    const s = withCli(await withCatalog())
    s.present.add(ROOT)
    const plan = await planInstall({ version: VERSION, modules: ['webgl'] }, s.deps)
    const webgl = releaseFixture().results[0].downloads[0].modules.find((m: any) => m.id === 'webgl')
    expect(plan).toMatchObject({ ok: true, installRoot: ROOT, freeBytes: 500 * GB, enough: true, eulas: [],
      downloadBytes: 4236703648 + 930219208, installedBytes: 8466397074 + webgl.installedSize.value })
    expect(s.calls[0].args).toEqual(['--format', 'ndjson', '--non-interactive', 'install-path'])
  })

  it('does not count the editor for an installed version and counts a reinstalled module', async () => {
    const s = setup()
    installed(s)
    s.files.set(`${ROOT}\\${VERSION}\\modules.json`, JSON.stringify([{ id: 'webgl', selected: true }]))
    withCli(await withCatalog(s))
    const plan = await planInstall({ version: VERSION, modules: ['webgl', 'ios'] }, s.deps)
    if (!plan.ok) throw new Error('plan failed')
    expect(plan.downloadBytes).toBe(343770192 + 930219208)
    expect(await planInstall({ version: VERSION, modules: ['webgl'] }, s.deps)).toMatchObject({ ok: true, downloadBytes: 930219208 })
    expect(await planInstall({ version: VERSION, modules: [] }, s.deps)).toMatchObject({ ok: false, code: 'invalidArguments' })
  })

  it('measures an installed editor\'s plan on the editor\'s own drive, not the CLI install path', async () => {
    const free: Record<string, number> = { 'D:\\Unity': 900 * GB }
    const s = setup({ statfs: async dir => ({ bavail: free[dir] ?? 1, bsize: 1 }) })
    s.state.editors = [{ version: VERSION, path: `D:\\Unity\\${VERSION}\\Editor\\Unity.exe` }]
    s.present.add('D:\\Unity')
    s.present.add(ROOT)
    withCli(await withCatalog(s))
    const plan = await planInstall({ version: VERSION, modules: ['webgl'] }, s.deps)
    expect(plan).toMatchObject({ ok: true, installRoot: `D:\\Unity\\${VERSION}`, freeBytes: 900 * GB, enough: true })
    expect(s.calls).toHaveLength(0)
    // A new editor still goes to the CLI install path, which here has no space.
    const other = setup({ statfs: async () => ({ bavail: 1, bsize: 1 }) })
    withCli(await withCatalog(other))
    expect(await planInstall({ version: VERSION, modules: ['webgl'] }, other.deps)).toMatchObject({ ok: true, installRoot: ROOT, enough: false })
  })

  it('treats unknown free space as enough and falls back to the default root when install-path fails', async () => {
    const s = withCli(await withCatalog(setup({ statfs: async () => { throw new Error('nope') } })))
    s.setInstallPath(null)
    const plan = await planInstall({ version: VERSION, modules: [] }, s.deps)
    expect(plan).toMatchObject({ ok: true, freeBytes: null, enough: true, installRoot: ROOT })
  })

  it('reports not enough space and the EULAs the selection needs', async () => {
    const s = withCli(await withCatalog(setup({ statfs: async () => ({ bavail: 1, bsize: 1024 }) })))
    const plan = await planInstall({ version: VERSION, modules: ['android'] }, s.deps)
    expect(plan).toMatchObject({ ok: true, freeBytes: 1024, enough: false })
    if (plan.ok) expect(plan.eulas.length).toBeGreaterThan(0)
  })

  it('returns cliMissing and the validation codes', async () => {
    const s = await withCatalog()
    expect(await planInstall({ version: VERSION, modules: [] }, s.deps)).toMatchObject({ ok: false, code: 'cliMissing' })
    withCli(s)
    expect(await planInstall({ version: 5, modules: [] }, s.deps)).toMatchObject({ code: 'invalidArguments' })
    expect(await planInstall({ version: VERSION, modules: ['--bad'] }, s.deps)).toMatchObject({ code: 'invalidArguments' })
    expect(await planInstall({ version: VERSION, modules: 'webgl' }, s.deps)).toMatchObject({ code: 'invalidArguments' })
    expect(await planInstall({ version: '1.0.0f1', modules: [] }, s.deps)).toMatchObject({ code: 'unknownVersion' })
    expect(await planInstall({ version: VERSION, modules: ['nope'] }, s.deps)).toMatchObject({ code: 'unknownModule' })
    expect(await planInstall({ version: VERSION, modules: ['visualstudioprofessionalunityworkload'] }, s.deps)).toMatchObject({ code: 'unknownModule' })
  })
})

describe('startInstall validation', () => {
  const start = (s: Ctx, req: any) => startInstall(req, () => {}, s.deps)

  it('returns each validation code without spawning', async () => {
    const s = await withCatalog()
    expect(await start(s, { version: VERSION, modules: [], acceptedEulas: false })).toMatchObject({ ok: false, code: 'cliMissing' })
    withCli(s)
    expect(await start(s, { version: 1, modules: [] })).toMatchObject({ code: 'invalidArguments' })
    expect(await start(s, { version: VERSION, modules: [1] })).toMatchObject({ code: 'invalidArguments' })
    expect(await start(s, { version: VERSION, modules: ['Bad Id'] })).toMatchObject({ code: 'invalidArguments' })
    expect(await start(s, { version: 'x', modules: [] })).toMatchObject({ code: 'unknownVersion' })
    expect(await start(s, { version: VERSION, modules: ['zzz'] })).toMatchObject({ code: 'unknownModule' })
    expect(await start(s, { version: VERSION, modules: ['android'] })).toMatchObject({ code: 'eulaNotAccepted' })
    expect(await start(s, { version: VERSION, modules: ['android'], acceptedEulas: 'yes' })).toMatchObject({ code: 'eulaNotAccepted' })
    expect(s.calls).toHaveLength(0)
  })

  it('refuses an installed editor with an empty module list', async () => {
    const s = setup()
    installed(s)
    withCli(await withCatalog(s))
    expect(await start(s, { version: VERSION, modules: [] })).toMatchObject({ ok: false, code: 'invalidArguments' })
    expect(s.calls).toHaveLength(0)
  })

  it('refuses when there is not enough space', async () => {
    const s = withCli(await withCatalog(setup({ statfs: async () => ({ bavail: 1, bsize: 1024 }) })))
    expect(await start(s, { version: VERSION, modules: ['webgl'] })).toMatchObject({ ok: false, code: 'notEnoughSpace' })
    expect(s.installCalls()).toHaveLength(0)
  })
})

describe('startInstall run', () => {
  const ready = async () => withCli(await withCatalog())

  it('builds the install argv for a new editor without --accept-eula', async () => {
    const s = await ready()
    const { promise, child, call } = await runInstall(s, { version: VERSION, modules: ['webgl', 'ios'], acceptedEulas: true })
    expect(call.command).toBe(CLI)
    expect(call.args).toEqual(['--format', 'ndjson', '--non-interactive', 'install', VERSION, '--changeset', '22188f149405', '--yes', '-m', 'webgl', '-m', 'ios'])
    expect(call.options).toMatchObject({ shell: false, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'], detached: false })
    child.close(1)
    await promise
  })

  it('adds --accept-eula only when a EULA is needed and accepted, and never --no-elevate', async () => {
    const s = await ready()
    const { promise, child, call } = await runInstall(s, { version: VERSION, modules: ['android'], acceptedEulas: true })
    expect(call.args.at(-1)).toBe('--accept-eula')
    expect(call.args).not.toContain('--no-elevate')
    child.close(1)
    await promise
  })

  it('passes an installed module to install-modules when the user ticks it again', async () => {
    const s = setup()
    installed(s)
    s.files.set(`${ROOT}\\${VERSION}\\modules.json`, JSON.stringify([{ id: 'webgl', selected: true }]))
    withCli(await withCatalog(s))
    const { promise, child, call } = await runInstall(s, { version: VERSION, modules: ['webgl'] })
    expect(call.args).toEqual(['--format', 'ndjson', '--non-interactive', 'install-modules', '--editor-version', VERSION, '-m', 'webgl'])
    child.close(1)
    await promise
  })

  it('uses install-modules for an installed editor and detaches on POSIX', async () => {
    const s = setup({ platform: 'darwin', homedir: '/Users/u', env: {}, arch: 'arm64' })
    s.present.add('/Users/u/.unity/bin/unity')
    s.state.editors = [{ version: VERSION, path: `/Applications/Unity/Hub/Editor/${VERSION}/Unity.app/Contents/MacOS/Unity` }]
    await loadCatalog(s.deps)
    const { promise, child, call } = await runInstall(s, { version: VERSION, modules: ['webgl', 'ios'], acceptedEulas: false })
    expect(call.args).toEqual(['--format', 'ndjson', '--non-interactive', 'install-modules', '--editor-version', VERSION, '-m', 'webgl', '-m', 'ios'])
    expect(call.options.detached).toBe(true)
    child.close(1)
    await promise
  })

  it('replays the webgl fixture to ok with completed [webgl]', async () => {
    const s = await ready()
    const { promise, child, events } = await runInstall(s, { version: VERSION, modules: ['webgl'] })
    child.out(fixture('cli-install-modules-webgl-ok.ndjson'))
    child.close(0)
    expect(await promise).toEqual({ ok: true, completed: ['webgl'] })
    expect(events[0]).toMatchObject({ phase: 'starting', pct: null })
    expect(events.some(e => e.phase === 'download' && e.message.startsWith('Downloading Web Build'))).toBe(true)
    expect(events.some(e => e.phase === 'install')).toBe(true)
    expect(events.at(-1)).toMatchObject({ phase: 'done', message: 'Install complete', stalled: false })
  })

  it('handles lines split across chunks and skips non-JSON lines', async () => {
    const s = await ready()
    const { promise, child, events } = await runInstall(s, { version: VERSION, modules: ['webgl'] })
    child.out('Adding module x as dependency of y.\n{"type":"progress","pct":')
    child.out('7,"msg":"Downloading A","phase":"download"}\n{not json}\n')
    child.out('{"type":"result","success":true,"data":{"completedUids":["webgl"]},"errors":[]}')
    child.close(0)
    expect(await promise).toEqual({ ok: true, completed: ['webgl'] })
    expect(events.filter(e => e.phase === 'download')).toHaveLength(1)
    expect(events.find(e => e.phase === 'download')).toMatchObject({ pct: 7, message: 'Downloading A' })
  })

  it('keeps a multi-byte character that is split across two chunks', async () => {
    const s = await ready()
    const { promise, child } = await runInstall(s, { version: VERSION, modules: ['webgl'] })
    const text = 'Yetersiz alan: \u00fc\u20ac'
    const line = Buffer.from(JSON.stringify({ type: 'result', success: false, data: {}, errors: [{ code: 'COMMAND_FAILED', message: text }] }) + '\n')
    const cut = line.indexOf(Buffer.from('\u20ac')) + 1
    child.stdout.emit('data', line.subarray(0, cut))
    child.stdout.emit('data', line.subarray(cut))
    child.close(6)
    expect(await promise).toEqual({ ok: false, code: 'cliFailed', message: text })
  })

  it('maps the android EULA refusal fixture to cliFailed with its message', async () => {
    const s = await ready()
    const { promise, child, events } = await runInstall(s, { version: VERSION, modules: ['webgl'] })
    child.out(fixture('cli-install-modules-android-eula-refused.ndjson'))
    child.close(6)
    const result = await promise
    expect(result).toMatchObject({ ok: false, code: 'cliFailed' })
    expect((result as any).message).toContain('require license acceptance')
    expect(events.at(-1)).toMatchObject({ phase: 'failed' })
  })

  it('maps ELEVATION_REQUIRED to elevationRefused', async () => {
    const s = await ready()
    const { promise, child } = await runInstall(s, { version: VERSION, modules: ['webgl'] })
    child.out('{"type":"result","success":false,"errors":[{"code":"ELEVATION_REQUIRED","message":"needs admin"}]}\n')
    child.close(5)
    expect(await promise).toMatchObject({ ok: false, code: 'elevationRefused', message: 'needs admin' })
  })

  it('returns exitCode with the stderr tail when there is no result line', async () => {
    const s = await ready()
    const { promise, child } = await runInstall(s, { version: VERSION, modules: ['webgl'] })
    child.stderr.emit('data', Buffer.from('x'.repeat(5000) + 'boom'))
    child.close(3)
    const result = await promise
    expect(result).toMatchObject({ ok: false, code: 'exitCode' })
    const message = (result as any).message as string
    expect(message.length).toBeLessThanOrEqual(4096)
    expect(message.endsWith('boom')).toBe(true)
  })

  it('replays the network-cut fixture: retrying on the retry line, final ok', async () => {
    const s = await ready()
    const { promise, child, events } = await runInstall(s, { version: VERSION, modules: ['ios', 'mac-mono', 'universal-windows-platform'] })
    child.out(fixture('cli-install-modules-network-cut-retry-ok.ndjson'))
    child.close(0)
    expect(await promise).toEqual({ ok: true, completed: ['ios', 'mac-mono', 'universal-windows-platform'] })
    const retry = events.filter(e => e.retrying)
    expect(retry.length).toBeGreaterThan(0)
    expect(retry[0].message).toMatch(/retrying/i)
    expect(events.at(-1)).toMatchObject({ phase: 'done', retrying: false })
  })

  it('flags stalled after 20 s of silence in download and clears it on the next line', async () => {
    const s = await ready()
    const { promise, child, events } = await runInstall(s, { version: VERSION, modules: ['webgl'] })
    child.lines(['{"type":"progress","pct":10,"msg":"Downloading A","phase":"download"}'])
    s.tick(19_000)
    expect(events.some(e => e.stalled)).toBe(false)
    s.tick(2_000)
    const stalled = events.filter(e => e.stalled)
    expect(stalled).toHaveLength(1)
    expect(stalled[0]).toMatchObject({ phase: 'download', pct: 10 })
    s.tick(5_000)
    expect(events.filter(e => e.stalled)).toHaveLength(1)
    child.lines(['{"type":"progress","pct":11,"msg":"Downloading A","phase":"download"}'])
    expect(events.at(-1)).toMatchObject({ pct: 11, stalled: false })
    child.close(1)
    await promise
  })

  it('does not flag stalled during the install phase', async () => {
    const s = await ready()
    const { promise, child, events } = await runInstall(s, { version: VERSION, modules: ['webgl'] })
    child.lines(['{"type":"progress","pct":50,"msg":"Installing A","phase":"install"}'])
    s.tick(60_000)
    expect(events.some(e => e.stalled)).toBe(false)
    child.close(1)
    await promise
  })

  it('rejects a second start while one runs, then frees the slot', async () => {
    const s = await ready()
    const first = await runInstall(s, { version: VERSION, modules: ['webgl'] })
    expect(await startInstall({ version: VERSION, modules: ['webgl'] }, () => {}, s.deps)).toMatchObject({ ok: false, code: 'busy' })
    first.child.close(1)
    await first.promise
    const second = await runInstall(s, { version: VERSION, modules: ['webgl'] })
    second.child.close(1)
    await second.promise
  })
})

describe('cancelInstall', () => {
  const ready = async () => withCli(await withCatalog())

  it('is idle when nothing runs', () => {
    expect(cancelInstall(setup().deps)).toEqual({ cancelled: false, reason: 'idle' })
  })

  it('kills the process tree during download and ends as cancelled', async () => {
    const s = await ready()
    const killed: number[] = []
    const realKill = s.deps.killTree
    s.deps.killTree = pid => { killed.push(pid); realKill(pid) }
    const { promise, child } = await runInstall(s, { version: VERSION, modules: ['webgl'] })
    child.lines(['{"type":"progress","pct":10,"msg":"Downloading A","phase":"download"}'])
    expect(cancelInstall(s.deps)).toEqual({ cancelled: true })
    expect(killed).toEqual([4242])
    expect(await promise).toEqual({ ok: false, code: 'cancelled' })
    expect(cancelInstall(s.deps)).toEqual({ cancelled: false, reason: 'idle' })
  })

  it('a cancel that arrives while the plan is still awaited stops the start before the spawn', async () => {
    let release!: () => void
    const gate = new Promise<void>(resolve => { release = resolve })
    let asked = false
    const s = withCli(await withCatalog(setup({ statfs: async () => { asked = true; await gate; return { bavail: 500 * GB, bsize: 1 } } })))
    const promise = startInstall({ version: VERSION, modules: ['webgl'] }, () => {}, s.deps)
    await until(() => asked)
    expect(cancelInstall(s.deps)).toEqual({ cancelled: true })
    release()
    expect(await promise).toEqual({ ok: false, code: 'cancelled' })
    expect(s.installCalls()).toHaveLength(0)
    expect(cancelInstall(s.deps)).toEqual({ cancelled: false, reason: 'idle' })
    // The flag does not leak into the next start.
    const next = await runInstall(s, { version: VERSION, modules: ['webgl'] })
    next.child.close(1)
    await next.promise
  })

  it('a success result wins over a cancel that came after it', async () => {
    const s = await ready()
    const { promise, child } = await runInstall(s, { version: VERSION, modules: ['webgl'] })
    child.out('{"type":"result","success":true,"data":{"completedUids":["webgl"]},"errors":[]}\n')
    expect(cancelInstall(s.deps)).toEqual({ cancelled: true })
    expect(await promise).toEqual({ ok: true, completed: ['webgl'] })
  })

  it('refuses during the install phase', async () => {
    const s = await ready()
    const { promise, child } = await runInstall(s, { version: VERSION, modules: ['webgl'] })
    child.lines(['{"type":"progress","pct":50,"msg":"Installing A","phase":"install"}'])
    expect(cancelInstall(s.deps)).toEqual({ cancelled: false, reason: 'installing' })
    expect(child.killed).toBe(false)
    child.out('{"type":"result","success":true,"data":{"completedUids":["webgl"]},"errors":[]}\n')
    child.close(0)
    expect(await promise).toEqual({ ok: true, completed: ['webgl'] })
  })
})
