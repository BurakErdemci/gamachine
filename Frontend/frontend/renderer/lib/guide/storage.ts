// "Watched", the install version and the core tour's done mark (REHBER-KAYITLARI.md section 6).
// Per-machine UI state, kept where the app keeps its other per-machine preferences (theme, intro:
// lib/appearance.ts): localStorage, every access in try/catch, so a refused storage only means
// nothing is remembered, never a broken screen.

export const KEYS = {
  seen: 'guide.seen',                 // {"<topic id>": <rev watched>}
  installedFrom: 'guide.installedFrom', // the app version of the first launch
  tourDone: 'tour.done',              // "1" once the core tour closed (finished or skipped)
} as const;

export type Seen = Record<string, number>;
type Store = Pick<Storage, 'getItem' | 'setItem'>;

function store(s?: Store): Store | null {
  try { return s ?? localStorage; } catch { return null; }
}
function read(key: string, s?: Store): string | null {
  try { return store(s)?.getItem(key) ?? null; } catch { return null; }
}
function write(key: string, value: string, s?: Store): void {
  try { store(s)?.setItem(key, value); } catch { /* Not remembered; the guide still works. */ }
}

export function loadSeen(s?: Store): Seen {
  try {
    const v = JSON.parse(read(KEYS.seen, s) || '{}');
    if (!v || typeof v !== 'object' || Array.isArray(v)) return {};
    const out: Seen = {};
    for (const [k, n] of Object.entries(v)) if (typeof n === 'number' && n > 0) out[k] = n;
    return out;
  } catch { return {}; }
}
export const saveSeen = (seen: Seen, s?: Store): void => write(KEYS.seen, JSON.stringify(seen), s);

/** The version this install started on; written once, on the first launch that reads it. */
export function installedFrom(appVersion: string, s?: Store): string {
  const v = read(KEYS.installedFrom, s);
  if (v) return v;
  write(KEYS.installedFrom, appVersion, s);
  return appVersion;
}

export const tourDone = (s?: Store): boolean => read(KEYS.tourDone, s) === '1';
export const markTourDone = (s?: Store): void => write(KEYS.tourDone, '1', s);
