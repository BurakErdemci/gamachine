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

export const rootIdOf = (conv: Pick<Conversation, 'id' | 'parent_id'>): number => conv.parent_id ?? conv.id;

export const rootsOf = (conversations: Conversation[]): Conversation[] =>
  conversations.filter(c => c.parent_id == null);

/** Root id of the family `convId` belongs to; `convId` itself when it is not listed. */
export const familyRootId = (conversations: Conversation[], convId: number | null): number | null => {
  if (convId == null) return null;
  const conv = conversations.find(c => c.id === convId);
  return conv ? rootIdOf(conv) : convId;
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

export const familyOf = (conversations: Conversation[], rootId: number | null): Family => {
  if (rootId == null) return { root: null, branches: [], visible: [], hidden: [] };
  const root = conversations.find(c => c.id === rootId) ?? null;
  const branches = conversations.filter(c => c.parent_id === rootId).sort(byCreation);
  return {
    root,
    branches,
    visible: branches.filter(b => !b.hidden),
    hidden: branches.filter(b => b.hidden),
  };
};

/** Most urgent status among `ids`, or undefined when none of them has one. */
export const mostUrgent = (convStatus: Record<number, ConvStatus> | undefined, ids: number[]): ConvStatus | undefined => {
  if (!convStatus) return undefined;
  const present = new Set(ids.map(id => convStatus[id]).filter(Boolean));
  return PRIORITY.find(s => present.has(s));
};
