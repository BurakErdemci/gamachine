import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useLang } from '../../lib/i18n';
import { addressToUrl, displayAddress } from '../../lib/browserAddress';

/** Main-process event and invoke channel for the agent browser (docs/agent-browser.md). */
export const BROWSER_CHANNEL = 'browser-live';

export interface BrowserState {
  active: boolean;
  conversationId: number | null;
  url: string;
  title: string;
  loading: boolean;
  userControl: boolean;
}

const EMPTY: BrowserState = { active: false, conversationId: null, url: '', title: '', loading: false, userControl: false };

const ipcOf = (): any => (typeof window !== 'undefined' ? (window as any).ipc : null);

const invoke = (payload: Record<string, unknown>) =>
  Promise.resolve(ipcOf()?.invoke?.(BROWSER_CHANNEL, payload)).catch(() => undefined);

function toState(raw: any): BrowserState {
  if (!raw || typeof raw !== 'object') return EMPTY;
  return {
    active: raw.active === true,
    conversationId: Number.isInteger(raw.conversationId) ? raw.conversationId : null,
    url: typeof raw.url === 'string' ? raw.url : '',
    title: typeof raw.title === 'string' ? raw.title : '',
    loading: raw.loading === true,
    userControl: raw.userControl === true,
  };
}

/**
 * The open chat's tab: one status read when the chat changes, then its event stream. Events of
 * other chats are dropped; a late status never rolls back a newer event.
 */
export function useBrowserLive(conversationId: number | null): BrowserState {
  const [state, setState] = useState<BrowserState>(EMPTY);
  useEffect(() => {
    setState(EMPTY);
    const ipc = ipcOf();
    if (!ipc || conversationId == null) return;
    let alive = true;
    let heard = false;
    Promise.resolve(ipc.invoke?.(BROWSER_CHANNEL, { op: 'status', conversationId }))
      .then((raw: any) => { if (alive && !heard && raw && !raw.error) setState(toState(raw)); })
      .catch(() => {});
    const off = ipc.on?.(BROWSER_CHANNEL, (raw: any) => {
      if (!raw || raw.conversationId !== conversationId) return;
      heard = true;
      if (alive) setState(toState(raw));
    });
    return () => { alive = false; if (typeof off === 'function') off(); };
  }, [conversationId]);
  return state;
}

// Anything that can sit above the native view: menus, modals and the approval card stack.
const OVERLAY_SELECTOR = '[role="dialog"], [role="alertdialog"], [role="menu"], .approval-slot, .approval';

const intersects = (a: DOMRect, b: DOMRect) =>
  a.width > 0 && a.height > 0 && b.width > 0 && b.height > 0
  && a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;

const STOP_ICON = <svg className="bp-stop-ic" viewBox="0 0 12 12" aria-hidden="true"><rect x="2" y="2" width="8" height="8" rx="1" /></svg>;

/**
 * The right panel's "Tarayıcı" tab. The page itself is a native view that main lays over the
 * placeholder, so this component only measures the placeholder and tells main when the view may
 * be shown: never while the tab is hidden or something in the DOM overlaps it. It shows the tab
 * of the chat open in the UI (`conversationId`); every op names that chat.
 */
