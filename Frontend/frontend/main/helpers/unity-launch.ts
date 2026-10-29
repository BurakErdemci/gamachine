import { listEditors, EditorDeps } from './unity-editors'

export interface LaunchDeps {
  editors: EditorDeps
  isTrusted: (p: string) => boolean
  readVersion: (dir: string) => string | null
  spawn: (command: string, args: string[], options: { detached: boolean; stdio: 'ignore' }) =>
    { unref: () => void; on: (event: 'error', listener: () => void) => unknown }
}

export function openProject(
  opts: { path: string; version?: string },
  deps: LaunchDeps,
): { opened: boolean; error?: string } {
  if (!deps.isTrusted(opts.path)) return { opened: false, error: 'Project folder is not trusted.' }
  const version = opts.version || deps.readVersion(opts.path)
  if (!version) return { opened: false, error: 'Unity version of the project is unknown.' }
  const editor = listEditors(deps.editors).editors.find(e => e.version === version)
  if (!editor) return { opened: false, error: `Unity ${version} is not installed.` }
  try {
    const child = deps.spawn(editor.path, ['-projectPath', opts.path], { detached: true, stdio: 'ignore' })
    child.on('error', () => { /* A late spawn failure must not crash the main process. */ })
    child.unref()
    return { opened: true }
  } catch (err: any) {
    return { opened: false, error: String(err?.message || err) }
  }
}
