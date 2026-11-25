/**
 * useTurnQueue — the messages waiting for the running turn, as one ordered list for the chat
 * flow (INFO_DESIGN.md C1: the queue's only place is the thread; withdraw, never edit).
 *
 * Two sources, because a message can wait in two places:
 * - the backend's turn queue (GET /conversations/{id}/queue): inputs that reached the server
 *   while a turn ran (another device, a message the server took in). Withdrawn with DELETE;
 *   409 means the server already handed it to a turn, so it can no longer be taken back.
 * - this window's own queue in useChat (a message typed while the turn runs is held here and
 *   sent when it ends, or handed to the running turn mid-way where the CLI allows it).
 * The server's items run first (they are already in line), then this window's.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import type { QueuedMessage } from './useChat';

export const TURN_QUEUE_POLL_MS = 2000;

export interface QueuedView {
  key: string;
  source: 'server' | 'local';
  id: number | string;
  text: string;
  images?: string[];
  createdAt?: string | null;
  /** Already handed to the running turn (read at its next step): no ordinal, not withdrawable. */
  sent?: boolean;
  /** Its turn ended without reading it: it may go now. */
  turnEnded?: boolean;
  /** Withdrawing failed because it already started (409), or for another reason. */
  note?: 'started' | 'failed';
  /** 1-based place in line among the waiting messages; undefined for a sent one. */
  place?: number;
}

type Http = { get: (...a: any[]) => Promise<any>; delete: (...a: any[]) => Promise<any> };

interface ServerItem { id: number | string; text: string; created_at?: string | null; order?: number }

export function parseServerQueue(data: any): ServerItem[] {
  const rows = Array.isArray(data) ? data : Array.isArray(data?.items) ? data.items : Array.isArray(data?.queue) ? data.queue : [];
  return rows
    .filter((r: any) => r && (typeof r.id === 'number' || typeof r.id === 'string') && typeof r.text === 'string')
    .map((r: any, i: number) => ({ id: r.id, text: r.text, created_at: r.created_at ?? null, order: typeof r.order === 'number' ? r.order : i }))
    .sort((a: ServerItem, b: ServerItem) => (a.order ?? 0) - (b.order ?? 0));
}

export function useTurnQueue({ API, token, conversationId, active, local, removeLocal, http = axios as unknown as Http, pollMs = TURN_QUEUE_POLL_MS }: {
  API?: string;
  token?: string | null;
  conversationId: number | null | undefined;
  /** A turn runs in this chat: only then can anything be waiting on the server. */
  active: boolean;
  local: QueuedMessage[];
  removeLocal: (id: number) => void;
  http?: Http;
  pollMs?: number;
}) {
  const [server, setServer] = useState<ServerItem[]>([]);
  const [notes, setNotes] = useState<Record<string, 'started' | 'failed'>>({});
  const [gone, setGone] = useState<Set<string>>(() => new Set());
  const convRef = useRef(conversationId);
  convRef.current = conversationId;
  // A queue read that lands after the turn ended (or after a withdraw) describes a past state: it
  // would bring back a removable bubble for a message a turn already claimed, and nothing would
  // correct it (audit F1). Reads are numbered; only one newer than the last applied read and than
  // the last fence (turn end, chat switch, withdraw) is applied, and only while a turn runs.
  const seqRef = useRef(0);
  const appliedRef = useRef(0);
  const barrierRef = useRef(0);
  const activeRef = useRef(active);
  activeRef.current = active;
  const fence = () => { barrierRef.current = seqRef.current + 1; };
  // Only a real token goes on the call: an empty one would override the session header
  // useAuth puts on axios.defaults (the trap useAIConfig.ts documents).
  const headers = token ? { headers: { 'X-Session-Token': token } } : undefined;

  const refresh = useCallback(async () => {
    const id = convRef.current;
    if (!API || id == null) return;
    const seq = ++seqRef.current;
    try {
      const res = await http.get(`${API}/conversations/${id}/queue`, headers);
      if (convRef.current !== id || !activeRef.current || seq <= appliedRef.current || seq < barrierRef.current) return;
      appliedRef.current = seq;
      setServer(parseServerQueue(res?.data));
    } catch {
      // No queue route (older backend) or a blip: this window's own queue still shows.
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [API, http, token]);

  useEffect(() => { fence(); setServer([]); setNotes({}); setGone(new Set()); }, [conversationId]);

  useEffect(() => {
    // The turn ended: whatever waited on the server is now the next turn's (or still waits for
    // one, which this list does not show without a running turn). No reconcile read: its answer
    // could only re-add what the ended turn's successor has already claimed.
    if (!active) { fence(); setServer([]); return; }
    void refresh();
    const id = setInterval(() => { void refresh(); }, pollMs);
    return () => clearInterval(id);
  }, [active, conversationId, pollMs, refresh]);

  const items = useMemo<QueuedView[]>(() => {
    const sent = local.filter(q => q.state === 'sent').map<QueuedView>(q => ({
      key: `l${q.id}`, source: 'local', id: q.id, text: q.draft || q.text, images: q.images, sent: true, turnEnded: q.turnEnded,
    }));
    const waiting: QueuedView[] = [
      ...server.map<QueuedView>(s => ({ key: `s${s.id}`, source: 'server', id: s.id, text: s.text, createdAt: s.created_at })),
      ...local.filter(q => q.state !== 'sent').map<QueuedView>(q => ({
        key: `l${q.id}`, source: 'local', id: q.id, text: q.draft || q.text, images: q.images,
      })),
    ].filter(v => !gone.has(v.key));
    waiting.forEach((v, i) => { v.place = i + 1; });
    return [...sent, ...waiting].map(v => (notes[v.key] ? { ...v, note: notes[v.key] } : v));
  }, [local, server, gone, notes]);

  const remove = useCallback(async (item: QueuedView) => {
    if (item.source === 'local') { removeLocal(item.id as number); return; }
    const id = convRef.current;
    if (!API || id == null) return;
    fence();
    try {
      await http.delete(`${API}/conversations/${id}/queue/${item.id}`, headers);
      setGone(prev => new Set(prev).add(item.key));
      setServer(prev => prev.filter(s => `s${s.id}` !== item.key));
    } catch (err: any) {
      // 409 "claimed": a turn already took it. (409 "not_user" is not the user's message.)
      const claimed = err?.response?.status === 409 && err?.response?.data?.detail?.code !== 'not_user';
      setNotes(prev => ({ ...prev, [item.key]: claimed ? 'started' : 'failed' }));
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [API, http, token, removeLocal]);

  return { items, remove, refresh };
}
