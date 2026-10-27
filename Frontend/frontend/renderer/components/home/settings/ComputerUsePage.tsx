import React, { useCallback, useEffect, useRef, useState } from 'react';

import { useLang, type TKey } from '../../../lib/i18n';
import { stripBidi } from '../../../lib/modelText';
import {
  computerUseStatus, hasComputerUseIpc, isComputerUseError, openScreenSettings, removeComputerGrant,
  requestAccessibility, restartComputerUse, setComputerUseEnabled, type ComputerUseStatus,
} from '../../../lib/computerUse';
import { Lamp, SetCard, SetGroup, SetPageHead, SetRow, SetSwitch } from './controls';

type Toast = (msg: string, type: 'success' | 'error' | 'warning' | 'info') => void;

/** How often the page re-reads the host while it is open: permissions change in System Settings. */
const REFRESH_MS = 4000;

const WARN_ITEMS: TKey[] = [
  'cu.set.warn.screen', 'cu.set.warn.modes', 'cu.set.warn.first', 'cu.set.warn.risky',
  'cu.set.warn.noPerAction', 'cu.set.warn.editors', 'cu.set.warn.stop',
];

/** The plain-language rules, shown whether or not this is the desktop app. */
const Notice = () => {
  const { t } = useLang();
  return (
    <div className="set-card cu-notice" data-testid="cu-notice">
      <p className="cu-notice-h">
        <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><path d="M10 3l7.5 13h-15z" /><path d="M10 8v3.6M10 13.6v.2" /></svg>
        <span>{t('cu.set.warnTitle')}</span>
      </p>
      <ul className="cu-rules">
        {WARN_ITEMS.map(k => (
          <li key={k} data-rule={k.slice('cu.set.warn.'.length)}>{t(k)}</li>
        ))}
      </ul>
    </div>
  );
};

/**
 * Settings > Bilgisayar: the computer-use switch, what it may do, the macOS permissions it
 * needs, the driver and the apps answered "Always". Every action goes through the main
 * process (`computer-use` channel); without it (tests, a browser) the page says so.
 */
