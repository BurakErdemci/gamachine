import { useEffect, useRef, useState } from 'react';
import type { ChatAttention } from '../hooks/home/useChat';

export interface TurnDoneEvent {
  /** The turn-end sequence number; also the band's React key, so a new turn replays it. */
  seq: number;
  convId: number;
}

/**
 * The on-screen chat's "finished" moment, for the achievement band (mockup #15 `.achv`).
 *
 * The renderer already has exactly one such moment: `attention[id].turnEnd`, the event
 * `useChatNotifications` turns into the desktop "finished" notification. That notification is
 * skipped for the chat the user is looking at; the band is its on-screen counterpart, so it
 * fires on the same event under the same conditions, only for the active chat:
 *  - a NEW turn end (its `seq` advanced while the chat was observed; a turn end already present
 *    when the chat is first seen, e.g. after a switch or a reload, is history, not news);
 *  - not failed;
 *  - no approval card open (a turn that ends on a card is announced by the card).
 */
export function useTurnDone(
  attention: Record<number, ChatAttention>,
  activeConvId: number | null,
  screenCardOpen: boolean,
): TurnDoneEvent | null {
  const seenRef = useRef<Map<number, number | null>>(new Map());
  const [event, setEvent] = useState<TurnDoneEvent | null>(null);

  useEffect(() => {
    const seen = seenRef.current;
    for (const [key, a] of Object.entries(attention)) {
      const id = Number(key);
      const seq = a.turnEnd ? a.turnEnd.seq : null;
      const known = seen.has(id);
      const before = seen.get(id) ?? null;
      seen.set(id, seq);
      if (!known || seq == null || seq === before) continue;
      if (id !== activeConvId) continue;
      if (a.turnEnd!.failed || a.awaiting || screenCardOpen) continue;
      setEvent({ seq, convId: id });
    }
  }, [attention, activeConvId, screenCardOpen]);

  // The band belongs to the chat it finished in: switching away drops it.
  useEffect(() => {
    setEvent(prev => (prev && prev.convId !== activeConvId ? null : prev));
  }, [activeConvId]);

  return event;
}
