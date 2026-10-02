import React from 'react';

import { useLang, type Lang, type TKey } from '../../../lib/i18n';
import { displayName } from '../../../lib/displayName';
import {
  CODE_FONTS, FONT_STACKS, READING_FONTS, TEXT_SIZES, THEMES, useAppearance,
  type CodeFont, type ReadingFont, type TextSize, type Theme,
} from '../../../lib/appearance';
import type { UnityMCPStatus } from '../../../hooks/home/useAIConfig';
import type { GenerationMode, UserData } from '../types';
import { confirmDialog } from '../../ui/ConfirmDialog';
import { Lamp, SetCard, SetGroup, SetPageHead, SetRow, SetSeg, SetSwitch, Chev } from './controls';
import appPackage from '../../../../package.json';

const FONT_NAMES = {
  inter: 'Inter', geist: 'Geist', 'plex-sans': 'IBM Plex Sans', figtree: 'Figtree', atkinson: 'Atkinson Hyperlegible',
  jetbrains: 'JetBrains Mono', 'geist-mono': 'Geist Mono', 'plex-mono': 'IBM Plex Mono', fira: 'Fira Code', cascadia: 'Cascadia Code',
} as const;

/** The app's own version (package.json), shown in the rail foot and on the account page. */
export const APP_VERSION: string = (appPackage as { version?: string }).version || '';

// ───────────────────────────── Genel ─────────────────────────────

export const GeneralPage = ({
  lang, onLangChange, autoTitles, autoTitlesSaving, onToggleAutoTitles,
  dictationAutoLang, dictationAutoLangSaving, onToggleDictationAutoLang, onOpenGuide, onReplayTour, tourSteps, saved,
}: {
  lang: Lang; onLangChange: (l: Lang) => void;
  autoTitles: boolean; autoTitlesSaving: boolean; onToggleAutoTitles?: () => Promise<boolean> | void;
  dictationAutoLang: boolean; dictationAutoLangSaving: boolean; onToggleDictationAutoLang?: () => Promise<boolean> | void;
  onOpenGuide?: () => void; onReplayTour?: () => void; tourSteps?: number;
  saved: () => void;
}) => {
  const { t } = useLang();
  return (
    <>
      <SetPageHead title={t('set.nav.genel')} lede={t('set.lede.genel')} />
      <SetGroup title={t('settings.language')}>
        <SetCard>
          <SetRow
            testId="set-lang-row"
            name={t('set.uiLang')}
            hint={t('set.uiLangHint')}
            control={(
              <SetSeg<Lang>
                label={t('set.uiLang')}
                value={lang}
                onChange={l => { onLangChange(l); saved(); }}
                options={[{ id: 'en', label: 'English', lang: 'en' }, { id: 'tr', label: 'Türkçe', lang: 'tr' }]}
              />
            )}
          />
        </SetCard>
      </SetGroup>
      {onToggleAutoTitles && (
        <SetGroup title={t('set.group.chat')} guide="settings-auto-title">
          <SetCard>
            <SetRow
              name={t('settings.autoTitles')}
              hint={t('settings.autoTitlesHint')}
              control={<SetSwitch checked={autoTitles} label={t('settings.autoTitles')} testId="auto-titles-toggle"
                disabled={autoTitlesSaving} onToggle={async () => { if (await onToggleAutoTitles() === true) saved(); }} />}
            />
          </SetCard>
        </SetGroup>
      )}
      {onToggleDictationAutoLang && (
        <SetGroup title={t('set.group.dictation')} guide="settings-dictation-lang">
          <SetCard>
            <SetRow
              name={t('settings.dictationAutoLang')}
              hint={t('settings.dictationAutoLangHint')}
              control={<SetSwitch checked={dictationAutoLang} label={t('settings.dictationAutoLang')} testId="dictation-auto-lang-toggle"
                disabled={dictationAutoLangSaving} onToggle={async () => { if (await onToggleDictationAutoLang() === true) saved(); }} />}
            />
          </SetCard>
        </SetGroup>
      )}
      {/* Round 12b: the guide and the first-launch tour. The opening animation keeps its own row
          under Görünüm. */}
      {(onOpenGuide || onReplayTour) && (
        <SetGroup title={t('set.group.intro')}>
          <SetCard>
            {onOpenGuide && (
              <SetRow
                name={t('set.guide')}
                hint={t('set.guideHint')}
                control={(
                  <button type="button" className="btn btn-ghost btn-sm" data-testid="set-guide-open" onClick={onOpenGuide}>
                    <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7" /><path d="M7.9 8a2.2 2.2 0 114.2.9c-.5 1-2.1 1.3-2.1 2.7" /><path d="M10 14.2v.1" /></svg>
                    {t('set.guideOpen')}
                  </button>
                )}
              />
            )}
            {onReplayTour && (
              <SetRow
                name={t('set.tour')}
                hint={t('set.tourHint', { n: tourSteps ?? 6 })}
                control={(
                  <button type="button" className="btn btn-ghost btn-sm" data-testid="set-tour-replay" onClick={onReplayTour}>
                    <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><path d="M4 10a6 6 0 1 0 1.8-4.3" /><path d="M4 4v3.5h3.5" /></svg>
                    {t('set.tourReplay')}
                  </button>
                )}
              />
            )}
          </SetCard>
        </SetGroup>
      )}
    </>
  );
};

