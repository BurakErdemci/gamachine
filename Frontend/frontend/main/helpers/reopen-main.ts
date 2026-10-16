// A Dock click must bring the main window back even while the indicator panel is visible (measured live 16 Oct 2026).
export function reopenMain(win: {
  isDestroyed(): boolean
  isMinimized(): boolean
  isVisible(): boolean
  restore(): void
  show(): void
  focus(): void
} | null | undefined): boolean {
  if (!win || win.isDestroyed()) return false
  if (win.isMinimized()) {
    try { win.restore() } catch { /* Continue to show and focus. */ }
  }
  if (!win.isVisible()) {
    try { win.show() } catch { /* Continue to focus. */ }
  }
  win.focus()
  return true
}
