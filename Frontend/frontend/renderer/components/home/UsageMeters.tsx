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
export const Meter = ({ value, loading = false, className = '' }: { value: number | null; loading?: boolean; className?: string }) => {
  const v = value == null ? 0 : clampPct(value);
  const on = loading || value == null ? 0 : Math.round(v / 10);
  const hot = !loading && value != null && v >= HOT_PCT;
  return (
    <span
      className={`meter${hot ? ' is-hot' : ''}${loading ? ' is-loading' : ''}${className ? ` ${className}` : ''}`}
      data-v={loading || value == null ? undefined : v}
      aria-hidden="true"
    >
      {Array.from({ length: 10 }, (_, i) => <i key={i} className={i < on ? 'on' : undefined} />)}
    </span>
  );
};

/** One "5 saat ▮▮▮▯ %62  18:40" line of the menu's and the settings page's usage box. */
export const UseRow = ({ label, win, state, nowIso }: { label: string; win: UsageWindow | null; state: MeterState; nowIso?: string | null }) => {
  const { lang } = useLang();
  const loading = state === 'loading';
  const v = win ? clampPct(win.used_pct) : null;
  return (
    <div className="use-row" data-testid="use-row">
      <span className="use-k">{label}</span>
      <Meter value={v} loading={loading || !win} />
      <span className="use-v num">{!loading && v != null ? `%${v}` : ''}</span>
      <span className="use-r">{!loading ? resetLabel(win, lang, nowIso) : ''}</span>
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