// ───────────────────────────── Görünüm ─────────────────────────────

// Each card previews its own world: shell, paper, ink, accent and the second colour of the theme
// (mockup swatch data; the colours are the themes' own tokens, written out so a card can show a
// theme other than the active one).
const THEME_SWATCH: Record<Theme, Record<string, string>> = {
  arena:  { '--sw-shell': '#141826', '--sw-paper': '#1F2433', '--sw-ink': '#DDDAD2', '--sw-acc': '#FF6B3D', '--sw-2': '#2CC5B4' },
  sade:   { '--sw-shell': '#13111B', '--sw-paper': '#1A1725', '--sw-ink': '#E6E2F0', '--sw-acc': '#A68BFA', '--sw-2': '#6FC3EA' },
  pafta:  { '--sw-shell': '#152033', '--sw-paper': '#EEE8DA', '--sw-ink': '#172235', '--sw-acc': '#C23B2A', '--sw-2': '#4F82B5' },
  atolye: { '--sw-shell': '#1E1A16', '--sw-paper': '#EEE3CE', '--sw-ink': '#2A1E14', '--sw-acc': '#BF6E14', '--sw-2': '#7FA674' },
};

export const AppearancePage = ({ saved }: { saved: () => void }) => {
  const { t } = useLang();
  const { appearance, setAppearance } = useAppearance();
  return (
    <>
      <SetPageHead title={t('set.nav.gorunum')} lede={t('set.lede.gorunum')} />
      <SetGroup title={t('settings.appearance.theme')}>
        <div className="theme-cards" data-guide="settings-themes" role="radiogroup" aria-label={t('settings.appearance.theme')}>
          {THEMES.map(theme => (
            <button
              key={theme}
              type="button"
              role="radio"
              aria-checked={appearance.theme === theme}
              data-theme-pick={theme}
              className="theme-card"
              style={THEME_SWATCH[theme] as React.CSSProperties}
              onClick={() => { setAppearance({ theme }); saved(); }}
            >
              <span className="tc-art" aria-hidden="true"><span className="tc-side" /><span className="tc-body"><i /><i /><i className="tc-acc" /></span></span>
              <span className="tc-name">{t(`settings.appearance.${theme}` as TKey)}</span>
              <span className="tc-sub">{t(`set.theme.${theme}Sub` as TKey)}</span>
            </button>
          ))}
        </div>
      </SetGroup>
      <SetGroup title={t('set.group.type')} note={t('set.group.typeNote')} guide="settings-fonts">
        <SetCard>
          <SetRow
            name={t('settings.appearance.readingFont')}
            hint={<span className="set-sample">{t('set.sampleBody')}</span>}
            control={(
              <span className="set-select">
                <select value={appearance.readingFont} aria-label={t('settings.appearance.readingFont')}
                  onChange={e => { setAppearance({ readingFont: e.target.value as ReadingFont }); saved(); }}>
                  {READING_FONTS.map(font => (
                    <option key={font} value={font} style={font === 'theme' ? undefined : { fontFamily: FONT_STACKS[font] }}>
                      {font === 'theme' ? t('settings.appearance.themeDefault') : FONT_NAMES[font]}
                    </option>
                  ))}
                </select>
                <Chev />
              </span>
            )}
          />
          <SetRow
            name={t('settings.appearance.codeFont')}
            hint={<span className="set-sample set-sample-mono" lang="en">public int Score {'{'} get; private set; {'}'}</span>}
            control={(
              <span className="set-select">
                <select value={appearance.codeFont} aria-label={t('settings.appearance.codeFont')}
                  onChange={e => { setAppearance({ codeFont: e.target.value as CodeFont }); saved(); }}>
                  {CODE_FONTS.map(font => (
                    <option key={font} value={font} style={font === 'theme' ? undefined : { fontFamily: FONT_STACKS[font] }}>
                      {font === 'theme' ? t('settings.appearance.themeDefault') : FONT_NAMES[font]}
                    </option>
                  ))}
                </select>
                <Chev />
              </span>
            )}
          />
          <SetRow
            name={t('settings.appearance.textSize')}
            hint={t('set.textSizeHint')}
            control={(
              <SetSeg<TextSize>
                label={t('settings.appearance.textSize')}
                value={appearance.textSize}
                onChange={textSize => { setAppearance({ textSize }); saved(); }}
                options={TEXT_SIZES.map(s => ({ id: s, label: t(`settings.appearance.${s}` as TKey) }))}
              />
            )}
          />
        </SetCard>
      </SetGroup>
      <SetGroup title={t('set.group.opening')}>
        <SetCard>
          <SetRow
            name={t('settings.appearance.intro')}
            hint={t('set.introHint')}
            control={<SetSwitch checked={appearance.intro} label={t('settings.appearance.intro')}
              onToggle={() => { setAppearance({ intro: !appearance.intro }); saved(); }} />}
          />
        </SetCard>
      </SetGroup>
    </>
  );
};

