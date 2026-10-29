import React from 'react';

import { useLang } from '../../../lib/i18n';
import { templateName } from '../../../lib/hub';
import {
  CancelButton, Elapsed, EditorsEmpty, FolderButton, FolderNote, LogTail, NameInput, NameNote, Panel, Tabs,
  TemplateChoices, Target, VersionSelect, onSubmit, phaseKey, useResult, type HubLayoutProps,
} from './parts';

/** The ruler spans two minutes: the measured 85-105 s fall near its right end. */
const RULER_MS = 120_000;
const TICKS = Array.from({ length: 25 }, (_, i) => i * 5);

/**
 * Pafta: the Hub is a drawing sheet. Tabs are the sheet index (A, B, C), installs are a bill of
 * materials, and a new project is filled into the sheet's TITLE BLOCK in the lower right while the
 * template choices are drawn as numbered views. Creating runs a vermilion cursor along a scale
 * ruler; the outcome is stamped.
 */
export const PaftaHub = ({ h, recent, onGetUnity }: HubLayoutProps) => {
  const { t, lang } = useLang();
  const result = useResult(h);
  const r = h.run;
  const sheetNo = `GM-${String(h.recentCount + 1).padStart(3, '0')}`;
  const today = new Intl.DateTimeFormat(lang, { day: '2-digit', month: '2-digit', year: 'numeric' }).format(new Date());
  return (
    <div className="hub" data-hub="pafta">
      <Tabs h={h} className="hub-tabs pf-index" labels={{ projects: t('hub.tabProjectsNote'), editors: t('hub.tabEditorsNote'), new: t('hub.tabNewNote') }} />
      <Panel h={h}>
        {h.view === 'projects' && recent}

        {h.view === 'editors' && (
          <section className="pf-sheet pf-bom-sheet">
            {h.editors === undefined && <p className="hub-wait" role="status">{t('hub.editorsLoading')}</p>}
            {h.editors !== undefined && !h.editors?.length && <EditorsEmpty h={h} onGetUnity={onGetUnity} />}
            {!!h.editors?.length && (
              <table className="pf-bom" data-testid="hub-editors">
                <caption>{t('hub.editorsHeadNote')}</caption>
                <thead><tr><th>{t('hub.colItem')}</th><th>{t('hub.colVersion')}</th><th>{t('hub.colPath')}</th><th>{t('hub.colUse')}</th></tr></thead>
                <tbody>
                  {h.editors.map((e, i) => (
                    <tr key={e.version}>
                      <td className="num">{String(i + 1).padStart(2, '0')}</td>
                      <td className="num pf-ver">{e.version}</td>
                      <td className="pf-path" title={e.path}>{e.path}</td>
                      <td className="num">{h.usage.get(e.version) ?? 0}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <button type="button" className="set-link pf-refresh" onClick={() => void h.refreshEditors()}>{t('hub.refresh')}</button>
          </section>
        )}

        {h.view === 'new' && r.state === 'idle' && (
          <form className="pf-sheet pf-draw" onSubmit={onSubmit(h)} noValidate>
            <Zones />
            <section className="pf-views">
              <h2 className="pf-cap" id="hub-tpl-label">{t('hub.stepTemplateNote')}</h2>
              <TemplateChoices h={h} className="hub-tpls pf-figs" show="short" />
            </section>
            <div className="pf-tb" role="group" aria-label={t('hub.titleBlock')}>
              <div className="pf-cell pf-c-name">
                <label className="pf-k" htmlFor="hub-name">{t('hub.fieldName')}</label>
                <NameInput h={h} className="hub-input pf-in" />
              </div>
              <div className="pf-cell pf-c-no"><span className="pf-k">{t('hub.sheetNo')}</span><b className="num">{sheetNo}</b></div>
              <div className="pf-cell pf-c-date"><span className="pf-k">{t('hub.date')}</span><span className="num">{today}</span></div>
              <div className="pf-cell pf-c-note"><NameNote h={h} /><FolderNote h={h} /></div>
              <div className="pf-cell pf-c-folder"><span className="pf-k">{t('hub.fieldFolder')}</span><FolderButton h={h} className="hub-folder pf-folder" /></div>
              <div className="pf-cell pf-c-ver"><label className="pf-k" htmlFor="hub-version">{t('hub.fieldVersion')}</label><VersionSelect h={h} className="hub-select pf-in" /></div>
              <div className="pf-cell pf-c-tpl"><span className="pf-k">{t('hub.fieldTemplate')}</span><b>{h.template ? templateName(h.template) : '—'}</b></div>
              <div className="pf-cell pf-c-target"><Target h={h} /><span className="hub-hint">{t('hub.createHint')}</span></div>
              <button type="submit" className="pf-cell pf-c-go" data-testid="hub-create"><span className="pf-stampbtn">{t('hub.createNote')}</span></button>
            </div>
          </form>
        )}

        {h.view === 'new' && r.state === 'creating' && (
          <section className="pf-sheet pf-run" data-testid="hub-progress" aria-busy="true">
            <Zones />
            <header className="pf-run-head">
              <span className="pf-k">{t(phaseKey(h))}</span>
              <h2 className="pf-run-name">{r.name}</h2>
              <span className="pf-t">t = <Elapsed ms={r.progress.elapsedMs} className="hub-clock num" /></span>
            </header>
            <div className="pf-ruler" role="progressbar" aria-label={t('hub.estimate')} aria-valuemin={0} aria-valuemax={120}
              aria-valuenow={Math.min(120, Math.round(r.progress.elapsedMs / 1000))}>
              {TICKS.map(s => <i key={s} className={s % 20 === 0 ? 'is-major' : ''} style={{ left: `${(s / 120) * 100}%` }}>{s % 20 === 0 && <span className="num">{s}</span>}</i>)}
              <span className="pf-band" aria-hidden="true" />
              <b className="pf-cursor" style={{ left: `${Math.min(1, r.progress.elapsedMs / RULER_MS) * 100}%` }} />
            </div>
            <p className="pf-legend">{t('hub.rulerLegend')}</p>
            <div className="pf-notes">
              <span className="pf-k">{t('hub.notes')}</span>
              <LogTail text={r.progress.logTail} className="hub-log pf-log" />
            </div>
            <CancelButton h={h} className="pf-cancel" />
          </section>
        )}

        {h.view === 'new' && result && (
          <section className={`pf-sheet pf-result is-${result.kind}`} data-testid={`hub-result-${result.kind}`}>
            <Zones />
            <span className="pf-stamp" aria-hidden="true">{t(result.kind === 'done' ? 'hub.stampDone' : result.kind === 'failed' ? 'hub.stampFailed' : 'hub.stampCancelled')}</span>
            <h2 className="pf-run-name">{result.title}</h2>
            <p className="pf-result-s" role={result.kind === 'failed' ? 'alert' : undefined}>{result.text}</p>
            <div className="hub-actions">{result.actions}</div>
          </section>
        )}
      </Panel>
    </div>
  );
};

/** The zone letters and numbers printed along a drawing frame's border. */
const Zones = () => (
  <span className="pf-zones" aria-hidden="true">
    {['1', '2', '3', '4'].map(n => <i key={`t${n}`} className="pf-z pf-z-t" style={{ left: `${(+n - 0.5) * 25}%` }}>{n}</i>)}
    {['A', 'B', 'C'].map((n, k) => <i key={`l${n}`} className="pf-z pf-z-l" style={{ top: `${(k + 0.5) * (100 / 3)}%` }}>{n}</i>)}
  </span>
);
