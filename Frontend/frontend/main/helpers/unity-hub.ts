import path from 'path'
import fs from 'fs'
import os from 'os'
import { execFileSync, spawn } from 'child_process'

// Unity's MSIX Hub: its AppData is virtualized under LocalCache, and it can only be started through AppsFolder.
export const UNITY_HUB_MSIX_FAMILY = 'UnityTechnologies.UnityHub_2vrhnee42bhxm'
export const UNITY_HUB_MSIX_URI = 'shell:AppsFolder\\' + UNITY_HUB_MSIX_FAMILY + '!UnityHub'

export function unityHubMsixCache(env: Record<string, string | undefined>): string | null {
  return env.LOCALAPPDATA ? path.win32.join(env.LOCALAPPDATA, 'Packages', UNITY_HUB_MSIX_FAMILY, 'LocalCache') : null
}

export function findUnityHub(
  platform: NodeJS.Platform,
  env: Record<string, string | undefined>,
  home: string,
  exists: (p: string) => boolean,
): string | null {
  const candidates: string[] = []
  if (platform === 'win32') {
    for (const key of ['ProgramFiles', 'ProgramFiles(x86)']) {
      if (env[key]) candidates.push(path.win32.join(env[key]!, 'Unity Hub', 'Unity Hub.exe'))
    }
    if (env.LOCALAPPDATA) {
      candidates.push(path.win32.join(env.LOCALAPPDATA, 'Programs', 'Unity Hub', 'Unity Hub.exe'))
    }
  } else if (platform === 'darwin') {
    candidates.push(path.posix.join(path.posix.sep, 'Applications', 'Unity Hub.app'),
      path.posix.join(home, 'Applications', 'Unity Hub.app'))
  }
  const classic = candidates.find(candidate => exists(candidate))
  if (classic) return classic
  // The package folder lives until the package is uninstalled, so its presence means the Hub is installed.
  if (platform === 'win32' && env.LOCALAPPDATA &&
      exists(path.win32.join(env.LOCALAPPDATA, 'Packages', UNITY_HUB_MSIX_FAMILY))) return UNITY_HUB_MSIX_URI
  return null
}

export function isHubRunning(platform: NodeJS.Platform = process.platform): boolean {
  try {
    if (platform === 'win32') {
      const out = execFileSync('tasklist', ['/FI', 'IMAGENAME eq Unity Hub.exe', '/FO', 'CSV', '/NH'],
        { encoding: 'utf-8', timeout: 2000, windowsHide: true })
      return /"Unity Hub\.exe"/i.test(out)
    }
    if (platform === 'darwin') {
      execFileSync('pgrep', ['-x', 'Unity Hub'], { timeout: 2000, stdio: 'ignore' })
      return true
    }
  } catch { /* tasklist/pgrep failed, or pgrep found no match (exit 1) */ }
  return false
}

// The editor connects to this pipe for licensing; it exists only while the Hub's licensing client is up.
// Listing the pipe directory, because fs.existsSync answers false for pipes that exist (measured on Node for Windows).
export function isLicensingReady(platform: NodeJS.Platform = process.platform): boolean {
  if (platform !== 'win32') return isHubRunning(platform)
  try {
    const name = ('Unity-LicenseClient-' + os.userInfo().username).toLowerCase()
    return fs.readdirSync('\\\\.\\pipe\\').some(entry => entry.toLowerCase() === name)
  } catch {
    return false
  }
}

// false when no Hub is installed or it could not be started. openPath is electron's shell.openPath (resolves '' on success).
export async function startHub(openPath: (p: string) => Promise<string>): Promise<boolean> {
  try {
    const hub = findUnityHub(process.platform, process.env, os.homedir(), fs.existsSync)
    if (!hub) return false
    if (hub === UNITY_HUB_MSIX_URI) {
      // A Store app cannot be started from its exe path; explorer.exe resolves the AppsFolder URI.
      // A spawn failure arrives as an 'error' event after this returns; without a listener it is an uncaught exception.
      const child = spawn('explorer.exe', [hub], { detached: true, stdio: 'ignore', windowsHide: true })
      child.once('error', (err) => console.error('[open-unity-hub] explorer.exe failed:', err))
      child.unref()
      return true
    }
    return await openPath(hub) === ''
  } catch {
    return false
  }
}
