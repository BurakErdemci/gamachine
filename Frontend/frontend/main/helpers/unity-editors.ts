import path from 'path'

export interface EditorDeps {
  exists: (p: string) => boolean
  readdir: (p: string) => string[]
  readFile: (p: string) => string
  platform: NodeJS.Platform
  env: Record<string, string | undefined>
  homedir: string
}

export interface UnityEditor { version: string; path: string }
export interface UnityTemplate { id: string; label: '2d' | '3d' | 'hdrp' | 'other'; file: string }

const pathFor = (platform: NodeJS.Platform) => (platform === 'win32' ? path.win32 : path.posix)

// The Hub keeps a user-chosen extra install root here; "" means the default root only.
function secondaryRoot(deps: EditorDeps): string | null {
  const p = pathFor(deps.platform)
  let file: string | null = null
  if (deps.platform === 'win32' && deps.env.APPDATA) {
    file = p.join(deps.env.APPDATA, 'UnityHub', 'secondaryInstallPath.json')
  } else if (deps.platform === 'darwin') {
    file = p.join(deps.homedir, 'Library', 'Application Support', 'UnityHub', 'secondaryInstallPath.json')
  }
  if (!file) return null
  try {
    if (!deps.exists(file)) return null
    const raw = deps.readFile(file).trim()
    let value: unknown = raw
    try { value = JSON.parse(raw) } catch { /* A bare path is accepted too. */ }
    return typeof value === 'string' && value.trim() ? value.trim() : null
  } catch {
    return null
  }
}

function editorRoots(deps: EditorDeps): string[] {
  const roots: string[] = []
  if (deps.platform === 'win32') {
    roots.push(path.win32.join(deps.env.ProgramFiles || 'C:\\Program Files', 'Unity', 'Hub', 'Editor'))
  } else if (deps.platform === 'darwin') {
    roots.push(path.posix.join(path.posix.sep, 'Applications', 'Unity', 'Hub', 'Editor'))
  } else {
    return roots
  }
  const extra = secondaryRoot(deps)
  if (extra && !roots.includes(extra)) roots.push(extra)
  return roots
}

function editorBinary(deps: EditorDeps, root: string, version: string): string {
  return deps.platform === 'win32'
    ? path.win32.join(root, version, 'Editor', 'Unity.exe')
    : path.posix.join(root, version, 'Unity.app', 'Contents', 'MacOS', 'Unity')
}

// "6000.0.62f1" -> [6000, 0, 62, 'f', 1]; anything unparseable sorts last.
function versionKey(version: string): [number, number, number, string, number] | null {
  const m = /^(\d+)\.(\d+)\.(\d+)([a-z])(\d+)/.exec(version)
  return m ? [Number(m[1]), Number(m[2]), Number(m[3]), m[4], Number(m[5])] : null
}

function newestFirst(a: UnityEditor, b: UnityEditor): number {
  const ka = versionKey(a.version)
  const kb = versionKey(b.version)
  if (!ka || !kb) return ka ? -1 : kb ? 1 : a.version.localeCompare(b.version)
  for (let i = 0; i < 5; i++) {
    if (ka[i] === kb[i]) continue
    return ka[i] < kb[i] ? 1 : -1
  }
  return 0
}

export function listEditors(deps: EditorDeps): { editors: UnityEditor[] } {
  const found = new Map<string, UnityEditor>()
  for (const root of editorRoots(deps)) {
    let entries: string[]
    try {
      entries = deps.readdir(root)
    } catch {
      continue
    }
    for (const version of entries) {
      if (found.has(version)) continue
      const binary = editorBinary(deps, root, version)
      try {
        if (deps.exists(binary)) found.set(version, { version, path: binary })
      } catch { /* An unreadable entry is skipped. */ }
    }
  }
  return { editors: [...found.values()].sort(newestFirst) }
}

export function templatesDir(editorPath: string, platform: NodeJS.Platform): string {
  return platform === 'win32'
    ? path.win32.join(path.win32.dirname(editorPath), 'Data', 'Resources', 'PackageManager', 'ProjectTemplates')
    : path.posix.join(path.posix.dirname(path.posix.dirname(editorPath)), 'Resources', 'PackageManager', 'ProjectTemplates')
}

function classify(file: string): UnityTemplate['label'] {
  if (file.includes('high-end')) return 'hdrp'
  if (file.includes('2d-cross-platform')) return '2d'
  if (file.includes('3d-cross-platform')) return '3d'
  return 'other'
}

export function listTemplates(version: string, deps: EditorDeps): { templates: UnityTemplate[] } {
  const editor = listEditors(deps).editors.find(e => e.version === version)
  if (!editor) return { templates: [] }
  let files: string[]
  try {
    files = deps.readdir(templatesDir(editor.path, deps.platform))
  } catch {
    return { templates: [] }
  }
  const templates = files.filter(f => f.endsWith('.tgz')).sort()
    .map(file => ({ id: file.slice(0, -'.tgz'.length), label: classify(file), file }))
  return { templates }
}
