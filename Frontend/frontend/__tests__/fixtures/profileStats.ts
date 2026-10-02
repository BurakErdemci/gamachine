/**
 * `GET /profile/stats` answers in the backend's shape (Backend/app/profile_stats.py compute()).
 * Dates: today is Friday 2 Oct 2026, so the 26-week window starts Monday 6 Apr 2026 and its last
 * two cells (Sat 3, Sun 4 Oct) are still to come.
 */
const DAY = 86_400_000
const START = Date.UTC(2026, 3, 6)
export const dayIndex = (iso: string) => Math.round((Date.parse(`${iso}T00:00:00Z`) - START) / DAY)

function fullHeat() {
  const days = new Array(182).fill(0)
  const levels = new Array(182).fill(0)
  const set = (iso: string, n: number, lvl: number) => { days[dayIndex(iso)] = n; levels[dayIndex(iso)] = lvl }
  set('2026-04-14', 2, 1)
  set('2026-06-02', 5, 2)
  set('2026-08-19', 27, 4) // the busiest day
  set('2026-08-20', 9, 3)
  set('2026-10-01', 4, 2)
  set('2026-10-02', 3, 2) // today
  return { start: '2026-04-06', today: '2026-10-02', days, levels }
}

export const FULL = {
  range: '6m', since: '2026-04-14', ledger_ok: true, xp_partial: false,
  xp: 8420, level: 14, level_xp: 820, level_need: 1400, rank: 'scene_master',
  counts: {
    tasks: 312, tasks_this_month: 48, tasks_last_month: 37,
    approved_cards: 1148, rejected_cards: 37, phone_approvals: 18, active_days: 163,
  },
  streak: { current: 6, longest: 23, longest_end: '2026-08-26' },
  best_hour: 23, busiest_weekday: 1,
  heatmap: fullHeat(),
  models: {
    mix: [
      { family: 'claude', turns: 181, share: 0.58 },
      { family: 'codex', turns: 84, share: 0.269 },
      { family: 'agy', turns: 34, share: 0.109 },
      { family: 'api-ollama', turns: 13, share: 0.042 },
    ],
    favourite: { model: 'claude-opus-5-5', family: 'claude', turns: 37 },
  },
  achievements: [
    { id: 'first_task', goal: 1, progress: 1, unlocked: true, unlocked_at: '2026-04-14 10:02:11', new: false },
    { id: 'tasks_100', goal: 100, progress: 100, unlocked: true, unlocked_at: '2026-07-03 21:40:00', new: false },
    { id: 'tasks_1000', goal: 1000, progress: 312, unlocked: false, unlocked_at: null, new: false },
    { id: 'night_owl', goal: 50, progress: 50, unlocked: true, unlocked_at: '2026-10-02 01:12:00', new: true },
    { id: 'streak_7', goal: 7, progress: 7, unlocked: true, unlocked_at: '2026-08-10 12:00:00', new: false },
    { id: 'pocket', goal: 50, progress: 18, unlocked: false, unlocked_at: null, new: false },
    { id: 'careful', goal: 100, progress: 100, unlocked: true, unlocked_at: '2026-06-01 09:00:00', new: false },
    { id: 'polyglot', goal: 3, progress: 3, unlocked: true, unlocked_at: '2026-05-20 15:30:00', new: false },
  ],
}

/** A fresh install: the backend's answer with an empty activity table and an empty ledger. */
export const EMPTY = {
  range: '6m', since: null, ledger_ok: true, xp_partial: false,
  xp: 0, level: 1, level_xp: 0, level_need: 100, rank: 'rookie',
  counts: {
    tasks: 0, tasks_this_month: 0, tasks_last_month: 0,
    approved_cards: 0, rejected_cards: 0, phone_approvals: 0, active_days: 0,
  },
  streak: { current: 0, longest: 0, longest_end: null },
  best_hour: null, busiest_weekday: null,
  heatmap: { start: '2026-04-06', today: '2026-10-02', days: new Array(182).fill(0), levels: new Array(182).fill(0) },
  models: { mix: [], favourite: null },
  achievements: [
    ['first_task', 1], ['tasks_100', 100], ['tasks_1000', 1000], ['night_owl', 50],
    ['streak_7', 7], ['pocket', 50], ['careful', 100], ['polyglot', 3],
  ].map(([id, goal]) => ({ id, goal, progress: 0, unlocked: false, unlocked_at: null, new: false })),
}

/** The approval ledger could not be read: its three counts are null, not zero. */
export const LEDGER_DOWN = {
  ...FULL,
  ledger_ok: false,
  xp_partial: true,
  // Non-ledger XP: 312 tasks * 10 + 163 active days * 20 (profile screen audit, 2 Oct 2026).
  xp: 6380, level: 11, level_xp: 880, level_need: 1100, rank: 'scene_master',
  counts: { ...FULL.counts, approved_cards: null, rejected_cards: null, phone_approvals: null },
  achievements: FULL.achievements.map(a => ['careful', 'pocket'].includes(a.id)
    ? { ...a, progress: null, unlocked: null, new: false } : a),
}

/** Same user, "Bu ay" range. */
export const MONTH = {
  ...FULL,
  range: 'month',
  counts: { ...FULL.counts, tasks: 48, approved_cards: 130, rejected_cards: 4, phone_approvals: 6, active_days: 2 },
  models: { ...FULL.models, mix: [{ family: 'codex', turns: 48, share: 1 }] },
}