// ───────────────────────────── Unity ─────────────────────────────

export const UnityPage = ({ status, toggling, onToggle, projectName, saved }: {
  status: UnityMCPStatus; toggling: boolean; onToggle: () => Promise<boolean> | void; projectName?: string | null; saved: () => void;
}) => {
  const { t } = useLang();
  // Each Unity state maps to a tone; settings.css turns the tone into colour.
  // The `blocked` entry is load-bearing: the backend has returned that value
  // since `b4065f1`, and a missing key crashed the old modal on open whenever a
  // foreign server held port 8080. `unknown` is a quiet grey with no pulse:
  // the state is not known, so it must not read as "working" (finding I-2).
  const CONFIG: Record<UnityMCPStatus, { label: string; tone: 'off' | 'danger' | 'busy' | 'ok' }> = {
    off:       { label: t('unity.off'),       tone: 'off' },
    blocked:   { label: t('unity.blocked'),   tone: 'danger' },
    starting:  { label: t('unity.starting'),  tone: 'busy' },
    running:   { label: t('unity.running'),   tone: 'busy' },
    connected: { label: t('unity.connected'), tone: 'ok' },
    unknown:   { label: t('unity.unknown'),   tone: 'off' },
  };
  const cfg = CONFIG[status] ?? CONFIG.unknown;
  // ON only while the server is OURS: `blocked` means a foreign server holds 8080, which is
  // worse than off. Listed one by one so a new state does not default to "on".
  const on = status === 'starting' || status === 'running' || status === 'connected';
  return (
    <>
      <SetPageHead title="Unity" lede={t('set.lede.unity')} />
      <SetGroup>
        <SetCard>
          <div className="set-row set-row-hero" data-guide="settings-unity-switch" data-testid="unity-mcp-row" data-status={status} data-tone={cfg.tone}>
            <svg className="ic set-hero-ic" viewBox="0 0 20 20" aria-hidden="true"><path d="M10 2.5l6.5 3.75v7.5L10 17.5l-6.5-3.75v-7.5z" /><path d="M10 10l6.5-3.75M10 10v7.5M10 10L3.5 6.25" /></svg>
            <div className="set-rt">
              <p className="set-name">{t('set.unity.conn')}</p>
              <div className="set-hint">
                <Lamp tone={cfg.tone === 'off' ? undefined : cfg.tone} />
                <b className="set-state">{cfg.label}</b>
                {projectName ? <> · <code className="set-key">{projectName}</code></> : null}
              </div>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={on}
              aria-label={t('set.unity.conn')}
              data-testid="unity-mcp-toggle"
              data-state={on ? 'on' : 'off'}
              aria-busy={toggling || undefined}
              onClick={async () => {
                if (toggling || status === 'starting') return;
                if (await onToggle() === true && status !== 'blocked') saved();
              }}
              disabled={toggling || status === 'starting'}
              className="set-switch"
            >
              <span className="set-knob" />
            </button>
          </div>
          <SetRow name={t('set.unity.offTitle')} hint={t('set.unity.offHint')} />
        </SetCard>
      </SetGroup>
      <SetGroup title={t('set.unity.trouble')}>
        <SetCard>
          <SetRow name={t('set.unity.closedT')} hint={t('set.unity.closedHint')} />
          <SetRow name={t('set.unity.blockedT')} hint={t('set.unity.blockedHint')} />
        </SetCard>
      </SetGroup>
    </>
  );
};

