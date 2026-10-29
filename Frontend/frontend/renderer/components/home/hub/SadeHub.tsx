import React from 'react';

import { useLang } from '../../../lib/i18n';
import {
  CancelButton, Elapsed, EditorsEmpty, FolderButton, FolderNote, LogTail, NameInput, NameNote, Panel, Tabs,
  TemplateChoices, Target, VersionSelect, onSubmit, phaseKey, useResult, type HubLayoutProps,
} from './parts';
import { estimate } from './useHub';

/**
 * Sade: the Hub reads like a well-made settings page. A quiet segmented control with the one
 * violet action at its end, a single-column form whose labels sit on the left, a plain table of
 * installs, and a progress row that is one hairline and a clock; the log stays folded.
 */
export const SadeHub = ({ h, recent, onGetUnity }: HubLayoutProps) => {
  const { t } = useLang();
  const result = useResult(h);
  const r = h.run;
  return (
    <div className="hub" data-hub="sade">
      <Tabs h={h} className="hub-tabs sd-seg" labels={{ projects: t('hub.tabProjects'), editors: t('hub.tabEditors'), new: t('hub.tabNew') }} />
      <Panel h={h}>
        {h.view === 'projects' && recent}

        {h.view === 'editors' && (
          <section className="sd-sheet">
            <header className="sd-sheet-head">
              <h2 className="sd-h">{t('hub.editorsHead')}</h2>
              <button type="button" className="set-link" onClick={() => void h.refreshEditors()}>{t('hub.refresh')}</button>
            </header>
            {h.editors === undefined && <p className="hub-wait" role="status">{t('hub.editorsLoading')}</p>}
            {h.editors !== undefined && !h.editors?.length && <EditorsEmpty h={h} onGetUnity={onGetUnity} />}
            {!!h.editors?.length && (
              <table className="sd-table" data-testid="hub-editors">
                <thead><tr><th>{t('hub.colVersion')}</th><th>{t('hub.colPath')}</th><th>{t('hub.colUse')}</th></tr></thead>
                <tbody>
                  {h.editors.map((e, i) => (
                    <tr key={e.version}>
                      <td className="num">{e.version}{i === 0 && <span className="sd-pill">{t('hub.editorLatest')}</span>}</td>
                      <td className="sd-path" title={e.path}>{e.path}</td>
                      <td className="num">{h.usage.get(e.version) ?? 0}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        )}

        {h.view === 'new' && r.state === 'idle' && (
          <form className="sd-sheet sd-form" onSubmit={onSubmit(h)} noValidate>
            <header className="sd-sheet-head sd-form-head">
              <h2 className="sd-h">{t('hub.newHead')}</h2>
              <p className="sd-lede">{t('hub.newLede')}</p>
            </header>
            <div className="sd-row">
              <label className="sd-lab" htmlFor="hub-name">{t('hub.fieldName')}</label>
              <div className="sd-ctl"><NameInput h={h} /><NameNote h={h} /></div>
            </div>
            <div className="sd-row">
              <span className="sd-lab">{t('hub.fieldFolder')}</span>
              <div className="sd-ctl"><FolderButton h={h} /><FolderNote h={h} /><Target h={h} /></div>
            </div>
            <div className="sd-row">
              <label className="sd-lab" htmlFor="hub-version">{t('hub.fieldVersion')}</label>
              <div className="sd-ctl"><VersionSelect h={h} /></div>
            </div>
            <div className="sd-row">
              <span className="sd-lab" id="hub-tpl-label">{t('hub.fieldTemplate')}</span>
              <div className="sd-ctl"><TemplateChoices h={h} className="hub-tpls sd-radios" /></div>
            </div>
            <footer className="sd-foot">
              <span className="hub-hint">{t('hub.createHint')}</span>
              <button type="submit" className="btn btn-primary" data-testid="hub-create">{t('hub.create')}</button>
            </footer>
          </form>
        )}

        {h.view === 'new' && r.state === 'creating' && (
          <section className="sd-sheet sd-run" data-testid="hub-progress" aria-busy="true">
            <div className="sd-run-top">
              <span className="sd-spin" aria-hidden="true" />
              <span className="sd-run-t"><b>{r.name}</b><span>{t(phaseKey(h))}</span></span>
              <Elapsed ms={r.progress.elapsedMs} />
            </div>
            <div className="sd-line" role="progressbar" aria-label={t('hub.estimate')} aria-valuemin={0} aria-valuemax={100}
              aria-valuenow={Math.round(estimate(r.progress.elapsedMs) * 100)}>
              <i style={{ width: `${estimate(r.progress.elapsedMs) * 100}%` }} />
            </div>
            <p className="hub-hint">{t('hub.estimate')}</p>
            <details className="sd-log">
              <summary>{t('hub.logShow')}</summary>
              <LogTail text={r.progress.logTail} />
            </details>
            <div className="sd-foot"><CancelButton h={h} /></div>
          </section>
        )}

        {h.view === 'new' && result && (
          <section className={`sd-sheet sd-result is-${result.kind}`} data-testid={`hub-result-${result.kind}`}>
            <span className="sd-mark" aria-hidden="true">
              <svg viewBox="0 0 20 20">{result.kind === 'done' ? <path d="M5 10.5l3.2 3L15 7" /> : <path d="M6.5 6.5l7 7M13.5 6.5l-7 7" />}</svg>
            </span>
            <h2 className="sd-h">{result.title}</h2>
            <p className="sd-result-s" role={result.kind === 'failed' ? 'alert' : undefined}>{result.text}</p>
            <div className="hub-actions">{result.actions}</div>
          </section>
        )}
      </Panel>
    </div>
  );
};
