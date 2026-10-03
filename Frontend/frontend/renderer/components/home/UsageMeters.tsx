import React from 'react';

import { useLang } from '../../lib/i18n';
import {
  HOT_PCT, clampPct, meterState, pickPair, resetLabel,
  type MeterState, type UsageFamily, type UsageLimits, type UsageWindow,
} from '../../lib/usageLimits';

/**
 * The usage meter (mockup `.meter[data-v]`): the Hafıza meter's ten segments, lit from the
 * percentage; at or above 80 % the lit part turns to the accent (the limit is near).
 * `loading` draws the empty track only: a skeleton, never a guessed value.
 */
export const Meter = ({ value, loading = false, className = '', base = 'meter', segments = 10 }: {
  value: number | null; loading?: boolean; className?: string;
  /** `energy` draws it with the composer strip's Hafıza bar rules (themes restyle that bar). */
  base?: 'meter' | 'energy'; segments?: number;
}) => {
  const v = value == null ? 0 : clampPct(value);
  const on = loading || value == null ? 0 : Math.round((v * segments) / 100);
  const hot = !loading && value != null && v >= HOT_PCT;
  return (
    <span
      className={`${base}${hot ? ' is-hot' : ''}${loading ? ' is-loading' : ''}${className ? ` ${className}` : ''}`}
      data-v={loading || value == null ? undefined : v}
      aria-hidden="true"
    >
      {Array.from({ length: segments }, (_, i) => <i key={i} className={i < on ? 'on' : undefined} />)}
    </span>
  );
};

/** One "5 saat ▮▮▮▯ %62 kullanıldı  yenilenme 18:40" line of the menu's and the settings page's usage box. */
export const UseRow = ({ label, win, state, nowIso }: { label: string; win: UsageWindow | null; state: MeterState; nowIso?: string | null }) => {
  const { lang, t } = useLang();
  const loading = state === 'loading';
  const v = win ? clampPct(win.used_pct) : null;
  const reset = !loading ? resetLabel(win, lang, nowIso) : '';
  // Spent, not remaining, spelled out: Claude's /usage counts what is used, the Codex app
  // what is left, and a bare "%66" next to the Codex app read as the opposite (owner, 2 Oct 2026).
  return (
    <div className="use-row" data-testid="use-row">
      <span className="use-k">{label}</span>
      <Meter value={v} loading={loading || !win} />
      <span className="use-v num">{!loading && v != null ? t('use.used', { yuzde: v }) : ''}</span>
      <span className="use-r">{reset ? t('use.resets', { zaman: reset }) : ''}</span>
    </div>
  );
};

/** The usage box of one family; null when there is nothing to show. */
export const UseBlock = ({ fam, modelId, nowIso, className = 'use-block' }: {
  fam: UsageFamily | null; modelId?: string | null; nowIso?: string | null; className?: string;
}) => {
  const { t } = useLang();
  const state = meterState(fam);
  if (state === 'none') return null;
  const { five, week } = pickPair(fam, modelId);
  return (
    <div className={className} data-state={state} data-testid="use-block">
      <UseRow label={t('use.fiveH')} win={five} state={state} nowIso={nowIso} />
      <UseRow label={t('use.week')} win={week} state={state} nowIso={nowIso} />
      {state === 'stale' && <span className="use-stale" data-testid="use-stale">{t('use.stale')}</span>}
    </div>
  );
};

/** The two small meters at the right of a provider row in the menu. */
export const UsePair = ({ fam, modelId }: { fam: UsageFamily | null; modelId?: string | null }) => {
  const { t } = useLang();
  const state = meterState(fam);
  if (state === 'none') return null;
  const { five, week } = pickPair(fam, modelId);
  const loading = state === 'loading';
  const label = loading ? t('use.loading')
    : t('use.pairLabel', { bes: five ? clampPct(five.used_pct) : '-', hafta: week ? clampPct(week.used_pct) : '-' });
  return (
    <span className="use-pair" data-state={state} data-testid="use-pair" role="img" aria-label={label}>
      <Meter value={five ? five.used_pct : null} loading={loading || !five} />
      <Meter value={week ? week.used_pct : null} loading={loading || !week} />
    </span>
  );
};

