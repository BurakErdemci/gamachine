// @vitest-environment node
import { spawnSync } from 'node:child_process'
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMacInstaller, type RunResult } from '../main/helpers/mac-install'

const BUNDLE_ID = 'com.burakerdemci.gamachine'
const ok = (stdout = '', stderr = ''): RunResult => ({ code: 0, stdout, stderr })

let root: string
let parent: string
let target: string
beforeEach(() => {
  root = mkdtempSync(path.join(tmpdir(), 'gm-mac-install-'))
  parent = path.join(root, 'Applications')
  target = path.join(parent, 'Gamachine.app')
  makeApp(target, 'old')
})
afterEach(() => {
  // Tests make folders read-only; give write back so the tree can be removed.
  spawnSync('chmod', ['-R', 'u+w', root])
  rmSync(root, { recursive: true, force: true })
})

function makeApp(app: string, marker: string) {
  mkdirSync(path.join(app, 'Contents', 'MacOS'), { recursive: true })
  writeFileSync(path.join(app, 'Contents', 'MacOS', 'Gamachine'), '')
  writeFileSync(path.join(app, 'marker'), marker)
}

interface Opts { codesign?: number; team?: string; version?: string; stagedId?: string; spctl?: number; ditto?: number; apps?: string[] }

// Fakes ditto/codesign/plutil/spctl; ditto unpacks a fake "new" app into the staging dir.
function fakeRun(opts: Opts = {}) {
  return vi.fn(async (cmd: string, args: string[]): Promise<RunResult> => {
    const tool = path.basename(cmd)
    if (tool === 'ditto') {
      if (opts.ditto) return { code: opts.ditto, stdout: '', stderr: 'ditto: no space left' }
      for (const name of opts.apps ?? ['Gamachine.app']) makeApp(path.join(args[3]!, name), 'new')
      return ok()
    }
    if (tool === 'codesign' && args[0] === '--verify') return opts.codesign ? { code: opts.codesign, stdout: '', stderr: 'a sealed resource is missing or invalid' } : ok()
    if (tool === 'codesign') return ok('', `Executable=x\nTeamIdentifier=${opts.team ?? 'MG4KS3D497'}\n`)
    if (tool === 'plutil') {
      const plist = args.at(-1)!
      if (args[1] === 'CFBundleShortVersionString') return ok(`${opts.version ?? '4.0.4'}\n`)
      return ok(`${plist.startsWith(target) ? BUNDLE_ID : opts.stagedId ?? BUNDLE_ID}\n`)
    }
    if (tool === 'spctl') return opts.spctl ? { code: opts.spctl, stdout: '', stderr: 'rejected' } : ok()
    throw new Error(`unexpected command ${cmd}`)
  })
}

function installer(opts: Opts = {}, extra: Record<string, unknown> = {}) {
  const run = fakeRun(opts)
  const spawnDetached = vi.fn((_cmd: string, _args: string[]) => ({ pid: 4242, kill: vi.fn() }))
  const logger = { log: vi.fn(), error: vi.fn() }
  const inst = createMacInstaller({
    execPath: path.join(target, 'Contents', 'MacOS', 'Gamachine'), pid: 999999, logDir: path.join(root, 'logs'),
    logger, run, spawnDetached, ...extra,
  })
  return { inst, run, spawnDetached, logger }
}

const leftovers = () => readdirSync(parent).filter((n) => n !== 'Gamachine.app')

