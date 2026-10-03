import React, { useEffect, useMemo, useRef, useState } from 'react';
import Head from 'next/head';

import { useLang, type Lang } from '../../lib/i18n';
import { displayName } from '../../lib/displayName';
import { isMacPlatform } from '../../lib/platformKeys';
import { useRecentWorkspaces, type RecentWorkspace } from '../../hooks/home/useRecentWorkspaces';
import type { UserData } from './types';
import { BrandLogo, MascotHead } from './BrandLogo';
import { GamachineFigure } from './GamachineMascot';
import { APP_VERSION } from './settings/SettingsPages';

type ToastKind = 'success' | 'error' | 'warning' | 'info';

export interface WorkspaceScreenProps {
  api: string;
  user: UserData | null;
  userName: string;
  /** Native folder dialog (optionally preselected); opens the choice and returns it. */
  onOpenFolder: (defaultPath?: string) => Promise<string | null>;
  onSelectWorkspace: (hostPath: string) => Promise<void> | void;
  onLogout: () => void;
  showToast: (message: string, type: ToastKind) => void;
}

export const UNITY_DOWNLOAD_URL = 'https://unity.com/download';

const Ic = ({ children, className = 'ic' }: { children: React.ReactNode; className?: string }) => (
  <svg className={className} viewBox="0 0 20 20" aria-hidden="true">{children}</svg>
);
const FolderIc = ({ className }: { className?: string }) => (
  <Ic className={className}><path d="M2.5 5.5a1 1 0 0 1 1-1h4l2 2h7a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1h-13a1 1 0 0 1-1-1z" /></Ic>
);

/** Fills `{key}` slots of a translated sentence with elements (code names, inline buttons). */
const rich = (text: string, parts: Record<string, React.ReactNode>) =>
  text.split(/(\{[a-zA-Z]+\})/).map((p, i) => {
    const k = /^\{([a-zA-Z]+)\}$/.exec(p);
    return <React.Fragment key={i}>{k && k[1] in parts ? parts[k[1]] : p}</React.Fragment>;
  });

/**
 * `last_accessed` is the backend's local wall-clock time ("YYYY-MM-DD HH:MM:SS"). Within a week it
 * reads as relative time in the active language; older entries show a short date.
 */
export function relativeWhen(stamp: string, lang: Lang, now: number = Date.now()): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/.exec(stamp || '');
  if (!m) return '';
  const d = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0));
  const sec = Math.min(0, Math.round((d.getTime() - now) / 1000));
  // Words only for "now" and "yesterday": Turkish 'auto' turns -2 days into the archaic "evvelsi gün".
  const words = new Intl.RelativeTimeFormat(lang, { numeric: 'auto' });
  const rtf = new Intl.RelativeTimeFormat(lang, { numeric: 'always' });
  if (sec > -60) return words.format(0, 'second');
  if (sec > -3600) return rtf.format(Math.round(sec / 60), 'minute');
  if (sec > -86400) return rtf.format(Math.round(sec / 3600), 'hour');
  const day = (t: Date) => new Date(t.getFullYear(), t.getMonth(), t.getDate()).getTime();
  const days = Math.round((day(d) - day(new Date(now))) / 86400000);
  if (days >= -1) return words.format(days, 'day');
  if (days >= -7) return rtf.format(days, 'day');
  const sameYear = d.getFullYear() === new Date(now).getFullYear();
  return new Intl.DateTimeFormat(lang, { day: 'numeric', month: 'short', ...(sameYear ? {} : { year: 'numeric' }) }).format(d);
}

const parentDir = (p: string): string | undefined => {
  const up = p.replace(/[\\/]+$/, '').replace(/[\\/][^\\/]*$/, '');
  if (!up) return p.startsWith('/') ? '/' : undefined;
  return /^[A-Za-z]:$/.test(up) ? `${up}\\` : up;
};

/**
 * The welcome screen / project picker (mockup round 12 screen 5, `section.welcome`), shown while no
 * workspace is open: the shell column on the left (lockup, waving mascot, greeting, open / new),
 * recent projects as cards on the right, and a first-launch variant when the list is empty.
 */
