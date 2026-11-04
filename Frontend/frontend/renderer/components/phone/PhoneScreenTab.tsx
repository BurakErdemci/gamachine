import React, { useCallback, useState } from 'react';
import { useLang } from '../../lib/i18n';
import { useLiveFeatures } from '../../lib/liveFeatures';
import { ComputerLivePanel, useNow } from '../home/ComputerLivePanel';

/**
 * The phone chat's "Ekran" tab: the computer-use live view, watch-only (zoom moves the picture,
 * nothing reaches the desktop) with the panel's own Stop kept as the emergency halt. While it is
 * on screen the panel reports `panel-visible`, which the PC keeps as the remote flag that drives
 * its frame capture (lib/remote/desktopRunner.ts).
 */
export function PhoneScreenTab({ visible }: { visible: boolean }) {
  const { t } = useLang();
  const features = useLiveFeatures();
  const [lastFrameAt, setLastFrameAt] = useState<number | null>(null);
  const onFrame = useCallback(() => setLastFrameAt(Date.now()), []);
  const now = useNow(visible && features.computer);

  if (!features.computer) {
    return (
      <section className="phone-screen" data-state="off">
        <p className="phone-screen-note">{t('cu.live.off')}</p>
        <p className="phone-screen-hint">{t('cu.live.offHint')}</p>
      </section>
    );
  }
  const ago = lastFrameAt === null ? null : Math.max(0, Math.round((now - lastFrameAt) / 1000));
  return (
    <section className="phone-screen" data-state="on">
      <p className="phone-screen-status" data-testid="phone-screen-status" role="status">
        {ago === null ? t('phone.screen.statusNoFrame') : t('phone.screen.status', { n: ago })}
      </p>
      <ComputerLivePanel visible={visible} zoomable onFrame={onFrame} />
    </section>
  );
}