describe('mac installer: where the app runs from', () => {
  it.each([
    ['the bundle cannot be determined', '/usr/local/bin/node', /bulunamadı/],
    ['the app is translocated', '/private/var/folders/x/T/AppTranslocation/ABCD/d/Gamachine.app/Contents/MacOS/Gamachine', /Translocation/],
    ['the app runs from a mounted image', '/Volumes/Gamachine 4.0.4-arm64/Gamachine.app/Contents/MacOS/Gamachine', /dmg/],
  ])('refuses before staging when %s', async (_name, execPath, reason) => {
    const { inst, run } = installer({}, { execPath })
    await expect(inst.refusal()).resolves.toMatch(reason)
    await expect(inst.stage('/x/update.zip', '4.0.4')).resolves.toMatch(reason)
    expect(run).not.toHaveBeenCalled()
  })

  it.skipIf(process.platform === 'win32' || process.getuid?.() === 0)('refuses before staging when the parent folder is not writable', async () => {
    chmodSync(parent, 0o555)
    const { inst, run } = installer()
    await expect(inst.refusal()).resolves.toMatch(/yazılabilir değil/)
    await expect(inst.stage('/x/update.zip', '4.0.4')).resolves.toMatch(/yazılabilir değil/)
    expect(run).not.toHaveBeenCalled()
  })

  it('accepts an app in a writable folder', async () => {
    await expect(installer().inst.refusal()).resolves.toBeUndefined()
  })
})

