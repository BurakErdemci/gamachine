import React, { useCallback, useEffect, useRef, useState } from 'react';

import { useLang, type TKey } from '../../../lib/i18n';
import {
  chooseAppPath, connectorsAvailable, listConnectors, openDownload, removeConnector, setAppPath, setConnectorEnabled,
  type ConnectorView, type Reply,
} from '../../../lib/connectors';
import { confirmDialog } from '../../ui/ConfirmDialog';
import { Lamp, SetCard, SetGroup, SetPageHead, SetRow, SetSwitch } from './controls';
import { ConnectorInstallFlow } from './ConnectorInstallFlow';

type Toast = (msg: string, type: 'success' | 'error' | 'warning' | 'info') => void;

const LAMP: Record<ConnectorView['state'], 'ok' | 'warn' | 'danger' | 'busy' | undefined> = {
  app_missing: 'warn', app_too_old: 'warn', not_installed: undefined, installing: 'busy',
  ready: 'ok', update_available: 'warn', error: 'danger',
};

/** The one thing to offer for a state; null when the state needs none (the switch or the badge is the answer). */
type Primary = 'download' | 'downloadSupported' | 'install' | 'update' | 'retry' | null;
function primaryFor(v: ConnectorView): Primary {
  switch (v.state) {
    case 'app_missing': return 'download';
    case 'app_too_old': return 'downloadSupported';
    case 'not_installed': return 'install';
    case 'update_available': return 'update';
    case 'error': return 'retry';
    default: return null;
  }
}

