import React, { useEffect, useState } from 'react';
import { useLang } from '../../lib/i18n';
import { MascotHead } from './BrandLogo';
import type { AchievementId } from '../../lib/profileStats';

/** Mockup #15: 240 ms in, 4 s hold, 400 ms out (base.css `achv-in-out`, 4640 ms in all). */
export const ACHV_LIFE_MS = 4640;

interface AchievementToastProps {
  /** A finished turn to celebrate (see lib/turnDone.ts); `null` = nothing on screen. */
  event: { seq: number } | null;
  /** The finished chat's title. */
  title?: string | null;
  xp?: number | null;
  achievement?: AchievementId | null;
}

/**
 * Turn completion and earned achievements share the theme slots and lifetime. Only a measured
 * turn gain earns an XP label (achievement band audit, 2 Oct 2026).
 */
export const AchievementToast: React.FC<AchievementToastProps> = ({ event, title, xp, achievement }) => {
  const { t } = useLang();
  const [goneSeq, setGoneSeq] = useState<number | null>(null);

  useEffect(() => {
    if (!event) return;
    const id = setTimeout(() => setGoneSeq(event.seq), ACHV_LIFE_MS);
    return () => clearTimeout(id);
  }, [event?.seq]);

  if (!event || goneSeq === event.seq) return null;
  const name = (title || '').trim();
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
          <span className="lex-d">{t(achievement ? 'achv.kicker' : 'achv.turnKicker')}</span>
          <span className="lex-q">{t(achievement ? 'achv.kicker' : 'achv.turnKicker')}</span>
          <span className="lex-s">{t(achievement ? 'achv.kicker' : 'achv.done')}</span>
        </span>
        <span className="achv-title">{achievement
          ? t(`pf.ach.${achievement}.name`)
          : t('achv.turnTitle', { ad: name || t('achv.untitled') })}</span>
        {achievement && <span className="achv-sub">{t(`pf.ach.${achievement}.done`)}</span>}
      </span>
      {!achievement && typeof xp === 'number' && Number.isInteger(xp) && xp > 0 && (
        <span className="achv-xp xp num">{t('achv.xp', { xp })}</span>
      )}
      <span className="achv-life" aria-hidden="true" />
    </div>
  );
};
