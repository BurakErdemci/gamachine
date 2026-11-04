import path from 'path'
import { StringDecoder } from 'string_decoder'
import { editorRoots, pathFor } from './unity-editors'
import { unityHubMsixCache } from './unity-hub'

// Unity editor install through the Unity CLI. The version catalog comes from Unity's public Release API
// because the CLI lists only 5 versions and cannot list modules of a version that is not installed
// (measured, Windows, CLI 1.0.0-beta.15). Gamachine never installs the CLI, never runs `unity auth` and never elevates.

export type CliState =
  | { available: true }
  | { available: false; reason: 'noHub' | 'openHubOnce' }
export interface InstallEula { label: string; url: string }
export interface InstallModule {
  id: string
  name: string
  category: string
  downloadBytes: number
  installedBytes: number
  preSelected: boolean
  eulas: InstallEula[]
  parts: { id: string; name: string; downloadBytes: number }[]
  installed: boolean
}
export interface InstallRelease {
  version: string
  changeset: string
  stream: 'LTS' | 'SUPPORTED'
  releaseDate: string
  recommended: boolean
  installed: boolean
  downloadBytes: number
  installedBytes: number
  modules: InstallModule[]
}
export type CatalogResult =
  | { ok: true; cli: CliState; releases: InstallRelease[]; requestedFound?: boolean }
  | { ok: false; cli: CliState; code: 'offline' | 'catalogFailed' }

export type PlanResult =
  | { ok: true; installRoot: string; downloadBytes: number; installedBytes: number; freeBytes: number | null; enough: boolean; eulas: InstallEula[] }
  | { ok: false; code: 'invalidArguments' | 'unknownVersion' | 'unknownModule' | 'cliMissing' }

export type InstallErrorCode = 'busy' | 'cliMissing' | 'invalidArguments' | 'unknownVersion' | 'unknownModule' |
  'eulaNotAccepted' | 'notEnoughSpace' | 'elevationRefused' | 'cancelled' | 'cliFailed' | 'exitCode'
export type InstallResult =
  | { ok: true; completed: string[] }
  | { ok: false; code: InstallErrorCode; message?: string; completed?: string[] }

export type InstallPhase = 'starting' | 'download' | 'install' | 'done' | 'failed'
export interface InstallProgress {
  phase: InstallPhase
  pct: number | null
  message: string
  stalled: boolean
  retrying: boolean
  elapsedMs: number
}

export interface InstallRequest { version: unknown; modules: unknown; acceptedEulas?: unknown }

interface ReadableLike { on: (event: 'data', listener: (chunk: Buffer | string) => void) => unknown }
export interface InstallChild {
  pid?: number
  stdout: ReadableLike | null
  stderr: ReadableLike | null
  kill: () => boolean | void
  once: (event: string, listener: (...args: any[]) => void) => unknown
}
export interface SpawnOptions {
  shell: false
  windowsHide: true
  stdio: ['ignore', 'pipe', 'pipe']
  detached: boolean
}
export interface InstallDeps {
  fetch: (url: string, init: { signal: AbortSignal }) => Promise<{ ok: boolean; status: number; json: () => Promise<any> }>
  spawn: (command: string, args: string[], options: SpawnOptions) => InstallChild
  exists: (p: string) => boolean
  readFile: (p: string) => string
  statfs: (p: string) => Promise<{ bavail: number | bigint; bsize: number | bigint }>
  platform: NodeJS.Platform
  arch: string
  env: Record<string, string | undefined>
  homedir: string
  now: () => number
  setInterval: (fn: () => void, ms: number) => unknown
  clearInterval: (handle: any) => void
  killTree: (pid: number) => void
  editors: () => { version: string; path: string }[]
  findHub: () => string | null
}

const RELEASE_API = 'https://services.api.unity.com/unity/editor/release/v1/releases'
const FETCH_TIMEOUT_MS = 15_000
const STALL_MS = 20_000
const STDERR_KEEP = 4096
const MODULE_ID = /^[a-z0-9][a-z0-9.+_-]*$/
const CLI_ARGS = ['--format', 'ndjson', '--non-interactive']

// Release API values reach the CLI argv and the UI; anything off-pattern is dropped, never repaired.
const VERSION_RE = /^\d+\.\d+\.\d+[abfpx]\d+$/
const CHANGESET_RE = /^[0-9a-f]{12}$/

