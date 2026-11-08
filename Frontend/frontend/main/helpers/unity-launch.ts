import { listEditors, EditorDeps } from './unity-editors'
import type { HubFailure } from './unity-create'
import path from 'path'

export interface LaunchDeps {
  editors: EditorDeps
  isTrusted: (p: string) => boolean
  isProject: (dir: string) => boolean
  readVersion: (dir: string) => string | null
  // Optional: without them there is no memory of earlier launches and no running-editor check.
  isEditorOpen?: (dir: string) => boolean
  now?: () => number
  // Optional: without them the editor is spawned without making sure Unity Hub is up.
  isHubRunning?: () => boolean
  startHub?: () => boolean | Promise<boolean>
  isLicensingReady?: () => boolean
  sleep?: (ms: number) => Promise<void>
  spawn: (command: string, args: string[], options: { detached: boolean; stdio: 'ignore' }) =>
    {
      unref: () => void
      on: (event: 'error', listener: (err: Error) => void) => unknown
      once: (event: 'spawn' | 'exit', listener: (code?: number | null) => void) => unknown
    }
}

// <...>/Unity.app/Contents/MacOS/Unity -> <...>/Unity.app, or null when the path is not inside a bundle.
export function bundleOf(editorPath: string): string | null {
  return /^(.+\.app)\/Contents\/MacOS\/[^/]+$/.exec(editorPath)?.[1] ?? null
}

export const LAUNCH_MEMORY_MS = 30_000

const HUB_POLL_MS = 500
const HUB_START_WAIT_MS = 30_000
const HUB_RUNNING_WAIT_MS = 5_000

// Without a running Hub the editor starts its own licensing client and shows "No valid Unity Editor license found".
// Polling is counted in sleeps, not read from a clock, so a fake sleep needs no fake time.
async function ensureHub(deps: LaunchDeps): Promise<boolean> {
  const { isHubRunning, startHub, isLicensingReady } = deps
  if (!isHubRunning || !startHub || !isLicensingReady) return true
  const sleep = deps.sleep ?? (ms => new Promise<void>(resolve => setTimeout(resolve, ms)))
  const wasRunning = isHubRunning()
  if (!wasRunning && !await startHub()) return false
  const polls = (wasRunning ? HUB_RUNNING_WAIT_MS : HUB_START_WAIT_MS) / HUB_POLL_MS
  for (let i = 0; i <= polls; i++) {
    if (isLicensingReady()) return true
    if (i < polls) await sleep(HUB_POLL_MS)
  }
  return wasRunning
}

const HUB_FLAGS = ['-useHub', '-hubIPC', '-cloudEnvironment', 'production']

// hubNotReady lives here, not in HubErrorCode (unity-create.ts), because only launching needs Unity Hub up.
type LaunchFailure = Omit<HubFailure, 'code'> & { code: HubFailure['code'] | 'hubNotReady' }
type LaunchResult = { opened: true; alreadyOpen?: true; error?: never; code?: never; params?: never } | ({ opened: false } & LaunchFailure)

// project -> launch time; null while that launch still awaits its 'spawn'/'exit'. Unity takes a while
// before it writes Library/EditorInstance.json, so a second press in that window must not spawn again.
const launches = new Map<string, number | null>()

export function _resetLaunchMemory(): void {
  launches.clear()
}

function launchKey(dir: string, platform: string): string {
  const resolved = (platform === 'win32' ? path.win32 : path).resolve(dir)
  return platform === 'win32' || platform === 'darwin' ? resolved.toLowerCase() : resolved
}

export async function openProject(opts: { path: string; version?: string }, deps: LaunchDeps): Promise<LaunchResult> {
  if (!deps.isTrusted(opts.path)) return { opened: false, error: 'Project folder is not trusted.', code: 'notTrusted' }
  if (!deps.isProject(opts.path)) return { opened: false, error: 'Not a Unity project folder.', code: 'notProject' }
  const key = launchKey(opts.path, deps.editors.platform)
  const remembered = launches.get(key)
  const recent = remembered === null || (remembered !== undefined && deps.now !== undefined && deps.now() - remembered < LAUNCH_MEMORY_MS)
  if (recent || deps.isEditorOpen?.(opts.path)) return { opened: true, alreadyOpen: true }
  launches.set(key, null)
  const result = await launchEditor(opts, deps)
  if (result.opened && deps.now) launches.set(key, deps.now())
  else launches.delete(key)
  return result
}

async function launchEditor(opts: { path: string; version?: string }, deps: LaunchDeps): Promise<LaunchResult> {
  const version = opts.version || deps.readVersion(opts.path)
  if (!version) return { opened: false, error: 'Unity version of the project is unknown.', code: 'versionUnknown' }
  const editor = listEditors(deps.editors).editors.find(e => e.version === version)
  if (!editor) return { opened: false, error: `Unity ${version} is not installed.`, code: 'editorMissing', params: { version } }
  if (!await ensureHub(deps)) {
    return { opened: false, error: 'Unity Hub could not be started or its license is not ready.', code: 'hubNotReady' }
  }
  try {
    // macOS: a direct child stays in Gamachine's responsibility group, so Dock keeps listing Gamachine after quit; open hands it to LaunchServices.
    const bundle = deps.editors.platform === 'darwin' ? bundleOf(editor.path) : null
    const child = bundle
      ? deps.spawn('/usr/bin/open', ['-n', '-a', bundle, '--args', '-projectPath', opts.path, ...HUB_FLAGS], { detached: true, stdio: 'ignore' })
      : deps.spawn(editor.path, ['-projectPath', opts.path, ...HUB_FLAGS], { detached: true, stdio: 'ignore' })
    return await new Promise(resolve => {
      // EACCES/ENOENT arrive as an async 'error'; the listener stays so a late one cannot crash main.
      child.on('error', (err: Error) => {
        const detail = String(err?.message || err)
        resolve({ opened: false, error: `Could not start Unity: ${detail}`, code: 'spawnFailed', params: { detail } })
      })
      if (bundle) {
        // open exits right after handing the app to LaunchServices; non-zero means it could not.
        child.once('spawn', () => child.unref())
        child.once('exit', code => resolve(code === 0
          ? { opened: true }
          : { opened: false, error: `Could not start Unity: open exited with code ${code}`, code: 'openExited', params: { code: String(code) } }))
        return
      }
      child.once('spawn', () => {
        child.unref()
        resolve({ opened: true })
      })
    })
  } catch (err: any) {
    const detail = String(err?.message || err)
    return { opened: false, error: detail, code: 'spawnFailed', params: { detail } }
  }
}
