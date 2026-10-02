import React, { useEffect, useMemo, useRef, useState } from 'react';

import { useLang, type Lang, type TKey } from '../../../lib/i18n';
import { displayName } from '../../../lib/displayName';
import type { RemoteStatus } from '../../../lib/remoteControl';
import type { UsageLimits } from '../../../lib/usageLimits';
import type { AvailableModelsState, UnityMCPStatus } from '../../../hooks/home/useAIConfig';
import type { AIConfig, GenerationMode, UserData } from '../types';
import { RemoteControlSection } from '../RemoteControlSection';
import { ModelsPage } from './ModelsPage';
import { APP_VERSION, AccountPage, AppearancePage, ApprovalPage, GeneralPage, UnityPage } from './SettingsPages';
import { SetPageHead } from './controls';
import { SETTINGS_PAGES, type SettingsPage } from './pages';

type Toast = (msg: string, type: 'success' | 'error' | 'warning' | 'info') => void;

export interface SettingsScreenProps {
  open: boolean;
  /** The page on screen; uncontrolled (starts on Genel) when omitted. */
  page?: SettingsPage;
  onPageChange?: (page: SettingsPage) => void;
  aiConfig: AIConfig;
  availableModels?: AvailableModelsState;
  providersWithKeys: string[];
  onClose: () => void;
  onLogout: () => void;
  onDeleteKey: (provider: string) => Promise<void>;
  defaultModel?: { provider_type: string; model_name: string } | null;
  onSaveDefaultModel?: (provider: string, model: string) => Promise<boolean>;
  onSaveApiKey?: (provider: string, key: string) => Promise<boolean>;
  onUseCustomModel?: (model: string) => Promise<boolean>;
  unityMcpStatus: UnityMCPStatus;
  unityMcpToggling: boolean;
  onToggleUnityMcp: () => void;
  unityProjectName?: string | null;
  lang: Lang;
  onLangChange: (l: Lang) => void;
  approvalMode?: GenerationMode;
  onApprovalModeChange?: (mode: GenerationMode) => void;
  autoTitles?: boolean;
  autoTitlesSaving?: boolean;
  onToggleAutoTitles?: () => void;
  dictationAutoLang?: boolean;
  dictationAutoLangSaving?: boolean;
  onToggleDictationAutoLang?: () => void;
  onRemoteStatus?: (status: RemoteStatus) => void;
  usage?: UsageLimits | null;
  user?: UserData | null;
  API?: string;
  http?: { get: (...a: any[]) => Promise<any>; post: (...a: any[]) => Promise<any> };
  showToast?: Toast;
}

const NAV_ICON: Record<SettingsPage, React.ReactNode> = {
  genel: <><path d="M4 6h8M15 6h1M4 14h2M9 14h7" /><circle cx="13.5" cy="6" r="1.6" /><circle cx="7.5" cy="14" r="1.6" /></>,
  modeller: <><rect x="3" y="3" width="5.5" height="5.5" rx="1" /><rect x="11.5" y="3" width="5.5" height="5.5" rx="1" /><rect x="3" y="11.5" width="5.5" height="5.5" rx="1" /><path d="M14.25 11.5v5.5M11.5 14.25H17" /></>,
  gorunum: <><path d="M10 3a7 7 0 100 14c1 0 1.4-.7 1.1-1.5-.4-1 .2-2 1.3-2H14a3 3 0 003-3C17 6 14 3 10 3z" /><circle cx="6.8" cy="9" r=".9" /><circle cx="9.5" cy="6.3" r=".9" /><circle cx="13" cy="7.2" r=".9" /></>,
  unity: <><path d="M10 2.5l6.5 3.75v7.5L10 17.5l-6.5-3.75v-7.5z" /><path d="M10 10l6.5-3.75M10 10v7.5M10 10L3.5 6.25" /></>,
  onay: <><path d="M10 2.8l5.6 2.2v4.4c0 3.6-2.4 6.2-5.6 7.6-3.2-1.4-5.6-4-5.6-7.6V5z" /><path d="M7.6 10l1.7 1.7 3.2-3.4" /></>,
  uzak: <><rect x="6" y="2.5" width="8" height="15" rx="1.6" /><path d="M9 15h2" /></>,
  hesap: <><circle cx="10" cy="7" r="3" /><path d="M4 17c.8-3.2 3.3-5 6-5s5.2 1.8 6 5" /></>,
};

