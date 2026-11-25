// macOS update install without Squirrel.Mac.
//
// Squirrel.Mac reads the whole update zip into one CFData and crashes on a 1.37 GB zip
// (EXC_BREAKPOINT in __CFDataGrow, measured 25 Nov 2026, 4.0.0 -> 4.0.3, 3 of 3 runs;
// electron/electron#55089). So on macOS electron-updater only checks and downloads; this
// module unpacks the verified zip next to the installed bundle, checks the unpacked app the
// way Gatekeeper would, and a detached shell helper swaps the bundles once the app has exited.
import { execFile, spawn } from 'node:child_process'
import { constants as fsConstants, mkdirSync, rmSync } from 'node:fs'
import { access, mkdtemp, readdir, rm, stat, writeFile } from 'node:fs/promises'
import path from 'node:path'

export const TEAM_ID = 'MG4KS3D497'
// Marks a staging dir as ours, so a leftover from a crashed session can be removed safely.
const STAGING_MARKER = '.gamachine-staging'
// Long enough for the app's before-quit teardown; a helper never swaps under a running app.
const HELPER_WAIT_SECONDS = 60
const STEP_TIMEOUT_MS = 15 * 60 * 1000

export interface RunResult { code: number; stdout: string; stderr: string }

export interface MacInstallDeps {
  // Path of the running executable (.../Name.app/Contents/MacOS/Name).
  execPath?: string
  pid?: number
  // Where the helper appends its log (the app's log folder).
  logDir: string
  logger: Pick<Console, 'log' | 'error'>
  // The rest default to the real implementations; tests inject fakes.
  run?: (cmd: string, args: string[]) => Promise<RunResult>
  spawnDetached?: (cmd: string, args: string[]) => { pid?: number; kill: () => void }
  isWritable?: (dir: string) => Promise<boolean>
  // Opens an app bundle; the helper treats exit 0 as "launched".
  launcher?: string
  helperWaitSeconds?: number
}

export interface StagedMacUpdate {
  version: string
  // Starts the detached helper that waits for this process to exit, then swaps the bundles.
  // relaunch: "Restart now" - open the new app afterwards (and the old one again if the swap
  // fails); false: a normal quit ("Later") - the app is not opened either way. Returns the
  // helper's log file. At most once.
  swap(relaunch: boolean): string
  // Stops a started helper and removes the staging dir.
  discard(): Promise<void>
  // The same, synchronously: at will-quit there is no later event loop turn to wait on.
  discardSync(): void
}

export interface MacInstaller {
  // Why this copy cannot replace itself, or undefined when it can.
  refusal(): Promise<string | undefined>
  // Unpacks and checks the zip; resolves the staged update or the reason it cannot be installed.
  stage(zipFile: string, version: string): Promise<StagedMacUpdate | string>
}

function runCommand(cmd: string, args: string[]): Promise<RunResult> {
  return new Promise((resolve) => {
    execFile(cmd, args, { timeout: STEP_TIMEOUT_MS, maxBuffer: 16 * 1024 * 1024 }, (err: any, stdout, stderr) => {
      const code = err ? (typeof err.code === 'number' ? err.code : 1) : 0
      resolve({ code, stdout: String(stdout), stderr: String(stderr || (err && !stderr ? err.message : '')) })
    })
  })
}

function spawnDetachedProcess(cmd: string, args: string[]) {
  const child = spawn(cmd, args, { detached: true, stdio: 'ignore' })
  child.on('error', () => {})
  child.unref()
  return { pid: child.pid, kill: () => { try { child.kill() } catch { /* already gone */ } } }
}

async function writable(dir: string): Promise<boolean> {
  try { await access(dir, fsConstants.W_OK); return true } catch { return false }
}

// The bundle that contains the running executable, or undefined.
export function bundlePath(execPath: string): string | undefined {
  const m = /^(\/.+\.app)\/Contents\/MacOS\/[^/]+$/.exec(execPath)
  return m?.[1]
}

const firstLine = (s: string) => s.trim().split('\n')[0] ?? ''