export const ComputerUsePage = ({ saved, showToast }: { saved: () => void; showToast?: Toast }) => {
  const { t, lang } = useLang();
  const desktop = hasComputerUseIpc();
  const [status, setStatus] = useState<ComputerUseStatus | null>(null);
  const [readFailed, setReadFailed] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const prevPerms = useRef<ComputerUseStatus['permissions'] | null>(null);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const refresh = useCallback(async () => {
    if (!desktop) return null;
    const next = await computerUseStatus();
    if (!alive.current) return null;
    if (isComputerUseError(next)) { setReadFailed(true); return null; }
    setReadFailed(false);
    setStatus(next);
    return next;
  }, [desktop]);

  const fail = useCallback((key: TKey = 'cu.set.failed') => {
    setMsg({ ok: false, text: t(key) });
    showToast?.(t(key), 'error');
  }, [t, showToast]);

  // A permission that turned granted needs a driver restart to take effect.
  useEffect(() => {
    if (!status) return;
    const before = prevPerms.current;
    prevPerms.current = status.permissions;
    if (!before || !status.settings.enabled || !status.available || status.platform !== 'darwin') return;
    const gained = (!before.accessibility && status.permissions.accessibility)
      || (before.screen !== 'granted' && status.permissions.screen === 'granted');
    if (gained) void restartComputerUse().then(() => refresh());
  }, [status, refresh]);

  useEffect(() => {
    if (!desktop) return;
    void refresh();
    const id = setInterval(() => { void refresh(); }, REFRESH_MS);
    const onFocus = () => { void refresh(); };
    window.addEventListener('focus', onFocus);
    return () => { clearInterval(id); window.removeEventListener('focus', onFocus); };
  }, [desktop, refresh]);

  const run = async (name: string, action: () => Promise<unknown>, done?: (r: unknown) => void) => {
    if (busy) return;
    setBusy(name);
    setMsg(null);
    try {
      const r = await action();
      if (isComputerUseError(r)) fail();
      else done?.(r);
    } finally {
      await refresh();
      if (alive.current) setBusy(null);
    }
  };

  const enabled = status?.settings.enabled === true;
  const available = status?.available === true;
  const perms = status?.permissions;
  const mac = (status?.platform ?? 'darwin') === 'darwin';
  const screenOk = perms?.screen === 'granted';

  const toggle = () => run('switch', () => setComputerUseEnabled(!enabled), r => {
    const on = (r as { enabled?: unknown })?.enabled;
    if (typeof on === 'boolean' && on === enabled) { fail(); return; }
    saved();
  });

  const masterHint = !desktop ? t('cu.set.desktopOnly')
    : readFailed ? t('cu.set.readFailed')
    : !status ? t('cu.set.loading')
    : !available ? t(mac ? 'cu.set.unavailable' : 'cu.set.unavailableOther')
    : enabled ? t('cu.set.masterOn') : t('cu.set.masterOff');
  const masterTone = !status || !available ? undefined : enabled ? 'ok' : undefined;

  const driverRunning = status?.running === true;
  const driverText = !enabled ? t('cu.set.driverOff') : driverRunning ? t('cu.set.driverRunning') : t('cu.set.driverDown');

  const dateOf = (iso: string) => {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString(lang === 'tr' ? 'tr-TR' : 'en-GB');
  };

  return (
    <div className="cu-page" data-testid="cu-page" data-desktop={desktop ? 'true' : 'false'}>
      <SetPageHead title={t('set.nav.bilgisayar')} lede={t(mac ? 'cu.set.lede' : 'cu.set.ledeOther')} />
      <p className="set-msg">{t('cu.set.providers')}</p>

      <SetGroup>
        <SetCard>
          <SetRow
            className="set-row-hero cu-master-row"
            testId="cu-master-row"
            name={t('cu.set.master')}
            hint={<><Lamp tone={masterTone} /><span data-testid="cu-master-state">{masterHint}</span></>}
            control={(
              <SetSwitch
                checked={enabled}
                onToggle={() => { void toggle(); }}
                label={t('cu.set.master')}
                disabled={!desktop || !status || !available || busy !== null}
                busy={busy === 'switch'}
                testId="cu-switch"
              />
            )}
          />
        </SetCard>
        {msg && (
          <p data-testid="cu-msg" className={`set-msg ${msg.ok ? 'set-msg-ok' : 'set-msg-err'}`} role={msg.ok ? 'status' : 'alert'}>
            {msg.text}
          </p>
        )}
      </SetGroup>

      <SetGroup>
        <Notice />
      </SetGroup>

      {desktop && status && available && (
        <>
          {mac && <SetGroup title={t('cu.set.perms')} note={t('cu.set.permsNote')} testId="cu-perms">
            <SetCard>
              <SetRow
                testId="cu-perm-ax"
                className={perms?.accessibility ? '' : 'cu-row-missing'}
                name={t('cu.set.ax')}
                hint={<><Lamp tone={perms?.accessibility ? 'ok' : 'warn'} /><b className="set-state">{perms?.accessibility ? t('cu.set.granted') : t('cu.set.missing')}</b> · {t('cu.set.axHint')}</>}
                control={!perms?.accessibility ? (
                  <button type="button" className="btn btn-ghost btn-sm" data-testid="cu-ask-ax" disabled={busy !== null}
                    onClick={() => { void run('ax', requestAccessibility); }}>
                    {t('cu.set.ask')}
                  </button>
                ) : undefined}
              />
              <SetRow
                testId="cu-perm-screen"
                className={screenOk ? '' : 'cu-row-missing'}
                name={t('cu.set.screen')}
                hint={<><Lamp tone={screenOk ? 'ok' : 'warn'} /><b className="set-state">{screenOk ? t('cu.set.granted') : t('cu.set.missing')}</b> · {t('cu.set.screenHint')}</>}
                control={!screenOk ? (
                  <button type="button" className="btn btn-ghost btn-sm" data-testid="cu-open-screen" disabled={busy !== null}
                    onClick={() => { void run('screen', openScreenSettings); }}>
                    {t('cu.set.openSettings')}
                  </button>
                ) : undefined}
              />
            </SetCard>
            {(!perms?.accessibility || !screenOk) && <p className="set-msg cu-after">{t('cu.set.afterGrant')}</p>}
          </SetGroup>}

          <SetGroup title={t('cu.set.driver')} testId="cu-driver">
            <SetCard>
              <SetRow
                testId="cu-driver-row"
                name={t('cu.set.driver')}
                hint={<><Lamp tone={!enabled ? undefined : driverRunning ? 'ok' : 'warn'} /><span data-testid="cu-driver-state">{driverText}</span></>}
                control={enabled ? (
                  <button type="button" className="btn btn-ghost btn-sm" data-testid="cu-restart" disabled={busy !== null}
                    onClick={() => { void run('restart', restartComputerUse, () => setMsg({ ok: true, text: t('cu.set.restarted') })); }}>
                    {t('cu.set.restart')}
                  </button>
                ) : undefined}
              />
            </SetCard>
          </SetGroup>
        </>
      )}

      {desktop && status && (
        <SetGroup title={t('cu.set.apps')} note={t('cu.set.appsNote')} testId="cu-apps">
          <SetCard>
            {status.settings.grants.length === 0 ? (
              <SetRow name={<span className="cu-empty">{t('cu.set.appsEmpty')}</span>} testId="cu-apps-empty" />
            ) : status.settings.grants.map(g => {
              const name = stripBidi(g.app_name);
              return (
                <SetRow
                  key={g.bundle_id}
                  testId={`cu-grant-${g.bundle_id}`}
                  name={name}
                  hint={<><span className="set-key">{stripBidi(g.bundle_id)}</span> <span className="cu-when">{t('cu.set.grantedAt', { tarih: dateOf(g.granted_at) })}</span></>}
                  control={(
                    <button type="button" className="btn btn-ghost btn-sm btn-danger" disabled={busy !== null}
                      aria-label={t('cu.set.removeLabel', { ad: name })} data-testid={`cu-remove-${g.bundle_id}`}
                      onClick={() => { void run('remove', () => removeComputerGrant(g.bundle_id), () => {
                        setMsg({ ok: true, text: t('cu.set.removed', { ad: name }) });
                        saved();
                      }); }}>
                      {t('cu.set.remove')}
                    </button>
                  )}
                />
              );
            })}
          </SetCard>
        </SetGroup>
      )}

      <p className="cu-denied" data-testid="cu-denied">
        <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="6.5" /><path d="M5.4 14.6l9.2-9.2" /></svg>
        <span>{t('cu.set.denied')}</span>
      </p>
    </div>
  );
};
