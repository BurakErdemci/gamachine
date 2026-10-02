/**
 * The maker profile's data (`GET /profile/stats`, Backend/app/profile_stats.py) and the pure
 * mapping from it to what each profile section draws. Nothing here invents a number: a value the
 * backend did not send (null) stays null and the view shows its own "not yet" text.
 */
import { messageAgent } from '../components/home/messageAgent';
import type { ModelFamily } from './modelFamily';
import type { Lang } from './i18n';

export type ProfileRange = 'month' | '6m' | 'all';
export const PROFILE_RANGES: readonly ProfileRange[] = ['month', '6m', 'all'];

export type ProfileRank = 'rookie' | 'prototyper' | 'scene_master' | 'prefab_wizard' | 'engine_whisperer';
/** Rank order and the level each one starts at (profile_stats.compute: a new title every 5 levels). */
export const RANKS: readonly ProfileRank[] = ['rookie', 'prototyper', 'scene_master', 'prefab_wizard', 'engine_whisperer'];
export const RANK_EVERY = 5;

export type AchievementId =
  | 'first_task' | 'tasks_100' | 'tasks_1000' | 'night_owl' | 'streak_7' | 'pocket' | 'careful' | 'polyglot';
export const ACHIEVEMENT_IDS: readonly AchievementId[] = [
  'first_task', 'tasks_100', 'tasks_1000', 'night_owl', 'streak_7', 'pocket', 'careful', 'polyglot',
];

export interface ProfileAchievement {
  id: AchievementId;
  goal: number;
  progress: number | null;
  unlocked: boolean | null;
  unlocked_at: string | null;
  new: boolean;
}

export interface ProfileStats {
  range: ProfileRange;
  since: string | null;
  ledger_ok: boolean;
  xp_partial: boolean;
  xp: number;
  level: number;
  level_xp: number;
  level_need: number;
  rank: ProfileRank;
  counts: {
    tasks: number;
    tasks_this_month: number;
    tasks_last_month: number;
    approved_cards: number | null;
    rejected_cards: number | null;
    phone_approvals: number | null;
    active_days: number;
  };
  streak: { current: number; longest: number; longest_end: string | null };
  best_hour: number | null;
  busiest_weekday: number | null;
  heatmap: { start: string; today: string; days: number[]; levels: number[] };
  models: {
    mix: { family: string; turns: number; share: number }[];
    favourite: { model: string; family: string | null; turns: number } | null;
  };
  achievements: ProfileAchievement[];
}

const int = (v: unknown, fallback = 0): number => (typeof v === 'number' && Number.isFinite(v) ? Math.max(0, Math.floor(v)) : fallback);
const intOrNull = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? Math.max(0, Math.floor(v)) : null);
const str = (v: unknown): string | null => (typeof v === 'string' && v ? v : null);
const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

/**
 * The response as the view may trust it, or null when it is not a profile answer at all. A
 * field of the wrong type falls back to "no data" (0 / null), never to a made-up value.
 */
