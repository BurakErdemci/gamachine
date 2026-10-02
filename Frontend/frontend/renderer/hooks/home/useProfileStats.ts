import { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';

import { normalizeProfileStats, type ProfileRange, type ProfileStats } from '../../lib/profileStats';

interface Options {
  api: string;
  token?: string | null;
  /** False until a user and a backend exist. */
  enabled?: boolean;
  /** Test seam; the app uses axios. */
  http?: { get: (url: string, config?: any) => Promise<{ data?: unknown }> };
}

/**
 * `GET ${api}/profile/stats?range=…` for the maker profile and the sidebar card.
 *
 * One hook for both on purpose: the backend reports an achievement as `new` exactly once (the
 * call that stores its first sighting). Two independent readers would let the sidebar's call
 * swallow the profile's "new" mark, so every answer passes through here and the ids seen as new
 * stay new for the rest of the session.
 *
 * Level and XP are all-time whatever the range, so the sidebar reads the latest answer of any
 * range. A failed read keeps the last answer (stale numbers beat none).
 */
export function useProfileStats({ api, token, enabled = true, http = axios }: Options) {
  const [range, setRange] = useState<ProfileRange>('6m');
  const [byRange, setByRange] = useState<Partial<Record<ProfileRange, ProfileStats>>>({});
  const [latest, setLatest] = useState<ProfileStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const newIds = useRef(new Set<string>());
  const seq = useRef(0);

  const load = useCallback(async (r: ProfileRange) => {
    if (!api || !enabled) return;
    const mine = ++seq.current;
    setLoading(true);
    try {
      const res = await http.get(`${api}/profile/stats`, {
        params: { range: r },
        headers: { 'X-Session-Token': token ?? '' },
      });
      // Guard every cache and session-new write, including responses issued before reset
      // (profile screen audit, 2 Oct 2026).
      if (mine !== seq.current) return;
      const stats = normalizeProfileStats(res?.data);
      if (!stats) throw new Error('not a profile answer');
      for (const a of stats.achievements) if (a.new) newIds.current.add(a.id);
      const marked: ProfileStats = {
        ...stats,
        achievements: stats.achievements.map(a => (newIds.current.has(a.id) && a.unlocked ? { ...a, new: true } : a)),
      };
      setByRange(prev => ({ ...prev, [r]: marked }));
      setLatest(marked);
      if (mine === seq.current) setFailed(false);
    } catch {
      if (mine === seq.current) setFailed(true);
    } finally {
      if (mine === seq.current) setLoading(false);
    }
  }, [api, token, enabled, http]);

  const rangeRef = useRef(range);
  rangeRef.current = range;
  const refresh = useCallback(() => load(rangeRef.current), [load]);

  useEffect(() => {
    if (enabled) void load(range);
  }, [enabled, range, load]);

  /** After a reset every cached range is stale: drop them and read the one on screen. */
  const afterReset = useCallback(async () => {
    ++seq.current;
    newIds.current.clear();
    setByRange({});
    setLatest(null);
    await load(rangeRef.current);
  }, [load]);

  return {
    range,
    setRange,
    data: byRange[range] ?? latest,
    latest,
    loading,
    failed,
    refresh,
    afterReset,
  };
}

export type ProfileStatsState = ReturnType<typeof useProfileStats>;
