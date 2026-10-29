import { listEditors, EditorDeps } from './unity-editors'

export interface LaunchDeps {
  editors: EditorDeps
  isTrusted: (p: string) => boolean
  isProject: (dir: string) => boolean
  readVersion: (dir: string) => string | null
  spawn: (command: string, args: string[], options: { detached: boolean; stdio: 'ignore' }) =>
    {
      unref: () => void
      on: (event: 'error', listener: (err: Error) => void) => unknown
      once: (event: 'spawn', listener: () => void) => unknown
    }
}

export async function openProject(
  opts: { path: string; version?: string },
  deps: LaunchDeps,
): Promise<{ opened: boolean; error?: string }> {
  if (!deps.isTrusted(opts.path)) return { opened: false, error: 'Project folder is not trusted.' }
  if (!deps.isProject(opts.path)) return { opened: false, error: 'Not a Unity project folder.' }
  const version = opts.version || deps.readVersion(opts.path)
  if (!version) return { opened: false, error: 'Unity version of the project is unknown.' }
  const editor = listEditors(deps.editors).editors.find(e => e.version === version)
  if (!editor) return { opened: false, error: `Unity ${version} is not installed.` }
  try {
    const child = deps.spawn(editor.path, ['-projectPath', opts.path], { detached: true, stdio: 'ignore' })
    return await new Promise(resolve => {
      // EACCES/ENOENT arrive as an async 'error'; the listener stays so a late one cannot crash main.
      child.on('error', (err: Error) => resolve({ opened: false, error: `Could not start Unity: ${err?.message || err}` }))
      child.once('spawn', () => {
        child.unref()
        resolve({ opened: true })
      })
    })
  } catch (err: any) {
    return { opened: false, error: String(err?.message || err) }
  }
}