// ───────────────────────────── Onay modu ─────────────────────────────

export const ApprovalPage = ({ mode, onChange, saved }: {
  mode?: GenerationMode; onChange?: (m: GenerationMode) => void; saved: () => void;
}) => {
  const { t } = useLang();
  const options: { id: GenerationMode; title: TKey; explain: TKey; warn: boolean; recommended: boolean }[] = [
    { id: 'step', title: 'set.mode.stepTitle', explain: 'settings.modeStepExplain', warn: false, recommended: false },
    { id: 'balanced', title: 'set.mode.balancedTitle', explain: 'settings.modeBalancedExplain', warn: false, recommended: true },
    { id: 'auto', title: 'set.mode.autoTitle', explain: 'settings.modeAutoExplain', warn: true, recommended: false },
  ];
  return (
    <>
      <SetPageHead title={t('set.nav.onay')} lede={t('set.lede.onay')} />
      <div className="mode-list" role="radiogroup" aria-label={t('set.nav.onay')}>
        {options.map(o => {
          const selected = mode === o.id;
          // The auto card keeps its warning whether or not it is selected:
          // the warning is about the mode itself, not about the current choice.
          return (
            <button
              key={o.id}
              type="button"
              role="radio"
              aria-checked={selected}
              data-mode={o.id}
              data-warn={o.warn ? 'true' : undefined}
              disabled={!onChange}
              onClick={() => { if (!selected) { onChange?.(o.id); saved(); } }}
              className={`mode-card${o.warn ? ' mode-card-warn' : ''}`}
            >
              <span className="mode-radio" aria-hidden="true" />
              <span className="mode-t">
                <span className="mode-name">
                  <span className="mode-title" data-warn={o.warn ? 'true' : undefined}>{t(o.title)}</span>
                  {o.recommended && <span data-testid="mode-recommended-badge" className="mode-tag">{t('mode.recommended')}</span>}
                </span>
                <span className="mode-desc">{t(o.explain)}</span>
                {o.warn && (
                  <span className="mode-risk">
                    <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><path d="M10 3l7.5 13h-15z" /><path d="M10 8.5v3.5M10 14.2v.3" /></svg>
                    {t('set.mode.autoRisk')}
                  </span>
                )}
              </span>
            </button>
          );
        })}
      </div>
    </>
  );
};

// ───────────────────────────── Hesap ─────────────────────────────

