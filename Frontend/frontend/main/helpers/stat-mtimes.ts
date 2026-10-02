export async function collectMtimes(
  entries: Array<[key: string, absPath: string]>,
  stat: (p: string) => Promise<{ mtimeMs: number }>,
  chunk = 64,
): Promise<Record<string, number>> {
  const mtimes: Record<string, number> = {}
  for (let i = 0; i < entries.length; i += chunk) {
    await Promise.all(entries.slice(i, i + chunk).map(async ([key, absPath]) => {
      try {
        mtimes[key] = Math.floor((await stat(absPath)).mtimeMs)
      } catch { /* Deleted or unavailable files have no mtime. */ }
    }))
  }
  return mtimes
}
