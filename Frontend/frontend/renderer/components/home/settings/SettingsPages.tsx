import React from 'react';

import { useLang, type Lang, type TKey } from '../../../lib/i18n';
import { displayName } from '../../../lib/displayName';
import { useSceneEditorSetting } from '../../../lib/sceneEditor';
import {
  CODE_FONTS, FONT_STACKS, READING_FONTS, TEXT_SIZES, THEMES, useAppearance,
  type CodeFont, type ReadingFont, type TextSize, type Theme,
} from '../../../lib/appearance';
import {
  OZEL_FONTS, OZEL_PRESET_IDS, OZEL_PRESETS, OZEL_STATUS, activeMode, activePalette, fixReadable, isReadable, mixOklab,
  normHex, ozelFlags, ozelFontStack, parseThemeText, presetSettings, readability, setOzel, themeText, useOzel,
  type OzelFont, type OzelMode, type OzelPalette, type OzelRead, type OzelSettings, type ThemeTextError,
} from '../../../lib/ozelTheme';
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
const THEME_SWATCH: Record<Exclude<Theme, 'ozel'>, Record<string, string>> = {
  arena:  { '--sw-shell': '#141826', '--sw-paper': '#1F2433', '--sw-ink': '#DDDAD2', '--sw-acc': '#FF6B3D', '--sw-2': '#2CC5B4' },
  sade:   { '--sw-shell': '#13111B', '--sw-paper': '#1A1725', '--sw-ink': '#E6E2F0', '--sw-acc': '#A68BFA', '--sw-2': '#6FC3EA' },
  pafta:  { '--sw-shell': '#152033', '--sw-paper': '#EEE8DA', '--sw-ink': '#172235', '--sw-acc': '#C23B2A', '--sw-2': '#4F82B5' },
  atolye: { '--sw-shell': '#1E1A16', '--sw-paper': '#EEE3CE', '--sw-ink': '#2A1E14', '--sw-acc': '#BF6E14', '--sw-2': '#7FA674' },
};

// ───────────── Özel (round 15): the fifth theme card and its settings group ─────────────

const OZEL_FONT_NAMES: Record<Exclude<OzelFont, 'default'>, string> = {
  inter: 'Inter', geist: 'Geist', 'plex-sans': 'IBM Plex Sans',
  'jetbrains-mono': 'JetBrains Mono', 'geist-mono': 'Geist Mono', 'plex-mono': 'IBM Plex Mono',
};
const OZEL_PLACEHOLDER = 'gm-tema:1;ad=Mono;bg=#000000;fg=#FFFFFF;vurgu=#000000;yazi=geist-mono';
type ColorKey = keyof OzelPalette;

/** The Özel card under the four character cards: its swatch is the user's live palette. */
const OzelCard = ({ checked, onPick }: { checked: boolean; onPick: () => void }) => {
  const { t } = useLang();
  const { settings, prefersDark } = useOzel();
  const p = activePalette(settings, prefersDark);
  const flags = ozelFlags(p);
  const style = {
    '--sw-shell': mixOklab(p.fg, p.bg, 0.035), '--sw-paper': p.bg, '--sw-ink': p.fg,
    '--sw-acc': flags.acc === 'off' ? p.fg : p.accent, '--sw-2': OZEL_STATUS[flags.tone].wait,
  } as React.CSSProperties;
  const now = settings.preset
    ? t(`set.oz.preset.${settings.preset}` as TKey)
    : settings.name && settings.name !== 'Özel' ? t('set.oz.nowChanged', { name: settings.name }) : t('set.oz.nowOwn');
  return (
    <button type="button" role="radio" aria-checked={checked} data-theme-pick="ozel" className="theme-card oz-card" style={style} onClick={onPick}>
      <span className="tc-art" aria-hidden="true"><span className="tc-side" /><span className="tc-body"><i /><i /><i className="tc-acc" /></span></span>
      <span className="oz-card-t">
        <span className="tc-name">{t('settings.appearance.ozel')}</span>
        <span className="tc-sub">{t('set.theme.ozelSub', { name: now })}</span>
      </span>
    </button>
  );
};

