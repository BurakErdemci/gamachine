import path from 'path'

export type TrashError =
  | 'invalid-path'
  | 'not-found'
  | 'protected'
  | 'open-workspace'
  | 'not-a-unity-project'
  | 'unity-open'
  | 'untrusted'
  | 'cancelled'
  | 'busy'
  | 'trash-failed'

export type TrashResult = { ok: true } | { error: TrashError }

/** What opening Temp/UnityLockfile for read-write did (Windows only). */
export type LockProbe = 'absent' | 'free' | 'busy'

export interface TrashDeps {
  platform: string
  /** Throws when the path does not exist. */
  realpath: (p: string) => string
  isDirectory: (p: string) => boolean
  exists: (p: string) => boolean
  /** Home, Desktop, Documents, Downloads: neither they nor anything above them may go. */
  protectedDirs: string[]
  /** userData and the install dirs: neither they, anything above them nor anything inside may go. */
  appDirs: string[]
  /** Workspaces the app has open (the one the renderer names); empty when none. */
  openWorkspaces: string[]
  probeLock: (file: string) => LockProbe
  /** Command lines of the running processes; null when they cannot be listed. */
  processCommands: () => Promise<string[] | null>
  /** dev+ino of a path; null when the platform cannot say. Catches aliases that realpath leaves apart. */
  identity?: (p: string) => string | null
  /** Asks the user, in the main process, about the resolved folder; true only for an explicit yes. */
  confirm: (real: string) => Promise<boolean>
  trashItem: (p: string) => Promise<void>
}

const flavor = (platform: string) => (platform === 'win32' ? path.win32 : path.posix)

/** The text of the native confirmation; the default and cancel button is "Vazgeç". */
export function trashPrompt(real: string, platform: string) {
  const bin = platform === 'win32' ? 'Geri Dönüşüm Kutusu' : 'Çöp Kutusu'
  return {
    message: `"${flavor(platform).basename(real)}" projesinin klasörü taşınacak`,
    detail: `${real}\n\n${bin}'na taşınacak. İstersen ${bin} içinden geri yükleyebilirsin.`,
    go: `${bin}'na taşı`,
    cancel: 'Vazgeç',
  }
}

