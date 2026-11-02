import path from 'path'

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