/** Arrows / Home / End move and select inside the group's radiogroups (Tab reaches each button). */
function moveRadio(e: React.KeyboardEvent<HTMLElement>) {
  const target = e.target as HTMLElement;
  if (target.getAttribute('role') !== 'radio') return;
  const group = target.closest('[role="radiogroup"]');
  if (!group || !['ArrowRight', 'ArrowDown', 'ArrowLeft', 'ArrowUp', 'Home', 'End'].includes(e.key)) return;
  const items = Array.from(group.querySelectorAll<HTMLElement>('[role="radio"]'));
  const i = items.indexOf(target);
  e.preventDefault();
  const n = e.key === 'Home' ? 0 : e.key === 'End' ? items.length - 1
    : (i + (e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
  items[n].focus();
  items[n].click();
}

/** Contrast ratios read like the mockup: floored to one decimal, the language's decimal mark. */
const fmtRatio = (n: number, lang: Lang) => {
  const v = (Math.floor(n * 10) / 10).toFixed(1);
  return lang === 'tr' ? v.replace('.', ',') : v;
};

const AlertIc = () => (
  <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><path d="M10 3l7.5 13h-15z" /><path d="M10 8.5v3.5M10 14.2v.3" /></svg>
);
const CheckIc = () => (
  <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><path d="M4.5 10.5l3.5 3.5 7.5-8" /></svg>
);

/** The Özel settings: three colours per mode, a font and the long-answer choice (rendered only in Özel). */
const OzelGroup = ({ saved }: { saved: () => void }) => {
  const { t, lang } = useLang();
  const { settings: s, prefersDark } = useOzel();
  const mode = activeMode(s, prefersDark);
  const p = s[mode];
  const flags = ozelFlags(p);
  const read = readability(p.fg, p.bg);
  const [drafts, setDrafts] = React.useState<Partial<Record<ColorKey, string>>>({});
  const [fixed, setFixed] = React.useState<{ mode: 'light' | 'dark'; before: OzelPalette; after: OzelPalette } | null>(null);
  const [copied, setCopied] = React.useState(false);
  const [copyError, setCopyError] = React.useState(false);
  const [impOpen, setImpOpen] = React.useState(false);
  const [impText, setImpText] = React.useState('');
  const focusNext = React.useRef<'undo' | 'fix' | 'imp-in' | 'imp-btn' | null>(null);
  const undoRef = React.useRef<HTMLButtonElement>(null);
  const fixRef = React.useRef<HTMLButtonElement>(null);
  const impBtnRef = React.useRef<HTMLButtonElement>(null);
  const impInRef = React.useRef<HTMLTextAreaElement>(null);
  const copyTimer = React.useRef<ReturnType<typeof setTimeout>>();
  React.useEffect(() => () => clearTimeout(copyTimer.current), []);
  // Focus moves after the render that shows its target (fix -> Geri al, Geri al -> fix, import box).
  React.useEffect(() => {
    const k = focusNext.current;
    if (!k) return;
    focusNext.current = null;
    ({ undo: undoRef, fix: fixRef, 'imp-in': impInRef, 'imp-btn': impBtnRef } as const)[k].current?.focus();
  });

  // Every change persists and repaints at once; a hand-made change leaves the preset and the fix note.
  const update = (next: OzelSettings) => { setFixed(null); setOzel(next); saved(); };
  const setColor = (k: ColorKey, v: string) => update({ ...s, preset: null, [mode]: { ...p, [k]: v } });
  const fix = () => {
    const after = fixReadable(p);
    setOzel({ ...s, preset: null, [mode]: after });
    setFixed({ mode, before: p, after });
    focusNext.current = 'undo';
    saved();
  };
  const undo = () => {
    if (!fixed) return;
    update({ ...s, [fixed.mode]: fixed.before });
    focusNext.current = 'fix';
  };
  const openImport = (on: boolean) => { setImpOpen(on); focusNext.current = on ? 'imp-in' : 'imp-btn'; };

  const parsed = parseThemeText(impText, s.font);
  const parseError = parsed.ok ? null : (parsed as Extract<typeof parsed, { ok: false }>).error;
  const impError = (e: ThemeTextError): string => {
    switch (e.code) {
      case 'empty': return t('set.oz.imp.empty');
      case 'prefix': return t('set.oz.imp.prefix');
      case 'pair': return t('set.oz.imp.pair', { text: e.text });
      case 'missing': return t('set.oz.imp.missing', { fields: e.fields.map(f => `${f} (${t(`set.oz.field.${f}` as TKey)})`).join(', ') });
      case 'color': return t('set.oz.imp.color', { field: e.field, value: e.value });
      case 'font': return t('set.oz.imp.font', { value: e.value, options: OZEL_FONTS.join(', ') });
    }
  };
  const impMsg = parsed.ok
    ? t('set.oz.imp.ok', { name: parsed.value.name, bg: parsed.value.bg, fg: parsed.value.fg })
      + (isReadable(parsed.value.fg, parsed.value.bg) ? '' : t('set.oz.imp.okWarn'))
    : impError(parseError!);
  const impTone = parsed.ok ? ' is-ok' : parseError!.code === 'empty' ? '' : ' is-err';
  const doImport = () => {
    if (!parsed.ok) { impInRef.current?.focus(); return; }
    const v = parsed.value;
    update({ ...s, preset: null, name: v.name, font: v.font, [mode]: { bg: v.bg, fg: v.fg, accent: v.accent } });
    setImpText('');
    openImport(false);
  };
  const copy = async () => {
    setCopied(false);
    setCopyError(false);
    clearTimeout(copyTimer.current);
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(themeText(s, prefersDark));
      setCopied(true);
      copyTimer.current = setTimeout(() => setCopied(false), 1600);
    } catch { setCopyError(true); }
  };

  const colorField = (k: ColorKey, label: string) => {
    const draft = drafts[k];
    return (
      <span className="oz-col">
        <label className="oz-dot" style={{ '--c': p[k] } as React.CSSProperties}>
          <input type="color" value={p[k].toLowerCase()} aria-label={t('set.oz.picker', { name: label })}
            onChange={e => { const v = normHex(e.target.value); if (v) setColor(k, v); }} />
        </label>
        <input className="set-input oz-hex" type="text" value={draft ?? p[k]} maxLength={7} spellCheck={false} autoComplete="off"
          aria-label={t('set.oz.hexLabel', { name: label })} aria-invalid={draft != null && !normHex(draft) ? true : undefined}
          onChange={e => {
            const raw = e.target.value;
            setDrafts(d => ({ ...d, [k]: raw }));
            const v = normHex(raw);
            if (v) setColor(k, v);
          }}
          onBlur={() => setDrafts(d => { const n = { ...d }; delete n[k]; return n; })}
          onKeyDown={e => { if (e.key === 'Enter') setDrafts(d => { const n = { ...d }; delete n[k]; return n; }); }} />
      </span>
    );
  };

  const guardText = flags.bad
    ? read.main < 4.5
      ? t('set.oz.guard.main', { ratio: fmtRatio(read.main, lang) })
      : t('set.oz.guard.faint', { ratio: fmtRatio(read.faint, lang) })
    : fixed
      ? fixed.before.bg !== fixed.after.bg
        ? t('set.oz.guard.fixedBoth', { from: fixed.before.fg, to: fixed.after.fg, bgFrom: fixed.before.bg, bgTo: fixed.after.bg })
        : t('set.oz.guard.fixed', { from: fixed.before.fg, to: fixed.after.fg })
      : '';
  const showFixed = !flags.bad && !!fixed;

  return (
    <section className="set-group oz-set" aria-label={t('set.oz.groupLabel')} onKeyDown={moveRadio}>
      <h2 className="set-gk">{t('set.oz.group')}<span className="set-gk-note">{t('set.oz.groupNote')}</span></h2>
      <SetCard>
        <SetRow
          className="oz-row-pre"
          name={t('set.oz.preset')}
          hint={<>{t('set.oz.presetHint')}{s.preset ? null : <span className="oz-pre-mod">{t('set.oz.presetChanged')}</span>}</>}
          control={(
            <span className="oz-presets" role="radiogroup" aria-label={t('set.oz.presetLabel')}>
              {OZEL_PRESET_IDS.map(id => {
                const pr = OZEL_PRESETS[id];
                const sw = pr[pr.mode];
                return (
                  <button key={id} type="button" role="radio" aria-checked={s.preset === id} className="oz-pre" data-oz-preset={id}
                    style={{ '--p-bg': sw.bg, '--p-fg': sw.fg, '--p-font': ozelFontStack(pr.font) } as React.CSSProperties}
                    onClick={() => update(presetSettings(id, s.mode === 'system' ? 'system' : undefined))}>
                    <span className="oz-pre-sw" aria-hidden="true">Aa</span>
                    <span className="oz-pre-n">{t(`set.oz.preset.${id}` as TKey)}</span>
                  </button>
                );
              })}
            </span>
          )}
        />
        <SetRow
          name={t('set.oz.mode')}
          hint={t('set.oz.modeHint')}
          control={(
            <SetSeg<OzelMode> label={t('set.oz.mode')} value={s.mode} onChange={m => update({ ...s, mode: m })}
              options={(['system', 'light', 'dark'] as const).map(m => ({ id: m, label: t(`set.oz.mode.${m}` as TKey) }))} />
          )}
        />
        <SetRow
          name={t('set.oz.accent')}
          hint={t('set.oz.accentHint') + (flags.acc === 'off' ? ` ${t('set.oz.accentOff')}` : '')}
          control={colorField('accent', t('set.oz.accent'))}
        />
        <SetRow name={t('set.oz.bg')} hint={t('set.oz.bgHint')} control={colorField('bg', t('set.oz.bg'))} />
        <SetRow
          className="oz-row-fg"
          name={t('set.oz.fg')}
          hint={t('set.oz.fgHint')}
          control={(
            <>
              {colorField('fg', t('set.oz.fg'))}
              <div className={`oz-guard${showFixed ? ' is-fixed' : ''}`} role="status" aria-live="polite" hidden={!flags.bad && !fixed} data-testid="oz-guard">
                {showFixed ? <CheckIc /> : <AlertIc />}
                <span className="oz-guard-t">{guardText}</span>
                {showFixed
                  ? <button ref={undoRef} type="button" className="set-link" onClick={undo}>{t('set.oz.guard.undo')}</button>
                  : <button ref={fixRef} type="button" className="btn btn-ghost btn-sm" onClick={fix}>{t('set.oz.guard.fix')}</button>}
              </div>
            </>
          )}
        />
        <SetRow
          name={t('set.oz.font')}
          hint={<span className="oz-ui-sample" style={{ fontFamily: ozelFontStack(s.font) }}>{t('set.oz.fontSample')}</span>}
          control={(
            <span className="set-select">
              <select value={s.font} aria-label={t('set.oz.font')}
                onChange={e => update({ ...s, preset: null, font: e.target.value as OzelFont })}>
                <option value="default">{t('settings.appearance.themeDefault')}</option>
                <optgroup label="Sans">
                  {(['inter', 'geist', 'plex-sans'] as const).map(f => <option key={f} value={f} style={{ fontFamily: ozelFontStack(f) }}>{OZEL_FONT_NAMES[f]}</option>)}
                </optgroup>
                <optgroup label="Mono">
                  {(['jetbrains-mono', 'geist-mono', 'plex-mono'] as const).map(f => <option key={f} value={f} style={{ fontFamily: ozelFontStack(f) }}>{OZEL_FONT_NAMES[f]}</option>)}
                </optgroup>
              </select>
              <Chev />
            </span>
          )}
        />
        <SetRow
          name={t('set.oz.read')}
          hint={t('set.oz.readHint')}
          control={(
            <SetSeg<OzelRead> label={t('set.oz.readLabel')} value={s.read} onChange={r => update({ ...s, read: r })}
              options={(['ui', 'read'] as const).map(r => ({ id: r, label: t(`set.oz.read.${r}` as TKey) }))} />
          )}
        />
        <SetRow
          className="oz-row-st"
          name={t('set.oz.status')}
          hint={t('set.oz.statusHint')}
          control={(
            <span className="oz-st" aria-label={t('set.oz.status')}>
              {(['wait', 'run', 'ok', 'err'] as const).map(k => (
                <span key={k} className="oz-st-i" data-st={k}><i />{t(`set.oz.st.${k}` as TKey)}</span>
              ))}
            </span>
          )}
        />
        <SetRow
          className="oz-row-io"
          name={t('set.oz.text')}
          hint={t('set.oz.textHint')}
          control={(
            <>
              <span className="set-field">
                <button type="button" className="btn btn-ghost btn-sm" onClick={copy}>
                  <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><rect x="7" y="7" width="9" height="9" rx="1.5" /><path d="M13 7V5.5A1.5 1.5 0 0 0 11.5 4h-6A1.5 1.5 0 0 0 4 5.5v6A1.5 1.5 0 0 0 5.5 13H7" /></svg>
                  <span className="oz-copy-t">{copied ? t('set.oz.copied') : t('set.oz.copy')}</span>
                </button>
                {copyError && <span className="oz-imp-msg is-err" role="status">{t('set.oz.copyError')}</span>}
                <button ref={impBtnRef} type="button" className="btn btn-ghost btn-sm" aria-expanded={impOpen} aria-controls="oz-imp"
                  onClick={() => openImport(!impOpen)}>
                  <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><path d="M10 4v9M6 9.5l4 4 4-4M4.5 16h11" /></svg>
                  {t('set.oz.import')}
                </button>
              </span>
              <div className="oz-imp" id="oz-imp" hidden={!impOpen}>
                <label className="oz-imp-k" htmlFor="oz-imp-in">{t('set.oz.paste')}</label>
                <textarea ref={impInRef} className="set-input oz-imp-in" id="oz-imp-in" rows={2} spellCheck={false}
                  aria-describedby="oz-imp-msg" placeholder={OZEL_PLACEHOLDER} value={impText}
                  aria-invalid={parseError && parseError.code !== 'empty' ? true : undefined}
                  onChange={e => setImpText(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); doImport(); }
                    // Esc closes only this box (the settings screen skips a handled Esc).
                    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); openImport(false); }
                  }} />
                <p className={`oz-imp-msg${impTone}`} id="oz-imp-msg">{impMsg}</p>
                <div className="oz-imp-act">
                  <button type="button" className="btn btn-primary btn-sm" aria-disabled={!parsed.ok} onClick={doImport}>{t('set.oz.apply')}</button>
                  <button type="button" className="set-link" onClick={() => openImport(false)}>{t('set.oz.cancel')}</button>
                </div>
              </div>
            </>
          )}
        />
      </SetCard>
    </section>
  );
};