/**
 * What the rail's search looks through: each page's own title and the names of its rows, in
 * both languages, so "tema" and "theme" both land on Görünüm.
 */
const SEARCH_KEYS: Record<SettingsPage, TKey[]> = {
  genel: ['set.nav.genel', 'set.uiLang', 'settings.language', 'settings.autoTitles', 'settings.dictationAutoLang', 'set.group.dictation'],
  modeller: ['set.nav.modeller', 'set.defaultModel', 'set.group.subs', 'set.group.keys', 'set.customModel', 'set.group.local'],
  gorunum: ['set.nav.gorunum', 'settings.appearance.theme', 'settings.appearance.readingFont', 'settings.appearance.codeFont', 'settings.appearance.textSize', 'settings.appearance.intro'],
  unity: ['set.unity.conn', 'set.unity.trouble'],
  onay: ['set.nav.onay', 'set.mode.stepTitle', 'set.mode.balancedTitle', 'set.mode.autoTitle'],
  uzak: ['set.nav.uzak', 'set.uzak.phones', 'set.uzak.pairNew', 'remote.relay', 'remote.keepAwake', 'remote.forget'],
  hesap: ['set.nav.hesap', 'set.hesap.logout', 'set.hesap.version'],
};
const EXTRA_TERMS: Partial<Record<SettingsPage, string[]>> = {
  unity: ['unity', 'mcp'], modeller: ['api', 'claude', 'codex', 'antigravity', 'ollama', 'openrouter'], uzak: ['qr', 'relay'],
};

/**
 * The settings screen (mockup round 11, `?screen=ayarlar&set=<page>`): a category rail on the
 * shell and one page at a time on paper. It replaces the "AI Yapılandırması" modal. Every
 * control applies the moment it changes and flashes "Saved"; only typed values (an API key, a
 * custom model id, a relay address) carry their own small button. Esc returns to the chat.
 */