export const WorkspaceScreen = ({
  api, user, userName, onOpenFolder, onSelectWorkspace, onLogout, showToast,
}: WorkspaceScreenProps) => {
  const { t, lang } = useLang();
  const { items, load, refresh, remove } = useRecentWorkspaces(api, user);
  const [query, setQuery] = useState('');
  const [drag, setDrag] = useState<string | null>(null);
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const rootRef = useRef<HTMLElement>(null);
  const mac = useMemo(isMacPlatform, []);
  const name = displayName(userName);
  const first = load === 'ok' && items.length === 0;

  const openFolder = (defaultPath?: string) => { void onOpenFolder(defaultPath); };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((mac ? e.metaKey : e.ctrlKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === 'o') {
        e.preventDefault();
        void onOpenFolder();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [mac, onOpenFolder]);

  // The "more" menu closes on Escape (focus back on its button) or on a press anywhere else.
  useEffect(() => {
    if (!menuFor) return;
    const root = rootRef.current;
    const button = () => [...(root?.querySelectorAll<HTMLElement>('.wl-proj') ?? [])]
      .find(li => li.dataset.key === menuFor)?.querySelector<HTMLElement>('.wl-proj-more');
    root?.querySelector<HTMLElement>('.wl-menu button')?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { setMenuFor(null); button()?.focus(); } };
    const onDown = (e: MouseEvent) => {
      const el = e.target as Element | null;
      if (!el?.closest?.('.wl-menu, .wl-proj-more')) setMenuFor(null);
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('mousedown', onDown);
    return () => { document.removeEventListener('keydown', onKey); document.removeEventListener('mousedown', onDown); };
  }, [menuFor]);

  const q = query.trim().toLocaleLowerCase(lang);
  const shown = q
    ? items.filter(it => `${it.name}\n${it.hostPath ?? it.backendPath}`.toLocaleLowerCase(lang).includes(q))
    : items;

  const openItem = async (it: RecentWorkspace) => {
    if (!it.hostPath) return;
    // Not yet granted to this window: the native dialog, preselected on it, is what grants access.
    if (it.status === 'untrusted') await onOpenFolder(it.hostPath);
    else await onSelectWorkspace(it.hostPath);
  };
  const dropItem = async (it: RecentWorkspace) => {
    setMenuFor(null);
    if (!(await remove(it))) showToast(t('welcome.removeFailed'), 'error');
  };
  const locate = async (it: RecentWorkspace) => {
    const chosen = await onOpenFolder(it.hostPath ? parentDir(it.hostPath) : undefined);
    if (chosen) await remove(it);
  };

  const newProject = async () => {
    let opened = false;
    try {
      const res = await (window as any).ipc?.invoke('open-unity-hub');
      opened = !!res?.opened;
    } catch { opened = false; }
    if (!opened) {
      showToast(t('welcome.hubFailed'), 'info');
      window.open(UNITY_DOWNLOAD_URL, '_blank');
    }
  };

  const onDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDrag(null);
    const file = e.dataTransfer?.files?.[0];
    if (!file) return;
    const ipc = (window as any).ipc;
    if (typeof ipc?.registerDroppedFolder !== 'function') { showToast(t('welcome.dropFailed'), 'error'); return; }
    try {
      // The File itself goes to the bridge; the main process resolves and vets its path.
      const res = await ipc.registerDroppedFolder(file);
      if (res && typeof res.path === 'string' && res.path) { await onSelectWorkspace(res.path); return; }
      if (res?.error === 'not-a-folder') showToast(t('welcome.dropNotFolder'), 'warning');
      else if (res?.error === 'not-a-unity-project') showToast(t('welcome.dropNotUnity'), 'warning');
      else showToast(t('welcome.dropFailed'), 'error');
    } catch {
      showToast(t('welcome.dropFailed'), 'error');
    }
  };
  const dropProps = (zone: string) => ({
    className: `wl-drop${zone === 'lg' ? ' wl-drop-lg' : ''}${drag === zone ? ' is-drag' : ''}`,
    'data-testid': `welcome-drop-${zone}`,
    onDragOver: (e: React.DragEvent) => { e.preventDefault(); if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy'; if (drag !== zone) setDrag(zone); },
    onDragLeave: (e: React.DragEvent) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDrag(null); },
    onDrop,
    onClick: () => openFolder(),
  });
  const pick = (label: string) => (
    <button type="button" className="set-link" onClick={e => { e.stopPropagation(); openFolder(); }}>{label}</button>
  );
  const code = { assets: <code>Assets</code>, settings: <code>ProjectSettings</code> };

  const card = (it: RecentWorkspace, n: number) => {
    const missing = it.status === 'missing';
    const last = it === items[0];
    const path = it.hostPath ?? it.backendPath;
    const key = it.backendPath;
    return (
      <li key={key} className={`wl-proj${last ? ' is-last' : ''}${missing ? ' is-missing' : ''}`}
        data-slot={String(n + 1).padStart(2, '0')} data-key={key} data-testid="welcome-card">
        {!missing && (
          <button type="button" className="wl-proj-open" aria-label={t('welcome.openProject', { name: it.name })} onClick={() => openItem(it)} />
        )}
        <span className="wl-thumb" aria-hidden="true">
          <span className="wl-thumb-k">{[...it.name][0] ?? '?'}</span>
          <Ic><path d="M10 2.5l6.5 3.75v7.5L10 17.5l-6.5-3.75v-7.5zM3.5 6.25L10 10l6.5-3.75M10 10v7.5" /></Ic>
        </span>
        {last && (
          <span className="wl-proj-tag"><span className="lex"><span className="lex-d">{t('welcome.lastTag')}</span><span className="lex-q">{t('welcome.lastTagQuest')}</span></span></span>
        )}
        <span className="wl-proj-name">{it.name}</span>
        <span className="wl-proj-path" title={path}>{path}</span>
        <span className="wl-proj-meta">
          {missing ? (
            <span className="wl-gone"><Ic className="ic ic-sm"><path d="M10 3l7.5 13.5h-15zM10 8.5v3.5M10 14.2v.1" /></Ic>{t('welcome.gone')}</span>
          ) : (
            <>
              {relativeWhen(it.lastAccessed, lang) && (
                <span className="wl-when"><Ic className="ic ic-sm"><circle cx="10" cy="10" r="7" /><path d="M10 6v4l2.5 2" /></Ic>{relativeWhen(it.lastAccessed, lang)}</span>
              )}
              {it.unityVersion
                ? <span className="wl-ver"><span lang="en">Unity</span> <span className="num">{it.unityVersion}</span></span>
                : <span className="wl-ver wl-ver-none">{t('welcome.versionUnknown')}</span>}
            </>
          )}
        </span>
        <span className="wl-proj-foot">
          {missing ? (
            <>
              <button type="button" className="set-link" onClick={() => locate(it)}>{t('welcome.locate')}</button>
              <button type="button" className="set-link" onClick={() => dropItem(it)}>{t('welcome.remove')}</button>
            </>
          ) : it.chatCount > 0 && (
            <span className="wl-chats">{t(it.chatCount === 1 ? 'welcome.chatOne' : 'welcome.chats', { n: it.chatCount })}</span>
          )}
        </span>
        {!missing && (
          <button type="button" className="wl-proj-more" aria-label={t('welcome.more', { name: it.name })}
            aria-haspopup="menu" aria-expanded={menuFor === key}
            onClick={() => setMenuFor(menuFor === key ? null : key)}>
            <Ic><circle cx="5" cy="10" r=".9" /><circle cx="10" cy="10" r=".9" /><circle cx="15" cy="10" r=".9" /></Ic>
          </button>
        )}
        {menuFor === key && (
          <div className="wl-menu" role="menu">
            <button type="button" role="menuitem" onClick={() => dropItem(it)}>{t('welcome.remove')}</button>
          </div>
        )}
      </li>
    );
  };

  return (
    <section ref={rootRef} className="welcome" aria-label={t('welcome.aria')} data-testid="welcome"
      data-wl-first={first ? '' : undefined}
      // A drop outside the zones must not navigate the window to the dropped file.
      onDragOver={e => e.preventDefault()} onDrop={e => e.preventDefault()}>
      <Head><title>Gamachine | Workspace</title></Head>
      <div className="wl-side shell">
        <div className="tex tex-shell" aria-hidden="true" />
        <div className="brand wl-brand"><BrandLogo /></div>
        <div className="wl-hero">
          <div className="wl-figure" aria-hidden="true">
            <GamachineFigure pose="wave" />
            <MascotHead className="mascot wl-head" />
          </div>
          <p className="wl-kicker"><span className="lex">
            <span className="lex-d">{t('welcome.kicker')}</span>
            <span className="lex-q">{t('welcome.kickerQuest')}</span>
            <span className="lex-p">{t('welcome.kickerNote')}</span>
            <span className="lex-w">{t('welcome.kickerShop')}</span>
          </span></p>
          {/* No name yet (local login): "Welcome," with nothing after it read as a broken sentence. */}
          <h1 className="wl-title">
            {t('workspace.welcomeNoName')}
            {name && <span className="wl-name-part">, <span className="gm-name-slot">{name}</span></span>}
          </h1>
          <p className="wl-sub">{t(first ? 'welcome.subFirst' : 'welcome.sub')}</p>
          <div className="wl-actions">
            <button type="button" className="btn btn-primary wl-open" onClick={() => openFolder()}>
              <FolderIc />{t('welcome.open')}<kbd aria-hidden="true">{mac ? '⌘O' : 'Ctrl O'}</kbd>
            </button>
            <button type="button" className="btn btn-ghost wl-new" aria-describedby="wl-new-hint" onClick={newProject}>
              <Ic><path d="M10 4v12M4 10h12" /></Ic>{t('welcome.new')}
              <Ic className="ic ic-sm wl-new-out"><path d="M8 4.5H4.5v11h11V12M11 4.5h4.5V9M15.5 4.5L9 11" /></Ic>
            </button>
          </div>
          <p className="wl-new-hint" id="wl-new-hint">{t('welcome.newHint')}</p>
        </div>
        <footer className="wl-foot">
          <span className="set-avatar wl-avatar" aria-hidden="true">
            {name
              ? [...name][0].toLocaleUpperCase(lang)
              : <Ic className="ic ic-sm"><circle cx="10" cy="7" r="3" /><path d="M4.5 16.5c.8-3 3-4.5 5.5-4.5s4.7 1.5 5.5 4.5" /></Ic>}
          </span>
          <span className="wl-me"><span className="wl-me-name">{name || t('welcome.account')}</span></span>
          <button type="button" className="wl-foot-btn wl-logout" onClick={onLogout}>
            <Ic><path d="M8 4.5H4.5v11H8M12 6.5L15.5 10 12 13.5M15.5 10H8" /></Ic><span>{t('welcome.logout')}</span>
          </button>
        </footer>
      </div>

      <div className="wl-main paper">
        <div className="wl-col">
          <header className="wl-main-head">
            <h2 className="wl-h"><span className="lex">
              <span className="lex-d">{t('welcome.recent')}</span>
              <span className="lex-q">{t('welcome.recentQuest')}</span>
              <span className="lex-p">{t('welcome.recentNote')}</span>
              <span className="lex-w">{t('welcome.recentShop')}</span>
            </span></h2>
            <span className="wl-count num" data-testid="welcome-count">{items.length}</span>
            <label className="wl-search">
              <Ic className="ic ic-sm"><circle cx="9" cy="9" r="5" /><path d="M13 13l3.5 3.5" /></Ic>
              <input type="text" value={query} onChange={e => setQuery(e.target.value)}
                placeholder={t('welcome.search')} aria-label={t('welcome.search')} autoComplete="off" />
            </label>
          </header>

          {load === 'failed' && (
            <p className="wl-note" role="status">{t('welcome.loadFailed')}
              <button type="button" className="set-link" onClick={() => refresh()}>{t('welcome.retry')}</button></p>
          )}
          {q && !shown.length && <p className="wl-note" role="status">{t('welcome.searchNone')}</p>}

          <ul className="wl-projects" aria-busy={load === 'loading'}>
            {shown.map(card)}
            <li {...dropProps('list')}>
              <FolderIc />
              <span className="wl-drop-t">{t('welcome.dropTitle')}</span>
              <span className="wl-drop-s">{rich(t('welcome.dropText'), { ...code, pick: pick(t('welcome.dropPick')) })}</span>
            </li>
          </ul>

          {first && (
            <div className="wl-first">
              <ol className="wl-steps">
                <li><span className="wl-step-n num">1</span><span className="wl-step-t"><b>{t('welcome.step1Title')}</b> {rich(t('welcome.step1'), code)}</span></li>
                <li><span className="wl-step-n num">2</span><span className="wl-step-t"><b>{t('welcome.step2Title')}</b> {t('welcome.step2')}</span></li>
                <li><span className="wl-step-n num">3</span><span className="wl-step-t"><b>{t('welcome.step3Title')}</b> {t('welcome.step3')}</span></li>
              </ol>
              <div {...dropProps('lg')}>
                <FolderIc />
                <span className="wl-drop-t">{t('welcome.dropLgTitle')}</span>
                <span className="wl-drop-s">{rich(t('welcome.dropLgText'), { pick: pick(t('welcome.dropLgPick')), new: <b>{t('welcome.new')}</b> })}</span>
              </div>
            </div>
          )}
        </div>
        {APP_VERSION && <p className="wl-ver-foot">Gamachine <span className="num">{APP_VERSION}</span></p>}
      </div>
    </section>
  );
};
