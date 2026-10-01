import { useEffect } from 'react';

/**
 * The Ctrl+N / Cmd+N "new chat" shortcut the sidebar's "New chat" row names.
 *
 * It is a window-level listener, so without guards it fired everywhere: inside the xterm
 * terminal (where Ctrl+N is readline's next-history, and every press POSTed a new
 * conversation), on key auto-repeat while held, inside a rename input or Monaco's textarea,
 * and behind an open modal. Each of those cases is ignored here; the event is left untouched
 * (no preventDefault) so the focused widget still gets its own Ctrl+N.
 */
export function isNewChatShortcut(e: KeyboardEvent): boolean {
  if (!(e.ctrlKey || e.metaKey) || e.shiftKey || e.altKey) return false;
  if (e.key.toLowerCase() !== 'n') return false;
  if (e.repeat || e.defaultPrevented) return false;

  const target = e.target instanceof Element ? e.target : null;
  if (target) {
    if (target.closest('.xterm')) return false;
    const tag = target.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return false;
    if ((target as HTMLElement).isContentEditable || target.closest('[contenteditable=""], [contenteditable="true"]')) return false;
  }

  // A modal owns the keyboard while it is open.
  if (typeof document !== 'undefined'
      && document.querySelector('[aria-modal="true"], [role="dialog"], [role="alertdialog"]')) return false;
  return true;
}

export function useNewChatShortcut(onNewChat: () => void): void {
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (!isNewChatShortcut(e)) return;
      e.preventDefault();
      onNewChat();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onNewChat]);
}
