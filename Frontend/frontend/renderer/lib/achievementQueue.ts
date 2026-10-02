import { useEffect, useRef, useState } from 'react';
import { ACHV_LIFE_MS } from '../components/home/AchievementToast';
import type { AchievementId } from './profileStats';

interface Band {
  seq: number;
  title?: string | null;
  achievement?: AchievementId;
  xp: number | null;
  gainAfter: number;
}

/** Achievements replace turns; queued achievements each keep their own lifetime
 * (achievement band audit, 2 Oct 2026). Sequence numbers belong to this display queue. */
export function useAchievementQueue(
  turn: { seq: number } | null,
  title: string | null | undefined,
  gain: { seq: number; xp: number } | null,
  unlocked: { seq: number; ids: AchievementId[] } | null,
) {
  const [bands, setBands] = useState<Band[]>([]);
  const seenTurn = useRef<number | null>(null);
  const seenUnlocked = useRef<number | null>(null);
  const displaySeq = useRef(0);

  useEffect(() => {
    const newTurn = turn != null && turn.seq !== seenTurn.current;
    const newUnlock = unlocked != null && unlocked.seq !== seenUnlocked.current;
    seenTurn.current = turn?.seq ?? null;
    seenUnlocked.current = unlocked?.seq ?? null;
    const additions: Band[] = newUnlock ? unlocked.ids.map(achievement => ({
      seq: ++displaySeq.current, achievement, xp: null, gainAfter: 0,
    })) : [];
    const turnBand: Band | null = newTurn ? {
      seq: ++displaySeq.current, title, xp: null, gainAfter: gain?.seq ?? 0,
    } : null;
    setBands(previous => {
      let next = previous;
      if (additions.length) next = [...next.filter(b => b.achievement), ...additions];
      if (turnBand && !next.some(b => b.achievement)) next = [turnBand];
      if (!turn) next = next.filter(b => b.achievement);
      const active = next[0];
      if (active && !active.achievement && gain && gain.seq > active.gainAfter && active.xp !== gain.xp) {
        next = [{ ...active, xp: gain.xp }, ...next.slice(1)];
      }
      return next;
    });
  }, [turn?.seq, title, gain, unlocked]);

  const active = bands[0] ?? null;
  useEffect(() => {
    if (!active) return;
    const seq = active.seq;
    const timer = setTimeout(() => {
      setBands(previous => previous[0]?.seq === seq ? previous.slice(1) : previous);
    }, ACHV_LIFE_MS);
    return () => clearTimeout(timer);
  }, [active?.seq]);

  return active;
}
