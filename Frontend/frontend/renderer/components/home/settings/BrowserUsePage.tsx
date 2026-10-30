import React, { useCallback, useEffect, useRef, useState } from 'react';

import { useLang } from '../../../lib/i18n';
import { LIVE_FEATURES_EVENT } from '../../../lib/liveFeatures';
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

const PAGE_ROWS = 100;
const SEARCH_DEBOUNCE_MS = 200;

interface SiteRow { site: string; cookies: number }
interface HistoryRow { url: string; title: string; lastVisit: number }

const isError = (v: unknown) => !!v && typeof v === 'object' && typeof (v as { error?: unknown }).error === 'string';

/** The sites that left cookies in the agent browser, one "Kaldır" per site (login and site data go together). */
const SitesCard = ({ showToast, setMsg }: { showToast?: Toast; setMsg: (m: string | null) => void }) => {
  const { t } = useLang();
  const [sites, setSites] = useState<SiteRow[] | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const load = useCallback(async () => {
    const r = await call({ op: 'sites' });
    if (!alive.current) return;
    if (isError(r) || !Array.isArray(r?.sites)) { setSites([]); setMsg(t('bp.sites.loadFailed')); return; }
    setSites(r.sites);
  }, [setMsg, t]);
  useEffect(() => { void load(); }, [load]);

  const remove = async (site: string) => {
    if (removing || !(await confirmDialog(t('bp.sites.removeConfirm', { site }), t('bp.sites.remove'), t('confirm.cancel')))) return;
    setRemoving(site);
    setMsg(null);
    try {
      const r = await call({ op: 'site-remove', site });
      if (isError(r)) {
        setMsg(t('bp.sites.removeFailed'));
        showToast?.(t('bp.sites.removeFailed'), 'error');
      } else {
        showToast?.(t('bp.sites.removed'), 'success');
      }
      await load();
    } finally {
      if (alive.current) setRemoving(null);
    }
  };

  return (
    <SetGroup title={t('bp.sites.title')} testId="bp-sites">
      <SetCard>
        {sites !== null && sites.length === 0 && <SetRow testId="bp-sites-empty" name={t('bp.sites.empty')} />}
        {(sites ?? []).map((row) => (
          <SetRow
            key={row.site}
            testId={`bp-site-${row.site}`}
            name={row.site}
            hint={t('bp.sites.cookies', { n: row.cookies })}
            control={(
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => { void remove(row.site); }}
                disabled={removing !== null} data-testid={`bp-site-remove-${row.site}`}>
                {t('bp.sites.remove')}
              </button>
            )}
          />
        ))}
      </SetCard>
      <p className="set-hint">{t('bp.sites.hint')}</p>
    </SetGroup>
  );
};

type ClearRange = 'hour' | 'day' | 'all';

/** The browsing history: search, delete one page, or delete the last hour, today or everything. */
const HistoryCard = ({ showToast, setMsg }: { showToast?: Toast; setMsg: (m: string | null) => void }) => {
  const { t } = useLang();
  const [entries, setEntries] = useState<HistoryRow[] | null>(null);
  const [input, setInput] = useState('');
  const [query, setQuery] = useState('');
  const [shown, setShown] = useState(PAGE_ROWS);
  const [busy, setBusy] = useState(false);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  useEffect(() => {
    const timer = setTimeout(() => { setQuery(input.trim()); setShown(PAGE_ROWS); }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [input]);

  const load = useCallback(async () => {
    const r = await call({ op: 'history', query });
    if (!alive.current) return;
    if (isError(r) || !Array.isArray(r?.entries)) { setEntries([]); setMsg(t('bp.hist.loadFailed')); return; }
    setEntries(r.entries);
  }, [query, setMsg, t]);
  useEffect(() => { void load(); }, [load]);

  const act = async (req: Record<string, unknown>, failed: string, done?: string) => {
    if (busy) return;
    setBusy(true);
    setMsg(null);
    try {
      const r = await call(req);
      if (isError(r)) {
        setMsg(t(failed as 'bp.hist.removeFailed'));
        showToast?.(t(failed as 'bp.hist.removeFailed'), 'error');
      } else if (done) {
        showToast?.(t(done as 'bp.hist.cleared'), 'success');
      }
      await load();
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  const clear = async (range: ClearRange) => {
    if (busy || !(await confirmDialog(t(`bp.hist.${range}Confirm`), t(`bp.hist.${range}`), t('confirm.cancel')))) return;
    await act({ op: 'history-clear', range }, 'bp.hist.clearFailed', 'bp.hist.cleared');
  };

  const rows = (entries ?? []).slice(0, shown);
  return (
    <SetGroup title={t('bp.hist.title')} testId="bp-history">
      <SetCard>
        <SetRow
          name={<input type="search" className="set-input" value={input} onChange={(e) => setInput(e.target.value)}
            placeholder={t('bp.hist.search')} aria-label={t('bp.hist.search')} maxLength={200} data-testid="bp-hist-search" />}
          control={(
            <span className="set-actions">
              {(['hour', 'day', 'all'] as const).map((range) => (
                <button key={range} type="button" className="btn btn-ghost btn-sm" onClick={() => { void clear(range); }}
                  disabled={busy} data-testid={`bp-hist-clear-${range}`}>
                  {t(`bp.hist.${range}`)}
                </button>
              ))}
            </span>
          )}
        />
        {entries !== null && entries.length === 0 && <SetRow testId="bp-hist-empty" name={query ? t('bp.hist.noMatch') : t('bp.hist.empty')} />}
        {rows.map((row) => (
          <SetRow
            key={row.url}
            testId="bp-hist-row"
            name={row.title || row.url}
            hint={<>{row.url} · {new Date(row.lastVisit).toLocaleString()}</>}
            control={(
              <button type="button" className="btn btn-ghost btn-sm" disabled={busy} data-testid="bp-hist-remove"
                onClick={() => { void act({ op: 'history-remove', url: row.url }, 'bp.hist.removeFailed'); }}>
                {t('bp.hist.remove')}
              </button>
            )}
          />
        ))}
        {entries !== null && entries.length > shown && (
          <SetRow name="" control={(
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setShown((n) => n + PAGE_ROWS)} data-testid="bp-hist-more">
              {t('bp.hist.more')}
            </button>
          )} />
        )}
      </SetCard>
      <p className="set-hint">{t('bp.hist.hint')}</p>
    </SetGroup>
  );
};

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
        window.dispatchEvent(new CustomEvent(LIVE_FEATURES_EVENT));
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
      {desktop && <SitesCard showToast={showToast} setMsg={setMsg} />}
      {desktop && <HistoryCard showToast={showToast} setMsg={setMsg} />}
    </>
  );
};
