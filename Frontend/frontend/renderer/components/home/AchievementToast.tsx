import React, { useEffect, useState } from 'react';
import { useLang } from '../../lib/i18n';
import { MascotHead } from './BrandLogo';
import type { AchievementId } from '../../lib/profileStats';
import type { LevelUp } from '../../hooks/home/useProfileStats';

/** Mockup #15: 240 ms in, 4 s hold, 400 ms out (base.css `achv-in-out`, 4640 ms in all). */
export const ACHV_LIFE_MS = 4640;

interface AchievementToastProps {
  /** The active announcement; `null` = nothing on screen. */
  event: { seq: number } | null;
  achievement?: AchievementId | null;
  levelUp?: LevelUp | null;
}

/** Earned achievements and level-ups share the theme slots and lifetime. */
export const AchievementToast: React.FC<AchievementToastProps> = ({ event, achievement, levelUp }) => {
  const { t } = useLang();
  const [goneSeq, setGoneSeq] = useState<number | null>(null);

  useEffect(() => {
    if (!event) return;
    const id = setTimeout(() => setGoneSeq(event.seq), ACHV_LIFE_MS);
    return () => clearTimeout(id);
  }, [event?.seq]);

  if (!event || (!achievement && !levelUp) || goneSeq === event.seq) return null;
  const kicker = t(achievement ? 'achv.kicker' : 'achv.levelKicker');
  return (
    <div
      key={event.seq}
      className="achv is-playing"
      role="status"
      aria-live="polite"
      data-testid="achievement-toast"
    >
      <span className="achv-icon" aria-hidden="true"><MascotHead className="mascot" /></span>
      <span className="achv-text">
        <span className="achv-kicker lex">
          <span className="lex-d">{kicker}</span>
          <span className="lex-q">{kicker}</span>
          <span className="lex-s">{kicker}</span>
        </span>
        <span className="achv-title">{achievement
          ? t(`pf.ach.${achievement}.name`)
          : t('achv.levelTitle', { n: levelUp!.level })}</span>
        {achievement && <span className="achv-sub">{t(`pf.ach.${achievement}.done`)}</span>}
        {!achievement && levelUp?.rankChanged && <span className="achv-sub">{t(`pf.rank.${levelUp.rank}`)}</span>}
      </span>
      <span className="achv-life" aria-hidden="true" />
    </div>
  );
};
