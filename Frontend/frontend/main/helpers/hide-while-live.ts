// During a live run Cmd+H minimizes instead of hiding (owner's call, 16 Oct 2026): a hidden app comes
// straight back while a run is live (measured, cause outside the main process) and minimize shows the indicator.
export function hideOrMinimize(deps: {
  platform: string
  live: () => boolean
  hideApp: () => void
  minimizeMain: () => boolean
}): void {
  let live = false
  if (deps.platform === 'darwin') {
    try { live = deps.live() } catch { /* Treat an unavailable live state as idle. */ }
  }
  if (live) {
    try { if (deps.minimizeMain()) return } catch { /* Fall back to hiding if minimizing fails. */ }
  }
  deps.hideApp()
}