const isCliBinary = (target: string, deps: InstallDeps) => {
  const base = pathFor(deps.platform).basename(target)
  return deps.platform === 'win32' ? base.toLowerCase() === 'unity.exe' : base === 'unity'
}

export function findUnityCli(deps: InstallDeps): string | null {
  const p = pathFor(deps.platform)
  // virtualized: the manifest of an MSIX Hub records paths as the Hub saw them, not as they exist outside the package.
  const manifests: { file: string; cache?: string }[] = []
  const fallbacks: string[] = []
  if (deps.platform === 'win32') {
    if (!deps.env.APPDATA && !deps.env.LOCALAPPDATA) return null
    const cache = unityHubMsixCache(deps.env)
    if (deps.env.APPDATA) manifests.push({ file: p.join(deps.env.APPDATA, 'UnityHub', 'cli-install.json') })
    if (cache) manifests.push({ file: p.join(cache, 'Roaming', 'UnityHub', 'cli-install.json'), cache })
    if (deps.env.LOCALAPPDATA) fallbacks.push(p.join(deps.env.LOCALAPPDATA, 'Unity', 'bin', 'unity.exe'))
    if (cache) fallbacks.push(p.join(cache, 'Local', 'Unity', 'bin', 'unity.exe'))
  } else if (deps.platform === 'darwin') {
    manifests.push({ file: p.join(deps.homedir, 'Library', 'Application Support', 'UnityHub', 'cli-install.json') })
    fallbacks.push(p.join(deps.homedir, '.unity', 'bin', 'unity'))
  } else {
    return null
  }
  const underPrefix = (target: string, prefix: string | undefined) => {
    if (!prefix) return null
    const t = p.normalize(target)
    const pre = p.normalize(prefix).replace(/\\+$/, '')
    return t.toLowerCase().startsWith(pre.toLowerCase() + '\\') ? t.slice(pre.length) : null
  }
  const resolveVirtualized = (target: string, cache: string): string => {
    if (deps.exists(target)) return target
    const local = underPrefix(target, deps.env.LOCALAPPDATA)
    if (local !== null) return p.join(cache, 'Local') + local
    const roaming = underPrefix(target, deps.env.APPDATA)
    if (roaming !== null) return p.join(cache, 'Roaming') + roaming
    return target
  }
  for (const { file, cache } of manifests) {
    try {
      if (!deps.exists(file)) continue
      const raw = JSON.parse(deps.readFile(file))?.path
      if (typeof raw !== 'string' || !raw) continue
      const target = cache ? resolveVirtualized(raw, cache) : raw
      // Only the CLI binary itself: a manifest must not point the install at any other program.
      if (isCliBinary(target, deps) && deps.exists(target)) return target
    } catch { /* An unreadable manifest falls through to the next one, then the default locations. */ }
  }
  return fallbacks.find(f => deps.exists(f)) ?? null
}

export function cliState(deps: InstallDeps): CliState {
  if (findUnityCli(deps)) return { available: true }
  return { available: false, reason: deps.findHub() ? 'openHubOnce' : 'noHub' }
}

// --- catalog ---

let lastCatalog: InstallRelease[] | null = null

type Eula = { url?: unknown; label?: unknown }
// null = not a usable size; the caller drops the release or module that carries it.
const bytes = (size: any): number | null =>
  typeof size?.value === 'number' && Number.isFinite(size.value) && size.value >= 0 ? size.value : null

const dedupeEulas = (eulas: InstallEula[]): InstallEula[] => {
  const seen = new Set<string>()
  return eulas.filter(e => !seen.has(e.url) && !!seen.add(e.url))
}

const collectEulas = (modules: any[]): InstallEula[] => dedupeEulas(modules.flatMap(m =>
  ((Array.isArray(m?.eula) ? m.eula : []) as Eula[]).filter(e => typeof e.url === 'string')
    .map(e => ({ label: typeof e.label === 'string' ? e.label : String(e.url), url: String(e.url) }))))

function flattenParts(m: any): any[] {
  const out: any[] = []
  for (const sub of Array.isArray(m?.subModules) ? m.subModules : []) out.push(sub, ...flattenParts(sub))
  return out
}

const editorDir = (editorBinary: string, deps: InstallDeps): string => {
  const p = pathFor(deps.platform)
  return deps.platform === 'win32'
    ? p.dirname(p.dirname(editorBinary))
    : p.dirname(p.dirname(p.dirname(p.dirname(editorBinary))))
}

