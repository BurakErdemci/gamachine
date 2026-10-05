import { useEffect, useRef, useState } from 'react';
import { ACHV_LIFE_MS } from '../components/home/AchievementToast';
import type { AchievementId } from './profileStats';
import type { LevelUp } from '../hooks/home/useProfileStats';

interface Band {
  seq: number;
  achievement?: AchievementId;
  levelUp?: LevelUp;
}

/** Each announcement keeps its own lifetime. Sequence numbers belong to this display queue. */
export function useAchievementQueue(
  unlocked: { seq: number; ids: AchievementId[] } | null,
  levelUp: LevelUp | null,
) {
  const [bands, setBands] = useState<Band[]>([]);
  const seenUnlocked = useRef<number | null>(null);
  const seenLevelUp = useRef<number | null>(null);
  const displaySeq = useRef(0);

  useEffect(() => {
    const newUnlock = unlocked != null && unlocked.seq !== seenUnlocked.current;
    const newLevelUp = levelUp != null && levelUp.seq !== seenLevelUp.current;
    seenUnlocked.current = unlocked?.seq ?? null;
    seenLevelUp.current = levelUp?.seq ?? null;
    const additions: Band[] = newUnlock ? unlocked.ids.map(achievement => ({
      seq: ++displaySeq.current, achievement,
    })) : [];
    if (newLevelUp) additions.push({ seq: ++displaySeq.current, levelUp });
    if (!unlocked && !levelUp) setBands([]);
    else if (additions.length) setBands(previous => [...previous, ...additions]);
  }, [unlocked, levelUp]);

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