export function normalizeProfileStats(raw: unknown): ProfileStats | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, any>;
  if (typeof r.level !== 'number' || !r.counts || typeof r.counts !== 'object' || !r.heatmap) return null;
  const c = r.counts;
  const ledgerOk = r.ledger_ok !== false;
  const heat = r.heatmap || {};
  const days = Array.isArray(heat.days) ? heat.days.map((d: unknown) => int(d)) : [];
  const levels = Array.isArray(heat.levels) ? heat.levels.map((d: unknown) => Math.min(4, int(d))) : [];
  const rank = RANKS.includes(r.rank) ? (r.rank as ProfileRank) : 'rookie';
  const range = PROFILE_RANGES.includes(r.range) ? (r.range as ProfileRange) : 'all';
  const mix = Array.isArray(r.models?.mix)
    ? r.models.mix
      .filter((m: any) => m && typeof m.family === 'string')
      .map((m: any) => ({ family: m.family as string, turns: int(m.turns), share: typeof m.share === 'number' && Number.isFinite(m.share) ? Math.max(0, Math.min(1, m.share)) : 0 }))
    : [];
  const fav = r.models?.favourite;
  const achievements: ProfileAchievement[] = Array.isArray(r.achievements)
    ? r.achievements
      .filter((a: any) => a && ACHIEVEMENT_IDS.includes(a.id))
      .map((a: any) => ({
        id: a.id as AchievementId,
        goal: Math.max(1, int(a.goal, 1)),
        progress: a.progress === null || (!ledgerOk && (a.id === 'careful' || a.id === 'pocket')) ? null : int(a.progress),
        unlocked: a.unlocked === null || (!ledgerOk && (a.id === 'careful' || a.id === 'pocket')) ? null : a.unlocked === true,
        unlocked_at: str(a.unlocked_at),
        new: a.new === true,
      }))
    : [];
  return {
    range,
    since: str(r.since),
    ledger_ok: ledgerOk,
    xp_partial: r.xp_partial === true,
    xp: int(r.xp),
    level: Math.max(1, int(r.level, 1)),
    level_xp: int(r.level_xp),
    level_need: Math.max(1, int(r.level_need, 100)),
    rank,
    counts: {
      tasks: int(c.tasks),
      tasks_this_month: int(c.tasks_this_month),
      tasks_last_month: int(c.tasks_last_month),
      // ledger_ok false: the backend could not read the approval ledger; its counts are unknown,
      // not zero.
      approved_cards: ledgerOk ? intOrNull(c.approved_cards) : null,
      rejected_cards: ledgerOk ? intOrNull(c.rejected_cards) : null,
      phone_approvals: ledgerOk ? intOrNull(c.phone_approvals) : null,
      active_days: int(c.active_days),
    },
    streak: {
      current: int(r.streak?.current),
      longest: int(r.streak?.longest),
      longest_end: str(r.streak?.longest_end),
    },
    best_hour: typeof r.best_hour === 'number' && r.best_hour >= 0 && r.best_hour < 24 ? Math.floor(r.best_hour) : null,
    busiest_weekday: typeof r.busiest_weekday === 'number' && r.busiest_weekday >= 0 && r.busiest_weekday < 7 ? Math.floor(r.busiest_weekday) : null,
    heatmap: {
      start: typeof heat.start === 'string' && ISO_DAY.test(heat.start) ? heat.start : '',
      today: typeof heat.today === 'string' && ISO_DAY.test(heat.today) ? heat.today : '',
      days,
      levels,
    },
    models: {
      mix,
      favourite: fav && typeof fav.model === 'string' && fav.model
        ? { model: fav.model, family: str(fav.family), turns: int(fav.turns) }
        : null,
    },
    achievements,
  };
}

// ---------- formatting ----------

export const localeOf = (lang: Lang) => (lang === 'tr' ? 'tr-TR' : 'en-GB');

export const formatNumber = (n: number, lang: Lang): string => new Intl.NumberFormat(localeOf(lang)).format(n);

