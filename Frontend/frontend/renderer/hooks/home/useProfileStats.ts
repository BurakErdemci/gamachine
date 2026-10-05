import { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';

import { ACHIEVEMENT_IDS, normalizeProfileStats, type AchievementId, type ProfileRange, type ProfileRank, type ProfileStats } from '../../lib/profileStats';

export interface LevelUp {
  seq: number;
  level: number;
  rank: ProfileRank;
  rankChanged: boolean;
}

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
  const [gain, setGain] = useState<{ seq: number; xp: number } | null>(null);
  const [unlocked, setUnlocked] = useState<{ seq: number; ids: AchievementId[] } | null>(null);
  const [levelUp, setLevelUp] = useState<LevelUp | null>(null);
  const latestRef = useRef<ProfileStats | null>(null);
  const newIds = useRef(new Set<string>());
  const seq = useRef(0);
  // Bumped only by a reset: answers issued before it must not write session-new ids, but a
  // merely superseded answer still may (guide audit, 2 Oct 2026).
  const epoch = useRef(0);
  // Unlock events carry their own counter, and an event that has not rendered yet absorbs the
  // next one: two answers settling in one React batch would otherwise overwrite each other and
  // lose an announcement forever, since `newIds` already holds both (guide fix verify, 2 Oct 2026).
  const unlockSeq = useRef(0);
  const renderedUnlockSeq = useRef<number | null>(null);
  useEffect(() => { renderedUnlockSeq.current = unlocked?.seq ?? null; }, [unlocked]);

  const load = useCallback(async (r: ProfileRange) => {
    if (!api || !enabled) return;
    const mine = ++seq.current;
    const myEpoch = epoch.current;
    setLoading(true);
    try {
      const res = await http.get(`${api}/profile/stats`, {
        params: { range: r },
        headers: { 'X-Session-Token': token ?? '' },
      });
      const stats = normalizeProfileStats(res?.data);
      if (!stats) throw new Error('not a profile answer');
      // The backend reports an unlock as `new` exactly once, so a successful answer that a newer
      // request superseded (range switched mid-refresh) carries the only announcement: record it
      // before the latest-request guard below. Answers issued before a reset stay dropped
      // (guide audit, 2 Oct 2026).
      if (myEpoch === epoch.current) {
        const fresh = ACHIEVEMENT_IDS.filter(id => !newIds.current.has(id)
          && stats.achievements.some(a => a.id === id && a.new && a.unlocked === true));
        if (fresh.length) {
          const eventSeq = ++unlockSeq.current;
          setUnlocked(prev => {
            const pending = prev && prev.seq !== renderedUnlockSeq.current ? prev.ids : [];
            const ids = ACHIEVEMENT_IDS.filter(id => pending.includes(id) || fresh.includes(id));
            return { seq: eventSeq, ids };
          });
        }
        for (const a of stats.achievements) if (a.new) newIds.current.add(a.id);
      }
      // Guard every cache write, including responses issued before reset
      // (profile screen audit, 2 Oct 2026).
      if (mine !== seq.current) return;
      // Compare accepted answers, never a partial ledger or the first answer after reset
      // (achievement band audit, 2 Oct 2026).
      const previous = latestRef.current;
      if (previous && !previous.xp_partial && !stats.xp_partial && stats.xp > previous.xp) {
        setGain({ seq: mine, xp: stats.xp - previous.xp });
      }
      if (previous && !previous.xp_partial && !stats.xp_partial && stats.level > previous.level) {
        setLevelUp({ seq: mine, level: stats.level, rank: stats.rank, rankChanged: stats.rank !== previous.rank });
      }
      const marked: ProfileStats = {
        ...stats,
        achievements: stats.achievements.map(a => (newIds.current.has(a.id) && a.unlocked ? { ...a, new: true } : a)),
      };
      setByRange(prev => ({ ...prev, [r]: marked }));
      setLatest(marked);
      latestRef.current = marked;
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

  // A session token identifies the profile owner; late answers from the old owner are dropped.
  useEffect(() => {
    ++seq.current;
    ++epoch.current;
    newIds.current.clear();
    latestRef.current = null;
    setGain(null);
    setUnlocked(null);
    setLevelUp(null);
    setByRange({});
    setLatest(null);
  }, [api, token]);

  useEffect(() => {
    if (enabled) void load(range);
  }, [enabled, range, load]);

  /** After a reset every cached range is stale: drop them and read the one on screen. */
  const afterReset = useCallback(async () => {
    ++seq.current;
    ++epoch.current;
    newIds.current.clear();
    latestRef.current = null;
    setGain(null);
    setUnlocked(null);
    setLevelUp(null);
    setByRange({});
    setLatest(null);
    await load(rangeRef.current);
  }, [load]);

  return {
    range,
    setRange,
    data: byRange[range] ?? latest,
    latest,
    gain,
    unlocked,
    levelUp,
    loading,
    failed,
    refresh,
    afterReset,
  };
}

export type ProfileStatsState = ReturnType<typeof useProfileStats>;
