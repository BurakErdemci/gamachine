import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useLang } from '../../lib/i18n';

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
export function BrowserPanel({ visible, conversationId }: { visible: boolean; conversationId: number | null }) {
  const { t } = useLang();
  const state = useBrowserLive(conversationId);
  const hostRef = useRef<HTMLDivElement>(null);
  const [covered, setCovered] = useState(false);
  const [stopping, setStopping] = useState(false);
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
  useEffect(() => { setStopping(false); setEditing(false); }, [state.active, conversationId]);
  useEffect(() => { setOpenUrl(''); }, [conversationId]);

  const send = (op: string, extra: Record<string, unknown> = {}) => {
    if (conversationId != null) void invoke({ op, conversationId, ...extra });
  };

  const stop = async () => {
    if (stopping || conversationId == null) return;
    setStopping(true);
    await invoke({ op: 'halt', conversationId });
    setStopping(false);
  };

  const submit = (op: 'navigate' | 'open', url: string) => {
    const target = url.trim();
    if (!target) return;
    send(op, { url: target });
  };

  const keyOf = (op: 'navigate' | 'open', url: string) => (e: React.KeyboardEvent) => {
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
            <input className="bp-address" type="text" value={editing ? draft : state.url} placeholder="https://"
              aria-label={t('bp.address')} title={state.url} spellCheck={false}
              onFocus={e => { setDraft(state.url); setEditing(true); e.currentTarget.select(); }}
              onBlur={() => setEditing(false)}
              onChange={e => setDraft(e.target.value)}
              onKeyDown={keyOf('navigate', draft)} />
            <button type="button" className="bp-stop" onClick={stop} disabled={stopping}>
              {STOP_ICON}<span>{t(stopping ? 'bp.stopping' : 'bp.stop')}</span>
            </button>
          </header>
          <div className="bp-control" data-user={state.userControl ? 'true' : 'false'}>
            {state.userControl ? (
              <>
                <span className="bp-control-t" role="status">{t('bp.userControl')}</span>
                <button type="button" className="bp-take bp-take-on" onClick={() => send('release')}>{t('bp.release')}</button>
              </>
            ) : (
              <button type="button" className="bp-take" onClick={() => send('take-over')}>{t('bp.takeOver')}</button>
            )}
          </div>
          <p className="bp-title" title={state.title}>
            <span className="bp-title-t">{state.title || state.url}</span>
            {state.loading && <span className="bp-loading" role="status">{t('bp.loading')}</span>}
          </p>
        </>
      )}
      <div className="bp-view" ref={hostRef} data-testid="bp-view">
        {!state.active && (
          <div className="bp-idle">
            <p className="bp-idle-title">{t(conversationId == null ? 'bp.noChat' : 'bp.idle')}</p>
            <div className="bp-open">
              <input className="bp-address" type="text" value={openUrl} placeholder="https://"
                aria-label={t('bp.address')} spellCheck={false} disabled={conversationId == null}
                onChange={e => setOpenUrl(e.target.value)}
                onKeyDown={keyOf('open', openUrl)} />
              <button type="button" className="bp-open-go" disabled={conversationId == null}
                onClick={() => submit('open', openUrl)}>{t('bp.open')}</button>
            </div>
          </div>
        )}
      </div>
      <p className="bp-note">{t('bp.stopNote')}</p>
    </section>
  );
}