export const AppearancePage = ({ saved }: { saved: () => void }) => {
  const { t } = useLang();
  const { appearance, setAppearance } = useAppearance();
  return (
    <>
      <SetPageHead title={t('set.nav.gorunum')} lede={t('set.lede.gorunum')} />
      <SetGroup title={t('settings.appearance.theme')}>
        <div className="theme-cards" data-guide="settings-themes" role="radiogroup" aria-label={t('settings.appearance.theme')}>
          {THEMES.map(theme => theme === 'ozel' ? (
            <OzelCard key={theme} checked={appearance.theme === theme} onPick={() => { setAppearance({ theme }); saved(); }} />
          ) : (
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
      {appearance.theme === 'ozel' && <OzelGroup saved={saved} />}
      <SetGroup title={t('set.group.type')} note={t('set.group.typeNote')} guide="settings-fonts">
        <SetCard>
          <SetRow
            name={t('settings.appearance.readingFont')}
            hint={<span className="set-sample" data-sample="body">{t('set.sampleBody')}</span>}
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
  const [sceneEditor, setSceneEditor] = useSceneEditorSetting();
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
      <SetGroup>
        <SetCard><SetRow name={t('sceneEditor.setting')} hint={t('sceneEditor.settingHint')}
          control={<SetSwitch checked={sceneEditor} label={t('sceneEditor.setting')}
            onToggle={() => { setSceneEditor(!sceneEditor); saved(); }} />} /></SetCard>
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
