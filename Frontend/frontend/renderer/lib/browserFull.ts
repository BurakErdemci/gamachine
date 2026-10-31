import { useCallback, useEffect, useRef, useState } from 'react';
import { isMacPlatform } from './browserSuggest';

type KeyLike = Pick<KeyboardEvent, 'key' | 'ctrlKey' | 'metaKey' | 'shiftKey' | 'altKey'>;

/** Ctrl+Shift+F (Cmd+Shift+F on macOS): the same chord on the toggle's tooltip. */
export function isBrowserFullShortcut(e: KeyLike, mac: boolean): boolean {
  if (e.altKey || !e.shiftKey || e.key.toLowerCase() !== 'f') return false;
  return mac ? e.metaKey && !e.ctrlKey : e.ctrlKey && !e.metaKey;
}

/**
 * Main sends this when the chord is pressed with focus inside the native page, where the renderer sees no keys (no
 * payload: toggle), and when its Esc rule ends full screen (`{ on: false }`).
 */
export const BROWSER_FULL_TOGGLE_CHANNEL = 'browser-full-toggle';

const MODAL = '[aria-modal="true"], [role="dialog"], [role="alertdialog"]';
// Kept-mounted popups sit in the DOM with `hidden` while closed; only an open one counts.
const hasOpenModal = () => [...document.querySelectorAll(MODAL)].some(el => !el.closest('[hidden], [aria-hidden="true"]'));

const ipcOf = (): any => (typeof window !== 'undefined' ? (window as any).ipc : null);
const live = (req: Record<string, unknown>) => {
  void Promise.resolve(ipcOf()?.invoke?.('browser-live', req)).catch(() => undefined);
};

/**
 * The agent browser's full-screen mode. `shown` is whether the browser view is on screen: the shortcut only acts then,
 * and the mode ends by itself the moment it is not. Esc pressed in this window reaches the renderer first: a control
 * that uses it (closes a popup, cancels an edit) calls preventDefault, and only an Esc nobody used goes to main's Esc
 * rule (op 'escape': drop the pick, leave annotate mode, else leave full screen). Main claims Esc itself in the native
 * page, overlay and box, where the renderer sees no keys; it knows full screen is on from op 'full'. `chatOpen` is the
 * floating chat over the full-screen page (collapsed to a toolbar button); `cardWaiting` (a request waits in the chat)
 * keeps it open, so the card is never hidden while the agent waits for it.
 */
export function useBrowserFull(shown: boolean, chatId?: number | null, cardWaiting = false) {
  const [on, setOn] = useState(false);
  const [chatOpen, setChatOpen] = useState(true);
  const full = on && shown;
  const waiting = useRef(cardWaiting);
  waiting.current = cardWaiting;

  useEffect(() => { if (!shown) setOn(false); }, [shown]);
  useEffect(() => { setOn(false); }, [chatId]);
  useEffect(() => { if (cardWaiting) setChatOpen(true); }, [cardWaiting]);

  useEffect(() => {
    if (!shown) return;
    const mac = isMacPlatform();
    const onKey = (e: KeyboardEvent) => {
      if (e.isComposing || e.defaultPrevented || e.repeat || !isBrowserFullShortcut(e, mac) || hasOpenModal()) return;
      e.preventDefault();
      setOn(v => !v);
    };
    let timer: ReturnType<typeof setTimeout> | undefined;
    const onEsc = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented || e.isComposing || e.repeat || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
      // Window listeners added after this one (ConfirmDialog, the image lightbox) run after it in the same phase, so
      // whether anything used the key is read once the event has been through all of them.
      clearTimeout(timer);
      timer = setTimeout(() => { if (!e.defaultPrevented) live({ op: 'escape' }); }, 0);
    };
    window.addEventListener('keydown', onKey, true);
    window.addEventListener('keydown', onEsc);
    const off = ipcOf()?.on?.(BROWSER_FULL_TOGGLE_CHANNEL, (p: any) => {
      if (p && p.on === false) { setOn(false); return; }
      if (!hasOpenModal()) setOn(v => !v);
    });
    return () => {
      window.removeEventListener('keydown', onKey, true);
      window.removeEventListener('keydown', onEsc);
      clearTimeout(timer);
      if (typeof off === 'function') off();
    };
  }, [shown]);

  // Main hears full screen on, and off when it ends or the app (or this hook) goes away.
  useEffect(() => {
    if (!full) return;
    live({ op: 'full', on: true });
    return () => live({ op: 'full', on: false });
  }, [full]);

  const toggle = useCallback(() => setOn(v => !v), []);
  // A waiting card keeps the chat open; once it is answered the user can collapse it again.
  const toggleChat = useCallback(() => setChatOpen(v => !v || waiting.current), []);
  return { full, toggle, chatOpen: chatOpen || cardWaiting, toggleChat };
}