/** The -projectPath values of a command line, quoted or not (an unquoted one runs to the next flag). */
const projectPathArgs = (command: string): string[] =>
  [...command.matchAll(/-projectpath\s+(?:"([^"]*)"|'([^']*)'|(.*?)(?=\s-[A-Za-z]|$))/gi)]
    .map(m => (m[1] ?? m[2] ?? m[3] ?? '').trim()).filter(Boolean)

/**
 * A Unity command line that opens this project: it names its path, or its -projectPath resolves to the same
 * folder (same canonical path or same dev+ino, so any alias counts), or the -projectPath cannot be compared (relative).
 */
const unityNames = (
  command: string, paths: string[], fold: (s: string) => string, isAbsolute: (s: string) => boolean,
  sameFolder: (arg: string) => boolean,
): boolean => {
  if (!/unity/i.test(command)) return false
  if (paths.some(x => fold(command).includes(fold(x)))) return true
  return projectPathArgs(command).some(arg => !isAbsolute(arg) || sameFolder(arg))
}

const resolved = (deps: TrashDeps, p: string): string => {
  try { return deps.realpath(p) } catch { return p }
}

// Forms Windows resolves to something else than they spell: device prefixes, trailing dots or spaces,
// 8.3 short names (when realpath did not expand them) and alternate data streams.
const suspiciousWindowsPath = (s: string): boolean => {
  if (/^[\\/]{2}[?.][\\/]/.test(s)) return true
  if (s.replace(/^[a-zA-Z]:/, '').includes(':')) return true
  return s.split(/[\\/]+/).some(seg => seg !== '.' && seg !== '..' && (/[. ]$/.test(seg) || /~\d+(\.[^.]*)?$/.test(seg)))
}

/**
 * Every check runs on the resolved path, and the folder that passes is the one trashed, so a link
 * to a protected folder is judged as that folder. Anything undecidable refuses.
 */
export async function checkTrashable(dir: unknown, deps: TrashDeps): Promise<{ error: TrashError } | { real: string; id: string | null }> {
  const p = flavor(deps.platform)
  if (typeof dir !== 'string' || !dir || !p.isAbsolute(dir)) return { error: 'invalid-path' }
  if (deps.platform === 'win32' && suspiciousWindowsPath(dir)) return { error: 'invalid-path' }
  let real: string
  try {
    real = deps.realpath(dir)
  } catch {
    return { error: 'not-found' }
  }
  if (!deps.isDirectory(real)) return { error: 'not-found' }
  if (deps.platform === 'win32' && suspiciousWindowsPath(real)) return { error: 'invalid-path' }

  const fold = (s: string) => (deps.platform === 'win32' || deps.platform === 'darwin' ? s.toLowerCase() : s)
  const norm = (s: string) => fold(p.normalize(s).replace(/[\\/]+$/, '') || p.normalize(s))
  const target = norm(real)
  const within = (outer: string, inner: string) => inner === outer || inner.startsWith(outer + p.sep)
  if (p.dirname(real) === real) return { error: 'protected' }
  const guard = (list: string[]) => list.map(d => norm(resolved(deps, d)))
  // dev+ino of a folder and every folder above it.
  const chain = (dir0: string): string[] => {
    const ids: string[] = []
    for (let d = dir0; ; d = p.dirname(d)) {
      const id = deps.identity?.(d)
      if (id) ids.push(id)
      if (p.dirname(d) === d) return ids
    }
  }
  const targetId = deps.identity?.(real) ?? null
  const above = (other: string) => !!targetId && chain(resolved(deps, other)).includes(targetId)
  const inside = (other: string) => { const id = deps.identity?.(resolved(deps, other)); return !!id && chain(real).includes(id) }
  if (guard(deps.protectedDirs).some(d => within(target, d)) || deps.protectedDirs.some(above)) return { error: 'protected' }
  if (guard(deps.appDirs).some(d => within(target, d) || within(d, target)) || deps.appDirs.some(d => above(d) || inside(d))) return { error: 'protected' }
  if (deps.openWorkspaces.some(w => within(target, norm(resolved(deps, w))) || above(w))) return { error: 'open-workspace' }

  if (!deps.isDirectory(p.join(real, 'Assets')) || !deps.isDirectory(p.join(real, 'ProjectSettings'))) {
    return { error: 'not-a-unity-project' }
  }

  const lock = p.join(real, 'Temp', 'UnityLockfile')
  if (deps.platform === 'win32') {
    if (deps.probeLock(lock) === 'busy') return { error: 'unity-open' }
  } else if (deps.exists(lock)) {
    const commands = await deps.processCommands().catch(() => null)
    if (!commands) return { error: 'unity-open' }
    const sameFolder = (arg: string) => {
      const there = resolved(deps, arg)
      const id = deps.identity?.(there)
      return norm(there) === target || (!!id && id === targetId)
    }
    if (commands.some(c => unityNames(c, [real, dir], fold, p.isAbsolute, sameFolder))) return { error: 'unity-open' }
  }
  return { real, id: targetId }
}

// Canonical paths with a trash request under way; a second request for one of them is refused.
const inFlight = new Set<string>()

export async function trashProject(dir: unknown, deps: TrashDeps): Promise<TrashResult> {
  const key = typeof dir === 'string' ? resolved(deps, dir) : null
  const slot = key && (deps.platform === 'win32' || deps.platform === 'darwin' ? key.toLowerCase() : key)
  if (slot && inFlight.has(slot)) return { error: 'busy' }
  if (slot) inFlight.add(slot)
  try {
    const checked = await checkTrashable(dir, deps)
    if ('error' in checked) return checked
    // The same directory must still be there after the box: without an identity to compare, nothing can be told apart.
    if (!checked.id) return { error: 'not-found' }
    if (!(await deps.confirm(checked.real).catch(() => false))) return { error: 'cancelled' }
    // The box stayed open for as long as the user took: judge the folder again before moving it.
    const again = await checkTrashable(dir, deps)
    if ('error' in again) return again
    if (again.real !== checked.real || !again.id || again.id !== checked.id) return { error: 'not-found' }
    try {
      await deps.trashItem(checked.real)
      return { ok: true }
    } catch {
      return { error: 'trash-failed' }
    }
  } finally {
    if (slot) inFlight.delete(slot)
  }
}
