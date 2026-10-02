/**
 * Subscription usage limits (`GET /usage/limits`): the 5-hour and weekly windows of the
 * Claude Code, Codex and Antigravity plans, as the backend measured them.
 *
 * The contract is fixed by the backend lane (2 Oct 2026). This file only READS it: nothing
 * here invents a number. A family missing from the response, or one whose status is
 * `unavailable`, simply has no meters.
 */

import { stripBidi } from './modelText';

export type UsageFamilyId = 'claude' | 'codex' | 'agy';
export type UsageStatus = 'ok' | 'loading' | 'error' | 'unavailable';

export interface UsageWindow {
  id: string;
  group: string | null;
  label: string;
  kind: '5h' | 'week';
  used_pct: number;
  resets_at: string | null;
  resets_text: string | null;
}

export interface UsageFamily {
  family: UsageFamilyId;
  status: UsageStatus;
  plan: string | null;
  measured_at: string | null;
  stale: boolean;
  error: string | null;
  windows: UsageWindow[];
}

export interface UsageLimits {
  now: string;
  families: UsageFamily[];
}

/** The model menu's CLI group key (ModelSelector `CLI_GROUPS`) -> the usage family. */
export const GROUP_USAGE_FAMILY: Record<string, UsageFamilyId> = {
  claude: 'claude',
  codex: 'codex',
  gemini: 'agy',
};

export function familyFor(limits: UsageLimits | null | undefined, family: UsageFamilyId | null | undefined): UsageFamily | null {
  if (!limits || !family || !Array.isArray(limits.families)) return null;
  return limits.families.find(f => f && f.family === family) ?? null;
}

/** Antigravity splits its quota by model group ("Gemini" vs "Claude and GPT"). */
function agyGroupMatches(group: string | null, modelId: string): boolean {
  const g = (group || '').toLowerCase();
  const m = modelId.toLowerCase();
  if (!g || !m) return false;
  if (m.startsWith('gemini')) return g.includes('gemini');
  if (m.startsWith('agy-claude') || m.startsWith('agy-gpt')) return g.includes('claude') || g.includes('gpt');
  return false;
}

function maxBy(list: UsageWindow[]): UsageWindow | null {
  let best: UsageWindow | null = null;
  for (const w of list) if (!best || w.used_pct > best.used_pct) best = w;
  return best;
}

export interface UsagePair {
  five: UsageWindow | null;
  week: UsageWindow | null;
}

/**
 * The two windows the chip and the menu rows draw for one family.
 *  - 5 h: the first window of kind "5h";
 *  - week: the window with id "week" (Claude's all-models week), else the first "week" window;
 *  - Antigravity: the group matching `modelId`; with no match (or no model of that family on
 *    screen) the fullest window of each kind, because that is the limit that bites first.
 */
export function pickPair(fam: UsageFamily | null, modelId?: string | null): UsagePair {
  const windows = Array.isArray(fam?.windows) ? fam!.windows.filter(w => w && typeof w.used_pct === 'number') : [];
  if (!fam || windows.length === 0) return { five: null, week: null };
  const fives = windows.filter(w => w.kind === '5h');
  const weeks = windows.filter(w => w.kind === 'week');
  if (fam.family === 'agy') {
    const id = modelId || '';
    const fiveHit = fives.find(w => agyGroupMatches(w.group, id));
    const weekHit = weeks.find(w => agyGroupMatches(w.group, id));
    return { five: fiveHit ?? maxBy(fives), week: weekHit ?? maxBy(weeks) };
  }
  return {
    five: fives[0] ?? null,
    week: weeks.find(w => w.id === 'week') ?? weeks[0] ?? null,
  };
}

/** How a family's meters are drawn. `none` = no meters at all (no numbers to show). */
export type MeterState = 'none' | 'loading' | 'ok' | 'stale';

export function meterState(fam: UsageFamily | null): MeterState {
  if (!fam) return 'none';
  if (fam.status === 'loading') return 'loading';
  if (fam.status === 'unavailable') return 'none';
  const hasWindows = Array.isArray(fam.windows) && fam.windows.length > 0;
  if (!hasWindows) return 'none';
  if (fam.status === 'error' || fam.stale) return 'stale';
  return 'ok';
}

export const HOT_PCT = 80;

export function clampPct(v: unknown): number {
  const n = typeof v === 'number' && Number.isFinite(v) ? v : 0;
  return Math.max(0, Math.min(100, Math.round(n)));
}

/**
 * The reset moment in the reader's own clock: "18:40" within a day, "Mon 09:00" further out.
 * Falls back to the backend's own `resets_text`, and to nothing when neither exists.
 */
export function resetLabel(w: UsageWindow | null, lang: string, nowIso?: string | null): string {
  if (!w) return '';
  if (w.resets_at) {
    const at = new Date(w.resets_at);
    if (!Number.isNaN(at.getTime())) {
      const locale = lang === 'tr' ? 'tr-TR' : 'en-GB';
      const now = nowIso ? new Date(nowIso) : new Date();
      const time = at.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' });
      const within = at.getTime() - (Number.isNaN(now.getTime()) ? Date.now() : now.getTime()) < 24 * 3600 * 1000;
      if (within) return time;
      return `${at.toLocaleDateString(locale, { weekday: 'short' })} ${time}`;
    }
  }
  return stripBidi(w.resets_text || '');
}

/** Minutes since the family was measured, or null when unknown. */
export function minutesSince(iso: string | null | undefined, nowIso?: string | null): number | null {
  if (!iso) return null;
  const at = new Date(iso).getTime();
  const now = nowIso ? new Date(nowIso).getTime() : Date.now();
  if (Number.isNaN(at) || Number.isNaN(now)) return null;
  return Math.max(0, Math.floor((now - at) / 60000));
}