// Positional arguments only: nothing from a path is ever interpolated into the script text.
export const SWAP_SCRIPT = `#!/bin/bash
# Gamachine update swap, written by main/helpers/mac-install.ts.
# swap.sh <pid> <installed.app> <staged.app> <staging-dir> <log> <wait-seconds> <launcher> <relaunch 1|0>
# relaunch=1 ("Restart now"): open the new app, and the old one again when the swap fails.
# relaunch=0 ("Later", a normal quit): the app is never opened; the user starts it when they like.
set -u
pid=$1 target=$2 staged=$3 staging=$4 log=$5 wait=$6 launcher=$7 relaunch=$8
backup="$(dirname "$target")/.$(basename "$target").backup-$pid"
say() { printf '%s [swap %s] %s\\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$$" "$*" >>"$log"; }
cleanup() { rm -rf "$staging" 2>>"$log" || say "could not remove $staging"; }
launch() { "$launcher" "$1" >>"$log" 2>&1; }
launch_old() {
  if [ "$relaunch" = 1 ]; then launch "$1" && say "old app relaunched" || say "could not relaunch the old app"
  else say "relaunch=0: the old app is not opened"; fi
}
trap 'say "helper exit $?"' EXIT
rollback() {
  say "FAILED: $1; restoring the old app"
  if [ -e "$target" ]; then mv "$target" "$staging/failed.app" 2>>"$log" || rm -rf "$target"; fi
  if mv "$backup" "$target" 2>>"$log"; then
    say "old app restored at $target"
    launch_old "$target"
  else
    say "RESTORE FAILED: the old app is at $backup"
  fi
  cleanup
  exit 1
}
say "waiting for pid $pid to exit (max \${wait}s); $target <- $staged"
ticks=$((wait * 10))
while kill -0 "$pid" 2>/dev/null; do
  ticks=$((ticks - 1))
  if [ "$ticks" -le 0 ]; then say "the app is still running after \${wait}s; nothing changed"; cleanup; exit 2; fi
  sleep 0.1
done
if [ ! -d "$staged" ]; then say "FAILED: staged app missing; nothing changed"; launch_old "$target"; cleanup; exit 1; fi
if [ -e "$backup" ]; then
  say "FAILED: $backup already exists; nothing changed"
  launch_old "$target"
  cleanup
  exit 1
fi
if ! mv "$target" "$backup" 2>>"$log"; then
  say "FAILED: cannot move $target aside; nothing changed"
  launch_old "$target"
  cleanup
  exit 1
fi
say "old app moved to $backup"
mv "$staged" "$target" 2>>"$log" || rollback "cannot move the new app into place"
if xattr -p com.apple.quarantine "$target" >/dev/null 2>&1; then
  xattr -dr com.apple.quarantine "$target" 2>>"$log" && say "quarantine removed" || say "could not remove quarantine"
fi
if [ "$relaunch" = 1 ]; then
  launch "$target" || rollback "the new app did not launch"
  say "new app launched"
else
  say "relaunch=0: the new app is not opened"
fi
rm -rf "$backup" 2>>"$log" && say "backup removed" || say "could not remove $backup"
cleanup
say "update installed"
exit 0
`

