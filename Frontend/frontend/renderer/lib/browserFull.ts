import { useCallback, useEffect, useState } from 'react';
import { isMacPlatform } from './browserSuggest';

type KeyLike = Pick<KeyboardEvent, 'key' | 'ctrlKey' | 'metaKey' | 'shiftKey' | 'altKey'>;

/** Ctrl+Shift+F (Cmd+Shift+F on macOS): the same chord on the toggle's tooltip. */
export function isBrowserFullShortcut(e: KeyLike, mac: boolean): boolean {
  if (e.altKey || !e.shiftKey || e.key.toLowerCase() !== 'f') return false;
  return mac ? e.metaKey && !e.ctrlKey : e.ctrlKey && !e.metaKey;
}

/**
 * The agent browser's full-screen mode. `shown` is whether the browser view is on screen: the
 * shortcut only acts then, and the mode ends by itself the moment it is not. Esc leaves unless
 * something inside the app already used it (the address box, a menu, a dialog).
 */
export function useBrowserFull(shown: boolean) {
  const [on, setOn] = useState(false);
  const full = on && shown;

  useEffect(() => { if (!shown) setOn(false); }, [shown]);

  useEffect(() => {
    if (!shown) return;
    const mac = isMacPlatform();
    const onKey = (e: KeyboardEvent) => {
      if (e.isComposing || e.defaultPrevented) return;
      if (isBrowserFullShortcut(e, mac)) { e.preventDefault(); setOn(v => !v); return; }
      if (e.key === 'Escape' && !e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey) setOn(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [shown]);

  const toggle = useCallback(() => setOn(v => !v), []);
  return { full, toggle };
}
