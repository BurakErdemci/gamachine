import React, { useEffect, useState } from 'react';
import { useLang } from '../../lib/i18n';
import { LIVE_CHANNEL, formatElapsed, haltKeys, useComputerLive, useNow } from './ComputerLivePanel';

const invoke = (req: Record<string, unknown>): Promise<any> => {
  const ipc = typeof window !== 'undefined' ? (window as any).ipc : null;
  return Promise.resolve(ipc?.invoke?.(LIVE_CHANNEL, req)).catch(() => null);
};

/**
 * The floating "Gamachine is using your computer" tag (pages/computer-live.tsx). The grip moves
 * the window; the body opens the live tab in the main window; Stop goes through main, which
 * holds the run's conversation and the host's halt op.
 */
export function ComputerLiveIndicator() {
  const { t } = useLang();
  const { state } = useComputerLive();
  const now = useNow(state.active);
  const [stopping, setStopping] = useState(false);

  // A new run is a new Stop; the previous run's pending state must not disable it.
  useEffect(() => { setStopping(false); }, [state.conversationId]);

  const stop = async () => {
    if (stopping) return;
    setStopping(true);
    await invoke({ op: 'halt' });
    // Success idles the run (main closes this window) and a failure leaves the run live: either way Stop is usable again.
    setStopping(false);
  };

  const app = state.appName ?? t('cu.live.unknownApp');
  const elapsed = state.startedAt !== null ? formatElapsed(now - state.startedAt) : '00:00';
  return (
    <div className="cul-tag" data-state={state.active ? 'active' : 'idle'}>
      <span className="cul-grip" title={t('cu.live.drag')} aria-hidden="true" />
      <button type="button" className="cul-tag-body" onClick={() => { void invoke({ op: 'open-panel' }); }}
        title={t('cu.live.open')}>
        <span className="cul-dot" aria-hidden="true" />
        <span className="cul-tag-text">
          {state.haltError
            ? <span className="cul-tag-title cul-error" role="alert">{t('cu.live.stopFailed')}</span>
            : <span className="cul-tag-title">{t('cu.live.using')}</span>}
          <span className="cul-tag-meta">
            <span className="cul-app">{app}</span>
            <span className="cul-sep" aria-hidden="true">·</span>
            <span className="cul-time" aria-label={t('cu.live.elapsed')}>{elapsed}</span>
          </span>
        </span>
      </button>
      <button type="button" className="cul-stop" onClick={stop} disabled={stopping}
        title={t('cu.live.stopTitle', { keys: haltKeys() })}>
        <svg className="cul-stop-ic" viewBox="0 0 12 12" aria-hidden="true"><rect x="2" y="2" width="8" height="8" rx="1" /></svg>
        <span>{t(stopping ? 'cu.live.stopping' : 'cu.live.stop')}</span>
      </button>
    </div>
  );
}