export const formatDecimal = (n: number, lang: Lang): string =>
  new Intl.NumberFormat(localeOf(lang), { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(n);

/** 'YYYY-MM-DD' (or a 'YYYY-MM-DD HH:MM:SS' local stamp) as a UTC midnight, or null. */
export function parseDay(value: string | null | undefined): Date | null {
  if (!value) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  if (!m) return null;
  const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
  return Number.isNaN(d.getTime()) ? null : d;
}

const dayFmt = (lang: Lang, opts: Intl.DateTimeFormatOptions) =>
  new Intl.DateTimeFormat(localeOf(lang), { ...opts, timeZone: 'UTC' });

/** "19 Ağu" / "19 Aug". */
export const formatShortDay = (value: string | Date, lang: Lang): string => {
  const d = typeof value === 'string' ? parseDay(value) : value;
  return d ? dayFmt(lang, { day: 'numeric', month: 'short' }).format(d) : '';
};

/** "14 Haziran 2026" / "14 June 2026". */
export const formatLongDay = (value: string, lang: Lang): string => {
  const d = parseDay(value);
  return d ? dayFmt(lang, { day: 'numeric', month: 'long', year: 'numeric' }).format(d) : '';
};

/** 0 = Monday (Python's weekday()), as the backend sends `busiest_weekday`. */
export const weekdayName = (index: number, lang: Lang, style: 'long' | 'short' = 'long'): string =>
  dayFmt(lang, { weekday: style }).format(new Date(Date.UTC(2024, 0, 1 + index))); // 1 Jan 2024 was a Monday

export const formatHour = (hour: number): string => `${String(hour).padStart(2, '0')}:00`;

// ---------- header ----------

/** The rank after `rank` and the level it starts at; null at the top rank. */
export function nextRank(rank: ProfileRank): { rank: ProfileRank; level: number } | null {
  const i = RANKS.indexOf(rank);
  if (i < 0 || i >= RANKS.length - 1) return null;
  return { rank: RANKS[i + 1], level: (i + 1) * RANK_EVERY };
}

/** XP bar fill, 0..100. */
export const xpPercent = (s: Pick<ProfileStats, 'level_xp' | 'level_need'>): number =>
  Math.max(0, Math.min(100, (s.level_xp / Math.max(1, s.level_need)) * 100));

// ---------- heat map ----------

export interface HeatCell { date: string; count: number; level: number; future: boolean }
export interface HeatModel {
  /** 26 columns (weeks) of 7 cells, Monday first. */
  weeks: HeatCell[][];
  /** Month label and the 1-based column it starts at. */
  months: { label: string; column: number }[];
  /** Days with at least one task in the window. */
  workedDays: number;
  /** The single busiest day in the window, or null with no task. */
  peak: { date: string; count: number } | null;
  /** Active days this calendar month so far, and the days elapsed in it. */
  monthActive: number;
  monthElapsed: number;
  empty: boolean;
}

const isoOf = (d: Date) => d.toISOString().slice(0, 10);

export function heatModel(heatmap: ProfileStats['heatmap'], lang: Lang): HeatModel {
  const start = parseDay(heatmap.start);
  const today = parseDay(heatmap.today);
  const weeks: HeatCell[][] = [];
  const months: { label: string; column: number }[] = [];
  let workedDays = 0;
  let peak: HeatModel['peak'] = null;
  let monthActive = 0;
  if (!start || !today) {
    return { weeks, months, workedDays, peak, monthActive, monthElapsed: 0, empty: true };
  }
  const monthName = dayFmt(lang, { month: 'short' });
  let lastMonth = -1;
  const total = Math.max(heatmap.days.length, 182);
  for (let w = 0; w * 7 < total; w++) {
    const col: HeatCell[] = [];
    const first = new Date(start.getTime() + w * 7 * 86_400_000);
    if (first.getUTCMonth() !== lastMonth && first.getUTCDate() <= 7) {
      months.push({ label: monthName.format(first), column: w + 1 });
      lastMonth = first.getUTCMonth();
    }
    for (let d = 0; d < 7; d++) {
      const i = w * 7 + d;
      const date = new Date(start.getTime() + i * 86_400_000);
      const future = date.getTime() > today.getTime();
      const count = future ? 0 : heatmap.days[i] ?? 0;
      const level = future ? 0 : Math.min(4, heatmap.levels[i] ?? 0);
      col.push({ date: isoOf(date), count, level, future });
      if (!future && count > 0) {
        workedDays++;
        if (!peak || count > peak.count) peak = { date: isoOf(date), count };
        if (date.getUTCFullYear() === today.getUTCFullYear() && date.getUTCMonth() === today.getUTCMonth()) monthActive++;
      }
    }
    weeks.push(col);
  }
  return { weeks, months, workedDays, peak, monthActive, monthElapsed: today.getUTCDate(), empty: workedDays === 0 };
}

// ---------- model mix ----------

export interface MixRow {
  key: string;
  /** Display name ("Claude Code", "Codex", "Ollama"), or null for the unknown / "other" rows. */
  name: string | null;
  /** ModelLogo brand key, or null. */
  brand: string | null;
  colour: ModelFamily;
  turns: number;
  percent: number;
  kind: 'family' | 'unknown' | 'rest';
}

/** Which of the four mockup colours a backend agent family wears. */
export function familyColour(family: string | null | undefined): ModelFamily {
  const f = (family || '').toLowerCase();
  if (f === 'claude' || f === 'api-anthropic') return 'claude';
  if (f === 'codex' || f === 'api-openai') return 'codex';
  if (f === 'agy' || f === 'api-google' || f === 'api-gemini') return 'gemini';
  return 'other';
}

/** Rows for the mix bar and its list: the first `max - 1` families, the rest folded into one. */
export function mixRows(mix: ProfileStats['models']['mix'], max = 5): MixRow[] {
  const shown = mix.length <= max ? mix : [
    ...mix.slice(0, max - 1),
    { family: 'rest', turns: mix.slice(max - 1).reduce((sum, m) => sum + m.turns, 0),
      share: mix.slice(max - 1).reduce((sum, m) => sum + m.share, 0) },
  ];
  const total = shown.reduce((sum, m) => sum + m.share, 0);
  // Normalize the backend's rounded shares, then award remaining points by remainder
  // (profile screen audit, 2 Oct 2026).
  const exact = shown.map(m => total > 0 ? m.share / total * 100 : 0);
  const percents = exact.map(Math.floor);
  const order = exact.map((value, i) => ({ i, remainder: value - percents[i] }))
    .sort((a, b) => b.remainder - a.remainder || a.i - b.i);
  const remaining = total > 0 ? 100 - percents.reduce((sum, p) => sum + p, 0) : 0;
  for (let i = 0; i < remaining; i++) percents[order[i].i]++;
  const row = (m: { family: string; turns: number }, i: number): MixRow => {
    if (mix.length > max && i === max - 1) {
      return { key: 'rest', name: null, brand: null, colour: 'other', turns: m.turns, percent: percents[i], kind: 'rest' };
    }
    if (m.family === 'unknown') {
      return { key: 'unknown', name: null, brand: null, colour: 'other', turns: m.turns, percent: percents[i], kind: 'unknown' };
    }
    const agent = messageAgent(m.family);
    return {
      key: m.family, name: agent?.name ?? m.family, brand: agent?.brand ?? null,
      colour: familyColour(m.family), turns: m.turns, percent: percents[i], kind: 'family',
    };
  };
  return shown.map(row);
}

/** The favourite model's name and logo. */
export function favouriteView(fav: NonNullable<ProfileStats['models']['favourite']>) {
  const agent = fav.family ? messageAgent(fav.family, fav.model) : null;
  return {
    agentName: agent?.name ?? null,
    brand: agent?.brand ?? null,
    model: agent?.model ?? fav.model,
    colour: familyColour(fav.family),
    turns: fav.turns,
  };
}

// ---------- tiles ----------

/** This month against last month, for the "Biten görev" tile's sub line. */
export function monthDelta(c: Pick<ProfileStats['counts'], 'tasks_this_month' | 'tasks_last_month'>): { kind: 'more' | 'less' | 'same'; n: number } {
  const d = c.tasks_this_month - c.tasks_last_month;
  return d > 0 ? { kind: 'more', n: d } : d < 0 ? { kind: 'less', n: -d } : { kind: 'same', n: 0 };
}

/** Tasks per active day in the range, or null with no active day. */
export const perActiveDay = (c: Pick<ProfileStats['counts'], 'tasks' | 'active_days'>): number | null =>
  (c.active_days > 0 ? c.tasks / c.active_days : null);
