/**
 * One-message-at-a-time navigation for the chat thread (buttons and Alt+Up/Down).
 *
 * Why it exists: with a broken mouse wheel the only way to scroll a long chat is dragging the
 * scrollbar thumb, and a small drag jumps far. This moves exactly to the neighbouring message.
 * System events (data-role="system") are skipped: they are not messages a reader steps through.
 */

export type ChatNavDirection = 'prev' | 'next';

/** Where a message's top lands: just under the 16px fade mask at the top of `.thread`. */
export const NAV_TOP_GAP_PX = 18;
/** A message whose top is within this of the landing line counts as "the current one". */
const NAV_EPSILON_PX = 4;

const MESSAGE_SELECTOR = 'article.msg[data-role="user"], article.msg[data-role="assistant"]';

export type ChatNavTarget =
  | { kind: 'message'; scrollTop: number }
  | { kind: 'bottom' };

/** Pure decision: what a prev/next press should do for this thread, or null for nothing. */
export function chatNavTarget(thread: HTMLElement, direction: ChatNavDirection): ChatNavTarget | null {
  const threadTop = thread.getBoundingClientRect().top;
  const tops = Array.from(thread.querySelectorAll<HTMLElement>(MESSAGE_SELECTOR))
    .map(el => el.getBoundingClientRect().top - threadTop);
  const toTarget = (rel: number): ChatNavTarget =>
    ({ kind: 'message', scrollTop: Math.max(0, thread.scrollTop + rel - NAV_TOP_GAP_PX) });

  if (direction === 'prev') {
    for (let i = tops.length - 1; i >= 0; i--) {
      if (tops[i] < NAV_TOP_GAP_PX - NAV_EPSILON_PX) return toTarget(tops[i]);
    }
    return null;
  }
  for (const rel of tops) {
    if (rel > NAV_TOP_GAP_PX + NAV_EPSILON_PX) return toTarget(rel);
  }
  return { kind: 'bottom' };
}

/**
 * Runs the decision. Past the last message "next" is the jump-to-latest, done by `onBottom`
 * (it re-arms auto-follow, which a bare scrollTo would not). Returns whether anything moved.
 */
export function stepChatMessage(
  thread: HTMLElement | null,
  direction: ChatNavDirection,
  onBottom: () => void,
): boolean {
  if (!thread) return false;
  const target = chatNavTarget(thread, direction);
  if (!target) return false;
  if (target.kind === 'bottom') onBottom();
  else thread.scrollTo({ top: target.scrollTop, behavior: 'smooth' });
  return true;
}

/**
 * Alt+ArrowUp / Alt+ArrowDown, but only where the keys mean nothing else: not in Monaco, the
 * terminal, any text field except the composer, not while a dialog or a composer menu (mention,
 * slash palette) is open — those own the arrows.
 */
export function chatNavShortcutDirection(e: KeyboardEvent): ChatNavDirection | null {
  if (!e.altKey || e.ctrlKey || e.metaKey || e.shiftKey || e.defaultPrevented) return null;
  if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return null;
  if (e.isComposing) return null;

  const target = e.target instanceof Element ? e.target : null;
  if (target) {
    if (target.closest('.xterm') || target.closest('.monaco-editor')) return null;
    const tag = target.tagName;
    const inComposer = tag === 'TEXTAREA' && !!target.closest('.composer');
    if (!inComposer && (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT')) return null;
    if ((target as HTMLElement).isContentEditable || target.closest('[contenteditable=""], [contenteditable="true"]')) return null;
  }
  if (typeof document !== 'undefined'
      && document.querySelector('[aria-modal="true"], [role="dialog"], [role="alertdialog"], [role="menu"], .composer-pop')) return null;
  return e.key === 'ArrowUp' ? 'prev' : 'next';
}
