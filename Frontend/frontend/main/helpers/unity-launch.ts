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
      once: (event: 'spawn' | 'exit', listener: (code?: number | null) => void) => unknown
    }
}

// <...>/Unity.app/Contents/MacOS/Unity -> <...>/Unity.app, or null when the path is not inside a bundle.
export function bundleOf(editorPath: string): string | null {
  return /^(.+\.app)\/Contents\/MacOS\/[^/]+$/.exec(editorPath)?.[1] ?? null
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
    // macOS: a direct child stays in Gamachine's responsibility group, so Dock keeps listing Gamachine after quit; open hands it to LaunchServices.
    const bundle = deps.editors.platform === 'darwin' ? bundleOf(editor.path) : null
    const child = bundle
      ? deps.spawn('/usr/bin/open', ['-n', '-a', bundle, '--args', '-projectPath', opts.path], { detached: true, stdio: 'ignore' })
      : deps.spawn(editor.path, ['-projectPath', opts.path], { detached: true, stdio: 'ignore' })
    return await new Promise(resolve => {
      // EACCES/ENOENT arrive as an async 'error'; the listener stays so a late one cannot crash main.
      child.on('error', (err: Error) => resolve({ opened: false, error: `Could not start Unity: ${err?.message || err}` }))
      if (bundle) {
        // open exits right after handing the app to LaunchServices; non-zero means it could not.
        child.once('spawn', () => child.unref())
        child.once('exit', code => resolve(code === 0
          ? { opened: true }
          : { opened: false, error: `Could not start Unity: open exited with code ${code}` }))
        return
      }
      child.once('spawn', () => {
        child.unref()
        resolve({ opened: true })
      })
    })
  } catch (err: any) {
    return { opened: false, error: String(err?.message || err) }
  }
}
