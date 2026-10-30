import { useEffect, useRef, useState } from 'react';

/** Whether the open chat's agent is using the browser: its turn is in flight and the host holds a live tab for it. */
export const browserRunningIn = (activeChats: ReadonlySet<number>, openConv: number | null, loading: boolean): boolean =>
  loading && openConv !== null && activeChats.has(openConv);

/** The chats with a live tab after one `browser-live` payload; a payload names one chat and changes only that chat. */
export function applyBrowserLive(activeChats: ReadonlySet<number>, raw: unknown): ReadonlySet<number> {
  const p = raw && typeof raw === 'object' ? raw as { active?: unknown; conversationId?: unknown } : {};
  const id = p.conversationId;
  if (typeof id !== 'number' || activeChats.has(id) === (p.active === true)) return activeChats;
  const next = new Set(activeChats);
  if (p.active === true) next.add(id);
  else next.delete(id);
  return next;
}

/**
 * The chats whose browser tab is live, from `browser-live` events (every chat's) and, for the open
 * chat, one status read when it opens. A status reply never overrides a chat an event already spoke for.
 */
export function useBrowserActiveChats(ipc: any, openConv: number | null, onEvent: (raw: unknown) => void): ReadonlySet<number> {
  const [activeChats, setActiveChats] = useState<ReadonlySet<number>>(() => new Set());
  const heard = useRef(new Set<number>());
  const latestOnEvent = useRef(onEvent);
  latestOnEvent.current = onEvent;

  useEffect(() => {
    if (!ipc) return;
    const off = ipc.on('browser-live', (raw: unknown) => {
      const id = (raw as { conversationId?: unknown } | null)?.conversationId;
      if (typeof id === 'number') heard.current.add(id);
      setActiveChats(prev => applyBrowserLive(prev, raw));
      latestOnEvent.current(raw);
    });
    return () => { if (typeof off === 'function') off(); };
  }, [ipc]);

  useEffect(() => {
    if (!ipc?.invoke || openConv === null) return;
    let alive = true;
    Promise.resolve(ipc.invoke('browser-live', { op: 'status', conversationId: openConv }))
      .then((raw: any) => {
        if (!alive || heard.current.has(openConv) || !raw || raw.error) return;
        setActiveChats(prev => applyBrowserLive(prev, { active: raw.active, conversationId: openConv }));
      })
      .catch(() => {});
    return () => { alive = false; };
  }, [ipc, openConv]);

  return activeChats;
}

/** Whether a computer-use run is live: one status read on mount, then every `computer-use-live` event. */
export function useComputerRunning(ipc: any, onEvent: (raw: unknown) => void): boolean {
  const [running, setRunning] = useState(false);
  const latestOnEvent = useRef(onEvent);
  latestOnEvent.current = onEvent;

  useEffect(() => {
    if (!ipc) return;
    let alive = true;
    let heard = false;
    Promise.resolve(ipc.invoke?.('computer-use-live', { op: 'status' }))
      .then((raw: any) => { if (alive && !heard && raw && !raw.error) setRunning(raw.active === true); })
      .catch(() => {});
    const off = ipc.on('computer-use-live', (raw: unknown) => {
      heard = true;
      setRunning((raw as { active?: unknown } | null)?.active === true);
      latestOnEvent.current(raw);
    });
    return () => { alive = false; if (typeof off === 'function') off(); };
  }, [ipc]);

  return running;
}