export function BrowserPanel({ visible, conversationId, running, onStop, onNeedChat }: {
  visible: boolean;
  conversationId: number | null;
  /** The chat on screen has a turn in flight. */
  running: boolean;
  /** The chat's own Stop: ends the whole turn, not only the browser tools. */
  onStop: () => Promise<void>;
  /** Creates the chat for an `open` from a fresh, unsaved chat; null when it failed. */
  onNeedChat: () => Promise<number | null>;
}) {
  const { t } = useLang();
  const state = useBrowserLive(conversationId);
  const hostRef = useRef<HTMLDivElement>(null);
  const [covered, setCovered] = useState(false);
  // idle -> busy (calls in flight) -> sent (waiting for the turn to end) -> stopped (until the next turn).
  const [stop, setStop] = useState<'idle' | 'busy' | 'sent' | 'stopped'>('idle');
  const [stopFailed, setStopFailed] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [openUrl, setOpenUrl] = useState('');
  const lastBounds = useRef('');
  const chatId = useRef(conversationId);
  chatId.current = conversationId;

  const measure = useCallback(() => {
    const el = hostRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const bounds = { x: Math.round(r.left), y: Math.round(r.top), width: Math.round(r.width), height: Math.round(r.height) };
    const key = JSON.stringify(bounds);
    if (key !== lastBounds.current && chatId.current != null) {
      lastBounds.current = key;
      void invoke({ op: 'set-bounds', conversationId: chatId.current, bounds });
    }
    const hit = Array.from(document.querySelectorAll(OVERLAY_SELECTOR))
      .some(o => !el.contains(o) && intersects(r, o.getBoundingClientRect()));
    setCovered(hit);
  }, []);

  // A new chat has its own tab, so its bounds are sent again.
  useEffect(() => { lastBounds.current = ''; measure(); }, [conversationId, measure]);

  useEffect(() => {
    const el = hostRef.current;
    if (!el) return;
    measure();
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(() => measure()) : null;
    ro?.observe(el);
    let frame = 0;
    const later = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => { frame = 0; measure(); });
    };
    const mo = new MutationObserver(later);
    mo.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['class', 'style', 'hidden', 'open'] });
    window.addEventListener('resize', later);
    window.addEventListener('scroll', later, true);
    return () => {
      ro?.disconnect();
      mo.disconnect();
      if (frame) cancelAnimationFrame(frame);
      window.removeEventListener('resize', later);
      window.removeEventListener('scroll', later, true);
    };
  }, [measure]);

  const show = visible && state.active && !covered && conversationId != null;
  useEffect(() => {
    if (conversationId != null) void invoke({ op: 'visible', conversationId, visible: show });
  }, [show, conversationId]);
  // Leaving a chat (or the panel) hides that chat's view.
  useEffect(() => () => {
    if (conversationId != null) void invoke({ op: 'visible', conversationId, visible: false });
  }, [conversationId]);
  useEffect(() => { setStop('idle'); setStopFailed(false); setEditing(false); }, [state.active, conversationId]);
  useEffect(() => {
    if (stop === 'sent' && !running) setStop('stopped');
    else if (stop === 'stopped' && running) setStop('idle');
  }, [stop, running]);
  useEffect(() => { setOpenUrl(''); }, [conversationId]);

  const send = (op: string, extra: Record<string, unknown> = {}) => {
    if (conversationId != null) void invoke({ op, conversationId, ...extra });
  };

  // The page stops at once (halt); the chat's Stop ends the turn itself.
  const doStop = async () => {
    if (stop !== 'idle' || !running || conversationId == null) return;
    const id = conversationId;
    setStop('busy');
    setStopFailed(false);
    try {
      const [halt] = await Promise.all([
        Promise.resolve(ipcOf()?.invoke?.(BROWSER_CHANNEL, { op: 'halt', conversationId: id })),
        onStop(),
      ]);
      if (chatId.current !== id) return;
      if (halt?.halted === false) throw new Error('halt refused');
      setStop('sent');
    } catch {
      if (chatId.current !== id) return;
      setStopFailed(true);
      setStop('idle');
    }
  };

  const openFromIdle = async (text: string) => {
    const target = addressToUrl(text);
    if (!target) return;
    const id = conversationId ?? await onNeedChat();
    if (id) void invoke({ op: 'open', conversationId: id, url: target });
  };

  const submit = (op: 'navigate', text: string) => {
    const target = addressToUrl(text);
    if (!target) return;
    send(op, { url: target });
  };

  const keyOf = (op: 'navigate', url: string) => (e: React.KeyboardEvent) => {
    // IME Enter commits the composition first.
    if (e.key !== 'Enter' || e.nativeEvent.isComposing || e.nativeEvent.keyCode === 229) return;
    e.preventDefault();
    submit(op, url);
    if (op === 'navigate') { setEditing(false); (e.currentTarget as HTMLInputElement).blur(); }
  };

  const nav = (op: 'back' | 'forward' | 'reload', glyph: string) => (
    <button type="button" className="bp-nav" onClick={() => send(op)} disabled={!state.active}
      aria-label={t(`bp.${op}`)} title={t(`bp.${op}`)}>{glyph}</button>
  );

  return (
    <section className="bp-panel" data-state={state.active ? 'active' : 'idle'} aria-label={t('ws.tabBrowser')}>
      {state.active && (
        <>
          <header className="bp-head">
            {nav('back', '‹')}
            {nav('forward', '›')}
            {nav('reload', '↻')}
            <span className="bp-dot" data-loading={state.loading ? 'true' : 'false'} aria-hidden="true" />
            <input className="bp-address" type="text" value={editing ? draft : displayAddress(state.url)} placeholder={t('bp.addressHint')}
              aria-label={t('bp.address')} title={state.url} spellCheck={false}
              onFocus={e => { setDraft(state.url); setEditing(true); e.currentTarget.select(); }}
              onBlur={() => setEditing(false)}
              onChange={e => setDraft(e.target.value)}
              onKeyDown={keyOf('navigate', draft)} />
            {state.userControl && <span className="bp-control-t" role="status">{t('bp.userControl')}</span>}
            <button type="button" className="bp-stop" onClick={doStop} disabled={stop !== 'idle' || !running}>
              {STOP_ICON}<span>{t(stop === 'busy' || stop === 'sent' ? 'bp.stopping' : stop === 'stopped' ? 'bp.stopped' : 'bp.stop')}</span>
            </button>
          </header>
          {stopFailed && <p className="bp-stop-failed" role="alert">{t('bp.stopFailed')}</p>}
          <p className="bp-title" title={state.title}>
            <span className="bp-title-t">{state.title || state.url}</span>
            {state.loading && <span className="bp-loading" role="status">{t('bp.loading')}</span>}
          </p>
        </>
      )}
      <div className="bp-view" ref={hostRef} data-testid="bp-view">
        {!state.active && (
          <div className="bp-idle">
            <p className="bp-idle-title">{t('bp.idle')}</p>
            <div className="bp-open">
              <input className="bp-address" type="text" value={openUrl} placeholder={t('bp.addressHint')}
                aria-label={t('bp.address')} spellCheck={false}
                onChange={e => setOpenUrl(e.target.value)}
                onKeyDown={e => {
                  if (e.key !== 'Enter' || e.nativeEvent.isComposing || e.nativeEvent.keyCode === 229) return;
                  e.preventDefault();
                  void openFromIdle(openUrl);
                }} />
              <button type="button" className="bp-open-go"
                onClick={() => void openFromIdle(openUrl)}>{t('bp.open')}</button>
            </div>
          </div>
        )}
      </div>
      <p className="bp-note">{t('bp.stopNote')}</p>
    </section>
  );
}
