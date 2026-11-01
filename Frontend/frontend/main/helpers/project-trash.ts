import path from 'path'
import { mt } from './main-i18n'

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
  const bin = mt(platform === 'win32' ? 'recycleBin' : 'trash')
  return {
    message: mt('trashMessage', { name: flavor(platform).basename(real) }),
    detail: mt('trashDetail', { path: real, bin }),
    go: mt('trashGo', { bin }),
    cancel: mt('trashCancel'),
  }
}

/**
 * The -projectPath values of a command line, as candidate lists (longest first), never trimmed: folder names may
 * begin, end or be full of whitespace. The flag counts only as a whole token followed by whitespace; a token that
 * begins with a quote runs to its closing quote, so flag-like text inside another option's value is no flag. A
 * quoted value is exactly the text between the quotes (one candidate). An unquoted value runs from its start up to
 * each whitespace character and up to the line end (CR/LF dropped).
 */
const projectPathArgs = (command: string): string[][] =>
  [...command.matchAll(/"[^"]*"\S*|'[^']*'\S*|\S+/g)]
    .filter(t => t[0].toLowerCase() === '-projectpath' && /\s/.test(command[(t.index ?? 0) + t[0].length] ?? ''))
    .map(t => {
      const m = command.slice((t.index ?? 0) + t[0].length).match(/^\s+(?:"([^"]*)"|'([^']*)'|(?=([^\r\n]*)))/)
      const quoted = m?.[1] ?? m?.[2]
      if (quoted !== undefined) return [quoted].filter(Boolean)
      const rest = m?.[3] ?? ''
      const cuts = [...[...rest.matchAll(/\s/g)].map(c => c.index ?? 0), rest.length]
      return cuts.map(i => rest.slice(0, i)).filter(Boolean).reverse()
    }).filter(list => list.length > 0)

/**
 * A Unity command line that opens this project or one nested in it: a -projectPath resolves to the folder or to a
 * folder inside it (by canonical path or dev+ino, so any alias counts), or cannot be compared (relative). Of an
 * unquoted value's candidates the longest one that resolves is the value. The target path appearing elsewhere in the text does not count.
 */
const unityNames = (
  command: string, isAbsolute: (s: string) => boolean, resolves: (arg: string) => boolean,
  inTarget: (arg: string) => boolean,
): boolean => {
  if (!/unity/i.test(command)) return false
  return projectPathArgs(command).some(list => {
    const arg = list.find(a => !isAbsolute(a) || resolves(a))
    return arg !== undefined && (!isAbsolute(arg) || inTarget(arg))
  })
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
  const inTarget = (other: string) => within(target, norm(resolved(deps, other))) || above(other)
  if (deps.openWorkspaces.some(inTarget)) return { error: 'open-workspace' }

  if (!deps.isDirectory(p.join(real, 'Assets')) || !deps.isDirectory(p.join(real, 'ProjectSettings'))) {
    return { error: 'not-a-unity-project' }
  }

  const lock = p.join(real, 'Temp', 'UnityLockfile')
  if (deps.platform === 'win32') {
    if (deps.probeLock(lock) === 'busy') return { error: 'unity-open' }
  } else if (deps.exists(lock)) {
    const commands = await deps.processCommands().catch(() => null)
    if (!commands) return { error: 'unity-open' }
    const resolvable = (arg: string) => { try { deps.realpath(arg); return true } catch { return false } }
    if (commands.some(c => unityNames(c, p.isAbsolute, resolvable, inTarget))) return { error: 'unity-open' }
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
    // The checks above awaited; the folder is looked at once more right before the move.
    const last = deps.identity?.(checked.real)
    if (!last || last !== checked.id) return { error: 'not-found' }
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
