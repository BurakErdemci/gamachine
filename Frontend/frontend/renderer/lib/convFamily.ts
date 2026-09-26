import type { Conversation } from '../components/home/types';
import type { ConvStatus } from '../hooks/home/useChat';
import type { TKey } from './i18n';

/** Shared by the sidebar rows and the chat tabs so a status reads the same in both. */
export const STATUS_DOT: Record<ConvStatus, { className: string; label: TKey }> = {
  running: { className: 'bg-blue-400 animate-pulse', label: 'sidebar.statusRunning' },
  awaiting: { className: 'bg-amber-400', label: 'sidebar.statusAwaiting' },
  unread: { className: 'bg-emerald-400', label: 'sidebar.statusUnread' },
};

// Same precedence `convStatus` applies within one chat: a pending decision
// outranks work in progress, which outranks a finished-unread turn.
const PRIORITY: ConvStatus[] = ['awaiting', 'running', 'unread'];

// A branch whose root is not in the list (deleted elsewhere, or not listed
// yet) stands as a root of its own; otherwise nothing would lead to it.
const branchParentIn = (conversations: Conversation[], conv: Pick<Conversation, 'parent_id'>): number | null =>
  conv.parent_id != null && conversations.some(c => c.id === conv.parent_id) ? conv.parent_id : null;

export const rootIdOf = (conversations: Conversation[], conv: Pick<Conversation, 'id' | 'parent_id'>): number =>
  branchParentIn(conversations, conv) ?? conv.id;

export const isBranchIn = (conversations: Conversation[], conv: Pick<Conversation, 'parent_id'>): boolean =>
  branchParentIn(conversations, conv) != null;

export const rootsOf = (conversations: Conversation[]): Conversation[] =>
  conversations.filter(c => !isBranchIn(conversations, c));

/** Root id of the family `convId` belongs to; `convId` itself when it is not listed. */
export const familyRootId = (conversations: Conversation[], convId: number | null): number | null => {
  if (convId == null) return null;
  const conv = conversations.find(c => c.id === convId);
  return conv ? rootIdOf(conversations, conv) : convId;
};

export interface Family {
  root: Conversation | null;
  /** All branches, oldest first. */
  branches: Conversation[];
  visible: Conversation[];
  hidden: Conversation[];
}

const byCreation = (a: Conversation, b: Conversation) =>
  (a.created_at || '').localeCompare(b.created_at || '') || a.id - b.id;

/**
 * `activeId` counts as visible even when hidden: the chat on screen always has
 * a tab, whatever the list says (a failed reopen, another client's hide).
 */
export const familyOf = (conversations: Conversation[], rootId: number | null, activeId: number | null = null): Family => {
  if (rootId == null) return { root: null, branches: [], visible: [], hidden: [] };
  const root = conversations.find(c => c.id === rootId) ?? null;
  const branches = conversations.filter(c => c.id !== rootId && c.parent_id === rootId).sort(byCreation);
  const shown = (b: Conversation) => !b.hidden || b.id === activeId;
  return {
    root,
    branches,
    visible: branches.filter(shown),
    hidden: branches.filter(b => !shown(b)),
  };
};

/** The tab left of `convId` in its family's tab row (root first), if any. */
export const leftTabOf = (conversations: Conversation[], convId: number, activeId: number | null): Conversation | null => {
  const fam = familyOf(conversations, familyRootId(conversations, convId), activeId);
  const tabs = [fam.root, ...fam.visible].filter((c): c is Conversation => !!c);
  return tabs[tabs.findIndex(c => c.id === convId) - 1] ?? null;
};

/**
 * Chats waiting on a decision, the one on screen excluded: its card is
 * already in front of the user, so counting it would point nowhere new.
 * Only listed chats count: a runtime can outlive its chat (deleted from
 * another window, dropped by a list refresh) and would point nowhere.
 */
export const awaitingElsewhere = (
  convStatus: Record<number, ConvStatus> | undefined, activeId: number | null, conversations: Conversation[],
): number => {
  const listed = new Set(conversations.map(c => c.id));
  return Object.entries(convStatus ?? {})
    .filter(([id, s]) => s === 'awaiting' && Number(id) !== activeId && listed.has(Number(id))).length;
};

/** Most urgent status among `ids`, or undefined when none of them has one. */
export const mostUrgent = (convStatus: Record<number, ConvStatus> | undefined, ids: number[]): ConvStatus | undefined => {
  if (!convStatus) return undefined;
  const present = new Set(ids.map(id => convStatus[id]).filter(Boolean));
  return PRIORITY.find(s => present.has(s));
};