// The Hub keeps modules.json beside an installed editor; absent or unreadable means "unknown" -> false.
function installedModuleIds(editorBinary: string, deps: InstallDeps): Set<string> {
  const p = pathFor(deps.platform)
  const dir = editorDir(editorBinary, deps)
  const ids = new Set<string>()
  try {
    const list = JSON.parse(deps.readFile(p.join(dir, 'modules.json')))
    for (const entry of Array.isArray(list) ? list : []) {
      // A stale entry (selected false or absent) is not an install.
      if (typeof entry?.id === 'string' && entry.selected === true) ids.add(entry.id)
    }
  } catch { /* Unknown. */ }
  return ids
}

function mapRelease(raw: any, deps: InstallDeps, platform: string, arch: string): InstallRelease | null {
  const downloads: any[] = Array.isArray(raw?.downloads) ? raw.downloads : []
  const dl = downloads.find(d => d?.platform === platform && d?.architecture === arch) ?? downloads[0]
  if (!dl || typeof raw?.version !== 'string' || !VERSION_RE.test(raw.version) ||
      typeof raw.shortRevision !== 'string' || !CHANGESET_RE.test(raw.shortRevision)) return null
  const downloadBytes = bytes(dl.downloadSize)
  const installedBytes = bytes(dl.installedSize)
  if (downloadBytes === null || installedBytes === null) return null
  const editor = deps.editors().find(e => e.version === raw.version)
  const have = editor ? installedModuleIds(editor.path, deps) : new Set<string>()
  const modules: InstallModule[] = []
  for (const m of Array.isArray(dl.modules) ? dl.modules : []) {
    if (m?.hidden !== false || typeof m?.id !== 'string' || !MODULE_ID.test(m.id)) continue
    const parts = flattenParts(m)
    const download = [m, ...parts].map(x => bytes(x.downloadSize))
    const disk = [m, ...parts].map(x => bytes(x.installedSize))
    if (download.includes(null) || disk.includes(null)) continue
    modules.push({
      id: m.id,
      name: String(m.name ?? m.id),
      category: String(m.category ?? ''),
      downloadBytes: download.reduce((a, b) => a! + b!, 0)!,
      installedBytes: disk.reduce((a, b) => a! + b!, 0)!,
      preSelected: m.preSelected === true,
      eulas: collectEulas([m, ...parts]),
      parts: parts.map(x => ({ id: String(x.id), name: String(x.name ?? x.id), downloadBytes: bytes(x.downloadSize)! })),
      installed: have.has(m.id),
    })
  }
  return {
    version: raw.version,
    changeset: raw.shortRevision,
    stream: raw.stream === 'SUPPORTED' ? 'SUPPORTED' : 'LTS',
    releaseDate: String(raw.releaseDate ?? ''),
    recommended: raw.recommended === true,
    installed: !!editor,
    downloadBytes,
    installedBytes,
    modules,
  }
}

class CatalogError extends Error {
  constructor(readonly code: 'offline' | 'catalogFailed') { super(code) }
}

async function getReleases(url: string, deps: InstallDeps): Promise<any[]> {
  let res: Awaited<ReturnType<InstallDeps['fetch']>>
  try {
    res = await deps.fetch(url, { signal: AbortSignal.timeout(FETCH_TIMEOUT_MS) })
  } catch {
    throw new CatalogError('offline')
  }
  if (!res.ok) throw new CatalogError('catalogFailed')
  try {
    const body = await res.json()
    if (!Array.isArray(body?.results)) throw new CatalogError('catalogFailed')
    return body.results
  } catch (err: any) {
    // A timeout while the body streams is still a network problem.
    throw new CatalogError(err?.name === 'TimeoutError' || err?.name === 'AbortError' ? 'offline' : 'catalogFailed')
  }
}

