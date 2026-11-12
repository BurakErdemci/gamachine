import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { useLang, type TKey } from '../../../lib/i18n';
import {
  formatDate, formatPct, formatSize, installFailureText, loadInstallCatalog, openUnityHub, planInstall,
  type CatalogResult, type CliState, type InstallRelease, type PlanResult,
} from '../../../lib/hub';
import type { Hub } from './useHub';

/** The theme's own words for the flow; everything else is shared plain language. */
export interface InstallWords {
  /** Heading of the flow. */
  title: TKey;
  /** The button that starts the install. */
  start: TKey;
  /** Heading once a new version is installed ({version}). */
  done: TKey;
}

/** Where Unity keeps every release, for a version its release API does not list. */
const UNITY_ARCHIVE_URL = 'https://unity.com/releases/editor/archive';

interface Props {
  h: Hub;
  words: InstallWords;
  /** Opens Unity's download page: the way out when there is no Unity Hub at all. */
  onGetUnity: () => void;
  className?: string;
}

/**
 * Installing a Unity editor (or adding modules to one) in four steps, one decision each: the
 * version, its modules, the licences (only when the selection has any), then the install itself.
 * The install lives in the hub.ts store, so this screen can unmount mid-install and come back.
 */
export const InstallFlow = ({ h, words, onGetUnity, className = 'hub-inst' }: Props) => (
  <div className={className} data-testid="hub-install">
    {h.install.state === 'idle' ? <Picker h={h} words={words} onGetUnity={onGetUnity} /> : <RunView h={h} words={words} />}
  </div>
);

type Step = 'version' | 'modules' | 'eulas';
const STEPS: { step: Step | 'run'; key: TKey }[] = [
  { step: 'version', key: 'hub.install.stepVersion' }, { step: 'modules', key: 'hub.install.stepModules' },
  { step: 'eulas', key: 'hub.install.stepEulas' }, { step: 'run', key: 'hub.install.stepRun' },
];

const Steps = ({ at }: { at: Step | 'run' }) => {
  const { t } = useLang();
  const i = STEPS.findIndex(s => s.step === at);
  return (
    <ol className="hub-inst-steps" aria-label={t('hub.install.stepsAria')}>
      {STEPS.map((s, k) => (
        <li key={s.step} className={k === i ? 'is-on' : k < i ? 'is-past' : undefined} aria-current={k === i ? 'step' : undefined}>
          <span className="num">{k + 1}</span>{t(s.key)}
        </li>
      ))}
    </ol>
  );
};

/** Module ids the user already has in another installed editor; empty when that is unknown. */
const ownedModules = (releases: InstallRelease[], except: string) =>
  new Set(releases.filter(r => r.installed && r.version !== except).flatMap(r => r.modules.filter(m => m.installed).map(m => m.id)));