export const SettingsScreen = (props: SettingsScreenProps) => {
  const {
    open, page: pageProp, onPageChange, onClose, lang, user, usage = null,
  } = props;
  const { t } = useLang();
  const [ownPage, setOwnPage] = useState<SettingsPage>('genel');
  const page = pageProp ?? ownPage;
  const go = (p: SettingsPage) => { setOwnPage(p); onPageChange?.(p); };

  const [fresh, setFresh] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const saved = () => {
    setFresh(true);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setFresh(false), 1600);
  };
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  const mainRef = useRef<HTMLDivElement>(null);
  useEffect(() => { if (mainRef.current) mainRef.current.scrollTop = 0; }, [page]);

  // Esc returns to the chat; a confirm dialog or an open select handles its own Esc first.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return;
      if (document.querySelector('[data-confirm-dialog], [role="alertdialog"]')) return;
      onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  const [query, setQuery] = useState('');
  const q = query.trim().toLocaleLowerCase(lang === 'tr' ? 'tr-TR' : 'en-GB');
  const matches = useMemo(() => {
    if (!q) return SETTINGS_PAGES as readonly SettingsPage[];
    return SETTINGS_PAGES.filter(p => {
      const words = [
        ...SEARCH_KEYS[p].flatMap(k => [t(k), k]),
        ...(EXTRA_TERMS[p] || []),
      ].join(' ').toLocaleLowerCase(lang === 'tr' ? 'tr-TR' : 'en-GB');
      return words.includes(q);
    });
  }, [q, t, lang]);

  if (!open) return null;

  const navItem = (p: SettingsPage) => (
    <li key={p}>
      <button type="button" className="set-nav-i" data-set-link={p} aria-current={page === p ? 'page' : undefined}
        data-current={page === p || undefined} onClick={() => go(p)}>
        <svg className="ic" viewBox="0 0 20 20" aria-hidden="true">{NAV_ICON[p]}</svg>
        <span lang={p === 'unity' ? 'en' : undefined}>{p === 'unity' ? 'Unity' : t(`set.nav.${p}` as TKey)}</span>
      </button>
    </li>
  );
  const name = displayName(user?.name) || t('set.me.anon');

  return (
    <section className="settings" aria-label={t('set.title')} data-set={page} data-testid="settings-screen">
      <nav className="set-rail shell" aria-label={t('set.railLabel')}>
        <div className="tex tex-shell" aria-hidden="true" />
        <button type="button" className="set-back" data-testid="settings-back" onClick={onClose}>
          <svg className="ic" viewBox="0 0 20 20" aria-hidden="true"><path d="M16 10H4.5M9 5l-5 5 5 5" /></svg>
          <span>{t('set.back')}</span>
          <kbd>Esc</kbd>
        </button>
        <p className="set-rail-title">{t('set.title')}</p>
        <label className="set-find">
          <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><circle cx="8.5" cy="8.5" r="5" /><path d="M12.2 12.2L16.5 16.5" /></svg>
          <input
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && matches[0]) go(matches[0]); }}
            placeholder={t('set.search')}
            aria-label={t('set.search')}
            data-testid="settings-search"
          />
        </label>
        <ul className="set-nav">
          {matches.filter(p => p !== 'hesap').map(navItem)}
          {matches.length === 0 && <li className="set-nav-none">{t('set.noMatch')}</li>}
        </ul>
        <div className="set-rail-foot">
          <button type="button" className="set-nav-i set-me" data-set-link="hesap" aria-current={page === 'hesap' ? 'page' : undefined}
            data-current={page === 'hesap' || undefined} onClick={() => go('hesap')}>
            <span className="set-avatar" aria-hidden="true">{name.charAt(0).toUpperCase()}</span>
            <span className="set-me-t"><span className="set-me-name">{name}</span><span className="set-me-sub">{t('set.me.sub')}</span></span>
          </button>
          {APP_VERSION && <p className="set-ver">Gamachine <span className="num">{APP_VERSION}</span></p>}
        </div>
      </nav>

      <div className="set-main paper" ref={mainRef}>
        <p className={`set-saved${fresh ? ' is-fresh' : ''}`} role="status" aria-live="polite" data-testid="settings-saved">
          <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><path d="M4.5 10.5l3.5 3.5 7.5-8" /></svg>
          <span className="set-saved-t">{fresh ? t('set.savedNow') : t('set.savedIdle')}</span>
        </p>
        <article className="set-page" data-page={page} key={page}>
          {page === 'genel' && (
            <GeneralPage
              lang={lang} onLangChange={props.onLangChange}
              autoTitles={props.autoTitles ?? true} autoTitlesSaving={props.autoTitlesSaving ?? false}
              onToggleAutoTitles={props.onToggleAutoTitles}
              dictationAutoLang={props.dictationAutoLang ?? false} dictationAutoLangSaving={props.dictationAutoLangSaving ?? false}
              onToggleDictationAutoLang={props.onToggleDictationAutoLang}
              saved={saved}
            />
          )}
          {page === 'modeller' && (
            <ModelsPage
              aiConfig={props.aiConfig} availableModels={props.availableModels} providersWithKeys={props.providersWithKeys}
              defaultModel={props.defaultModel} onSaveDefaultModel={props.onSaveDefaultModel}
              onSaveApiKey={props.onSaveApiKey} onDeleteKey={props.onDeleteKey} onUseCustomModel={props.onUseCustomModel}
              usage={usage} API={props.API} http={props.http} token={user?.sessionToken} showToast={props.showToast}
              saved={saved}
            />
          )}
          {page === 'gorunum' && <AppearancePage saved={saved} />}
          {page === 'unity' && (
            <UnityPage status={props.unityMcpStatus} toggling={props.unityMcpToggling} onToggle={props.onToggleUnityMcp}
              projectName={props.unityProjectName} saved={saved} />
          )}
          {page === 'onay' && <ApprovalPage mode={props.approvalMode} onChange={props.onApprovalModeChange} saved={saved} />}
          {page === 'uzak' && (
            <>
              <SetPageHead title={t('set.nav.uzak')} lede={t('remote.hint')} />
              <RemoteControlSection onStatus={props.onRemoteStatus} onSaved={saved} />
            </>
          )}
          {page === 'hesap' && <AccountPage user={user} onLogout={props.onLogout} />}
        </article>
      </div>
    </section>
  );
};