export const AccountPage = ({ user, userName, onSaveName, onLogout, onOpenProfile, onProfileReset, saved, showToast }: {
  user?: UserData | null;
  userName?: string;
  onSaveName?: (name: string) => Promise<boolean>;
  onLogout: () => void;
  /** The hero row's "Yapımcı profili" button (mockup round 11 hesap page). */
  onOpenProfile?: () => void;
  /** Called after a confirmed reset succeeded, so the profile and the sidebar card re-read. */
  onProfileReset?: () => void;
  saved?: () => void;
  showToast?: (msg: string, type: 'success' | 'error' | 'warning' | 'info') => void;
}) => {
  const { t } = useLang();
  const currentName = displayName(userName ?? user?.name);
  const name = currentName || t('set.me.anon');
  const [nameInput, setNameInput] = React.useState(currentName);
  const [savingName, setSavingName] = React.useState(false);
  const [nameSaveCount, setNameSaveCount] = React.useState(0);
  // Minor audit fixes, 2 Oct 2026: normalization may leave the saved prop unchanged.
  React.useEffect(() => { setNameInput(currentName); }, [currentName, nameSaveCount]);
  const saveName = async () => {
    if (!onSaveName || savingName || nameInput.trim() === currentName) return;
    setSavingName(true);
    try {
      if (await onSaveName(nameInput)) { setNameSaveCount(count => count + 1); saved?.(); }
      else showToast?.(t('set.hesap.nameFailed'), 'error');
    } catch {
      showToast?.(t('set.hesap.nameFailed'), 'error');
    } finally {
      setSavingName(false);
    }
  };
  const [resetting, setResetting] = React.useState(false);
  // POST /profile/reset needs the UI secret, which only the main process holds: the renderer
  // asks through the 'profile-reset' channel (main/helpers/profile-reset.ts).
  const resetStats = async () => {
    if (resetting) return;
    const ok = await confirmDialog(t('set.hesap.resetConfirm'), t('set.hesap.resetGo'), t('confirm.cancel'));
    if (!ok) return;
    setResetting(true);
    try {
      const ipc = typeof window !== 'undefined' ? (window as any).ipc : undefined;
      if (!ipc?.invoke) throw new Error('no ipc');
      await ipc.invoke('profile-reset');
      saved?.();
      showToast?.(t('set.hesap.resetDone'), 'success');
      onProfileReset?.();
    } catch {
      showToast?.(t('set.hesap.resetFailed'), 'error');
    } finally {
      setResetting(false);
    }
  };
  return (
    <>
      <SetPageHead title={t('set.nav.hesap')} lede={t('set.lede.hesap')} />
      <SetGroup>
        <SetCard>
          <div className="set-row set-row-hero">
            <span className="set-avatar set-avatar-lg" aria-hidden="true">{name.charAt(0).toUpperCase()}</span>
            <div className="set-rt"><p className="set-name">{name}</p></div>
            {onOpenProfile && (
              <button type="button" className="btn btn-ghost btn-sm" onClick={onOpenProfile} data-testid="settings-open-profile">
                {t('set.hesap.profile')}
              </button>
            )}
          </div>
          {onSaveName && (
            <SetRow name={t('set.hesap.name')} hint={t('set.hesap.nameHint')} control={(
              <span className="set-field">
                <input className="set-input" type="text" data-testid="settings-name-input"
                  maxLength={40} autoComplete="off" placeholder={t('set.hesap.namePlaceholder')}
                  aria-label={t('set.hesap.name')} value={nameInput} disabled={savingName}
                  onChange={e => setNameInput(e.target.value)}
                  onKeyDown={e => {
                    // Minor audit fixes, 2 Oct 2026: IME Enter commits composition first.
                    if (e.key === 'Enter' && (e.nativeEvent.isComposing || e.nativeEvent.keyCode === 229)) return;
                    if (e.key === 'Enter') { e.preventDefault(); void saveName(); }
                    // Display name, 2 Oct 2026: restore the draft before the screen sees Esc.
                    if (e.key === 'Escape') { e.preventDefault(); setNameInput(currentName); e.currentTarget.blur(); }
                  }}
                />
                <button type="button" data-testid="settings-name-save" className="btn btn-ghost btn-sm"
                  disabled={savingName || nameInput.trim() === currentName} onClick={() => { void saveName(); }}>
                  {t('set.hesap.nameSave')}
                </button>
              </span>
            )} />
          )}
          <SetRow name={t('set.hesap.version')} hint={<>Gamachine <span className="num">{APP_VERSION}</span></>} />
        </SetCard>
      </SetGroup>
      <SetGroup>
        <SetCard>
          <SetRow
            name={t('set.hesap.reset')}
            hint={t('set.hesap.resetHint')}
            control={(
              <button type="button" onClick={resetStats} disabled={resetting} data-testid="settings-reset-stats" className="btn btn-ghost btn-sm btn-danger">
                {t('set.hesap.reset')}
              </button>
            )}
          />
        </SetCard>
      </SetGroup>
      <SetGroup>
        <SetCard>
          <SetRow
            name={t('set.hesap.logout')}
            hint={t('set.hesap.logoutHint')}
            control={(
              <button type="button" onClick={onLogout} data-testid="settings-logout" className="btn btn-ghost btn-sm btn-danger">
                <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><path d="M8 4H4.5v12H8" /><path d="M12 6.5L15.5 10 12 13.5M15.5 10H8" /></svg>
                {t('set.hesap.logout')}
              </button>
            )}
          />
        </SetCard>
      </SetGroup>
    </>
  );
};
