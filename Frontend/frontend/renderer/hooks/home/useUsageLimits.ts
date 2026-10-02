import { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';

import type { UsageLimits } from '../../lib/usageLimits';

/** Menu open: the user is looking at the numbers, so they follow within a minute. */
export const USAGE_POLL_OPEN_MS = 60_000;
/** Menu closed: only the chip's two hairlines read it. */
export const USAGE_POLL_IDLE_MS = 5 * 60_000;

// The last answer per backend, kept across mounts so a reopened menu or settings page
// draws the cached numbers at once instead of empty meters.
const cache = new Map<string, UsageLimits>();

/** Test seam: forget every cached answer. */
export function resetUsageLimitsCache(): void {
  cache.clear();
}

function isLimits(v: unknown): v is UsageLimits {
  return !!v && typeof v === 'object' && Array.isArray((v as UsageLimits).families);
}

interface Options {
  api: string;
  token?: string | null;
  http?: { get: (...a: any[]) => Promise<any> };
  /** The model menu is open: poll every minute and ask once for a fresh measurement. */
  menuOpen?: boolean;
  /** False until a user and a backend exist. */
  enabled?: boolean;
  openMs?: number;
  idleMs?: number;
}

/**
 * `GET ${api}/usage/limits`, with the session token every other call sends.
 * A failed request keeps the last answer (stale numbers beat none, and the families carry
 * their own `stale` flag); with no answer at all there are simply no meters.
 */
export function useUsageLimits({
  api, token, http = axios, menuOpen = false, enabled = true, openMs = USAGE_POLL_OPEN_MS, idleMs = USAGE_POLL_IDLE_MS,
}: Options) {
  const [data, setData] = useState<UsageLimits | null>(() => (api ? cache.get(api) ?? null : null));
  const [failed, setFailed] = useState(false);
  const seqRef = useRef(0);
  const followUpRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const delayRef = useRef(4000);

  const load = useCallback(async (refresh = false, followUp = false): Promise<void> => {
    if (followUpRef.current !== null) clearTimeout(followUpRef.current);
    followUpRef.current = null;
    if (!followUp) delayRef.current = 4000;
    if (!api || !enabled) return;
    const seq = ++seqRef.current;
    try {
      const res = await http.get(`${api}/usage/limits`, {
        params: refresh ? { refresh: 1 } : undefined,
        headers: { 'X-Session-Token': token ?? '' },
      });
      if (seq !== seqRef.current) return;
      if (isLimits(res?.data)) {
        cache.set(api, res.data);
        setData(res.data);
        setFailed(false);
        // The backend refreshes in the background (settings audit, 2 Oct 2026).
        // Follow the cache until ready without starting another refresh.
        if (res.data.families.some(f => f && (f.status === 'loading' || (f.stale && f.status !== 'error')))) {
          const delay = delayRef.current;
          delayRef.current = Math.min(delay * 2, 30000);
          followUpRef.current = setTimeout(() => { void load(false, true); }, delay);
        } else {
          delayRef.current = 4000;
        }
      } else {
        setFailed(true);
      }
    } catch {
      if (seq === seqRef.current) setFailed(true);
    }
  }, [api, token, http, enabled]);

  useEffect(() => () => {
    ++seqRef.current;
    if (followUpRef.current !== null) clearTimeout(followUpRef.current);
    followUpRef.current = null;
  }, [load]);

  // First read once a backend and a user exist (a menu opening first reads it below instead).
  useEffect(() => {
    if (api && enabled && !menuOpen) void load(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, enabled, load]);

  // The poll: slow while only the chip reads it, every minute while the menu is open.
  useEffect(() => {
    if (!api || !enabled) return;
    const id = setInterval(() => { void load(false); }, menuOpen ? openMs : idleMs);
    return () => clearInterval(id);
  }, [api, enabled, menuOpen, openMs, idleMs, load]);

  // Opening the menu asks the backend to measure again (once per opening).
  useEffect(() => {
    if (menuOpen && api && enabled) void load(true);
  }, [menuOpen, api, enabled, load]);

  return { data, failed, reload: load };
}
