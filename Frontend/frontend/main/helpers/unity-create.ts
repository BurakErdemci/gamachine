import path from 'path'

export type CreatePhase = 'starting' | 'running' | 'done' | 'failed'
export interface CreateProgress { phase: CreatePhase; elapsedMs: number; logTail?: string }
export type CreateResult = { ok: true; path: string } | { ok: false; error: string }

export interface CreateChild {
  kill: () => boolean | void
  once: (event: string, listener: (...args: any[]) => void) => unknown
}

export interface CreateDeps {
  platform: NodeJS.Platform
  exists: (p: string) => boolean
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

export function cancelCreate(): { cancelled: boolean } {
  if (!active) return { cancelled: false }
  cancelRequested = true
  active.kill()
  return { cancelled: true }
}

export function createProject(opts: CreateOptions, deps: CreateDeps): Promise<CreateResult> {
  const p = deps.platform === 'win32' ? path.win32 : path.posix
  const fail = (error: string): CreateResult => ({ ok: false, error })
  if (active) return Promise.resolve(fail('Another project is already being created.'))
  if (!NAME_PATTERN.test(opts.name) || opts.name !== opts.name.trim()) {
    return Promise.resolve(fail('Project name may use letters, digits, space, - and _ only (1-64 characters).'))
  }
  if (!p.isAbsolute(opts.parentDir) || !deps.exists(opts.parentDir)) {
    return Promise.resolve(fail('Parent folder does not exist.'))
  }
  const target = p.join(opts.parentDir, opts.name)
  if (deps.exists(target)) return Promise.resolve(fail('A folder with this name already exists.'))
  if (deps.platform === 'win32' && target.length > MAX_WINDOWS_PATH) {
    return Promise.resolve(fail(`Project path is too long (${target.length} > ${MAX_WINDOWS_PATH}); choose a shorter location.`))
  }

  const logFile = deps.logFile()
  const args = ['-batchmode', '-quit', '-createProject', target]
  if (opts.templatePath) args.push('-cloneFromTemplate', opts.templatePath)
  args.push('-logFile', logFile)

  const started = deps.now()
  const emit = (phase: CreatePhase) => {
    let logTail: string | undefined
    try { logTail = deps.readLogTail(logFile) } catch { /* The log may not exist yet. */ }
    opts.onProgress({ phase, elapsedMs: deps.now() - started, ...(logTail ? { logTail } : {}) })
  }

  return new Promise<CreateResult>(resolve => {
    cancelRequested = false
    let child: CreateChild
    try {
      child = deps.spawn(opts.editorPath, args)
    } catch (err: any) {
      resolve(fail(String(err?.message || err)))
      return
    }
    active = child
    emit('starting')
    const timer = setInterval(() => emit('running'), deps.intervalMs ?? 1000)

    let settled = false
    const finish = (result: CreateResult) => {
      if (settled) return
      settled = true
      clearInterval(timer)
      active = null
      emit(result.ok ? 'done' : 'failed')
      resolve(result)
    }
    // Windows: Unity.exe is a GUI binary, so only the child's own 'exit' is a reliable end.
    child.once('error', (err: Error) => finish(fail(`Could not start Unity: ${err?.message || err}`)))
    child.once('exit', (code: number | null) => {
      if (cancelRequested) return finish(fail('cancelled'))
      if (code !== 0) return finish(fail(`Unity exited with code ${code}.`))
      if (!deps.isProject(target)) return finish(fail('Unity finished but the project was not created.'))
      deps.registerTrusted(target)
      finish({ ok: true, path: target })
    })
  })
}
