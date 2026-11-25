/**
 * useHelpers — the helpers (sub-agents) a chat started, read from GET /subagents/list.
 *
 * Polled every 2 s only while at least one helper runs (or the fuse holds the chat), and not at
 * all for a chat that never started one: a closed chat with twelve finished helpers must not keep
 * a timer alive. `nudge` re-reads once when the caller sees something that may have started or
 * finished a helper (a turn ending, a helper result arriving), which restarts the poll if needed.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';

export type HelperStatus = 'running' | 'done' | 'idle' | 'error' | 'stopped' | 'interrupted';
export type HelperPhase = 'thinking' | 'writing' | 'running_tool' | 'waiting_approval' | 'queued';

export interface HelperStep { tool: string; target?: string | null; seconds?: number | null }
export interface HelperLastStep { tool: string; target?: string | null; ok?: boolean; seconds?: number | null }

export interface HelperProgress {
  phase?: HelperPhase | null;
  step?: HelperStep | null;
  steps_done?: number;
  steps_failed?: number;
  elapsed_s?: number;
  last_said?: string | null;
  idle_s?: number | null;
  card?: { title?: string | null; waiting_s?: number | null } | null;
  last_steps?: HelperLastStep[];
}

export interface Helper {
  agent_id: string;
  chat_id: number;
  name: string;
  model_name?: string | null;
  provider_type?: string | null;
  effort?: string | null;
  status: HelperStatus;
  last_result?: string | null;
  progress?: HelperProgress | null;
}

/**
 * Backend SUBAGENTS_API.md: `tripped` once the chat's automatic turns reached the limit,
 * `paused` after the user's Stop, `held` = results waiting. Continue is offered while held > 0.
 */
export interface FuseState { tripped: boolean; paused?: boolean; held: number; count?: number; limit?: number }

export interface HelpersApi {
  helpers: Helper[];
  fuse: FuseState | null;
  /** When the last list arrived (ms): the strip adds the time since then to running counters. */
  fetchedAt: number;
  refresh: () => Promise<void>;
  stop: (agentId: string) => Promise<void>;
  stopAll: () => Promise<void>;
  /** The fuse notice's Continue: allows ten more automatic turns. */
  continueTurns: () => Promise<boolean>;
}

type Http = { get: (...a: any[]) => Promise<any>; post: (...a: any[]) => Promise<any> };

export const HELPER_POLL_MS = 2000;

const STATUSES: HelperStatus[] = ['running', 'done', 'idle', 'error', 'stopped', 'interrupted'];

/**
 * The list endpoint's rows (backend SUBAGENTS_API.md: `{agents: [...], fuse, text}`, a row is the
 * subagents table row: `id`, `child_conv`, ...). Named here as agent_id / chat_id; malformed rows
 * are dropped.
 */
export function parseHelperList(data: any): { helpers: Helper[]; fuse: FuseState | null } {
  const rows = Array.isArray(data) ? data
    : Array.isArray(data?.agents) ? data.agents
    : Array.isArray(data?.subagents) ? data.subagents
    : Array.isArray(data?.helpers) ? data.helpers
    : Array.isArray(data?.rows) ? data.rows : [];
  const helpers: Helper[] = rows
    .filter((r: any) => r && typeof r === 'object' && (r.agent_id ?? r.id) != null && typeof r.name === 'string')
    .map((r: any) => ({
      ...r,
      agent_id: String(r.agent_id ?? r.id),
      chat_id: Number(r.chat_id ?? r.child_conv),
      status: STATUSES.includes(r.status) ? r.status : 'idle',
    }));
  const f = data && !Array.isArray(data) ? data.fuse : null;
  const held = f && typeof f === 'object' ? Number(f.held) : 0;
  const fuse = f && typeof f === 'object'
    ? { tripped: !!f.tripped, paused: !!f.paused, held: Number.isFinite(held) ? held : 0,
        ...(typeof f.count === 'number' ? { count: f.count } : {}), ...(typeof f.limit === 'number' ? { limit: f.limit } : {}) }
    : null;
  return { helpers, fuse };
}

export function useHelpers({ API, conversationId, token, http = axios as unknown as Http, nudge, pollMs = HELPER_POLL_MS }: {
  API?: string;
  conversationId: number | null | undefined;
  token?: string | null;
  http?: Http;
  nudge?: unknown;
  pollMs?: number;
}): HelpersApi {
  const [helpers, setHelpers] = useState<Helper[]>([]);
  const [fuse, setFuse] = useState<FuseState | null>(null);
  const [fetchedAt, setFetchedAt] = useState(() => Date.now());
  const convRef = useRef(conversationId);
  convRef.current = conversationId;
  // Only a real token goes on the call: an empty one would override the session header
  // useAuth puts on axios.defaults (the trap useAIConfig.ts documents).
  const headers = token ? { headers: { 'X-Session-Token': token } } : undefined;

  const refresh = useCallback(async () => {
    const id = convRef.current;
    if (!API || id == null) return;
    try {
      const res = await http.get(`${API}/subagents/list?conversation_id=${id}`, headers);
      if (convRef.current !== id) return;
      const parsed = parseHelperList(res?.data);
      setHelpers(parsed.helpers);
      setFuse(parsed.fuse);
      setFetchedAt(Date.now());
    } catch {
      // A backend without helpers (or a blip) leaves the strip as it was; the next nudge retries.
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [API, http, token]);

  // A different chat: forget the old one's helpers at once, then read the new one's.
  useEffect(() => {
    setHelpers([]);
    setFuse(null);
    void refresh();
  }, [conversationId, refresh]);

  useEffect(() => { if (nudge !== undefined) void refresh(); }, [nudge, refresh]);

  const live = helpers.some(h => h.status === 'running') || !!fuse?.tripped || (fuse?.held ?? 0) > 0;
  useEffect(() => {
    if (!live) return;
    const id = setInterval(() => { void refresh(); }, pollMs);
    return () => clearInterval(id);
  }, [live, pollMs, refresh]);

  const stop = useCallback(async (agentId: string) => {
    if (!API) return;
    try { await http.post(`${API}/subagents/stop`, { conversation_id: convRef.current, agent_id: agentId }, headers); } catch { /* the next read shows the truth */ }
    await refresh();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [API, http, token, refresh]);

  const stopAll = useCallback(async () => {
    if (!API) return;
    const running = helpers.filter(h => h.status === 'running');
    await Promise.all(running.map(h =>
      http.post(`${API}/subagents/stop`, { conversation_id: convRef.current, agent_id: h.agent_id }, headers).catch(() => {})));
    await refresh();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [API, http, token, helpers, refresh]);

  const continueTurns = useCallback(async () => {
    const id = convRef.current;
    if (!API || id == null) return false;
    try {
      await http.post(`${API}/subagents/continue`, { conversation_id: id }, headers);
    } catch (err: any) {
      // 409 nothing_held: the results were already released (a second click, another window).
      if (err?.response?.status !== 409) return false;
    }
    setFuse(f => (f ? { ...f, tripped: false, paused: false, held: 0 } : f));
    await refresh();
    return true;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [API, http, token, refresh]);

  return { helpers, fuse, fetchedAt, refresh, stop, stopAll, continueTurns };
}
