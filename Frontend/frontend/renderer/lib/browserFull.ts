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
// Kept-mounted popups (the strip's More menu) sit in the DOM with `hidden` while closed; only an open one counts.
const isOpen = (selector: string) => [...document.querySelectorAll(selector)].some(el => !el.closest('[hidden], [aria-hidden="true"]'));
const hasOpenModal = () => isOpen(MODAL);
// What keeps its own Esc in this window: a dialog, a menu or list popup, or a field of the browser panel (the address
// box drops an edit on Esc).
const ESC_OWNERS = `${MODAL}, [role="menu"], [role="listbox"]`;
const FIELD = 'input, textarea, select, [contenteditable]';
export function escOwnedHere(): boolean {
  if (isOpen(ESC_OWNERS)) return true;
  const t = document.activeElement;
  return t instanceof Element && !!t.closest('.bp-panel') && t.matches(FIELD);
}

const ipcOf = (): any => (typeof window !== 'undefined' ? (window as any).ipc : null);

/**
 * The agent browser's full-screen mode. `shown` is whether the browser view is on screen: the shortcut only acts then,
 * and the mode ends by itself the moment it is not. Esc is main's (browser-host.ts escape()), wherever focus is: this
 * hook tells it whether full screen is on and whether something in this window owns Esc right now, over the browser
 * channel's `full` op. `chatOpen` is the floating chat over the full-screen page (collapsed to a toolbar button).
 */
export function useBrowserFull(shown: boolean, chatId?: number | null) {
  const [on, setOn] = useState(false);
  const [chatOpen, setChatOpen] = useState(true);
  const full = on && shown;
  const [owned, setOwned] = useState(false);

  useEffect(() => { if (!shown) setOn(false); }, [shown]);
  useEffect(() => { setOn(false); }, [chatId]);

  useEffect(() => {
    if (!shown) return;
    const mac = isMacPlatform();
    const onKey = (e: KeyboardEvent) => {
      if (e.isComposing || e.defaultPrevented || e.repeat || !isBrowserFullShortcut(e, mac) || hasOpenModal()) return;
      e.preventDefault();
      setOn(v => !v);
    };
    window.addEventListener('keydown', onKey, true);
    const off = ipcOf()?.on?.(BROWSER_FULL_TOGGLE_CHANNEL, (p: any) => {
      if (p && p.on === false) { setOn(false); return; }
      if (!hasOpenModal()) setOn(v => !v);
    });
    return () => {
      window.removeEventListener('keydown', onKey, true);
      if (typeof off === 'function') off();
    };
  }, [shown]);

  // Who owns Esc changes with the DOM (a popup opens) and with focus (the address box).
  useEffect(() => {
    if (!shown) { setOwned(false); return; }
    let frame = 0;
    const check = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => { frame = 0; setOwned(escOwnedHere()); });
    };
    setOwned(escOwnedHere());
    const mo = new MutationObserver(check);
    mo.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['role', 'aria-modal', 'open', 'hidden'] });
    document.addEventListener('focusin', check);
    document.addEventListener('focusout', check);
    return () => {
      mo.disconnect();
      if (frame) cancelAnimationFrame(frame);
      document.removeEventListener('focusin', check);
      document.removeEventListener('focusout', check);
    };
  }, [shown]);

  const last = useRef('');
  useEffect(() => {
    const key = `${full}:${owned}`;
    if (key === last.current) return;
    last.current = key;
    void Promise.resolve(ipcOf()?.invoke?.('browser-live', { op: 'full', on: full, owned })).catch(() => undefined);
  }, [full, owned]);
  // The app going away (or this hook) leaves main with full screen off.
  useEffect(() => () => {
    void Promise.resolve(ipcOf()?.invoke?.('browser-live', { op: 'full', on: false, owned: false })).catch(() => undefined);
  }, []);

  const toggle = useCallback(() => setOn(v => !v), []);
  const toggleChat = useCallback(() => setChatOpen(v => !v), []);
  return { full, toggle, chatOpen, toggleChat };
}