export function createMacInstaller(deps: MacInstallDeps): MacInstaller {
  const execPath = deps.execPath ?? process.execPath
  const pid = deps.pid ?? process.pid
  const run = deps.run ?? runCommand
  const spawnDetached = deps.spawnDetached ?? spawnDetachedProcess
  const isWritable = deps.isWritable ?? writable
  const launcher = deps.launcher ?? '/usr/bin/open'
  const { logger } = deps
  const installed = bundlePath(execPath)

  const refusal = async (): Promise<string | undefined> => {
    if (!installed) return `uygulama paketi bulunamadı: ${execPath}`
    if (installed.includes('/AppTranslocation/')) return 'uygulama karantina kopyasından (App Translocation) çalışıyor; Uygulamalar klasörüne taşıyıp yeniden aç'
    if (installed.startsWith('/Volumes/')) return 'uygulama bağlı bir diskten (dmg) çalışıyor; Uygulamalar klasörüne taşı'
    if (!(await isWritable(path.dirname(installed)))) return `${path.dirname(installed)} yazılabilir değil`
    return undefined
  }

  // Leftovers of earlier sessions that never quit cleanly; only dirs carrying our marker.
  const removeStale = async (parent: string, prefix: string) => {
    let names: string[] = []
    try { names = await readdir(parent) } catch { return }
    for (const name of names.filter((n) => n.startsWith(prefix))) {
      const dir = path.join(parent, name)
      try {
        await stat(path.join(dir, STAGING_MARKER))
        await rm(dir, { recursive: true, force: true })
        logger.log('[updater] eski hazırlık klasörü silindi:', dir)
      } catch { /* not ours */ }
    }
  }

  const check = async (app: string, version: string, bundleId: string): Promise<string | undefined> => {
    const sig = await run('/usr/bin/codesign', ['--verify', '--deep', '--strict', app])
    if (sig.code !== 0) return `imza doğrulanamadı (codesign): ${firstLine(sig.stderr)}`
    const info = await run('/usr/bin/codesign', ['-dv', '--verbose=2', app])
    const team = /^TeamIdentifier=(\S+)$/m.exec(info.stderr + '\n' + info.stdout)?.[1]
    if (team !== TEAM_ID) return `beklenmeyen geliştirici kimliği: ${team ?? 'yok'}`
    const plist = path.join(app, 'Contents', 'Info.plist')
    const shortVersion = (await run('/usr/bin/plutil', ['-extract', 'CFBundleShortVersionString', 'raw', '-o', '-', plist])).stdout.trim()
    if (shortVersion !== version) return `paket sürümü ${shortVersion || 'yok'}, beklenen ${version}`
    const stagedId = (await run('/usr/bin/plutil', ['-extract', 'CFBundleIdentifier', 'raw', '-o', '-', plist])).stdout.trim()
    if (!stagedId || stagedId !== bundleId) return `paket kimliği ${stagedId || 'yok'}, beklenen ${bundleId}`
    const gk = await run('/usr/sbin/spctl', ['--assess', '--type', 'execute', app])
    if (gk.code !== 0) return `Gatekeeper kabul etmedi (spctl): ${firstLine(gk.stderr)}`
    return undefined
  }

  const stage = async (zipFile: string, version: string): Promise<StagedMacUpdate | string> => {
    const refused = await refusal()
    if (refused) return refused
    const target = installed!
    const parent = path.dirname(target)
    const prefix = `.${path.basename(target, '.app')}-update-`
    await removeStale(parent, prefix)
    let staging: string
    try {
      // Same folder as the bundle, so the swap is a rename on one volume.
      staging = await mkdtemp(path.join(parent, prefix))
      await writeFile(path.join(staging, STAGING_MARKER), '')
    } catch (e) {
      return `hazırlık klasörü açılamadı: ${(e as any)?.message || e}`
    }
    const fail = async (reason: string) => {
      await rm(staging, { recursive: true, force: true }).catch(() => {})
      return reason
    }
    try {
      const started = Date.now()
      const unzip = await run('/usr/bin/ditto', ['-x', '-k', zipFile, staging])
      if (unzip.code !== 0) return await fail(`zip açılamadı (ditto): ${firstLine(unzip.stderr)}`)
      const apps = (await readdir(staging)).filter((n) => n.endsWith('.app'))
      if (apps.length !== 1) return await fail(`zip içinde tek bir .app yok: ${apps.join(', ') || 'hiç'}`)
      const staged = path.join(staging, apps[0]!)
      const installedId = (await run('/usr/bin/plutil', ['-extract', 'CFBundleIdentifier', 'raw', '-o', '-', path.join(target, 'Contents', 'Info.plist')])).stdout.trim()
      const bad = await check(staged, version, installedId)
      if (bad) return await fail(bad)
      // Written now, so quit time only spawns it.
      await writeFile(path.join(staging, 'swap.sh'), SWAP_SCRIPT, { mode: 0o700 })
      logger.log(`[updater] güncelleme hazırlandı ve doğrulandı (${Math.round((Date.now() - started) / 1000)} sn):`, staged)
      return makeStaged(version, target, staged, staging)
    } catch (e) {
      return await fail(`hazırlık hatası: ${(e as any)?.message || e}`)
    }
  }

  const makeStaged = (version: string, target: string, staged: string, staging: string): StagedMacUpdate => {
    let helper: { kill: () => void } | undefined
    const logFile = path.join(deps.logDir, 'update-swap.log')
    return {
      version,
      swap: (relaunch) => {
        if (helper) return logFile
        const script = path.join(staging, 'swap.sh')
        // Synchronous: at will-quit there is no later event loop turn to wait on.
        mkdirSync(deps.logDir, { recursive: true })
        helper = spawnDetached('/bin/bash', [script, String(pid), target, staged, staging, logFile, String(deps.helperWaitSeconds ?? HELPER_WAIT_SECONDS), launcher, relaunch ? '1' : '0'])
        logger.log(`[updater] kurulum yardımcısı başlatıldı (relaunch=${relaunch ? 1 : 0}), günlük:`, logFile)
        return logFile
      },
      discardSync: () => {
        helper?.kill()
        try { rmSync(staging, { recursive: true, force: true }) } catch (e) { logger.error('[updater] hazırlık klasörü silinemedi:', (e as any)?.message || e) }
      },
      discard: async () => {
        helper?.kill()
        await rm(staging, { recursive: true, force: true }).catch((e) => logger.error('[updater] hazırlık klasörü silinemedi:', e?.message || e))
      },
    }
  }

  return { refusal, stage }
}
