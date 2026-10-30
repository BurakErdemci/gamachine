import { useCallback, useEffect, useRef, useState } from 'react';
import { computerUseStatus, isComputerUseError } from './computerUse';

export interface LiveFeatures { computer: boolean; browser: boolean }

/** Fired by the settings pages after a switch changed, so the Canlı tab re-reads both. */
export const LIVE_FEATURES_EVENT = 'app-live-features-changed';

async function readBrowser(): Promise<boolean> {
  const ipc = typeof window !== 'undefined' ? (window as any).ipc : null;
  if (!ipc?.invoke) return false;
  try {
    const r = await ipc.invoke('browser-live', { op: 'settings' });
    return r?.enabled === true;
  } catch {
    return false;
  }
}

/** Which live sides are switched on (and, for computer use, available on this OS). Anything unreadable counts as off. */
export function useLiveFeatures(): LiveFeatures {
  const [features, setFeatures] = useState<LiveFeatures>({ computer: false, browser: false });
  const alive = useRef(true);
  const latest = useRef(0);

  const refresh = useCallback(async () => {
    const seq = ++latest.current;
    const [status, browser] = await Promise.all([computerUseStatus().catch(() => null), readBrowser()]);
    if (!alive.current || seq !== latest.current) return;
    const computer = !!status && !isComputerUseError(status) && status.settings.enabled === true && status.available === true;
    setFeatures(prev => (prev.computer === computer && prev.browser === browser ? prev : { computer, browser }));
  }, []);

  useEffect(() => {
    alive.current = true;
    void refresh();
    window.addEventListener(LIVE_FEATURES_EVENT, refresh);
    return () => { alive.current = false; window.removeEventListener(LIVE_FEATURES_EVENT, refresh); };
  }, [refresh]);

  return features;
}
