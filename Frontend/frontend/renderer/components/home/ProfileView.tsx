import React, { useEffect, useRef } from 'react';

import { useLang, type Lang, type TKey } from '../../lib/i18n';
import { displayName } from '../../lib/displayName';
import {
  PROFILE_RANGES, favouriteView, formatDecimal, formatHour, formatLongDay, formatNumber, formatShortDay,
  heatModel, mixRows, monthDelta, nextRank, perActiveDay, weekdayName, xpPercent,
  type AchievementId, type ProfileAchievement, type ProfileRange, type ProfileStats,
} from '../../lib/profileStats';
import { ModelLogo } from '../ui/ModelLogos';
import { MascotHead } from './BrandLogo';

export interface ProfileViewProps {
  open: boolean;
  onClose: () => void;
  /** The selected range's answer, or the previous answer while its replacement loads. */
  data: ProfileStats | null;
  range: ProfileRange;
  onRangeChange: (r: ProfileRange) => void;
  loading?: boolean;
  failed?: boolean;
  onRetry?: () => void;
  userName?: string | null;
}

// Badge icons, from the mockup's sprite (index.html <symbol id="i-…">), 20x20 stroke icons.
const ACH_ICON: Record<AchievementId, React.ReactNode> = {
  first_task: <path d="M5 17.5V3M5 3.5h9.5l-2 3.2 2 3.3H5" />,
  tasks_100: <><path d="M3.5 6.5l1.5-1v9" /><rect x="7.5" y="5.5" width="4" height="9" rx="2" /><rect x="13" y="5.5" width="4" height="9" rx="2" /></>,
  tasks_1000: <><path d="M3 7l7-3.5L17 7l-7 3.5z" /><path d="M3 10.5L10 14l7-3.5M3 14l7 3.5 7-3.5" /></>,
  night_owl: <path d="M15.5 12.5A6.5 6.5 0 017.5 4.5a6.5 6.5 0 108 8z" />,
  streak_7: <><circle cx="10" cy="10" r="7" /><path d="M10 6v4.3l2.8 1.8" /></>,
  pocket: <><rect x="6" y="2.5" width="8" height="15" rx="1.6" /><path d="M9 15h2" /></>,
  careful: <><path d="M10 2.8l5.6 2.2v4.4c0 3.6-2.4 6.2-5.6 7.6-3.2-1.4-5.6-4-5.6-7.6V5z" /><path d="M7.6 10l1.7 1.7 3.2-3.4" /></>,
  polyglot: <><rect x="3" y="3" width="6" height="6" rx="1.2" /><rect x="11" y="3" width="6" height="6" rx="1.2" /><rect x="3" y="11" width="6" height="6" rx="1.2" /><path d="M14 11.5v5M11.5 14h5" /></>,
};
const LOCK_ICON = <><rect x="4.5" y="9" width="11" height="8" rx="1" /><path d="M7 9V6.5a3 3 0 016 0V9" /></>;

const Num = ({ children }: { children: React.ReactNode }) => <span className="num">{children}</span>;

const percent = (n: number, lang: Lang) =>
  new Intl.NumberFormat(lang === 'tr' ? 'tr-TR' : 'en-GB', { style: 'percent', maximumFractionDigits: 0 }).format(n / 100);

interface Tile { id: string; label: string; value: React.ReactNode; sub: string; tip?: string; unknown?: boolean }

/**
 * The maker profile (mockup screen 2, `?screen=profil`, approved 2 Oct 2026; PROFIL-PLANI.md).
 * Sections in mockup order with real data only: header, stat tiles, 26-week heat map, model mix
 * with the favourite model, achievement shelf. Mockup tiles with no data source yet (most
 * written file, Unity actions, objects placed) are left out, as are the mockup's quips that are
 * not computed from the user's own numbers. A fresh install renders every section empty.
 */
