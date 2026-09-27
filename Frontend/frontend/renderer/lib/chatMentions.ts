import type { Conversation } from '../components/home/types';
import { isBranchIn } from './convFamily';

/**
 * `@<id>` names another chat by its DB id. Titles come from a chat's first
 * message, so several chats can share one; the number is what the backend
 * resolves for the model (`agentic/mailbox.py: mention_block`). An id, never a
 * position in a list: positions shift when a chat is deleted.
 */

export interface MentionTarget {
  id: number;
  title: string;
  parentId: number | null;
  parentTitle: string | null;
}

/** The `@query` being typed at the caret: `@` at the start or after whitespace. */
export const mentionQueryAt = (text: string, caret: number): { start: number; query: string } | null => {
  const before = text.slice(0, Math.max(0, caret));
  const at = before.lastIndexOf('@');
  if (at < 0) return null;
  if (at > 0 && !/\s/.test(before[at - 1])) return null;
  const query = before.slice(at + 1);
  if (/[\s@]/.test(query) || query.length > 40) return null;
  return { start: at, query };
};

// `I`/`İ` lowercase differently in Turkish than in English, and a user types
// either; folding `ı` into `i` lets "is" find "Island" and "ısı" alike.
export const foldForSearch = (s: string): string =>
  (s || '').toLocaleLowerCase('tr').replace(/ı/g, 'i');

export const mentionTargets = (
  conversations: Conversation[], currentId: number | null, query: string, limit = 50,
): MentionTarget[] => {
  const q = foldForSearch(query.trim());
  const numeric = /^[0-9]+$/.test(q);
  const out: MentionTarget[] = [];
  for (const c of conversations) {
    if (c.id === currentId || c.side_of != null) continue;
    const title = c.title || '';
    if (q && !(numeric && String(c.id).startsWith(q)) && !foldForSearch(title).includes(q)) continue;
    const branch = isBranchIn(conversations, c);
    const parent = branch ? conversations.find(p => p.id === c.parent_id) ?? null : null;
    out.push({ id: c.id, title, parentId: parent?.id ?? null, parentTitle: parent?.title ?? null });
  }
  if (numeric) {
    const exact = out.findIndex(m => String(m.id) === q);
    if (exact > 0) out.unshift(...out.splice(exact, 1));
  }
  return out.slice(0, limit);
};

// Same boundaries as the backend's `_MENTION_RE`: not glued to a word or
// another `@` on either side, so an e-mail or `@12abc` is not a mention.
export const MENTION_PATTERN = /(?<![\p{L}\p{N}_@])@([0-9]{1,9})(?![\p{L}\p{N}_@])/gu;
