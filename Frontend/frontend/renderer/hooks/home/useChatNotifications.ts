/**
 * Desktop notifications for parallel chats, modelled on the Codex desktop app:
 * a chat that needs an approval, or whose turn ends, raises an OS notification
 * unless the user is already looking at it (that chat on screen in a focused
 * window). Clicking one focuses the window and opens that chat.
 *
 * Every notification is tied to an id that names one event: a gate id, a
 * file-card marker, a turn-end sequence number. An id is notified at most once,
 * so re-renders, chat switches and polls that report the same state again
 * produce nothing. An event that happens while the user is looking is spent
 * silently; it does not fire later when the window loses focus.
 *
 * "Approval needed" is announced when a chat ENTERS awaiting: a new request id
 * in a chat that was not awaiting at the previous observation. A second card
 * queued behind an open one is the same wait, not a new one.
 */
import { useEffect, useRef } from 'react';
import type { Conversation } from '../../components/home/types';
import type { ChatAttention } from './useChat';
import { cevir } from '../../lib/i18n';
import { stripBidi } from '../../lib/modelText';

const getIpc = () => (typeof window !== 'undefined' ? (window as any).ipc : null);

/**
 * Remembered event ids before the oldest absent ones are evicted. At-most-once
 * holds within this many newer events: a card parked while its chat is left
 * drops out of `attention`, and if this many other events pass before it comes
 * back it can fire again. Ids are short strings, so the bound costs little.
 */
const SEEN_MAX = 20000;
/** Chat titles are shortened so the event text after them survives the main process cap. */
const TITLE_MAX = 60;

export interface NotifyPayload {
  title: string;
  body: string;
  conversationId?: number;
}

/** Is the user looking at this window right now? */
export const windowAttended = (): boolean =>
  typeof document !== 'undefined' && document.visibilityState === 'visible' && document.hasFocus();

const chatName = (conversations: Conversation[], id: number): string => {
  const raw = stripBidi(conversations.find(c => c.id === id)?.title ?? '').trim();
  const chars = Array.from(raw);
  const short = chars.length > TITLE_MAX ? `${chars.slice(0, TITLE_MAX - 1).join('')}…` : raw;
  return short || cevir('notify.untitledChat');
};

interface ChatNotificationsParams {
  conversations: Conversation[];
  activeConvId: number | null;
  attention: Record<number, ChatAttention>;
  /** Requests with no known owner chat (the tray). */
  trayGates: Array<{ gateId: string }>;
  /** The bridge poll has answered once; see `useMCPApproval().synced`. */
  bridgeSynced: boolean;
  /**
   * A file card is open in the page's slot. That slot only ever holds the
   * on-screen chat's card and is not part of the chat's runtime, so without
   * this the chat looked idle and its turn end announced "finished" over an
   * open card (Codex notifyaudit, open-file-card).
   */
  screenCardOpen: boolean;
  /** Opens a chat the way a sidebar click does. */
  onOpenConversation: (conv: Conversation) => void;
}

export const useChatNotifications = ({
  conversations, activeConvId, attention, trayGates, bridgeSynced, screenCardOpen, onOpenConversation,
}: ChatNotificationsParams) => {
  const seenRef = useRef<Set<string>>(new Set());
  const syncedBeforeRef = useRef(false);
  const awaitingBeforeRef = useRef<Record<number, boolean>>({});
  const conversationsRef = useRef(conversations);
  conversationsRef.current = conversations;
  const openRef = useRef(onOpenConversation);
  openRef.current = onOpenConversation;

  useEffect(() => {
    const seen = seenRef.current;
    const present = new Set<string>();
    const fresh = (key: string) => {
      present.add(key);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    };
    // Bridge and tray requests in the first poll answer were waiting before
    // this renderer started; announcing them would be a storm on every reload.
    const baseline = !syncedBeforeRef.current;
    syncedBeforeRef.current = bridgeSynced;
    const attended = windowAttended();
    const awaitingBefore = awaitingBeforeRef.current;
    const out: NotifyPayload[] = [];

    for (const [key, a] of Object.entries(attention)) {
      const id = Number(key);
      // Every id is marked, even when an earlier one already decided the
      // outcome, so none of them can fire on a later render.
      const newApproval = a.approvals.map(k => fresh(`${id}|${k}`)).includes(true);
      const newBridge = a.bridgeGates.map(g => fresh(`${id}|bridge:${g}`)).includes(true) && !baseline;
      const ended = a.turnEnd && fresh(`${id}|turn:${a.turnEnd.seq}`) ? a.turnEnd : null;
      const awaiting = a.awaiting || (id === activeConvId && screenCardOpen);
      if (id === activeConvId && attended) continue;
      const name = () => chatName(conversationsRef.current, id);
      if ((newApproval || newBridge) && !awaitingBefore[id]) {
        out.push({ title: cevir('notify.title'), body: cevir('notify.awaiting', { baslik: name() }), conversationId: id });
      } else if (ended && !awaiting && !newApproval && !newBridge) {
        // A turn that ends with a card still open is announced by the card.
        out.push({
          title: cevir('notify.title'),
          body: cevir(ended.failed ? 'notify.failed' : 'notify.finished', { baslik: name() }),
          conversationId: id,
        });
      }
    }

    const newTray = trayGates.map(g => fresh(`tray|${g.gateId}`)).includes(true) && !baseline;
    // The tray sits outside every chat, so a focused window is enough to see it.
    if (newTray && !attended) out.push({ title: cevir('notify.title'), body: cevir('notify.trayAwaiting') });

    awaitingBeforeRef.current = Object.fromEntries(
      Object.entries(attention).map(([key, a]) => [key, a.awaiting]));
    // Evict the oldest ids that are not present now (Set iteration is
    // insertion order). The old prune replaced the set with only the present
    // ids, so a briefly absent id fired again right after 2000 events (Codex
    // notifyaudit, seen-pruning).
    for (const key of seen) {
      if (seen.size <= SEEN_MAX) break;
      if (!present.has(key)) seen.delete(key);
    }

    const ipc = getIpc();
    if (!ipc?.invoke) return;
    for (const payload of out) {
      try {
        Promise.resolve(ipc.invoke('notify', payload)).catch((err: unknown) => console.warn('[notify] not shown:', err));
      } catch (err) {
        console.warn('[notify] not shown:', err);
      }
    }
  }, [attention, trayGates, bridgeSynced, screenCardOpen, activeConvId]);

  useEffect(() => {
    const ipc = getIpc();
    if (!ipc?.on) return;
    const off = ipc.on('open-conversation', (raw: unknown) => {
      if (typeof raw !== 'number' || !Number.isSafeInteger(raw) || raw <= 0) return;
      // A chat missing from the list was deleted (or not loaded yet); there is
      // nothing to open.
      const conv = conversationsRef.current.find(c => c.id === raw);
      if (conv) openRef.current(conv);
    });
    return () => { if (typeof off === 'function') off(); };
  }, []);
};