const ConnectorCard = ({ view, onChanged, onView, showToast, saved }: {
  view: ConnectorView; onChanged: () => void; onView: (v: ConnectorView) => void; showToast?: Toast; saved: () => void;
}) => {
  const { t, lang } = useLang();
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const id = view.id;
  const installed = view.state === 'ready' || view.state === 'update_available';
  const primary = primaryFor(view);
  const found = view.app.chosen;
  const stateLabel = view.state === 'ready' && view.enabled ? t('conn.state.enabled') : t(`conn.state.${view.state}` as TKey);

  const run = async (work: () => Promise<Reply<ConnectorView | { cancelled: true }>>, failed: TKey = 'conn.actionFailed') => {
    if (busy) return;
    setBusy(true);
    try {
      const r = await work();
      if (!alive.current) return;
      if (!r.ok) { showToast?.(t(failed), 'error'); return; }
      if ('id' in r.data) { onView(r.data); saved(); }
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  const onPrimary = async () => {
    if (primary === 'download' || primary === 'downloadSupported') {
      const r = await openDownload(id);
      if (!r.ok) showToast?.(t('conn.downloadFailed'), 'error');
    } else {
      // Install, update and retry (a failed install or a broken one: a repair) all start from the consent step.
      setConsent(true);
    }
  };

  const remove = async () => {
    if (!(await confirmDialog(t('conn.removeConfirm', { name: view.name }), t('conn.remove'), t('confirm.cancel')))) return;
    await run(() => removeConnector(id), 'conn.removeFailed');
    // A Remove that left files answers with an error; the reloaded card shows what is left.
    if (alive.current) onChanged();
  };

  const primaryLabel: Record<Exclude<Primary, null>, string> = {
    download: t('conn.act.download', { name: view.name }),
    downloadSupported: t('conn.act.downloadSupported'),
    install: t('conn.act.install'),
    update: t('conn.act.update'),
    retry: t('conn.act.retry'),
  };

  return (
    <SetGroup testId={`conn-card-${id}`}>
      <SetCard>
        <SetRow
          name={view.name}
          hint={view.summary[lang] ?? view.summary.en}
          control={(
            <span data-guide={id === 'blender' ? 'integration-switch-blender' : undefined}>
              <SetSwitch
                checked={view.enabled}
                label={t('conn.switch', { name: view.name })}
                disabled={!installed || busy}
                busy={busy}
                testId={`conn-switch-${id}`}
                onToggle={() => { void run(() => setConnectorEnabled(id, !view.enabled), 'conn.enableFailed'); }}
              />
            </span>
          )}
        />
        <SetRow
          testId={`conn-status-${id}`}
          name={(
            <span>
              {t('conn.status')} <Lamp tone={LAMP[view.state]} /> {stateLabel}
              {found ? ` · ${t('conn.found', { name: view.name, version: found.version })}` : ''}
            </span>
          )}
          hint={view.state === 'error' ? (view.error?.message || t('conn.failedGeneric')) : undefined}
          control={(
            <>
              {view.state === 'ready' && view.enabled && view.connected && (
                <span className="set-saved" data-testid={`conn-connected-${id}`}>{t('conn.connected')}</span>
              )}
              {primary && !consent && (
                <button type="button" className="btn btn-primary btn-sm" onClick={() => { void onPrimary(); }}
                  data-testid={`conn-primary-${id}`} data-action={primary}>
                  {primaryLabel[primary]}
                </button>
              )}
            </>
          )}
        />
        <ConnectorInstallFlow view={view} consent={consent} onConsentClose={() => setConsent(false)}
          onChanged={onChanged} showToast={showToast} />
        <details className="set-row" data-testid={`conn-details-${id}`}>
          <summary>{t('conn.details')}</summary>
          <Details view={view} busy={busy} run={run} onRepair={() => setConsent(true)} onRemove={() => { void remove(); }} />
        </details>
      </SetCard>
    </SetGroup>
  );
};

const Details = ({ view, busy, run, onRepair, onRemove }: {
  view: ConnectorView; busy: boolean;
  run: (work: () => Promise<Reply<ConnectorView | { cancelled: true }>>, failed?: TKey) => Promise<void>;
  onRepair: () => void; onRemove: () => void;
}) => {
  const { t, lang } = useLang();
  const { app } = view;
  const id = view.id;
  const caps = view.capabilities[lang] ?? view.capabilities.en;
  const canFix = view.installed !== null && view.state !== 'installing';
  return (
    <div className="set-rt">
      <p className="set-name">{t('conn.app.versions')}</p>
      {app.found.length === 0 && <p className="set-hint">{t('conn.app.none', { min: app.min_version })}</p>}
      {app.found.length === 1 && <p className="set-hint" data-testid={`conn-path-${id}`}>{app.found[0].version}{' · '}{app.found[0].path}</p>}
      {app.found.length > 1 && (
        <select aria-label={t('conn.app.pick')} value={app.chosen?.path ?? ''} disabled={busy} data-testid={`conn-picker-${id}`}
          onChange={e => { const path = e.target.value; void run(() => setAppPath(id, path), 'conn.pathFailed'); }}>
          {app.found.map(f => <option key={f.path} value={f.path}>{f.version}{' · '}{f.path}</option>)}
        </select>
      )}
      <p className="set-hint">
        <button type="button" className="set-link" disabled={busy} data-testid={`conn-choose-${id}`}
          onClick={() => { void run(() => chooseAppPath(id), 'conn.pathFailed'); }}>
          {t('conn.app.choose')}
        </button>
        {app.custom_path && (
          <>
            {' '}
            <button type="button" className="set-link" disabled={busy} data-testid={`conn-auto-${id}`}
              onClick={() => { void run(() => setAppPath(id, null), 'conn.pathFailed'); }}>
              {t('conn.app.auto')}
            </button>
          </>
        )}
      </p>
      {view.installed && (
        <>
          <p className="set-hint" data-testid={`conn-installed-${id}`}>{t('conn.installedVersion', { version: view.installed.version })}</p>
          {view.installed.addon_owner === 'user' && (
            <p className="set-hint" data-testid={`conn-addon-user-${id}`}>{t('conn.addonUser')}</p>
          )}
        </>
      )}
      {canFix && (
        <p className="set-hint">
          <button type="button" className="set-link" disabled={busy} onClick={onRepair} data-testid={`conn-repair-${id}`}>{t('conn.repair')}</button>
          {' '}
          <button type="button" className="set-link set-link-danger" disabled={busy} onClick={onRemove} data-testid={`conn-remove-${id}`}>{t('conn.remove')}</button>
        </p>
      )}
      <p className="set-name">{t('conn.caps')}</p>
      <ul className="set-hint" data-testid={`conn-caps-${id}`}>{caps.map(c => <li key={c}>{c}</li>)}</ul>
      <p className="set-hint" data-testid={`conn-risk-${id}`}>{t('conn.riskLabel')}: {view.risk[lang] ?? view.risk.en}</p>
    </div>
  );
};

/**
 * Settings > Bağlayıcılar: the external apps the agent can drive, one card each, installed on demand.
 * The install itself (plan, progress, cancel) lives in ConnectorInstallFlow.
 */
export const ConnectorsPage = ({ saved, showToast }: { saved: () => void; showToast?: Toast }) => {
  const { t } = useLang();
  const desktop = connectorsAvailable();
  const [items, setItems] = useState<ConnectorView[] | null>(null);
  const [failed, setFailed] = useState(false);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const load = useCallback(async () => {
    if (!desktop) return;
    const r = await listConnectors();
    if (!alive.current) return;
    if (!r.ok) { setFailed(true); return; }
    setFailed(false);
    setItems(r.data.connectors);
  }, [desktop]);
  useEffect(() => { void load(); }, [load]);

  const replace = useCallback((v: ConnectorView) => {
    setItems(list => (list ? list.map(x => (x.id === v.id ? v : x)) : list));
  }, []);

  return (
    <>
      <SetPageHead title={t('set.nav.baglayicilar')} lede={t('conn.lede')} />
      {!desktop && <p className="set-hint" role="status">{t('conn.noDesktop')}</p>}
      {desktop && failed && (
        <p className="set-hint" role="alert" data-testid="conn-load-failed">
          {t('conn.loadFailed')}{' '}
          <button type="button" className="set-link" onClick={() => { void load(); }}>{t('conn.act.retry')}</button>
        </p>
      )}
      {desktop && !failed && items === null && <p className="set-hint" role="status">{t('conn.loading')}</p>}
      {(items ?? []).map(v => (
        <ConnectorCard key={v.id} view={v} onChanged={() => { void load(); }} onView={replace} showToast={showToast} saved={saved} />
      ))}
      {items !== null && items.length > 0 && <p className="set-hint">{t('conn.switchNote')}</p>}
    </>
  );
};
