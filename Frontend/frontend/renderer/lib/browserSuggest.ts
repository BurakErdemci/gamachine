// Omnibox suggestions, the Suggested grid and the tab shortcuts of the agent browser panel: pure,
// so the panel only renders what these return. History entries come from main (browser-history.ts).
import { addressToUrl, displayAddress, isSearchUrl } from './browserAddress';

export interface HistoryItem {
  url: string;
  title: string;
  favicon: string;
  lastVisit: number;
  visits: number;
}

export type Suggestion =
  | { kind: 'search' | 'open'; url: string; text: string }
  | { kind: 'history'; url: string; text: string; title: string; favicon: string };

export interface Site {
  url: string;
  label: string;
  favicon: string;
}

const DAY_MS = 86_400_000;

/** Keeps only well-formed entries, so a malformed reply never reaches rendering. */
export function cleanHistory(raw: unknown): HistoryItem[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item): HistoryItem[] => {
    if (!item || typeof item !== 'object') return [];
    const { url, title, favicon, lastVisit, visits } = item as Record<string, unknown>;
    if (typeof url !== 'string' || !/^https?:\/\//i.test(url)) return [];
    return [{
      url,
      title: typeof title === 'string' ? title : '',
      favicon: typeof favicon === 'string' ? favicon : '',
      lastVisit: typeof lastVisit === 'number' && Number.isFinite(lastVisit) ? lastVisit : 0,
      visits: typeof visits === 'number' && visits > 0 ? visits : 1,
    }];
  });
}

const bare = (url: string) => url.replace(/^https?:\/\/(www\.)?/i, '').toLowerCase();

function score(item: HistoryItem, words: string[], whole: string): number {
  const address = bare(item.url);
  const title = item.title.toLowerCase();
  let points = Math.log2(1 + item.visits);
  if (address.startsWith(whole)) points += 6;
  else if (words.every(w => address.split(/[./?#=&_-]/).some(part => part.startsWith(w)))) points += 3;
  if (title.split(/\s+/).some(part => part.startsWith(words[0]))) points += 2;
  return points;
}

/**
 * The rows under the address bar: first what Enter does with the text (open an address or search the
 * web), then the remembered pages that contain every typed word, best match first.
 */
export function suggest(input: string, history: HistoryItem[], max = 6): Suggestion[] {
  const text = input.trim();
  const target = addressToUrl(text);
  if (!target) return [];
  const rows: Suggestion[] = [{ kind: isSearchUrl(target) ? 'search' : 'open', url: target, text }];
  const words = text.toLowerCase().split(/\s+/);
  const whole = bare(text);
  const matches = history
    .filter(item => {
      const haystack = `${bare(item.url)} ${item.title.toLowerCase()}`;
      return words.every(w => haystack.includes(w));
    })
    .map(item => ({ item, points: score(item, words, whole) }))
    .sort((a, b) => b.points - a.points || b.item.lastVisit - a.item.lastVisit);
  const seen = new Set([target]);
  for (const { item } of matches) {
    if (rows.length >= max) break;
    if (seen.has(item.url)) continue;
    seen.add(item.url);
    rows.push({ kind: 'history', url: item.url, text: item.title || displayAddress(item.url), title: item.title, favicon: item.favicon });
  }
  return rows;
}

/** One tile per site, the ones visited most and most recently first. */
export function suggestedSites(history: HistoryItem[], max = 8, now = Date.now()): Site[] {
  const sites = new Map<string, { best: HistoryItem; visits: number; latest: number }>();
  for (const item of history) {
    const host = displayAddress(item.url);
    const site = sites.get(host);
    if (!site) {
      sites.set(host, { best: item, visits: item.visits, latest: item.lastVisit });
      continue;
    }
    site.visits += item.visits;
    site.latest = Math.max(site.latest, item.lastVisit);
    if (item.visits > site.best.visits) site.best = item;
  }
  const weight = (s: { visits: number; latest: number }) => s.visits / (1 + Math.max(0, now - s.latest) / (30 * DAY_MS));
  return [...sites.entries()]
    .sort((a, b) => weight(b[1]) - weight(a[1]))
    .slice(0, max)
    .map(([host, s]) => ({
      url: s.best.url,
      label: host,
      favicon: history.find(item => displayAddress(item.url) === host && item.favicon)?.favicon ?? '',
    }));
}

export type TabAction = { action: 'new' | 'close' | 'reopen' } | { action: 'select'; index: number };

export interface KeyLike {
  key: string;
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
}

/** Cmd on macOS, Ctrl elsewhere. Reads the platform when called so a test can change it. */
export function isMacPlatform(): boolean {
  return typeof navigator !== 'undefined' && /mac|iphone|ipad/i.test(navigator.platform || '');
}

/** The tab shortcut a key press asks for; copy, paste, undo and every other key are left alone. */
export function tabShortcutOf(e: KeyLike, mac: boolean): TabAction | null {
  if (e.altKey || !(mac ? e.metaKey && !e.ctrlKey : e.ctrlKey && !e.metaKey)) return null;
  const key = e.key.toLowerCase();
  if (key === 't') return { action: e.shiftKey ? 'reopen' : 'new' };
  if (e.shiftKey) return null;
  if (key === 'w') return { action: 'close' };
  return /^[1-9]$/.test(key) ? { action: 'select', index: Number(key) } : null;
}

/** "Ctrl+T" or "⌘T" for tooltips. */
export function shortcutLabel(keys: string, mac: boolean): string {
  return mac ? `⌘${keys.replace('Shift+', '⇧')}` : `Ctrl+${keys}`;
}
