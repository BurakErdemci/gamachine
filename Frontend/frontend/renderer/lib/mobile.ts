import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { isRemotePage } from './remote/transport';

/** Phone width for the web remote: below a tablet's portrait width (768 px). */
export const PHONE_QUERY = '(max-width: 767px)';

/**
 * True on the relay-served web remote at phone width, live across resize and rotation. The
 * desktop app (app:// or localhost) is never a remote page, so it never enters phone mode.
 * Starts false and is measured after mount, so the server render and hydration agree.
 */
export function usePhoneLayout(): boolean {
  const [phone, setPhone] = useState(false);
  useEffect(() => {
    if (!isRemotePage() || typeof window.matchMedia !== 'function') return;
    const mq = window.matchMedia(PHONE_QUERY);
    const update = () => setPhone(mq.matches);
    update();
    mq.addEventListener?.('change', update);
    // Rotation on older WebKit fires resize without a media-query change event.
    window.addEventListener('resize', update);
    return () => {
      mq.removeEventListener?.('change', update);
      window.removeEventListener('resize', update);
    };
  }, []);
  return phone;
}

export type PhoneScreen = 'list' | 'chat';
export type PhoneChatTab = 'chat' | 'screen';

/** The key this module adds to `history.state`; the URL never changes. */
const STATE_KEY = 'gmPhone';
const inChatEntry = (state: unknown): boolean =>
  !!state && typeof state === 'object' && (state as Record<string, unknown>)[STATE_KEY] === 'chat';

/**
 * Which phone screen shows (the chat list or one chat, and the chat's tab). Opening a chat pushes
 * one history entry with the same URL, so the phone's own Back returns to the list; the in-app
 * back button goes through history too when that entry is there. A popstate that leaves or enters
 * the phone's chat entry is consumed here: the page URL is the same, so the router has nothing to do.
 */
export function usePhoneRoute() {
  const [screen, setScreen] = useState<PhoneScreen>('list');
  const [chatTab, setChatTab] = useState<PhoneChatTab>('chat');
  const screenRef = useRef(screen);
  screenRef.current = screen;

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const onPop = (e: PopStateEvent) => {
      const toChat = inChatEntry(e.state);
      if (!toChat && screenRef.current !== 'chat') return;
      e.stopImmediatePropagation();
      setScreen(toChat ? 'chat' : 'list');
      if (!toChat) setChatTab('chat');
    };
    // Capture: runs before the router's own popstate listener on the window.
    window.addEventListener('popstate', onPop, true);
    return () => window.removeEventListener('popstate', onPop, true);
  }, []);

  const openChat = useCallback((tab: PhoneChatTab = 'chat') => {
    setChatTab(tab);
    if (screenRef.current === 'chat') return;
    setScreen('chat');
    try {
      window.history.pushState({ ...(window.history.state ?? {}), [STATE_KEY]: 'chat' }, '');
    } catch { /* history unavailable: the in-app back button still works */ }
  }, []);

  const back = useCallback(() => {
    if (typeof window !== 'undefined' && inChatEntry(window.history.state)) {
      window.history.back();
      return;
    }
    setScreen('list');
    setChatTab('chat');
  }, []);

  return { screen, chatTab, setChatTab, openChat, back };
}

/**
 * How far the on-screen keyboard (or any bottom browser bar) covers the layout viewport, in px:
 * the gap between the layout viewport's bottom and the visual viewport's. 0 where visualViewport
 * is missing or while `enabled` is false.
 */
export function useVisualViewportInset(enabled = true): number {
  const [inset, setInset] = useState(0);
  useEffect(() => {
    const vv = typeof window !== 'undefined' ? window.visualViewport : null;
    if (!enabled || !vv) { setInset(0); return; }
    const update = () => setInset(Math.max(0, Math.round(window.innerHeight - (vv.height + vv.offsetTop))));
    update();
    vv.addEventListener('resize', update);
    vv.addEventListener('scroll', update);
    return () => {
      vv.removeEventListener('resize', update);
      vv.removeEventListener('scroll', update);
    };
  }, [enabled]);
  return inset;
}

// How many phone shells are on screen. RemoteGate's fixed banner steps aside only while one is:
// the shell's own connection line then says the same, with the last-seen time; screens without the
// shell (the project picker) keep the banner.
let shells = 0;
const shellListeners = new Set<() => void>();
const subscribeShells = (cb: () => void) => { shellListeners.add(cb); return () => { shellListeners.delete(cb); }; };

/**
 * Marks a phone shell that is drawn (components/phone/PhoneShell.tsx). `shown` is false while a
 * full-screen page (settings, profile, guide) covers it: the banner then says the PC is away.
 */
export function useMarkPhoneShell(shown = true): void {
  useEffect(() => {
    if (!shown) return;
    shells += 1;
    shellListeners.forEach(l => l());
    return () => { shells -= 1; shellListeners.forEach(l => l()); };
  }, [shown]);
}

/** False while the page is hidden (phone locked, another app in front); true where there is no document state. */
export function usePageVisible(): boolean {
  const [visible, setVisible] = useState(() => typeof document === 'undefined' || document.visibilityState !== 'hidden');
  useEffect(() => {
    const update = () => setVisible(document.visibilityState !== 'hidden');
    update();
    document.addEventListener('visibilitychange', update);
    return () => document.removeEventListener('visibilitychange', update);
  }, []);
  return visible;
}

/** True while a phone shell is on screen. */
export function usePhoneShellShown(): boolean {
  return useSyncExternalStore(subscribeShells, () => shells > 0, () => false);
}
