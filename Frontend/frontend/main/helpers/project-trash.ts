import path from 'path'

export type TrashError =
  | 'invalid-path'
  | 'not-found'
  | 'protected'
  | 'open-workspace'
  | 'not-a-unity-project'
  | 'unity-open'
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
  /** The workspace open in the app; null when the app has none. */
  openWorkspace: string | null
  probeLock: (file: string) => LockProbe
  /** Command lines of the running processes; null when they cannot be listed. */
  processCommands: () => Promise<string[] | null>
  trashItem: (p: string) => Promise<void>
}

const flavor = (platform: string) => (platform === 'win32' ? path.win32 : path.posix)

const resolved = (deps: TrashDeps, p: string): string => {
  try { return deps.realpath(p) } catch { return p }
}

/**
 * Every check runs on the resolved path, and the folder that passes is the one trashed, so a link
 * to a protected folder is judged as that folder. Anything undecidable refuses.
 */
export async function checkTrashable(dir: unknown, deps: TrashDeps): Promise<{ error: TrashError } | { real: string }> {
  const p = flavor(deps.platform)
  if (typeof dir !== 'string' || !dir || !p.isAbsolute(dir)) return { error: 'invalid-path' }
  let real: string
  try {
    real = deps.realpath(dir)
  } catch {
    return { error: 'not-found' }
  }
  if (!deps.isDirectory(real)) return { error: 'not-found' }

  const fold = (s: string) => (deps.platform === 'win32' || deps.platform === 'darwin' ? s.toLowerCase() : s)
  const norm = (s: string) => fold(p.normalize(s).replace(/[\\/]+$/, '') || p.normalize(s))
  const target = norm(real)
  const within = (outer: string, inner: string) => inner === outer || inner.startsWith(outer + p.sep)
  if (p.dirname(real) === real) return { error: 'protected' }
  const guard = (list: string[]) => list.map(d => norm(resolved(deps, d)))
  if (guard(deps.protectedDirs).some(d => within(target, d))) return { error: 'protected' }
  if (guard(deps.appDirs).some(d => within(target, d) || within(d, target))) return { error: 'protected' }
  if (deps.openWorkspace && within(target, norm(resolved(deps, deps.openWorkspace)))) return { error: 'open-workspace' }

  if (!deps.isDirectory(p.join(real, 'Assets')) || !deps.isDirectory(p.join(real, 'ProjectSettings'))) {
    return { error: 'not-a-unity-project' }
  }

  const lock = p.join(real, 'Temp', 'UnityLockfile')
  if (deps.platform === 'win32') {
    if (deps.probeLock(lock) === 'busy') return { error: 'unity-open' }
  } else if (deps.exists(lock)) {
    const commands = await deps.processCommands().catch(() => null)
    if (!commands) return { error: 'unity-open' }
    if (commands.some(c => /unity/i.test(c) && (c.includes(real) || c.includes(dir)))) return { error: 'unity-open' }
  }
  return { real }
}

export async function trashProject(dir: unknown, deps: TrashDeps): Promise<TrashResult> {
  const checked = await checkTrashable(dir, deps)
  if ('error' in checked) return checked
  try {
    await deps.trashItem(checked.real)
    return { ok: true }
  } catch {
    return { error: 'trash-failed' }
  }
}
