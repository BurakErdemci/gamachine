function isEditable(node: Node | null): boolean {
  const element = node instanceof Element ? node : node?.parentElement
  return !!element?.closest('input, textarea, [contenteditable]:not([contenteditable="false" i]), .monaco-editor')
}

export function selectedTextToCopy(selection: Selection | null, active: Element | null): string | null {
  if (!selection || selection.isCollapsed || isEditable(active) ||
      isEditable(selection.anchorNode) || isEditable(selection.focusNode)) return null
  const text = selection.toString()
  return text.trim() ? text : null
}

export function installCopyOnSelect(
  isEnabled: () => boolean,
  write: (text: string) => void | Promise<void> = text => navigator.clipboard?.writeText(text),
): () => void {
  let pending: ReturnType<typeof setTimeout> | undefined
  const onMouseUp = () => {
    if (!isEnabled()) return
    clearTimeout(pending)
    pending = setTimeout(() => {
      pending = undefined
      if (!isEnabled()) return
      const text = selectedTextToCopy(document.getSelection(), document.activeElement)
      if (text !== null) {
        try { void Promise.resolve(write(text)).catch(() => {}) } catch { /* Clipboard may be unavailable. */ }
      }
    }, 0)
  }
  document.addEventListener('mouseup', onMouseUp, true)
  return () => {
    document.removeEventListener('mouseup', onMouseUp, true)
    clearTimeout(pending)
  }
}
