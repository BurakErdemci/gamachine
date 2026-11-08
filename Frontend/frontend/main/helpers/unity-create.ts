import fs from 'fs'
import path from 'path'
import { HUB_FLAGS } from './unity-launch'

export type CreatePhase = 'starting' | 'running' | 'done' | 'failed'
export interface CreateProgress { phase: CreatePhase; elapsedMs: number; logTail?: string }
export type HubErrorCode = 'busy' | 'nameInvalid' | 'parentMissing' | 'exists' | 'pathTooLong' |
  'spawnFailed' | 'exited' | 'notCreated' | 'cancelled' | 'notProject' | 'notTrusted' |
  'versionUnknown' | 'editorMissing' | 'invalidArguments' | 'parentNotPicked' | 'templateUnknown' | 'openExited' | 'hubNotReady'
export type Cleanup = 'removed' | 'notOurs' | 'removeFailed' | 'pathChanged' | 'none'
export interface HubFailure {
  error: string
  code: HubErrorCode
  params?: Record<string, string | number>
  cleanup?: Cleanup
  path?: string
}
export type CreateResult = { ok: true; path: string } | ({ ok: false } & HubFailure)

export interface CreateChild {
  kill: () => boolean | void
  exitCode?: number | null
  signalCode?: string | null
  once: (event: string, listener: (...args: any[]) => void) => unknown
}

export interface CreateDeps {
  platform: NodeJS.Platform
  exists: (p: string) => boolean
  realpath: (p: string) => string
  removeDir: (dir: string) => Promise<void>
  delay?: (ms: number) => Promise<void>
  lstat?: (p: string) => { isDirectory: () => boolean; isSymbolicLink: () => boolean; birthtimeMs: number }
  // Optional: without it the editor is spawned without making sure Unity Hub is up.
  ensureHub?: () => Promise<boolean>
  spawn: (command: string, args: string[]) => CreateChild
  readLogTail: (file: string) => string
  isProject: (dir: string) => boolean
  registerTrusted: (dir: string) => void
  logFile: () => string
  now: () => number
  intervalMs?: number
}

export interface CreateOptions {
  editorPath: string
  parentDir: string
  name: string
  templatePath?: string
  onProgress: (progress: CreateProgress) => void
}

// Library\PackageCache breaks past MAX_PATH on Windows; measured limit with margin.
const MAX_WINDOWS_PATH = 150
const NAME_PATTERN = /^[\p{L}\p{N} _-]{1,64}$/u

let active: CreateChild | null = null
let cancelRequested = false
// True while waiting for Unity Hub, before the child exists: a second creation must still answer busy.
let waitingForHub = false

export function cancelCreate(): { cancelled: boolean } {
  if (!active) return { cancelled: false }
  if (active.exitCode != null || active.signalCode != null) return { cancelled: false }
  cancelRequested = true
  active.kill()
  return { cancelled: true }
}

