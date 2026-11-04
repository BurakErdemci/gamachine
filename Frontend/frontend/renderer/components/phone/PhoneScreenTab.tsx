import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useLang } from '../../lib/i18n';
import { useLiveFeatures } from '../../lib/liveFeatures';
import { usePageVisible } from '../../lib/mobile';
import { ComputerLivePanel, useNow } from '../home/ComputerLivePanel';

/**
 * The phone chat's "Ekran" tab: the computer-use live view, watch-only (zoom moves the picture,
 * nothing reaches the desktop) with the panel's own Stop kept as the emergency halt. While it is
 * on screen the panel reports `panel-visible`, which the PC keeps as the remote flag that drives
 * its frame capture (lib/remote/desktopRunner.ts). A hidden page (phone locked, another app in front)
 * is not watching, and a link that comes back says it again: the PC may have restarted and forgotten.
 */
export function PhoneScreenTab({ visible: tabShown, connected = true }: { visible: boolean; connected?: boolean }) {
  const { t } = useLang();
  const features = useLiveFeatures();
  const visible = tabShown && usePageVisible();
  const [lastFrameAt, setLastFrameAt] = useState<number | null>(null);
  const onFrame = useCallback(() => setLastFrameAt(Date.now()), []);
  const now = useNow(visible && features.computer);
  const [reconnects, setReconnects] = useState(0);
  const wasConnected = useRef(connected);
  useEffect(() => {
    if (connected && !wasConnected.current) setReconnects(n => n + 1);
    wasConnected.current = connected;
  }, [connected]);

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
      <ComputerLivePanel visible={visible} zoomable onFrame={onFrame} resend={reconnects} />
    </section>
  );
}