/**
 * The chip's two hairlines (5 h over week). Nothing at all for a family with no numbers,
 * so an API or local model's chip is just its mark and name.
 */
export const ChipUse = ({ limits, family, modelId, providerName }: {
  limits: UsageLimits | null; family: UsageFamily['family'] | null; modelId?: string | null; providerName: string;
}) => {
  const { t } = useLang();
  const fam = family ? (limits?.families || []).find(f => f?.family === family) ?? null : null;
  const state = meterState(fam);
  if (state === 'none') return null;
  const { five, week } = pickPair(fam, modelId);
  const loading = state === 'loading';
  const bar = (w: UsageWindow | null) => {
    const v = loading || !w ? 0 : clampPct(w.used_pct);
    return <i className={!loading && w && v >= HOT_PCT ? 'is-hot' : undefined} style={{ ['--u' as any]: `${v}%` }} />;
  };
  return (
    <>
      <span className="chip-use" data-state={state} data-testid="chip-use" aria-hidden="true">
        {bar(five)}{bar(week)}
      </span>
      <span className="sr-only">
        {loading ? `${providerName} · ${t('use.loading')}` : t('use.sr', {
          ad: providerName, bes: five ? clampPct(five.used_pct) : '-', hafta: week ? clampPct(week.used_pct) : '-',
        })}
      </span>
    </>
  );
};

/**
 * The composer strip's "Kota" group: the current chat's family, 5 h and week, each a short bar
 * and its number. Nothing at all while there are no numbers (API / local models, not loaded
 * yet), so the strip reserves no room for it. The title carries both reset times.
 */
export const StripUse = ({ limits, family, modelId, nowIso, withSep = false }: {
  limits: UsageLimits | null; family: UsageFamily['family'] | null; modelId?: string | null; nowIso?: string | null;
  /** Lead with the strip's dot separator, so it goes away together with the group. */
  withSep?: boolean;
}) => {
  const { lang, t } = useLang();
  const fam = family ? (limits?.families || []).find(f => f?.family === family) ?? null : null;
  const state = meterState(fam);
  if (state === 'none' || state === 'loading') return null;
  const { five, week } = pickPair(fam, modelId);
  if (!five && !week) return null;
  const line = (label: string, w: UsageWindow) => {
    const reset = resetLabel(w, lang, nowIso ?? limits?.now);
    return `${label}: ${t('use.used', { yuzde: clampPct(w.used_pct) })}${reset ? `, ${t('use.resets', { zaman: reset })}` : ''}`;
  };
  const title = [
    five && line(t('use.fiveH'), five),
    week && line(t('use.week'), week),
    state === 'stale' && t('use.stale'),
  ].filter(Boolean).join('\n');
  const part = (key: '5h' | 'week', label: string, w: UsageWindow) => {
    const v = clampPct(w.used_pct);
    return (
      <span className={`strip-use-part strip-use-${key}`} data-hot={v >= HOT_PCT || undefined}>
        {label}
        <Meter value={v} base="energy" segments={5} className="strip-use-meter" />
        <b className="num">{t('strip.pct', { yuzde: v })}</b>
      </span>
    );
  };
  return (
    <>
      {withSep && <span className="strip-sep strip-sep-use" aria-hidden="true" />}
      <span
        className="strip-item strip-usage"
        data-guide="strip-usage"
        data-testid="strip-usage"
        data-state={state}
        role="group"
        title={title}
        aria-label={`${t('strip.usage')}. ${title}`}
      >
        {t('strip.usage')}
        {five && part('5h', t('strip.usage5h'), five)}
        {five && week && <span className="strip-use-dot" aria-hidden="true">·</span>}
        {week && part('week', t('strip.usageWeek'), week)}
      </span>
    </>
  );
};
