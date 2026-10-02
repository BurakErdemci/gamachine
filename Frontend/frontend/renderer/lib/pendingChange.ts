import { useSyncExternalStore } from 'react';

/**
 * The file change a card is waiting on, as the workspace's Kod tab shows it (mockup
 * `.ws-change` / `.ws-pending`: "this change is waiting for your approval" + Accept / Reject).
 *
 * The decision logic stays where it is. The card that owns the change (FileCreationApproval for
 * created / rewritten files, DiffViewer for a proposed fix) publishes the diff and the SAME two
 * handlers its own buttons call; the strip only calls them. Deciding on either surface runs one
 * code path, and the card's state change (next file, done, gone) republishes or withdraws the
 * entry, so the other surface follows. Nothing here sends, writes or decides.
 */
export interface PendingChange {
  /** Stable while the same change waits; a new id tells the strip a new change arrived. */
  id: string;
  /** File name shown in the strip and the file tab. */
  name: string;
  /** Workspace path the change writes, when the card knows it. */
  path?: string;
  /** Content before / after; `original` is '' for a new file. */
  original: string;
  modified: string;
  accept: () => void;
  reject: () => void;
  /** A decision is in flight on the card: both surfaces hold their buttons. */
  busy?: boolean;
}

let entries: PendingChange[] = [];
const listeners = new Set<() => void>();
const emit = () => listeners.forEach(l => l());

/**
 * Publish (or replace, by id) a pending change; returns the withdraw function. The most recent
 * publication is the one shown: two cards waiting at once is possible (an unityMCP gate next to
 * a chat card) and the newer one is the one the user was just asked about.
 */
export function publishPendingChange(change: PendingChange): () => void {
  entries = [...entries.filter(e => e.id !== change.id), change];
  emit();
  return () => {
    const before = entries.length;
    entries = entries.filter(e => e !== change);
    if (entries.length !== before) emit();
  };
}

const snapshot = () => (entries.length ? entries[entries.length - 1] : null);

export function usePendingChange(): PendingChange | null {
  return useSyncExternalStore(
    (l) => { listeners.add(l); return () => { listeners.delete(l); }; },
    snapshot,
    () => null,
  );
}

/**
 * Lines added / removed between two texts, for the crumb's "+3 −1". A multiset count, not a
 * real diff: it is a size hint next to Monaco's own diff, and a line moved within the file
 * counts as unchanged, which is what the user would say too.
 */
export function lineDelta(original: string, modified: string): { add: number; del: number } {
  const before = new Map<string, number>();
  const a = original ? original.split(/\r?\n/) : [];
  const b = modified ? modified.split(/\r?\n/) : [];
  for (const line of a) before.set(line, (before.get(line) ?? 0) + 1);
  let add = 0;
  for (const line of b) {
    const n = before.get(line) ?? 0;
    if (n > 0) before.set(line, n - 1);
    else add++;
  }
  let del = 0;
  for (const n of before.values()) del += n;
  return { add, del };
}