/** `requested` (a project's version) is fetched by exact lookup when it is not among the newest lists. */
export async function loadCatalog(deps: InstallDeps, requested?: string): Promise<CatalogResult> {
  const cli = cliState(deps)
  const platform = deps.platform === 'win32' ? 'WINDOWS' : deps.platform === 'darwin' ? 'MAC_OS' : null
  if (!platform) return { ok: false, cli, code: 'catalogFailed' }
  const arch = deps.arch === 'arm64' ? 'ARM64' : 'X86_64'
  const target = `platform=${platform}&architecture=${arch}`
  try {
    const [lts, supported] = await Promise.all(['LTS', 'SUPPORTED'].map(stream =>
      getReleases(`${RELEASE_API}?limit=10&order=RELEASE_DATE_DESC&stream=${stream}&${target}`, deps)))
    const releases = new Map<string, InstallRelease>()
    for (const raw of [...lts, ...supported]) {
      const release = mapRelease(raw, deps, platform, arch)
      if (release && !releases.has(release.version)) releases.set(release.version, release)
    }
    // "Add modules" must work for an installed editor that fell out of the newest-10 lists.
    const want = typeof requested === 'string' && VERSION_RE.test(requested) ? requested : undefined
    const missing = deps.editors().map(e => e.version).filter(v => !releases.has(v)).slice(0, 5)
    if (want && !releases.has(want) && !missing.includes(want)) missing.push(want)
    const extra = await Promise.all(missing.map(v =>
      getReleases(`${RELEASE_API}?version=${encodeURIComponent(v)}&${target}`, deps).catch(() => [] as any[])))
    for (const raw of extra.flat()) {
      const release = mapRelease(raw, deps, platform, arch)
      if (release && !releases.has(release.version)) releases.set(release.version, release)
    }
    const list = [...releases.values()].sort((a, b) => Date.parse(b.releaseDate) - Date.parse(a.releaseDate))
    lastCatalog = list
    return { ok: true, cli, releases: list, ...(want ? { requestedFound: releases.has(want) } : {}) }
  } catch (err) {
    if (err instanceof CatalogError) return { ok: false, cli, code: err.code }
    throw err
  }
}

// --- plan ---

type Selection =
  | { ok: true; release: InstallRelease; modules: InstallModule[]; editorInstalled: boolean }
  | { ok: false; code: 'invalidArguments' | 'unknownVersion' | 'unknownModule' }

function resolveSelection(req: InstallRequest, deps: InstallDeps): Selection {
  if (!req || typeof req.version !== 'string' || !Array.isArray(req.modules) ||
      !req.modules.every(id => typeof id === 'string' && MODULE_ID.test(id))) {
    return { ok: false, code: 'invalidArguments' }
  }
  const release = lastCatalog?.find(r => r.version === req.version)
  if (!release) return { ok: false, code: 'unknownVersion' }
  const ids = [...new Set(req.modules as string[])]
  const modules: InstallModule[] = []
  for (const id of ids) {
    const found = release.modules.find(m => m.id === id)
    if (!found) return { ok: false, code: 'unknownModule' }
    modules.push(found)
  }
  const editorInstalled = deps.editors().some(e => e.version === release.version)
  // install-modules with nothing to add has no meaning. Installed modules stay: ticking one reinstalls it.
  if (editorInstalled && modules.length === 0) return { ok: false, code: 'invalidArguments' }
  return { ok: true, release, modules, editorInstalled }
}

function parseJsonLine(line: string): any | null {
  const trimmed = line.trim()
  if (!trimmed.startsWith('{')) return null
  try { return JSON.parse(trimmed) } catch { return null }
}

