import React, { useEffect, useState } from 'react';
import { useLang } from '../../lib/i18n';
import { MascotHead } from './BrandLogo';

// TODO(profile): XP has no data source yet (ENTEGRASYON-PLANI "Later"); the band shows the
// mockup's constant. Sade hides it (theme-sade.thread.css `.achv .xp`), as in the mockup.
export const DONE_XP = 120;

/** Mockup #15: 240 ms in, 4 s hold, 400 ms out (base.css `achv-in-out`, 4640 ms in all). */
export const ACHV_LIFE_MS = 4640;

interface AchievementToastProps {
  /** A finished turn to celebrate (see lib/turnDone.ts); `null` = nothing on screen. */
  event: { seq: number } | null;
  /** The finished chat's title. */
  title?: string | null;
}

/**
 * The achievement band / "Done" toast (mockup `.achv`). Arena: a chamfered HUD toast with the
 * mascot tile, "Achievement unlocked", the XP and a lifetime fuse; Sade: a quiet notification
 * that reads "Done" without mascot or XP; Pafta / Atolye: their own tag. Same DOM in every theme,
 * the words switch through the `.lex` slot.
 */
export const AchievementToast: React.FC<AchievementToastProps> = ({ event, title }) => {
  const { t } = useLang();
  const [goneSeq, setGoneSeq] = useState<number | null>(null);

  useEffect(() => {
    if (!event) return;
    const id = setTimeout(() => setGoneSeq(event.seq), ACHV_LIFE_MS);
    return () => clearTimeout(id);
  }, [event]);

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
          <span className="lex-d">{t('achv.kicker')}</span>
          <span className="lex-q">{t('achv.kicker')}</span>
          <span className="lex-s">{t('achv.done')}</span>
        </span>
        <span className="achv-title">{t('achv.title', { ad: name || t('achv.untitled') })}</span>
      </span>
      <span className="achv-xp xp num">{t('achv.xp', { xp: DONE_XP })}</span>
      <span className="achv-life" aria-hidden="true" />
    </div>
  );
};
