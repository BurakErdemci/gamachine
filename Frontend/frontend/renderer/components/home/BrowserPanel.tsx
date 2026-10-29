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
}

const EMPTY: BrowserState = { active: false, conversationId: null, url: '', title: '', loading: false };

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
  };
}

/** One status read on mount, then the event stream; a late status never rolls back a newer event. */
export function useBrowserLive(): BrowserState {
  const [state, setState] = useState<BrowserState>(EMPTY);
  useEffect(() => {
    const ipc = ipcOf();
    if (!ipc) return;
    let alive = true;
    let heard = false;
    Promise.resolve(ipc.invoke?.(BROWSER_CHANNEL, { op: 'status' }))
      .then((raw: any) => { if (alive && !heard && raw && !raw.error) setState(toState(raw)); })
      .catch(() => {});
    const off = ipc.on?.(BROWSER_CHANNEL, (raw: any) => {
      heard = true;
      if (alive) setState(toState(raw));
    });
    return () => { alive = false; if (typeof off === 'function') off(); };
  }, []);
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
 * be shown: never while the tab is hidden or something in the DOM overlaps it.
 */
export function BrowserPanel({ visible }: { visible: boolean }) {
  const { t } = useLang();
  const state = useBrowserLive();
  const hostRef = useRef<HTMLDivElement>(null);
  const [covered, setCovered] = useState(false);
  const [stopping, setStopping] = useState(false);
  const lastBounds = useRef('');

  const measure = useCallback(() => {
    const el = hostRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const bounds = { x: Math.round(r.left), y: Math.round(r.top), width: Math.round(r.width), height: Math.round(r.height) };
    const key = JSON.stringify(bounds);
    if (key !== lastBounds.current) {
      lastBounds.current = key;
      void invoke({ op: 'set-bounds', bounds });
    }
    const hit = Array.from(document.querySelectorAll(OVERLAY_SELECTOR))
      .some(o => !el.contains(o) && intersects(r, o.getBoundingClientRect()));
    setCovered(hit);
  }, []);

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

  const show = visible && state.active && !covered;
  useEffect(() => { void invoke({ op: 'visible', visible: show }); }, [show]);
  useEffect(() => () => { void invoke({ op: 'visible', visible: false }); }, []);
  useEffect(() => { setStopping(false); }, [state.active, state.conversationId]);

  const stop = async () => {
    if (stopping) return;
    setStopping(true);
    await invoke({ op: 'halt' });
    setStopping(false);
  };

  return (
    <section className="bp-panel" data-state={state.active ? 'active' : 'idle'} aria-label={t('ws.tabBrowser')}>
      <header className="bp-head">
        <span className="bp-dot" data-loading={state.loading ? 'true' : 'false'} aria-hidden="true" />
        <input className="bp-address" type="text" readOnly value={state.url} placeholder="https://"
          aria-label={t('bp.address')} title={state.url} />
        <button type="button" className="bp-stop" onClick={stop} disabled={!state.active || stopping}>
          {STOP_ICON}<span>{t(stopping ? 'bp.stopping' : 'bp.stop')}</span>
        </button>
      </header>
      {state.active && (
        <p className="bp-title" title={state.title}>
          <span className="bp-title-t">{state.title || state.url}</span>
          {state.loading && <span className="bp-loading" role="status">{t('bp.loading')}</span>}
        </p>
      )}
      <div className="bp-view" ref={hostRef} data-testid="bp-view">
        {!state.active && (
          <div className="bp-idle">
            <p className="bp-idle-title">{t('bp.idle')}</p>
          </div>
        )}
      </div>
      <p className="bp-note">{t('bp.stopNote')}</p>
    </section>
  );
}
