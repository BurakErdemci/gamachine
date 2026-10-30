import React, { useCallback, useEffect, useRef, useState } from 'react';

import { useLang } from '../../../lib/i18n';
import { confirmDialog } from '../../ui/ConfirmDialog';
import { Lamp, SetCard, SetGroup, SetPageHead, SetRow, SetSwitch } from './controls';

type Toast = (msg: string, type: 'success' | 'error' | 'warning' | 'info') => void;

const CHANNEL = 'browser-live';

const ipc = (): any => (typeof window !== 'undefined' ? (window as any).ipc ?? null : null);

async function call(req: Record<string, unknown>): Promise<any> {
  const channel = ipc();
  if (!channel?.invoke) return { error: 'no_desktop' };
  try {
    return await channel.invoke(CHANNEL, req);
  } catch {
    return { error: 'ipc_failed' };
  }
}

const isError = (v: unknown) => !!v && typeof v === 'object' && typeof (v as { error?: unknown }).error === 'string';

/**
 * The agent browser's switch (`browser_use_enabled`), shown under the computer-use one. The
 * read and the change go through main on the `browser-live` channel (ops `settings`, `enable`,
 * `disable`), because changing it needs the UI secret.
 */
export const BrowserUsePage = ({ saved, showToast }: { saved: () => void; showToast?: Toast }) => {
  const { t } = useLang();
  const desktop = ipc() !== null;
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [readFailed, setReadFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const refresh = useCallback(async () => {
    if (!desktop) return;
    const r = await call({ op: 'settings' });
    if (!alive.current) return;
    if (isError(r) || typeof r?.enabled !== 'boolean') { setReadFailed(true); return; }
    setReadFailed(false);
    setEnabled(r.enabled);
  }, [desktop]);
  useEffect(() => { void refresh(); }, [refresh]);

  const toggle = async () => {
    if (busy || enabled === null) return;
    setBusy(true);
    setMsg(null);
    try {
      const r = await call({ op: enabled ? 'disable' : 'enable' });
      if (isError(r) || typeof r?.enabled !== 'boolean' || r.enabled === enabled) {
        setMsg(t('bp.set.failed'));
        showToast?.(t('bp.set.failed'), 'error');
      } else {
        setEnabled(r.enabled);
        saved();
      }
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  const [clearing, setClearing] = useState(false);
  const clearData = async () => {
    if (clearing || !(await confirmDialog(t('bp.set.clearConfirm'), t('bp.set.clear'), t('confirm.cancel')))) return;
    setClearing(true);
    setMsg(null);
    try {
      const r = await call({ op: 'clear-browsing-data' });
      if (isError(r)) {
        setMsg(t('bp.set.clearFailed'));
        showToast?.(t('bp.set.clearFailed'), 'error');
      } else {
        showToast?.(t('bp.set.cleared'), 'success');
      }
    } finally {
      if (alive.current) setClearing(false);
    }
  };

  const hint = !desktop ? t('bp.set.desktopOnly')
    : readFailed ? t('bp.set.readFailed')
    : enabled === null ? t('bp.set.loading')
    : enabled ? t('bp.set.masterOn') : t('bp.set.masterOff');

  return (
    <>
      <SetPageHead title={t('set.nav.tarayici')} lede={t('bp.set.lede')} />
      <SetGroup title={t('bp.set.title')} testId="bp-settings">
        <SetCard>
          <SetRow
            className="set-row-hero cu-master-row"
            testId="bp-master-row"
            name={t('bp.set.master')}
            hint={<><Lamp tone={enabled ? 'ok' : undefined} /><span data-testid="bp-master-state">{hint}</span></>}
            control={(
              <SetSwitch
                checked={enabled === true}
                onToggle={() => { void toggle(); }}
                label={t('bp.set.master')}
                disabled={!desktop || enabled === null || busy}
                busy={busy}
                testId="bp-switch"
              />
            )}
          />
          <SetRow
            testId="bp-clear-row"
            name={t('bp.set.clear')}
            hint={t('bp.set.clearHint')}
            control={(
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => { void clearData(); }}
                disabled={!desktop || clearing} data-testid="bp-clear">
                {t('bp.set.clear')}
              </button>
            )}
          />
        </SetCard>
        <p className="set-hint" data-testid="bp-privacy">{t('bp.set.privacy')}</p>
        {msg && <p data-testid="bp-msg" className="set-msg set-msg-err" role="alert">{msg}</p>}
      </SetGroup>
    </>
  );
};
