import path from 'path'

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
  return candidates.find(candidate => exists(candidate)) ?? null
}
