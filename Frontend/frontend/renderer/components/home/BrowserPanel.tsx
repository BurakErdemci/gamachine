import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useLang } from '../../lib/i18n';
import { addressToUrl, displayAddress } from '../../lib/browserAddress';
import { cleanHistory, isMacPlatform, suggest, suggestedSites, tabShortcutOf, type HistoryItem } from '../../lib/browserSuggest';
import { BrowserTabs, NewTabGrid, SuggestionList, type BrowserTab } from './BrowserTabs';

/** Main-process event and invoke channel for the agent browser (docs/agent-browser.md). */
export const BROWSER_CHANNEL = 'browser-live';

export interface BrowserState {
  active: boolean;
  conversationId: number | null;
  url: string;
  title: string;
  loading: boolean;
  /** The chat's agent tab is taken over by the user. */
  userControl: boolean;
  tabs: BrowserTab[];
  activeTabId: number | null;
}

const EMPTY: BrowserState = { active: false, conversationId: null, url: '', title: '', loading: false, userControl: false, tabs: [], activeTabId: null };

function toTabs(raw: unknown): BrowserTab[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((tab: any): BrowserTab[] => (tab && Number.isInteger(tab.id) ? [{
    id: tab.id,
    url: typeof tab.url === 'string' ? tab.url : '',
    title: typeof tab.title === 'string' ? tab.title : '',
    loading: tab.loading === true,
    favicon: typeof tab.favicon === 'string' ? tab.favicon : '',
    agent: tab.agent === true,
  }] : []));
}

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
    tabs: toTabs(raw.tabs),
    activeTabId: Number.isInteger(raw.activeTabId) ? raw.activeTabId : null,
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
    Promise.resolve(ipc.invoke?.(BROWSER_CHANNEL, { op: 'panel-status', conversationId }))
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
const OVERLAY_SELECTOR = '[role="dialog"], [role="alertdialog"], [role="menu"], [role="listbox"], .approval-slot, .approval';

const intersects = (a: DOMRect, b: DOMRect) =>
  a.width > 0 && a.height > 0 && b.width > 0 && b.height > 0
  && a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;

const STOP_ICON = <svg className="bp-stop-ic" viewBox="0 0 12 12" aria-hidden="true"><rect x="2" y="2" width="8" height="8" rx="1" /></svg>;

