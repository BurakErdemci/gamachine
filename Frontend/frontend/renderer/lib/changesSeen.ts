export interface Seen {
  v: 1;
  at: number;
  files: Record<string, string>;
}

const normKey = (key: string): string => key.replace(/\\/g, '/').toLowerCase();
const storageKey = (ws: string): string => `gm-changes-seen:${normKey(ws).replace(/\/+$/, '')}`;

export function loadSeen(ws: string): Seen | null {
  try {
    const raw = localStorage.getItem(storageKey(ws));
    if (!raw) return null;
    const value = JSON.parse(raw);
    if (!value || value.v !== 1 || !Number.isFinite(value.at)
      || !value.files || typeof value.files !== 'object' || Array.isArray(value.files)
      || Object.values(value.files).some(status => typeof status !== 'string')) return null;
    return value;
  } catch { return null; }
}

export function saveSeen(ws: string, files: Record<string, string>, at: number): Seen {
  const seen: Seen = { v: 1, at, files: Object.fromEntries(Object.entries(files).map(([key, status]) => [normKey(key), status])) };
  try { localStorage.setItem(storageKey(ws), JSON.stringify(seen)); } catch { /* Keep the in-memory snapshot when storage is denied. */ }
  return seen;
}

export function clearSeen(ws: string): void {
  try { localStorage.removeItem(storageKey(ws)); } catch { /* The current list can still show all files. */ }
}

export function isUnseen(key: string, status: string, mtime: number | undefined, seen: Seen | null): boolean {
  return seen === null || seen.files[normKey(key)] !== status || (mtime !== undefined && mtime > seen.at);
}
