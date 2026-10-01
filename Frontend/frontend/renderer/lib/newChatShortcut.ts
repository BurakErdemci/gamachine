import { useEffect } from 'react';

/**
 * The Ctrl+N / Cmd+N "new chat" shortcut the sidebar's "New chat" row names.
 *
 * It is a window-level listener, so without guards it fired everywhere: inside the xterm
 * terminal (where Ctrl+N is readline's next-history, and every press POSTed a new
 * conversation), on key auto-repeat while held, inside a rename input or Monaco's textarea,
 * and behind an open modal. Each of those cases is ignored here; the event is left untouched
 * (no preventDefault) so the focused widget still gets its own Ctrl+N.
 *
 * The one text field it does fire from is the composer: the user is usually typing there when
 * they want a fresh chat, and Ctrl+N means nothing to a plain textarea. A blanket TEXTAREA guard
 * made the shortcut work only with nothing focused (P2 audit). The side question panel is a
 * `role="dialog"`, so while it is open the modal guard below still blocks the shortcut, even
 * from the composer: the panel owns the keyboard then.
 */
export function isNewChatShortcut(e: KeyboardEvent): boolean {
  if (!(e.ctrlKey || e.metaKey) || e.shiftKey || e.altKey) return false;
  if (e.key.toLowerCase() !== 'n') return false;
  if (e.repeat || e.defaultPrevented) return false;

  const target = e.target instanceof Element ? e.target : null;
  if (target) {
    if (target.closest('.xterm') || target.closest('.monaco-editor')) return false;
    const tag = target.tagName;
    const inComposer = tag === 'TEXTAREA' && !!target.closest('.composer');
    if (!inComposer && (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT')) return false;
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
