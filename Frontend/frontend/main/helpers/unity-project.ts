import fs from 'fs'
import path from 'path'

function isDirectory(dir: string): boolean {
  try {
    return fs.statSync(dir).isDirectory()
  } catch {
    return false
  }
}

export function isUnityProjectDir(dir: string): boolean {
  return isDirectory(dir) && isDirectory(path.join(dir, 'Assets')) &&
    isDirectory(path.join(dir, 'ProjectSettings'))
}

export function readUnityVersion(dir: string): string | null {
  let fd: number | undefined
  try {
    const versionFile = path.join(dir, 'ProjectSettings', 'ProjectVersion.txt')
    if (!fs.statSync(versionFile).isFile()) return null
    fd = fs.openSync(versionFile, 'r')
    const buffer = Buffer.alloc(4096)
    const bytes = fs.readSync(fd, buffer, 0, buffer.length, 0)
    const line = buffer.toString('utf8', 0, bytes).split(/\r?\n/)
      .find(value => /^m_EditorVersion:/.test(value))
    return line?.slice('m_EditorVersion:'.length).trim() || null
  } catch {
    return null
  } finally {
    if (fd !== undefined) {
      try { fs.closeSync(fd) } catch { /* The inspection must not throw. */ }
    }
  }
}

// Unity keeps Library/EditorInstance.json (with its process_id) while the editor runs; the backend reads the same file.
export function isEditorOpen(dir: string): boolean {
  try {
    const info = JSON.parse(fs.readFileSync(path.join(dir, 'Library', 'EditorInstance.json'), 'utf8'))
    const pid = info?.process_id
    if (!Number.isInteger(pid) || pid <= 1) return false
    try {
      process.kill(pid, 0)
      return true
    } catch (err: any) {
      return err?.code === 'EPERM'
    }
  } catch {
    return false
  }
}

export function inspectProject(dir: string): { exists: boolean; unityVersion: string | null } {
  const exists = isDirectory(dir)
  return { exists, unityVersion: exists ? readUnityVersion(dir) : null }
}