export const ProfileView = ({
  open, onClose, data, range, onRangeChange, loading = false, failed = false, onRetry, userName,
}: ProfileViewProps) => {
  const { t, lang } = useLang();
  const rootRef = useRef<HTMLElement>(null);

  // Esc returns to the chat. The settings screen sits above the profile and owns Esc while open;
  // a confirm dialog handles its own Esc first.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return;
      if (document.querySelector('[data-confirm-dialog], [role="alertdialog"], [data-testid="settings-screen"]')) return;
      onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  useEffect(() => {
    if (open && rootRef.current) {
      rootRef.current.scrollTop = 0;
      rootRef.current.focus({ preventScroll: true });
    }
  }, [open]);

  if (!open) return null;

  const name = displayName(userName);
  const n = (v: number) => formatNumber(v, lang);
  const days = (v: number) => t(v === 1 ? 'pf.day' : 'pf.days', { n: n(v) });

  const back = (
    <button type="button" className="pf-back" onClick={onClose} data-testid="profile-back">
      <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><path d="M11.5 5.5L7 10l4.5 4.5M7.5 10H16" /></svg>
      <span>{t('pf.back')}</span>
      <kbd>Esc</kbd>
    </button>
  );

  const rangeTabs = (
    <div className="pf-range" role="tablist" aria-label={t('pf.rangeLabel')} aria-busy={loading || undefined}>
      {PROFILE_RANGES.map(r => (
        <button key={r} type="button" role="tab" aria-selected={r === range} data-range={r}
          onClick={() => { if (r !== range) onRangeChange(r); }}>
          {t(`pf.range.${r}` as TKey)}
        </button>
      ))}
    </div>
  );
  const readState = loading ? <p className="pf-state" role="status">{t('pf.loading')}</p>
    : failed ? (
      <div className="pf-state" role="alert">
        <p>{t('pf.failed')}</p>
        {onRetry && <button type="button" className="btn btn-ghost btn-sm" onClick={onRetry}>{t('pf.retry')}</button>}
      </div>
    ) : null;

  if (!data) {
    return (
      <main className="profile paper" aria-label={t('pf.title')} data-testid="profile-view" ref={rootRef} tabIndex={-1}>
        <div className="pf-wrap">
          {back}
          {rangeTabs}
          {readState ?? <p className="pf-state" role="status">{t('pf.loading')}</p>}
        </div>
      </main>
    );
  }

  const s = data;
  const c = s.counts;
  const rankName = t(`pf.rank.${s.rank}` as TKey);
  const next = nextRank(s.rank);
  const heat = heatModel(s.heatmap, lang);
  const mix = mixRows(s.models.mix);
  const fav = s.models.favourite ? favouriteView(s.models.favourite) : null;
  const unlocked = s.achievements.filter(a => a.unlocked).length;
  const avg = perActiveDay(c);

  // ---- stat tiles: only those with a data source (PROFIL-PLANI §5) ----
  const delta = monthDelta(c);
  const ledgerTile = (v: number | null) => (v == null ? { value: '—', unknown: true, tip: t('pf.stat.ledgerTip') } : { value: <Num>{n(v)}</Num> });
  const cards = ledgerTile(c.approved_cards);
  const phone = ledgerTile(c.phone_approvals);
  const tiles: Tile[] = [
    {
      id: 'tasks', label: t('pf.stat.tasks'), value: <Num>{n(c.tasks)}</Num>,
      sub: c.tasks_this_month === 0 && c.tasks_last_month === 0
        ? t('pf.stat.tasksNone')
        : t(delta.kind === 'more' ? 'pf.stat.tasksMore' : delta.kind === 'less' ? 'pf.stat.tasksLess' : 'pf.stat.tasksSame',
          { month: n(c.tasks_this_month), n: n(delta.n) }),
    },
    {
      id: 'cards', label: t('pf.stat.cards'), ...cards,
      sub: c.approved_cards == null ? t('pf.stat.ledgerDown')
        : c.rejected_cards ? t('pf.stat.cardsRejected', { n: n(c.rejected_cards) }) : t('pf.stat.cardsNoReject'),
    },
    { id: 'days', label: t('pf.stat.days'), value: <Num>{n(c.active_days)}</Num>, sub: t('pf.stat.daysSub') },
    {
      id: 'streak', label: t('pf.stat.streak'),
      value: <><Num>{n(s.streak.longest)}</Num> {t(s.streak.longest === 1 ? 'pf.day' : 'pf.days', { n: '' }).trim()}</>,
      sub: s.streak.longest === 0 ? t('pf.stat.streakNone')
        : s.streak.current > 0 && s.streak.current === s.streak.longest ? t('pf.stat.streakNow')
          : s.streak.longest_end ? t('pf.stat.streakEnded', { date: formatShortDay(s.streak.longest_end, lang) }) : '',
    },
    {
      id: 'hour', label: t('pf.stat.hour'),
      value: s.best_hour == null ? '—' : <Num>{formatHour(s.best_hour)}</Num>,
      unknown: s.best_hour == null,
      sub: s.best_hour == null ? t('pf.stat.hourNone') : t('pf.stat.hourSub'),
    },
    {
      id: 'phone', label: t('pf.stat.phone'), ...phone,
      sub: c.phone_approvals == null ? t('pf.stat.ledgerDown') : t('pf.stat.phoneSub'),
    },
  ];

  // A quip only when it is the user's own number: a running streak of two days or more.
  const quip = s.streak.current >= 2
    ? t('pf.quip.streak', { n: n(s.streak.current), next: n(s.streak.current + 1) })
    : null;

  const achTile = (a: ProfileAchievement) => {
    const nameKey = `pf.ach.${a.id}.name` as TKey;
    if (a.unlocked === null) {
      return (
        <li key={a.id} className="achv-tile is-unknown" data-ach={a.id} title={t('pf.stat.ledgerTip')}>
          <span className="achv-badge" aria-hidden="true"><svg className="ic" viewBox="0 0 20 20">{ACH_ICON[a.id]}</svg></span>
          <span className="achv-t">{t(nameKey)}</span>
          <span className="achv-d">—</span>
        </li>
      );
    }
    if (a.unlocked) {
      return (
        <li key={a.id} className={`achv-tile is-got${a.new ? ' is-new' : ''}`} data-ach={a.id}>
          <span className="achv-badge" aria-hidden="true"><svg className="ic" viewBox="0 0 20 20">{ACH_ICON[a.id]}</svg></span>
          <span className="achv-t">{t(nameKey)}{a.new && <span className="sr-only"> · {t('pf.ach.new')}</span>}</span>
          <span className="achv-d">
            {t(`pf.ach.${a.id}.done` as TKey)}
            {a.unlocked_at && <> · <Num>{formatShortDay(a.unlocked_at, lang)}</Num></>}
          </span>
        </li>
      );
    }
    const pct = Math.max(0, Math.min(100, ((a.progress ?? 0) / a.goal) * 100));
    return (
      <li key={a.id} className="achv-tile is-locked" data-ach={a.id}>
        <span className="achv-badge" aria-hidden="true"><svg className="ic" viewBox="0 0 20 20">{LOCK_ICON}</svg></span>
        <span className="achv-t">{t(nameKey)}<span className="sr-only"> · {t('pf.ach.locked')}</span></span>
        <span className="achv-d">
          <span className="achv-hint">{t(`pf.ach.${a.id}.hint` as TKey)}</span>
          <span className="achv-pl">
            <span className="achv-prog" aria-hidden="true"><span style={{ width: `${pct}%` }} /></span>
            <Num>{a.progress === null ? '—' : n(a.progress)} / {n(a.goal)}</Num>
          </span>
        </span>
      </li>
    );
  };

  return (
    <main className="profile paper" aria-label={t('pf.title')} data-testid="profile-view" data-range={range}
      aria-busy={loading || undefined} ref={rootRef} tabIndex={-1}>
      <div className="pf-wrap">
        {back}
        <header className="pf-head">
          <div className="pf-emblem">
            <div className="lvl lvl-lg" aria-label={t('pf.levelN', { n: s.level })} data-testid="profile-level">
              <span className="lvl-k" aria-hidden="true">{t('pf.level')}</span>
              <span className="lvl-n" aria-hidden="true">{s.level}</span>
            </div>
            <MascotHead className="mascot pf-mascot" />
          </div>
          <div className="pf-id">
            <p className="pf-kicker">{t('pf.title')}</p>
            <h1 className="pf-name">
              {name
                ? <><span className="pf-name-t">{name}</span> <span className="pf-rank">{rankName}</span></>
                : <span className="pf-name-t">{rankName}</span>}
            </h1>
            <div className="pf-xp">
              <span className="xp-bar" aria-hidden="true"><span style={{ width: `${xpPercent(s)}%` }} /></span>
              <span className="pf-xp-text">
                <Num>{t('pf.xpText', { xp: n(s.level_xp), need: n(s.level_need) })}</Num>
                {' · '}
                {next
                  ? <>{t('pf.nextRank')} <b>{t(`pf.rank.${next.rank}` as TKey)}</b> ({t('pf.nextRankAt', { n: next.level })})</>
                  : t('pf.topRank')}
              </span>
              {s.xp_partial && <small className="pf-xp-partial" title={t('pf.stat.ledgerTip')}>{t('pf.xpPartial')}</small>}
              <span className="pf-since">{s.since ? t('pf.since', { date: formatLongDay(s.since, lang) }) : t('pf.noRecords')}</span>
            </div>
          </div>
          <div className="pf-side">
            {rangeTabs}
            {quip && <p className="pf-quip">{quip}</p>}
          </div>
        </header>
        {readState}

        <section className="pf-stats" aria-label={t('pf.statsLabel')}>
          {tiles.map(tile => (
            <div key={tile.id} className={`pf-stat${tile.unknown ? ' is-unknown' : ''}`} data-stat={tile.id} title={tile.tip}>
              <span className="pf-stat-k">{tile.label}</span>
              <span className="pf-stat-v">{tile.value}</span>
              <span className="pf-stat-s">{tile.sub}</span>
            </div>
          ))}
        </section>

        <div className="pf-row">
          <section className={`pf-card pf-heat${heat.empty ? ' is-empty' : ''}`} aria-label={t('pf.heat.label')}>
            <header className="pf-card-head">
              <h2 className="pf-h">{t('pf.heat.title')}</h2>
              <span className="pf-card-meta" data-testid="heat-meta">
                {heat.peak
                  ? t('pf.heat.meta', { days: n(heat.workedDays), date: formatShortDay(heat.peak.date, lang), n: n(heat.peak.count) })
                  : t('pf.heat.empty')}
              </span>
              <span className="heat-legend" aria-hidden="true">
                {t('pf.heat.less')} <i className="h0" /><i className="h1" /><i className="h2" /><i className="h3" /><i className="h4" /> {t('pf.heat.more')}
              </span>
            </header>
            <div className="heat">
              <div className="heat-months" aria-hidden="true">
                {heat.months.map(m => <span key={`${m.column}`} style={{ gridColumn: m.column }}>{m.label}</span>)}
              </div>
              <div className="heat-days" aria-hidden="true">
                <span>{weekdayName(0, lang, 'short')}</span><span /><span>{weekdayName(2, lang, 'short')}</span><span />
                <span>{weekdayName(4, lang, 'short')}</span><span /><span />
              </div>
              <div className="heat-grid" data-testid="heat-grid">
                {heat.weeks.flat().map(cell => (cell.future
                  ? <i key={cell.date} className="h-x" data-day={cell.date} />
                  : <i key={cell.date} className={`h${cell.level}`} data-day={cell.date}
                    title={t('pf.heat.cell', { date: formatShortDay(cell.date, lang), n: n(cell.count) })} />))}
              </div>
            </div>
            <dl className="heat-facts">
              <div><dt>{t('pf.heat.current')}</dt><dd>{days(s.streak.current)}</dd></div>
              <div><dt>{t('pf.heat.month')}</dt><dd><Num>{n(heat.monthActive)}</Num> / <Num>{n(heat.monthElapsed)}</Num></dd></div>
              <div><dt>{t('pf.heat.busiest')}</dt><dd>{s.busiest_weekday == null ? '—' : weekdayName(s.busiest_weekday, lang)}</dd></div>
              <div><dt>{t('pf.heat.avg')}</dt><dd>{avg == null ? '—' : t('pf.heat.avgN', { n: formatDecimal(avg, lang) })}</dd></div>
            </dl>
          </section>

          <section className="pf-card pf-models" aria-label={t('pf.mix.label')}>
            <header className="pf-card-head"><h2 className="pf-h">{t('pf.mix.title')}</h2><span className="pf-card-meta">{t('pf.mix.meta')}</span></header>
            {mix.length > 0 ? (
              <>
                <div className="mix" aria-hidden="true">
                  {mix.map(m => <span key={m.key} className="mix-seg" data-m={m.colour} style={{ width: `${m.percent}%` }} />)}
                </div>
                <ul className="mix-list">
                  {mix.map(m => (
                    <li key={m.key} data-m={m.colour} data-family={m.key}>
                      <span className="model-chip" lang={m.kind === 'family' ? 'en' : undefined}>
                        {m.brand ? <ModelLogo provider={m.brand} size={13} className="mix-logo" /> : <span className="mix-dot" />}
                        {m.kind === 'unknown' ? t('pf.mix.unknown') : m.kind === 'rest' ? t('pf.mix.rest') : m.name}
                      </span>
                      <span className="mix-n"><Num>{percent(m.percent, lang)}</Num> · {t('pf.mix.turns', { n: n(m.turns) })}</span>
                    </li>
                  ))}
                </ul>
              </>
            ) : (
              <p className="pf-empty">{t('pf.mix.empty')}</p>
            )}
            <div className="pf-fav" data-testid="profile-favourite">
              <p className="pf-fav-k">{t('pf.fav.title')} <span className="pf-fav-meta">· {t('pf.fav.meta')}</span></p>
              {fav ? (
                <p className="pf-fav-v" data-m={fav.colour}>
                  {fav.brand && <ModelLogo provider={fav.brand} size={16} className="mix-logo" />}
                  <span className="pf-fav-name" lang="en">{fav.model}</span>
                  {fav.agentName && <span className="pf-fav-agent" lang="en">{fav.agentName}</span>}
                  <span className="pf-fav-n">{t('pf.mix.turns', { n: n(fav.turns) })}</span>
                </p>
              ) : (
                <p className="pf-fav-v is-none"><span>{t('pf.fav.none')}</span><span className="pf-fav-hint">{t('pf.fav.noneHint')}</span></p>
              )}
            </div>
          </section>
        </div>

        <section className="pf-card pf-shelf" aria-label={t('pf.shelf.label')}>
          <header className="pf-card-head">
            <h2 className="pf-h">{t('pf.shelf.title')}</h2>
            <span className="pf-card-meta"><Num>{t('pf.shelf.meta', { n: unlocked, total: s.achievements.length })}</Num></span>
          </header>
          <ul className="shelf">{s.achievements.map(achTile)}</ul>
        </section>
      </div>
    </main>
  );
};