/**
 * The right panel's "Tarayıcı" tab. The page itself is a native view that main lays over the
 * placeholder, so this component only measures the placeholder and tells main when the view may
 * be shown: never while the tab is hidden, something in the DOM overlaps it or the selected tab
 * has no page yet (the Suggested grid shows instead). It shows the tabs of the chat open in the
 * UI (`conversationId`); every op names that chat.
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
  // The omnibox: rows appear once the user types, over the remembered pages.
  const [typed, setTyped] = useState(false);
  const [row, setRow] = useState(-1);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const mac = isMacPlatform();
  const lastBounds = useRef('');
  const chatId = useRef(conversationId);
  chatId.current = conversationId;
  const prevRunning = useRef(running);
  const runningNow = useRef(running);
  const stopGen = useRef(0);
  const opening = useRef(false);

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

  const blank = state.active && (state.url === '' || state.url === 'about:blank');
  const show = visible && state.active && !covered && !blank && conversationId != null;
  useEffect(() => {
    if (conversationId != null) void invoke({ op: 'visible', conversationId, visible: show });
  }, [show, conversationId]);
  // Leaving a chat (or the panel) hides that chat's view.
  useEffect(() => () => {
    if (conversationId != null) void invoke({ op: 'visible', conversationId, visible: false });
  }, [conversationId]);
  useEffect(() => { stopGen.current += 1; setStop('idle'); setStopFailed(false); setEditing(false); }, [state.active, conversationId]);
  // A new turn starts: whatever the last stop was doing, the button is armed again and results still in flight are dropped.
  useEffect(() => {
    if (running && !prevRunning.current) { stopGen.current += 1; setStop('idle'); setStopFailed(false); }
    prevRunning.current = running;
    runningNow.current = running;
  }, [running]);
  useEffect(() => {
    if (stop === 'sent' && !running) setStop('stopped');
  }, [stop, running]);
  useEffect(() => { setOpenUrl(''); }, [conversationId]);

  const loadHistory = useCallback(() => {
    void invoke({ op: 'history' }).then((r: any) => setHistory(cleanHistory(r?.entries)));
  }, []);
  const emptyView = !state.active || blank;
  useEffect(() => { if (visible && emptyView) loadHistory(); }, [visible, emptyView, loadHistory]);

  const rows = useMemo(() => (editing && typed ? suggest(draft, history) : []), [editing, typed, draft, history]);
  useEffect(() => { setRow(-1); }, [draft]);

  const send = (op: string, extra: Record<string, unknown> = {}) => {
    if (conversationId != null) void invoke({ op, conversationId, ...extra });
  };

  // The page stops at once (halt); the chat's Stop ends the turn itself.
  const doStop = async () => {
    if (stop !== 'idle' || !running || conversationId == null) return;
    const id = conversationId;
    const gen = stopGen.current;
    setStop('busy');
    setStopFailed(false);
    try {
      // A refused halt is not a failure: the chat's Stop halts the browser tools too.
      await Promise.all([invoke({ op: 'halt', conversationId: id }), onStop()]);
      if (stopGen.current !== gen) return;
      setStop(runningNow.current ? 'sent' : 'stopped');
    } catch {
      if (stopGen.current !== gen) return;
      setStopFailed(true);
      setStop('idle');
    }
  };

  // A new tab of the user's own; without an address it is a blank tab showing the Suggested grid.
  const openTab = async (target?: string) => {
    if (opening.current) return;
    opening.current = true;
    try {
      const id = conversationId ?? await onNeedChat();
      if (id) void invoke(target ? { op: 'open', conversationId: id, url: target } : { op: 'open', conversationId: id });
    } finally {
      opening.current = false;
    }
  };
  const openFromIdle = async (text: string) => {
    const target = addressToUrl(text);
    if (target) await openTab(target);
  };

  const pick = (url: string) => {
    send('navigate', { url });
    setEditing(false);
    setTyped(false);
    (document.activeElement as HTMLElement | null)?.blur?.();
  };

  const onShortcut = (e: React.KeyboardEvent) => {
    const shortcut = tabShortcutOf(e, mac);
    if (!shortcut || e.nativeEvent.isComposing) return;
    const { tabs, activeTabId } = state;
    let handled = true;
    if (shortcut.action === 'new') void openTab();
    else if (shortcut.action === 'reopen') send('reopen-tab');
    else if (shortcut.action === 'close' && tabs.length > 0 && activeTabId != null) send('close-tab', { tabId: activeTabId });
    else if (shortcut.action === 'select' && tabs.length > 0) {
      const target = shortcut.index === 9 ? tabs[tabs.length - 1] : tabs[shortcut.index - 1];
      if (target) send('select', { tabId: target.id });
    } else handled = false;
    if (handled) e.preventDefault();
  };

  const submit = (text: string) => {
    const target = addressToUrl(text);
    if (!target) return;
    send('navigate', { url: target });
  };

  const keyOf = (url: string) => (e: React.KeyboardEvent) => {
    if (e.nativeEvent.isComposing || e.nativeEvent.keyCode === 229) return;
    if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && rows.length > 0) {
      e.preventDefault();
      setRow(r => (e.key === 'ArrowDown' ? (r + 1) % rows.length : (r <= 0 ? rows.length : r) - 1));
      return;
    }
    if (e.key === 'Escape' && editing) { e.preventDefault(); setEditing(false); setTyped(false); (e.currentTarget as HTMLInputElement).blur(); return; }
    // IME Enter commits the composition first.
    if (e.key !== 'Enter') return;
    e.preventDefault();
    if (rows[row]) { pick(rows[row].url); return; }
    submit(url);
    setEditing(false);
    setTyped(false);
    (e.currentTarget as HTMLInputElement).blur();
  };

  const nav = (op: 'back' | 'forward' | 'reload', glyph: string) => (
    <button type="button" className="bp-nav" onClick={() => send(op)} disabled={!state.active}
      aria-label={t(`bp.${op}`)} title={t(`bp.${op}`)}>{glyph}</button>
  );

  const agentShown = state.tabs.find(tab => tab.id === state.activeTabId)?.agent === true;
  const listId = 'bp-suggest';

  return (
    <section className="bp-panel" data-state={state.active ? 'active' : 'idle'} aria-label={t('ws.tabBrowser')} onKeyDown={onShortcut}>
      {state.tabs.length > 0 && (
        <BrowserTabs tabs={state.tabs} activeId={state.activeTabId} mac={mac}
          onSelect={tabId => send('select', { tabId })} onClose={tabId => send('close-tab', { tabId })} onNew={() => void openTab()} />
      )}
      {state.active && (
        <header className="bp-head">
          {nav('back', '‹')}
          {nav('forward', '›')}
          {nav('reload', '↻')}
          <div className="bp-omni" data-agent={agentShown ? 'true' : 'false'}>
            <span className="bp-dot" data-loading={state.loading ? 'true' : 'false'} aria-hidden="true" />
            <input className="bp-address" type="text" value={editing ? draft : displayAddress(state.url)} placeholder={t('bp.addressHint')}
              role="combobox" aria-expanded={rows.length > 0} aria-controls={listId} aria-autocomplete="list"
              aria-activedescendant={rows[row] ? `${listId}-${row}` : undefined}
              aria-label={t('bp.address')} title={state.url} spellCheck={false}
              onFocus={e => { setDraft(state.url); setEditing(true); setTyped(false); loadHistory(); e.currentTarget.select(); }}
              onBlur={() => { setEditing(false); setTyped(false); }}
              onChange={e => { setDraft(e.target.value); setTyped(true); }}
              onKeyDown={keyOf(draft)} />
            {state.userControl && agentShown && (
              <span className="bp-control-t" role="status" title={t('bp.userControl')}>{t('bp.userControlShort')}</span>
            )}
            {state.loading && <span className="bp-sr" role="status">{t('bp.loading')}</span>}
            {rows.length > 0 && <SuggestionList id={listId} items={rows} active={row} onPick={pick} />}
          </div>
          <button type="button" className="bp-stop" onClick={doStop} disabled={stop !== 'idle' || !running} title={t('bp.stopNote')}>
            {STOP_ICON}<span>{t(stop === 'busy' || stop === 'sent' ? 'bp.stopping' : stop === 'stopped' ? 'bp.stopped' : 'bp.stop')}</span>
          </button>
        </header>
      )}
      {stopFailed && <p className="bp-stop-failed" role="alert">{t('bp.stopFailed')}</p>}
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
        {emptyView && (
          <NewTabGrid sites={suggestedSites(history)} onPick={url => (state.active ? send('navigate', { url }) : void openTab(url))} />
        )}
      </div>
    </section>
  );
}
