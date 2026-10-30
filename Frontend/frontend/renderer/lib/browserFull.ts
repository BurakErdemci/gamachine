import { useCallback, useEffect, useState } from 'react';
import { isMacPlatform } from './browserSuggest';

type KeyLike = Pick<KeyboardEvent, 'key' | 'ctrlKey' | 'metaKey' | 'shiftKey' | 'altKey'>;

/** Ctrl+Shift+F (Cmd+Shift+F on macOS): the same chord on the toggle's tooltip. */
export function isBrowserFullShortcut(e: KeyLike, mac: boolean): boolean {
  if (e.altKey || !e.shiftKey || e.key.toLowerCase() !== 'f') return false;
  return mac ? e.metaKey && !e.ctrlKey : e.ctrlKey && !e.metaKey;
}

/** Main sends this when the chord is pressed with focus inside the native page, where the renderer sees no keys. */
export const BROWSER_FULL_TOGGLE_CHANNEL = 'browser-full-toggle';

const MODAL = '[aria-modal="true"], [role="dialog"], [role="alertdialog"]';
const hasOpenModal = () => !!document.querySelector(MODAL);

/**
 * The agent browser's full-screen mode. `shown` is whether the browser view is on screen: the
 * shortcut only acts then, and the mode ends by itself the moment it is not. Esc leaves only when
 * nothing else owns it: no open dialog, and focus on the page body or inside the browser panel
 * (a popup or the composer closes itself on Esc without marking the event). The listener runs in
 * the capture phase so the DOM is read before such a popup removes itself.
 */
export function useBrowserFull(shown: boolean) {
  const [on, setOn] = useState(false);
  const full = on && shown;

  useEffect(() => { if (!shown) setOn(false); }, [shown]);

  useEffect(() => {
    if (!shown) return;
    const mac = isMacPlatform();
    const onKey = (e: KeyboardEvent) => {
      if (e.isComposing || e.defaultPrevented || e.repeat) return;
      if (isBrowserFullShortcut(e, mac)) {
        if (hasOpenModal()) return;
        e.preventDefault();
        setOn(v => !v);
        return;
      }
      if (e.key !== 'Escape' || e.ctrlKey || e.metaKey || e.altKey || e.shiftKey || hasOpenModal()) return;
      const t = e.target;
      if (t instanceof Element && t !== document.body && t !== document.documentElement) {
        // The address box uses Esc to drop an edit, so a field inside the panel keeps its Esc.
        if (!t.closest('.bp-panel') || t.matches('input, textarea, select, [contenteditable]')) return;
      }
      setOn(false);
    };
    window.addEventListener('keydown', onKey, true);
    const ipc = typeof window !== 'undefined' ? (window as any).ipc : null;
    const off = ipc?.on?.(BROWSER_FULL_TOGGLE_CHANNEL, () => { if (!hasOpenModal()) setOn(v => !v); });
    return () => {
      window.removeEventListener('keydown', onKey, true);
      if (typeof off === 'function') off();
    };
  }, [shown]);

  const toggle = useCallback(() => setOn(v => !v), []);
  return { full, toggle };
}