export function createProject(opts: CreateOptions, deps: CreateDeps): Promise<CreateResult> {
  const p = deps.platform === 'win32' ? path.win32 : path.posix
  const fail = (error: string, code: HubErrorCode, params?: HubFailure['params']): CreateResult =>
    ({ ok: false, error, code, ...(params ? { params } : {}) })
  if (active || waitingForHub) return Promise.resolve(fail('Another project is already being created.', 'busy'))
  if (!NAME_PATTERN.test(opts.name) || opts.name !== opts.name.trim()) {
    return Promise.resolve(fail('Project name may use letters, digits, space, - and _ only (1-64 characters).', 'nameInvalid'))
  }
  if (!p.isAbsolute(opts.parentDir)) return Promise.resolve(fail('Parent folder does not exist.', 'parentMissing'))
  // Resolve once so a symlink re-pointed while Unity runs cannot redirect the cleanup.
  let parent: string
  try {
    parent = deps.realpath(opts.parentDir)
  } catch {
    return Promise.resolve(fail('Parent folder does not exist.', 'parentMissing'))
  }
  const target = p.join(parent, opts.name)
  if (deps.exists(target)) return Promise.resolve(fail('A folder with this name already exists.', 'exists'))
  if (deps.platform === 'win32' && target.length > MAX_WINDOWS_PATH) {
    return Promise.resolve(fail(`Project path is too long (${target.length} > ${MAX_WINDOWS_PATH}); choose a shorter location.`, 'pathTooLong', { length: target.length, max: MAX_WINDOWS_PATH }))
  }

  const logFile = deps.logFile()
  const args = ['-batchmode', '-quit', '-createProject', target]
  if (opts.templatePath) args.push('-cloneFromTemplate', opts.templatePath)
  args.push(...HUB_FLAGS, '-logFile', logFile)

  const started = deps.now()
  const startedAt = Date.now()
  const emit = (phase: CreatePhase) => {
    let logTail: string | undefined
    try { logTail = deps.readLogTail(logFile) } catch { /* The log may not exist yet. */ }
    opts.onProgress({ phase, elapsedMs: deps.now() - started, ...(logTail ? { logTail } : {}) })
  }

  const run = () => new Promise<CreateResult>(resolve => {
    cancelRequested = false
    let child: CreateChild
    try {
      child = deps.spawn(opts.editorPath, args)
    } catch (err: any) {
      const detail = String(err?.message || err)
      resolve(fail(detail, 'spawnFailed', { detail }))
      return
    }
    active = child
    emit('starting')
    const timer = setInterval(() => emit('running'), deps.intervalMs ?? 1000)

    let settled = false
    let cleaning = false
    const finish = (result: CreateResult) => {
      if (settled) return
      settled = true
      clearInterval(timer)
      active = null
      emit(result.ok ? 'done' : 'failed')
      resolve(result)
    }
    // A failed run leaves a half-made folder that would block retrying the same name.
    const failAndClean = async (error: string, code: HubErrorCode, params?: HubFailure['params']) => {
      if (settled || cleaning) return
      cleaning = true
      clearInterval(timer)
      let cleanup: Cleanup = 'none'
      // The same ownership rule applies after a retry delay: the path may have been replaced.
      const isOurs = () => {
        try {
          const st = (deps.lstat ?? fs.lstatSync)(target)
          return st.isDirectory() && !st.isSymbolicLink() && st.birthtimeMs > 0 && st.birthtimeMs > startedAt + 1
        } catch { return false }
      }
      let resolved: string | null = null
      try { resolved = deps.realpath(target) } catch { /* Nothing at target, so nothing to remove. */ }
      if (resolved === target) {
        // Only a directory born after this attempt began is provably ours; a renamed-in project is not.
        // Date.now() drops the fraction of a millisecond that birthtimeMs keeps, hence the + 1.
        if (!isOurs()) {
          cleanup = 'notOurs'
          if (code !== 'cancelled') error += ` The folder was not removed because it was not created by this attempt: ${target}`
        } else {
          const delay = deps.delay ?? (ms => new Promise<void>(done => setTimeout(done, ms)))
          for (let attempt = 0; attempt < 5; attempt++) {
            if (attempt > 0) {
              resolved = null
              try { resolved = deps.realpath(target) } catch { /* The folder may have disappeared. */ }
              if (resolved !== target) {
                cleanup = resolved === null ? 'none' : 'pathChanged'
                if (resolved !== null && code !== 'cancelled') error += ` The partial folder was not removed because its path changed: ${target}`
                break
              }
              if (!isOurs()) {
                cleanup = 'notOurs'
                if (code !== 'cancelled') error += ` The folder was not removed because it was not created by this attempt: ${target}`
                break
              }
            }
            try {
              await deps.removeDir(target)
              cleanup = 'removed'
              break
            } catch (err: any) {
              if (attempt < 4) {
                await delay(1000)
              } else {
                cleanup = 'removeFailed'
                console.error('[hub] partial folder not moved to Trash:', target, err?.message || err)
                if (code !== 'cancelled') error += ` The partial folder could not be removed: ${target}`
              }
            }
          }
        }
      } else if (resolved !== null) {
        cleanup = 'pathChanged'
        if (code !== 'cancelled') error += ` The partial folder was not removed because its path changed: ${target}`
      }
      finish({ ok: false, error, code, ...(params ? { params } : {}), cleanup, ...(cleanup !== 'none' ? { path: target } : {}) })
    }
    // Windows: Unity.exe is a GUI binary, so only the child's own 'exit' is a reliable end.
    // Unity never ran, so a folder at target now is not ours to remove.
    child.once('error', (err: Error) => {
      if (cleaning) return
      const detail = String(err?.message || err)
      finish(fail(`Could not start Unity: ${detail}`, 'spawnFailed', { detail }))
    })
    child.once('exit', (code: number | null) => {
      // An 'error' already ended this attempt and freed the slot; a retry may own the folder now.
      if (settled || cleaning) return
      // A finished project wins over a cancel that landed after Unity had already exited.
      if (code === 0 && deps.isProject(target)) {
        deps.registerTrusted(target)
        return finish({ ok: true, path: target })
      }
      if (cancelRequested) return void failAndClean('cancelled', 'cancelled')
      if (code !== 0) return void failAndClean(`Unity exited with code ${code}.`, 'exited', { code: code ?? 'null' })
      void failAndClean('Unity finished but the project was not created.', 'notCreated')
    })
  })
  if (!deps.ensureHub) return run()
  waitingForHub = true
  return deps.ensureHub().catch(() => false).finally(() => { waitingForHub = false }).then(ready =>
    ready ? run() : fail('Unity Hub could not be started or its license is not ready.', 'hubNotReady'))
}