const Picker = ({ h, words, onGetUnity }: Omit<Props, 'className'>) => {
  const { t, lang } = useLang();
  // undefined = loading, null = no answer of the expected shape
  const [catalog, setCatalog] = useState<CatalogResult | null | undefined>(undefined);
  const [step, setStep] = useState<Step>('version');
  const [version, setVersion] = useState('');
  const [picked, setPicked] = useState<Set<string>>(new Set());
  // undefined = being worked out for the current selection, null = no usable answer
  const [plan, setPlan] = useState<PlanResult | null | undefined>(undefined);
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const [hubOpened, setHubOpened] = useState<boolean | undefined>(undefined);
  // The version a project card asked for was not in Unity's answer.
  const [wantMissing, setWantMissing] = useState('');
  // Set once the card's version was consumed: the rerun that clearing it causes must not reload.
  const wantConsumed = useRef(false);
  // The versions the user's projects use and that are not installed (main looks up installed ones anyway).
  // Read at load time, not a load dependency: projects arriving later must not reload a flow the user is in.
  const neededRef = useRef<string[]>([]);
  neededRef.current = [...h.neededBy.keys()].filter(v => !h.editors?.some(e => e.version === v));
  const alive = useRef(true);
  const planSeq = useRef(0);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const releases = catalog?.ok ? catalog.releases : [];
  const release = releases.find(r => r.version === version);
  const owned = useMemo(() => ownedModules(releases, version), [releases, version]);

  const choose = useCallback((r: InstallRelease, all: InstallRelease[]) => {
    const had = ownedModules(all, r.version);
    setVersion(r.version);
    setPicked(new Set(r.modules.filter(m => !m.installed && (had.size ? had.has(m.id) : m.preSelected)).map(m => m.id)));
    setStep('modules');
  }, []);

  const load = useCallback(async () => {
    setCatalog(undefined);
    setWantMissing('');
    const want = h.installWant;
    const res = await loadInstallCatalog(want || undefined, neededRef.current);
    if (!alive.current) return;
    setCatalog(res);
    // A project's missing editor is preselected as a new install; one Unity does not list gets a plain message.
    // Either way the request is used up, so a remount does not apply it over the user's own pick.
    if (want && res?.ok) {
      const wanted = res.releases.find(r => r.version === want);
      if (!wanted) setWantMissing(want);
      wantConsumed.current = true;
      h.consumeInstallWant();
      if (wanted && !wanted.installed) { choose(wanted, res.releases); return; }
    }
    // "Add modules" on an editor row skips the version step.
    const target = h.installFor && res?.ok ? res.releases.find(r => r.version === h.installFor) : undefined;
    if (target && res?.ok) choose(target, res.releases);
  }, [h.installFor, h.installWant, h.consumeInstallWant, choose]);
  useEffect(() => {
    if (!h.installWant && wantConsumed.current) { wantConsumed.current = false; return; }
    void load();
  }, [load, h.installWant]);

  // Every change of the selection asks main again; only the answer for the latest one is kept.
  const pickedKey = [...picked].sort().join(',');
  const planning = step !== 'version';
  useEffect(() => {
    if (!planning || !version) return;
    const seq = ++planSeq.current;
    setPlan(undefined);
    void planInstall({ version, modules: pickedKey ? pickedKey.split(',') : [] }).then(res => {
      if (alive.current && seq === planSeq.current) setPlan(res);
    });
  }, [planning, version, pickedKey]);

  const start = (acceptedEulas: boolean) => {
    if (!release) return;
    const names: Record<string, string> = {};
    for (const m of release.modules) { names[m.id] = m.name; for (const p of m.parts) names[p.id] = p.name; }
    h.startInstall({ req: { version: release.version, modules: [...picked], acceptedEulas }, addTo: release.installed, names });
  };

  const size = (b: number) => formatSize(b, lang);
  const close = <button type="button" className="set-link hub-inst-close" onClick={h.closeInstall} data-testid="hub-install-close">{t('hub.install.close')}</button>;
  const head = (at: Step) => (
    <header className="hub-inst-head">
      <h3 className="hub-inst-h">{t(words.title)}</h3>
      {close}
      <Steps at={at} />
    </header>
  );

  if (catalog === undefined) return <>{head('version')}<p className="hub-wait" role="status">{t('hub.install.catalogLoading')}</p></>;

  // The install tool comes with Unity Hub; without it nothing below can work.
  if (catalog && !catalog.cli.available) {
    const { reason } = catalog.cli as Extract<CliState, { available: false }>;
    const noHub = reason === 'noHub';
    return (
      <>
        {head('version')}
        <div className="hub-inst-cli" role="status" data-testid={`hub-install-cli-${reason}`}>
          <b className="hub-inst-cli-t">{t(noHub ? 'hub.install.noHubTitle' : 'hub.install.openHubTitle')}</b>
          <p>{t(noHub ? 'hub.install.noHub' : 'hub.install.openHubOnce')}</p>
          {hubOpened === false && <p className="hub-note is-bad">{t('hub.install.openHubFailed')}</p>}
          <div className="hub-actions">
            {noHub
              ? <button type="button" className="btn btn-primary" onClick={onGetUnity}>{t('hub.getUnity')}</button>
              : <button type="button" className="btn btn-primary" onClick={() => void openUnityHub().then(ok => alive.current && setHubOpened(ok))}
                  data-testid="hub-install-open-hub">{t('hub.install.openHub')}</button>}
            <button type="button" className="btn btn-ghost" onClick={() => void load()} data-testid="hub-install-recheck">{t('hub.install.recheck')}</button>
          </div>
        </div>
      </>
    );
  }

  if (!catalog || !catalog.ok) {
    const code = catalog ? (catalog as Extract<CatalogResult, { ok: false }>).code : 'catalogFailed';
    return (
      <>
        {head('version')}
        <div className="hub-wait" role="alert" data-testid={`hub-install-${code}`}>
          <span>{t(code === 'offline' ? 'hub.install.offline' : 'hub.install.catalogFailed')}</span>{' '}
          <button type="button" className="set-link" onClick={() => void load()} data-testid="hub-install-retry-catalog">{t('hub.retry')}</button>
        </div>
      </>
    );
  }

  if (step === 'version') {
    const rec = releases.find(r => r.recommended) ?? releases.find(r => r.stream === 'LTS') ?? releases[0];
    // The versions the user's projects need come first and in the open, whatever their age or stream.
    const forProjects = releases.filter(r => r !== rec && !r.installed && h.neededBy.has(r.version));
    const rest = releases.filter(r => r !== rec && !forProjects.includes(r));
    const usedBy = (v: string) => {
      const names = h.neededBy.get(v);
      if (!names?.length) return null;
      const shown = names.length > 2 ? `${names.slice(0, 2).join(', ')} +${names.length - 2}` : names.join(', ');
      return <span className="hub-inst-used" data-testid={`hub-install-used-${v}`}>{t('hub.install.usedBy', { names: shown })}</span>;
    };
    const pickBtn = (r: InstallRelease, cls: string) => (r.installed
      ? <span className="hub-inst-pill is-installed">{t('hub.install.installed')}</span>
      : <button type="button" className={cls} onClick={() => choose(r, releases)} data-testid={`hub-install-pick-${r.version}`}>{t('hub.install.pick')}</button>);
    return (
      <>
        {head('version')}
        {wantMissing && (
          <div className="hub-wait" role="status" data-testid="hub-install-want-missing">
            <span>{t('hub.install.versionMissing', { version: wantMissing })}</span>{' '}
            <button type="button" className="set-link" onClick={() => window.open(UNITY_ARCHIVE_URL, '_blank')}
              data-testid="hub-install-archive">{t('hub.install.openArchive')}</button>
          </div>
        )}
        {!rec && !wantMissing && <p className="hub-wait" role="status">{t('hub.install.noReleases')}</p>}
        {rec && (
          <div className={`hub-inst-card${rec.installed ? ' is-installed' : ''}`} data-testid="hub-install-recommended">
            <span className="hub-inst-tag">{t('hub.install.recommended')}</span>
            <b className="hub-inst-ver num">{rec.version}</b>
            <span className="hub-inst-meta">{t(rec.stream === 'LTS' ? 'hub.install.streamLts' : 'hub.install.streamSupported')} · {formatDate(rec.releaseDate, lang)}</span>
            <span className="hub-inst-size num">{t('hub.install.sizes', { download: size(rec.downloadBytes), disk: size(rec.installedBytes) })}</span>
            {usedBy(rec.version)}
            {pickBtn(rec, 'btn btn-primary hub-inst-go')}
          </div>
        )}
        {!!forProjects.length && (
          <section className="hub-inst-group hub-inst-projects" data-testid="hub-install-for-projects">
            <h4 className="hub-inst-group-h">{t('hub.install.forProjects')}</h4>
            <ul className="hub-inst-rows">
              {forProjects.map(r => (
                <li key={r.version} className="hub-inst-row" data-testid={`hub-install-ver-${r.version}`}>
                  <b className="num">{r.version}</b>
                  {usedBy(r.version)}
                  <span className="hub-inst-size num">{size(r.downloadBytes)}</span>
                  {pickBtn(r, 'set-link')}
                </li>
              ))}
            </ul>
          </section>
        )}
        {!!rest.length && (
          <details className="hub-inst-more" data-testid="hub-install-more">
            <summary>{t('hub.install.otherVersions')}</summary>
            {(['LTS', 'SUPPORTED'] as const).map(stream => {
              const list = rest.filter(r => r.stream === stream);
              if (!list.length) return null;
              return (
                <section key={stream} className="hub-inst-group">
                  <h4 className="hub-inst-group-h">{t(stream === 'LTS' ? 'hub.install.streamLts' : 'hub.install.streamSupported')}</h4>
                  <p className="hub-hint">{t(stream === 'LTS' ? 'hub.install.streamLtsNote' : 'hub.install.streamSupportedNote')}</p>
                  <ul className="hub-inst-rows">
                    {list.map(r => (
                      <li key={r.version} className={`hub-inst-row${r.installed ? ' is-installed' : ''}`} data-testid={`hub-install-ver-${r.version}`}>
                        <b className="num">{r.version}</b>
                        {usedBy(r.version) ?? <span className="hub-inst-meta">{formatDate(r.releaseDate, lang)}</span>}
                        <span className="hub-inst-size num">{size(r.downloadBytes)}</span>
                        {pickBtn(r, 'set-link')}
                      </li>
                    ))}
                  </ul>
                </section>
              );
            })}
          </details>
        )}
      </>
    );
  }

  if (!release) {
    return <>{head('modules')}<p className="hub-wait" role="alert" data-testid="hub-install-unknown">{t('hub.install.notInCatalog', { version })}</p></>;
  }

  // The note names Windows' permission prompt; other platforms never show it.
  const onWindows = typeof navigator !== 'undefined' && /Win/i.test(navigator.platform || navigator.userAgent || '');
  const adminNote = onWindows ? <p className="hub-hint hub-inst-admin">{t('hub.install.adminNote')}</p> : null;

  if (step === 'eulas' && plan?.ok) {
    const all = plan.eulas.every(e => ticked.has(e.url));
    return (
      <>
        {head('eulas')}
        <p className="hub-inst-lede">{t('hub.install.eulaLede')}</p>
        <ul className="hub-inst-eulas" data-testid="hub-install-eulas">
          {plan.eulas.map(e => (
            <li key={e.url} className="hub-inst-eula">
              <span className="hub-inst-eula-t">{e.label}</span>
              <button type="button" className="set-link" onClick={() => window.open(e.url, '_blank')}>{t('hub.install.openText')}</button>
              <label className="hub-inst-check">
                <input type="checkbox" checked={ticked.has(e.url)} data-testid="hub-install-eula-check"
                  onChange={ev => setTicked(prev => { const n = new Set(prev); if (ev.target.checked) n.add(e.url); else n.delete(e.url); return n; })} />
                <span>{t('hub.install.accept')}</span>
              </label>
            </li>
          ))}
        </ul>
        {adminNote}
        <footer className="hub-inst-foot">
          <button type="button" className="btn btn-ghost" onClick={() => setStep('modules')}>{t('hub.install.backStep')}</button>
          <button type="button" className="btn btn-primary hub-inst-go" disabled={!all} onClick={() => start(true)} data-testid="hub-install-start">{t(words.start)}</button>
        </footer>
      </>
    );
  }

  const toggle = (id: string) => setPicked(prev => { const n = new Set(prev); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const nothingToAdd = release.installed && !picked.size;
  const ready = !!plan?.ok && plan.enough && !nothingToAdd;
  const needsEulas = !!plan?.ok && plan.eulas.length > 0;
  return (
    <>
      {head('modules')}
      <h4 className="hub-inst-sub">
        {t(release.installed ? 'hub.install.modulesForInstalled' : 'hub.install.modulesFor', { version: release.version })}
      </h4>
      <p className="hub-inst-lede">{t('hub.install.modulesLede')}</p>
      <ul className="hub-inst-mods" data-testid="hub-install-modules">
        {release.modules.map(m => (
          <li key={m.id} className={`hub-inst-mod${m.installed ? ' is-installed' : ''}`} data-testid={`hub-install-mod-${m.id}`}>
            <label className="hub-inst-check">
              <input type="checkbox" checked={picked.has(m.id)} onChange={() => toggle(m.id)} />
              <span className="hub-inst-mod-n">{m.name}</span>
              {m.installed && <span className="hub-inst-pill is-installed">{t('hub.install.installed')}</span>}
              {!m.installed && owned.has(m.id) && <span className="hub-inst-pill">{t('hub.install.hadBefore')}</span>}
              <span className="hub-inst-size num">{size(m.downloadBytes)}</span>
            </label>
            {!!m.parts.length && (
              <span className="hub-inst-parts">{t('hub.install.parts', { names: m.parts.map(p => p.name).join(', '), size: size(m.downloadBytes) })}</span>
            )}
          </li>
        ))}
      </ul>
      <p className={`hub-inst-total num${plan?.ok && !plan.enough ? ' is-short' : ''}`} data-testid="hub-install-total" aria-live="polite">
        {plan === undefined && t('hub.install.planning')}
        {plan === null && t('hub.install.planFailed')}
        {plan && !plan.ok && installFailureText(plan as Extract<PlanResult, { ok: false }>, t)}
        {plan?.ok && t(plan.freeBytes === null ? 'hub.install.totalNoFree' : 'hub.install.total', {
          download: size(plan.downloadBytes), disk: size(plan.installedBytes), free: size(plan.freeBytes ?? 0),
        })}
        {plan?.ok && !plan.enough && <> {t('hub.install.notEnough')}</>}
      </p>
      {nothingToAdd && <p className="hub-hint">{t('hub.install.pickSomething')}</p>}
      {!needsEulas && adminNote}
      <footer className="hub-inst-foot">
        {!h.installFor && <button type="button" className="btn btn-ghost" onClick={() => setStep('version')}>{t('hub.install.backStep')}</button>}
        <button type="button" className="btn btn-primary hub-inst-go" disabled={!ready} data-testid="hub-install-next"
          onClick={() => { if (needsEulas) { setTicked(new Set()); setStep('eulas'); } else start(false); }}>
          {needsEulas ? t('hub.install.next') : t(words.start)}
        </button>
      </footer>
    </>
  );
};

const RunView = ({ h, words }: { h: Hub; words: InstallWords }) => {
  const { t, lang } = useLang();
  const r = h.install;
  if (r.state === 'idle') return null;
  const { version } = r.target.req;
  const head = (title: string) => (
    <header className="hub-inst-head">
      <h3 className="hub-inst-h">{title}</h3>
      <Steps at="run" />
    </header>
  );

  if (r.state === 'installing') {
    const p = r.progress;
    // The CLI cannot be stopped once it is installing (killing it there does not stop the install).
    const finishing = p.phase === 'install' || r.cancelRefused;
    const pct = p.pct === null ? null : Math.min(100, Math.max(0, p.pct));
    return (
      <section className="hub-inst-run" data-testid="hub-install-progress" aria-busy="true">
        {head(t(r.target.addTo ? 'hub.install.addingTitle' : 'hub.install.installingTitle', { version }))}
        <div className="hub-inst-bar" role="progressbar" aria-label={t('hub.install.barAria')} aria-valuemin={0} aria-valuemax={100}
          aria-valuenow={pct ?? undefined}>
          <i style={{ width: `${pct ?? 0}%` }} />
        </div>
        <p className="hub-inst-msg">
          {pct !== null && <b className="num">{formatPct(pct, lang)}</b>}{' '}
          <span data-testid="hub-install-message">{p.message || t('hub.install.starting')}</span>
        </p>
        {p.stalled && <p className="hub-inst-state" role="status" data-testid="hub-install-stalled">{t('hub.install.stalled')}</p>}
        {p.retrying && <p className="hub-inst-state" role="status" data-testid="hub-install-retrying">{t('hub.install.retrying')}</p>}
        {finishing && <p className="hub-inst-state" role="status" data-testid="hub-install-finishing">{t('hub.install.finishing')}</p>}
        {r.cancelIdle && <p className="hub-inst-state" role="status" data-testid="hub-install-cancel-idle">{t('hub.install.cancelIdle')}</p>}
        <p className="hub-hint">{t('hub.install.keepsGoing')}</p>
        <footer className="hub-inst-foot">
          <button type="button" className="btn btn-ghost hub-cancel" onClick={h.cancelInstall} disabled={finishing || r.cancelling}
            data-testid="hub-install-cancel">{t(r.cancelling ? 'hub.cancelling' : 'hub.cancel')}</button>
        </footer>
      </section>
    );
  }

  if (r.state === 'done') {
    return (
      <section className="hub-inst-run is-done" data-testid="hub-install-done">
        {head(t(r.target.addTo ? 'hub.install.doneModules' : words.done, { version }))}
        <p>{t('hub.install.doneText')}</p>
        <div className="hub-actions">
          <button type="button" className="btn btn-primary hub-inst-go" onClick={() => h.newWithVersion(version)} data-testid="hub-install-create">
            {t('hub.install.createWith')}
          </button>
          <button type="button" className="btn btn-ghost" onClick={h.closeInstall} data-testid="hub-install-close">{t('hub.install.close')}</button>
        </div>
      </section>
    );
  }

  const names = r.completed.map(id => r.target.names[id] ?? id);
  return (
    <section className={`hub-inst-run is-${r.state}`} data-testid={`hub-install-${r.state}`}>
      {head(t(r.state === 'cancelled' ? 'hub.install.cancelledTitle' : 'hub.install.failedTitle', { version }))}
      <p className="hub-inst-reason" role={r.state === 'failed' ? 'alert' : undefined} data-testid="hub-install-reason">{installFailureText(r, t)}</p>
      {!!names.length && <p data-testid="hub-install-completed">{t('hub.install.partsDone', { names: names.join(', ') })}</p>}
      <div className="hub-actions">
        <button type="button" className="btn btn-primary hub-inst-go" onClick={() => h.startInstall(r.target)} data-testid="hub-install-retry">{t('hub.retry')}</button>
        <button type="button" className="btn btn-ghost" onClick={h.closeInstall} data-testid="hub-install-close">{t('hub.install.close')}</button>
      </div>
    </section>
  );
};