describe('mac installer: staging', () => {
  it('unpacks the zip next to the bundle with ditto and checks the unpacked app', async () => {
    const { inst, run } = installer()
    const staged = await inst.stage('/cache/update.zip', '4.0.4')
    expect(typeof staged).toBe('object')
    const [dir] = leftovers()
    expect(dir).toMatch(/^\.Gamachine-update-/)
    expect(run.mock.calls[0]).toEqual(['/usr/bin/ditto', ['-x', '-k', '/cache/update.zip', path.join(parent, dir!)]])
    const stagedApp = path.join(parent, dir!, 'Gamachine.app')
    expect(run).toHaveBeenCalledWith('/usr/bin/codesign', ['--verify', '--deep', '--strict', stagedApp])
    expect(run).toHaveBeenCalledWith('/usr/sbin/spctl', ['--assess', '--type', 'execute', stagedApp])
    expect(readFileSync(path.join(target, 'marker'), 'utf8')).toBe('old')
  })

  it.each([
    ['codesign fails', { codesign: 3 }, /codesign/],
    ['the team is another developer', { team: 'ZZZZZZZZZZ' }, /ZZZZZZZZZZ/],
    ['the version differs from the verified manifest', { version: '4.0.3' }, /4\.0\.3.*4\.0\.4/],
    ['spctl rejects it', { spctl: 3 }, /spctl/],
    ['the bundle id differs', { stagedId: 'com.other.app' }, /com\.other\.app/],
    ['ditto fails', { ditto: 1 }, /ditto/],
    ['the zip holds no single app', { apps: ['A.app', 'B.app'] }, /tek bir \.app/],
  ])('installs nothing and removes the staging dir when %s', async (_name, opts, reason) => {
    const { inst } = installer(opts as Opts)
    await expect(inst.stage('/cache/update.zip', '4.0.4')).resolves.toMatch(reason)
    expect(leftovers()).toEqual([])
    expect(readFileSync(path.join(target, 'marker'), 'utf8')).toBe('old')
  })

  it('removes staging leftovers it made in an earlier session, and nothing else', async () => {
    const ours = path.join(parent, '.Gamachine-update-old1')
    mkdirSync(ours)
    writeFileSync(path.join(ours, '.gamachine-staging'), '')
    const foreign = path.join(parent, '.Gamachine-update-notours')
    mkdirSync(foreign)
    await installer().inst.stage('/cache/update.zip', '4.0.4')
    expect(existsSync(ours)).toBe(false)
    expect(existsSync(foreign)).toBe(true)
  })

  it('swap starts one detached helper with the pid, both bundles and the log path; discard stops it', async () => {
    const { inst, spawnDetached } = installer()
    const staged: any = await inst.stage('/cache/update.zip', '4.0.4')
    const dir = path.join(parent, leftovers()[0]!)
    const log = staged.swap(true)
    expect(staged.swap(false)).toBe(log)
    expect(log).toBe(path.join(root, 'logs', 'update-swap.log'))
    expect(spawnDetached).toHaveBeenCalledTimes(1)
    const [cmd, args] = spawnDetached.mock.calls[0]!
    expect(cmd).toBe('/bin/bash')
    expect(args).toEqual([path.join(dir, 'swap.sh'), '999999', target, path.join(dir, 'Gamachine.app'), dir, log, '60', '/usr/bin/open', '1'])
    expect(readFileSync(path.join(dir, 'swap.sh'), 'utf8')).toMatch(/^#!\/bin\/bash/)
    await staged.discard()
    expect(spawnDetached.mock.results[0]!.value.kill).toHaveBeenCalled()
    expect(existsSync(dir)).toBe(false)
  })

  it('swap(false) passes relaunch=0 to the helper', async () => {
    const { inst, spawnDetached } = installer()
    const staged: any = await inst.stage('/cache/update.zip', '4.0.4')
    staged.swap(false)
    expect(spawnDetached.mock.calls[0]![1].at(-1)).toBe('0')
  })

  it('discardSync stops a started helper and removes the staging dir at once', async () => {
    const { inst, spawnDetached } = installer()
    const staged: any = await inst.stage('/cache/update.zip', '4.0.4')
    const dir = path.join(parent, leftovers()[0]!)
    staged.swap(true)
    staged.discardSync()
    expect(spawnDetached.mock.results[0]!.value.kill).toHaveBeenCalled()
    expect(existsSync(dir)).toBe(false)
    expect(() => staged.discardSync()).not.toThrow()
    expect(leftovers()).toEqual([])
  })
})

// The real swap.sh, run synchronously on a fake bundle tree with a fake launcher.
describe.skipIf(process.platform === 'win32')('mac installer: the swap helper', () => {
  function rig(opts: { launcherFailsOnNew?: boolean; pid?: number; wait?: number } = {}) {
    const launches = path.join(root, 'launches.txt')
    const launcher = path.join(root, 'launcher.sh')
    writeFileSync(launcher, `#!/bin/bash\n${opts.launcherFailsOnNew ? 'grep -q new "$1/marker" && exit 1\n' : ''}echo "$1" >> "${launches}"\n`, { mode: 0o755 })
    let status: number | null = null
    const spawnDetached = (cmd: string, args: string[]) => {
      status = spawnSync(cmd, args, { encoding: 'utf8' }).status
      return { pid: 1, kill: () => {} }
    }
    const exited = spawnSync('true').pid
    const inst = createMacInstaller({
      execPath: path.join(target, 'Contents', 'MacOS', 'Gamachine'), pid: opts.pid ?? exited, logDir: path.join(root, 'logs'),
      logger: { log() {}, error() {} }, run: fakeRun(), spawnDetached, launcher, helperWaitSeconds: opts.wait,
    })
    return {
      inst, status: () => status, pid: opts.pid ?? exited,
      launches: () => (existsSync(launches) ? readFileSync(launches, 'utf8').trim().split('\n') : []),
      log: () => readFileSync(path.join(root, 'logs', 'update-swap.log'), 'utf8'),
    }
  }
  const marker = () => readFileSync(path.join(target, 'marker'), 'utf8')

  it('swaps the new app in, launches it and removes the backup and staging dir', async () => {
    const r = rig()
    const staged: any = await r.inst.stage('/cache/update.zip', '4.0.4')
    const stagedApp = path.join(parent, leftovers()[0]!, 'Gamachine.app')
    const quarantined = process.platform === 'darwin'
      && spawnSync('xattr', ['-w', 'com.apple.quarantine', '0081;00000000;Test;', stagedApp]).status === 0
    staged.swap(true)
    expect(r.status()).toBe(0)
    expect(marker()).toBe('new')
    expect(leftovers()).toEqual([])
    expect(r.launches()).toEqual([target])
    expect(r.log()).toMatch(/update installed[\s\S]*helper exit 0/)
    if (quarantined) expect(spawnSync('xattr', ['-p', 'com.apple.quarantine', target]).status).not.toBe(0)
  })

  it('restores and relaunches the old app when the new one cannot be moved into place', async () => {
    const r = rig()
    const staged: any = await r.inst.stage('/cache/update.zip', '4.0.4')
    // A read-only staging dir: the old app moves aside, the new one cannot leave staging.
    chmodSync(path.join(parent, leftovers()[0]!), 0o555)
    staged.swap(true)
    expect(r.status()).toBe(1)
    expect(marker()).toBe('old')
    expect(r.launches()).toEqual([target])
    expect(r.log()).toMatch(/FAILED: cannot move the new app into place/)
    expect(r.log()).toMatch(/old app restored/)
    expect(readdirSync(parent).filter((n) => n.includes('backup'))).toEqual([])
  })

  it('restores and relaunches the old app when the new one does not launch', async () => {
    const r = rig({ launcherFailsOnNew: true })
    const staged: any = await r.inst.stage('/cache/update.zip', '4.0.4')
    staged.swap(true)
    expect(r.status()).toBe(1)
    expect(marker()).toBe('old')
    expect(r.launches()).toEqual([target])
    expect(r.log()).toMatch(/the new app did not launch/)
    expect(leftovers()).toEqual([])
  })

  it('changes nothing while the app is still running past the wait', async () => {
    const r = rig({ pid: process.pid, wait: 1 })
    const staged: any = await r.inst.stage('/cache/update.zip', '4.0.4')
    staged.swap(true)
    expect(r.status()).toBe(2)
    expect(marker()).toBe('old')
    expect(r.launches()).toEqual([])
    expect(leftovers()).toEqual([])
    expect(r.log()).toMatch(/still running/)
  })

  // "Later" (a normal quit): the helper swaps but never opens any app.
  it('relaunch=0: swaps the new app in and does not open it', async () => {
    const r = rig()
    const staged: any = await r.inst.stage('/cache/update.zip', '4.0.4')
    staged.swap(false)
    expect(r.status()).toBe(0)
    expect(marker()).toBe('new')
    expect(r.launches()).toEqual([])
    expect(leftovers()).toEqual([])
    expect(r.log()).toMatch(/relaunch=0: the new app is not opened[\s\S]*update installed/)
  })

  it('relaunch=0: when the new app cannot be moved into place the old one is restored and not opened', async () => {
    const r = rig()
    const staged: any = await r.inst.stage('/cache/update.zip', '4.0.4')
    chmodSync(path.join(parent, leftovers()[0]!), 0o555)
    staged.swap(false)
    expect(r.status()).toBe(1)
    expect(marker()).toBe('old')
    expect(r.launches()).toEqual([])
    expect(r.log()).toMatch(/old app restored/)
    expect(r.log()).toMatch(/relaunch=0: the old app is not opened/)
    expect(readdirSync(parent).filter((n) => n.includes('backup'))).toEqual([])
  })

  it('relaunch=0: a launcher that would fail on the new app is never called', async () => {
    const r = rig({ launcherFailsOnNew: true })
    const staged: any = await r.inst.stage('/cache/update.zip', '4.0.4')
    staged.swap(false)
    expect(r.status()).toBe(0)
    expect(marker()).toBe('new')
    expect(r.launches()).toEqual([])
  })

  // The backup folder is named after the exited app's pid, so a leftover of the same name is predictable.
  it.each([[true, 1], [false, 0]])('refuses before moving anything when the backup path already exists (relaunch=%s)', async (relaunch, opened) => {
    const r = rig()
    const staged: any = await r.inst.stage('/cache/update.zip', '4.0.4')
    const stagedApp = path.join(parent, leftovers()[0]!, 'Gamachine.app')
    const backup = path.join(parent, `.Gamachine.app.backup-${r.pid}`)
    mkdirSync(backup)
    writeFileSync(path.join(backup, 'keep'), 'precious')
    staged.swap(relaunch)
    expect(r.status()).toBe(1)
    expect(marker()).toBe('old')
    expect(readFileSync(path.join(backup, 'keep'), 'utf8')).toBe('precious')
    expect(existsSync(stagedApp)).toBe(false)
    expect(r.launches()).toHaveLength(opened)
    expect(r.log()).toMatch(/already exists; nothing changed/)
    expect(readdirSync(parent).sort()).toEqual(['.Gamachine.app.backup-' + r.pid, 'Gamachine.app'].sort())
  })
})
