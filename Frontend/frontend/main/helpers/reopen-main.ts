// A visible indicator panel stops macOS from de-minimizing the main window on a Dock click (16 Oct 2026).
export function reopenMain(win: {
  isDestroyed(): boolean
  isMinimized(): boolean
  isVisible(): boolean
  restore(): void
  show(): void
  focus(): void
} | null | undefined): boolean {
  if (!win || win.isDestroyed()) return false
  if (win.isMinimized()) win.restore()
  if (!win.isVisible()) win.show()
  win.focus()
  return true
}
