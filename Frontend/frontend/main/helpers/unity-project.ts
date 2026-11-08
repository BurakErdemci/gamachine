import { execFileSync } from 'child_process'
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

// Image name of a live pid (Windows `Unity.exe`, elsewhere the `comm` basename), or null when it cannot be read.
export function queryProcessImage(pid: number): string | null {
  try {
    if (process.platform === 'win32') {
      const out = execFileSync('tasklist', ['/FI', `PID eq ${pid}`, '/FO', 'CSV', '/NH'],
        { encoding: 'utf8', timeout: 2000, windowsHide: true })
      return /^"([^"]+)"/m.exec(out)?.[1] ?? null
    }
    const out = execFileSync('ps', ['-o', 'comm=', '-p', String(pid)], { encoding: 'utf8', timeout: 2000 }).trim()
    return out ? path.basename(out) : null
  } catch {
    return null
  }
}

function isUnityImage(image: string | null): boolean {
  return process.platform === 'win32' ? image?.toLowerCase() === 'unity.exe' : image === 'Unity'
}

// Unity keeps Library/EditorInstance.json (with its process_id) while the editor runs; the backend reads the same file.
// A crash leaves the file behind and the OS reuses the pid, so a live pid only counts when it is a Unity editor.
export function isEditorOpen(dir: string, queryImage: (pid: number) => string | null = queryProcessImage): boolean {
  try {
    const info = JSON.parse(fs.readFileSync(path.join(dir, 'Library', 'EditorInstance.json'), 'utf8'))
    const pid = info?.process_id
    if (!Number.isInteger(pid) || pid <= 1) return false
    try {
      process.kill(pid, 0)
    } catch (err: any) {
      if (err?.code !== 'EPERM') return false
    }
    return isUnityImage(queryImage(pid))
  } catch {
    return false
  }
}

export function inspectProject(dir: string): { exists: boolean; unityVersion: string | null } {
  const exists = isDirectory(dir)
  return { exists, unityVersion: exists ? readUnityVersion(dir) : null }
}