// Runs a short CLI query and returns its stdout, or null when it fails or hangs.
function captureCli(cli: string, args: string[], deps: InstallDeps): Promise<string | null> {
  return new Promise(resolve => {
    let child: InstallChild
    try {
      child = deps.spawn(cli, args, { shell: false, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'], detached: false })
    } catch {
      resolve(null)
      return
    }
    let out = ''
    const decoder = new StringDecoder('utf8')
    const startedAt = deps.now()
    let done = false
    const finish = (value: string | null) => {
      if (done) return
      done = true
      deps.clearInterval(timer)
      resolve(value)
    }
    const timer = deps.setInterval(() => {
      if (deps.now() - startedAt < 10_000) return
      try { child.kill() } catch { /* Already gone. */ }
      finish(null)
    }, 1000)
    child.stdout?.on('data', chunk => { out += decoder.write(Buffer.from(chunk)) })
    child.once('error', () => finish(null))
    child.once('close', (code: number | null) => finish(code === 0 ? out : null))
  })
}

async function resolveInstallRoot(cli: string, deps: InstallDeps): Promise<string> {
  const out = await captureCli(cli, [...CLI_ARGS, 'install-path'], deps)
  for (const line of (out ?? '').split(/\r?\n/)) {
    const msg = parseJsonLine(line)
    if (msg?.type === 'result' && msg.success === true && typeof msg.data?.path === 'string' && msg.data.path) return msg.data.path
  }
  return editorRoots(deps)[0] ?? ''
}

async function freeBytesAt(root: string, deps: InstallDeps): Promise<number | null> {
  const p = pathFor(deps.platform)
  let dir = root
  while (!deps.exists(dir)) {
    const parent = p.dirname(dir)
    if (parent === dir) return null
    dir = parent
  }
  try {
    const st = await deps.statfs(dir)
    const free = Number(st.bavail) * Number(st.bsize)
    return Number.isFinite(free) ? free : null
  } catch {
    return null
  }
}

async function buildPlan(sel: Extract<Selection, { ok: true }>, cli: string, deps: InstallDeps) {
  // Modules go into the editor's own folder, which may sit on another drive than the CLI's install path.
  const editor = sel.editorInstalled ? deps.editors().find(e => e.version === sel.release.version) : undefined
  const installRoot = editor ? editorDir(editor.path, deps) : await resolveInstallRoot(cli, deps)
  const freeBytes = await freeBytesAt(installRoot, deps)
  const editorBytes = sel.editorInstalled ? { d: 0, i: 0 } : { d: sel.release.downloadBytes, i: sel.release.installedBytes }
  const downloadBytes = editorBytes.d + sel.modules.reduce((s, m) => s + m.downloadBytes, 0)
  const installedBytes = editorBytes.i + sel.modules.reduce((s, m) => s + m.installedBytes, 0)
  return {
    installRoot,
    downloadBytes,
    installedBytes,
    freeBytes,
    // Both the archive and the unpacked files sit on disk at once.
    enough: freeBytes === null || freeBytes >= downloadBytes + installedBytes,
    eulas: selectionEulas(sel.modules),
  }
}

const selectionEulas = (modules: InstallModule[]): InstallEula[] => dedupeEulas(modules.flatMap(m => m.eulas))

export async function planInstall(req: InstallRequest, deps: InstallDeps): Promise<PlanResult> {
  const cli = findUnityCli(deps)
  if (!cli) return { ok: false, code: 'cliMissing' }
  const sel = resolveSelection(req, deps)
  if ('code' in sel) return { ok: false, code: sel.code }
  return { ok: true, ...(await buildPlan(sel, cli, deps)) }
}

// --- start / cancel ---

interface ActiveInstall {
  child: InstallChild
  phase: InstallPhase
  cancelRequested: boolean
}

let busy = false
let active: ActiveInstall | null = null
// A start that has not spawned yet (it still awaits the plan or the install path); a cancel then only sets the flag.
let pending: { cancelRequested: boolean } | null = null

export function cancelInstall(deps: InstallDeps): { cancelled: boolean; reason?: 'installing' | 'idle' } {
  if (!active) {
    if (!pending) return { cancelled: false, reason: 'idle' }
    pending.cancelRequested = true
    return { cancelled: true }
  }
  // Measured: killing the CLI in its install phase does not stop the install.
  if (active.phase === 'install') return { cancelled: false, reason: 'installing' }
  active.cancelRequested = true
  const pid = active.child.pid
  if (pid === undefined) active.child.kill()
  else deps.killTree(pid)
  return { cancelled: true }
}

export async function startInstall(
  req: InstallRequest,
  onProgress: (progress: InstallProgress) => void,
  deps: InstallDeps,
): Promise<InstallResult> {
  const fail = (code: InstallErrorCode, message?: string, completed?: string[]): InstallResult =>
    ({ ok: false, code, ...(message ? { message } : {}), ...(completed ? { completed } : {}) })
  if (busy) return fail('busy')
  busy = true
  const mine = { cancelRequested: false }
  pending = mine
  try {
    const cli = findUnityCli(deps)
    if (!cli) return fail('cliMissing')
    const sel = resolveSelection(req, deps)
    if ('code' in sel) return fail(sel.code)
    const eulas = selectionEulas(sel.modules)
    const needsEula = eulas.length > 0
    if (needsEula && req.acceptedEulas !== true) return fail('eulaNotAccepted')
    const plan = await buildPlan(sel, cli, deps)
    // The last await before the spawn: an accepted cancel decides the outcome, and everything after this runs
    // synchronously up to the spawn, so nothing can slip in later.
    if (mine.cancelRequested) return fail('cancelled')
    if (!plan.enough) return fail('notEnoughSpace')

    const version = sel.release.version
    const args = [...CLI_ARGS]
    if (sel.editorInstalled) args.push('install-modules', '--editor-version', version)
    else args.push('install', version, '--changeset', sel.release.changeset, '--yes')
    for (const m of sel.modules) args.push('-m', m.id)
    if (needsEula) args.push('--accept-eula')
    return await runInstall(cli, args, onProgress, deps, fail)
  } finally {
    busy = false
    active = null
    pending = null
  }
}

function runInstall(
  cli: string,
  args: string[],
  onProgress: (progress: InstallProgress) => void,
  deps: InstallDeps,
  fail: (code: InstallErrorCode, message?: string, completed?: string[]) => InstallResult,
): Promise<InstallResult> {
  return new Promise(resolve => {
    let child: InstallChild
    try {
      child = deps.spawn(cli, args, {
        shell: false,
        windowsHide: true,
        stdio: ['ignore', 'pipe', 'pipe'],
        detached: deps.platform !== 'win32',
      })
    } catch (err: any) {
      resolve(fail('cliFailed', String(err?.message || err)))
      return
    }
    const state: ActiveInstall = { child, phase: 'starting', cancelRequested: false }
    active = state
    const startedAt = deps.now()
    let lastLineAt = startedAt
    let pct: number | null = null
    let message = ''
    let stalled = false
    let result: any = null
    let stderrTail = ''
    let buffer = ''
    let settled = false
    const outDecoder = new StringDecoder('utf8')
    const errDecoder = new StringDecoder('utf8')

    const emit = (phase: InstallPhase = state.phase) => onProgress({
      phase,
      pct,
      message,
      stalled,
      retrying: /retrying/i.test(message),
      elapsedMs: deps.now() - startedAt,
    })
    emit()

    const timer = deps.setInterval(() => {
      if (!stalled && state.phase === 'download' && deps.now() - lastLineAt >= STALL_MS) {
        stalled = true
        emit()
      }
    }, 1000)

    const handleLine = (line: string) => {
      if (!line.trim()) return
      lastLineAt = deps.now()
      const wasStalled = stalled
      stalled = false
      const msg = parseJsonLine(line)
      if (msg?.type === 'progress') {
        if (msg.phase === 'download' || msg.phase === 'install') state.phase = msg.phase
        else if (state.phase === 'starting') state.phase = 'download'
        pct = typeof msg.pct === 'number' ? msg.pct : null
        message = typeof msg.msg === 'string' ? msg.msg : ''
        emit()
      } else if (msg?.type === 'result') {
        result = msg
        if (wasStalled) emit()
      } else if (wasStalled) {
        emit()
      }
    }

    child.stdout?.on('data', chunk => {
      buffer += outDecoder.write(Buffer.from(chunk))
      let nl: number
      while ((nl = buffer.indexOf('\n')) >= 0) {
        handleLine(buffer.slice(0, nl))
        buffer = buffer.slice(nl + 1)
      }
    })
    child.stderr?.on('data', chunk => { stderrTail = (stderrTail + errDecoder.write(Buffer.from(chunk))).slice(-STDERR_KEEP) })

    const finish = (outcome: InstallResult) => {
      if (settled) return
      settled = true
      deps.clearInterval(timer)
      stalled = false
      emit(outcome.ok ? 'done' : 'failed')
      resolve(outcome)
    }
    child.once('error', (err: Error) => {
      if (settled) return
      message = String(err?.message || err)
      finish(fail('cliFailed', message))
    })
    // 'close', not 'exit': the last stdout lines (the result) may still be in the pipe at 'exit'.
    child.once('close', (code: number | null) => {
      if (settled) return
      handleLine(buffer)
      buffer = ''
      const completed = Array.isArray(result?.data?.completedUids) ? result.data.completedUids.map(String) : undefined
      // A success line wins over a cancel that came after it (the install did finish), as in unity-create.ts.
      if (result?.success === true) return finish({ ok: true, completed: completed ?? [] })
      if (state.cancelRequested) return finish(fail('cancelled'))
      if (result?.success === false) {
        const first = result.errors?.[0]
        if (first?.code === 'ELEVATION_REQUIRED') return finish(fail('elevationRefused', first.message, completed))
        message = typeof first?.message === 'string' ? first.message : message
        return finish(fail('cliFailed', typeof first?.message === 'string' ? first.message : undefined, completed))
      }
      const tail = stderrTail.trim()
      finish(fail('exitCode', tail || `Unity CLI exited with code ${code}.`))
    })
  })
}
