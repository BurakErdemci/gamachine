import React from 'react';

import { useLang } from '../../../lib/i18n';
import {
  CancelButton, Elapsed, EditorsEmpty, FolderButton, FolderNote, LogTail, NameInput, NameNote, Panel, Tabs,
  TemplateChoices, Target, VersionSelect, onSubmit, phaseKey, useResult, type HubLayoutProps,
} from './parts';
import { estimate } from './useHub';

/** The gauge sweeps 240 degrees, from -120 (rest) to +120. */
const SWEEP = 240;
const DIAL_TICKS = Array.from({ length: 13 }, (_, i) => -120 + i * 20);

/**
 * Atölye: the Hub is the workshop. Tabs are drawer pulls, the installed editors hang as tags on
 * the pegboard, a new project is a paper WORK ORDER with a torn stub and ruled lines, the template
 * choices are brass tokens, and creating shows a brass gauge beside a receipt strip of the log.
 */
export const AtolyeHub = ({ h, recent, onGetUnity }: HubLayoutProps) => {
  const { t, lang } = useLang();
  const result = useResult(h);
  const r = h.run;
  const orderNo = String(h.recentCount + 1).padStart(3, '0');
  const today = new Intl.DateTimeFormat(lang, { day: 'numeric', month: 'long' }).format(new Date());
  return (
    <div className="hub" data-hub="atolye">
      <Tabs h={h} className="hub-tabs at-drawers" labels={{ projects: t('hub.tabProjectsShop'), editors: t('hub.tabEditorsShop'), new: t('hub.tabNewShop') }} />
      <Panel h={h}>
        {h.view === 'projects' && recent}

        {h.view === 'editors' && (
          <section className="at-board">
            <h2 className="at-h">{t('hub.editorsHeadShop')}</h2>
            {h.editors === undefined && <p className="hub-wait" role="status">{t('hub.editorsLoading')}</p>}
            {h.editors !== undefined && !h.editors?.length && <EditorsEmpty h={h} onGetUnity={onGetUnity} />}
            {!!h.editors?.length && (
              <ul className="at-peg" data-testid="hub-editors">
                {h.editors.map((e, i) => (
                  <li key={e.version} className={`at-tool${i === 0 ? ' is-top' : ''}`} style={{ ['--tilt' as any]: `${((i * 7) % 5) - 2}deg` }}>
                    <span className="at-tool-k">Unity</span>
                    <b className="at-tool-v num">{e.version}</b>
                    <span className="at-tool-u">{i === 0 ? `${t('hub.editorLatest')} · ` : ''}{t('hub.editorUsedBy', { n: h.usage.get(e.version) ?? 0 })}</span>
                    <span className="at-tool-p" title={e.path}>{e.path}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}

        {h.view === 'new' && r.state === 'idle' && (
          <form className="at-order" onSubmit={onSubmit(h)} noValidate>
            <aside className="at-stub" aria-hidden="true">
              <span className="at-grommet" />
              <span className="at-stub-no">No {orderNo}</span>
            </aside>
            <div className="at-body">
              <header className="at-order-head">
                <h2 className="at-h">{t('hub.newHeadShop')}</h2>
                <span className="at-date">{today}</span>
              </header>
              <div className="at-line">
                <label className="at-k" htmlFor="hub-name">{t('hub.fieldName')}</label>
                <NameInput h={h} className="hub-input at-in" />
              </div>
              <NameNote h={h} />
              <div className="at-line">
                <span className="at-k">{t('hub.fieldFolder')}</span>
                <FolderButton h={h} className="hub-folder at-folder" />
              </div>
              <FolderNote h={h} />
              <div className="at-line">
                <label className="at-k" htmlFor="hub-version">{t('hub.fieldVersion')}</label>
                <VersionSelect h={h} className="hub-select at-in" />
              </div>
              <div className="at-job">
                <span className="at-k" id="hub-tpl-label">{t('hub.stepTemplateShop')}</span>
                <TemplateChoices h={h} className="hub-tpls at-tokens" show="short" />
              </div>
              <footer className="at-foot">
                <span className="at-foot-t"><Target h={h} /><span className="hub-hint">{t('hub.createHint')}</span></span>
                <button type="submit" className="btn btn-primary at-go" data-testid="hub-create">{t('hub.createShop')}</button>
              </footer>
            </div>
          </form>
        )}

        {h.view === 'new' && r.state === 'creating' && (
          <section className="at-bench" data-testid="hub-progress" aria-busy="true">
            <div className="at-gauge" role="progressbar" aria-label={t('hub.estimate')} aria-valuemin={0} aria-valuemax={100}
              aria-valuenow={Math.round(estimate(r.progress.elapsedMs) * 100)}>
              <svg viewBox="-110 -110 220 180" aria-hidden="true">
                <path className="at-dial-arc" d="M -77.9 45 A 90 90 0 1 1 77.9 45" />
                {/* The measured 85-105 s land in this last stretch of the sweep. */}
                <path className="at-dial-band" d="M 89.5 -9.4 A 90 90 0 0 1 77.9 45" />
                {DIAL_TICKS.map(a => <line key={a} className="at-dial-tick" x1="0" y1="-90" x2="0" y2={a % 60 === 0 ? -74 : -80} transform={`rotate(${a})`} />)}
                <g className="at-needle" style={{ transform: `rotate(${-120 + SWEEP * estimate(r.progress.elapsedMs)}deg)` }}>
                  <path d="M -3 8 L 0 -78 L 3 8 Z" />
                </g>
                <circle className="at-hub" r="9" />
              </svg>
              <Elapsed ms={r.progress.elapsedMs} className="hub-clock num at-clock" />
              <span className="at-gauge-k">{t(phaseKey(h))}</span>
            </div>
            <div className="at-receipt">
              <span className="at-receipt-h"><b>{r.name}</b><span className="num">No {orderNo}</span></span>
              <LogTail text={r.progress.logTail} className="hub-log at-log" />
            </div>
            <p className="at-est hub-hint">{t('hub.estimate')}</p>
            <CancelButton h={h} className="btn btn-ghost hub-cancel at-stop" />
          </section>
        )}

        {h.view === 'new' && result && (
          <section className={`at-order at-result is-${result.kind}`} data-testid={`hub-result-${result.kind}`}>
            <aside className="at-stub" aria-hidden="true">
              <span className="at-grommet" />
              <span className="at-stub-no">No {orderNo}</span>
            </aside>
            <div className="at-body">
              <span className="at-punch" aria-hidden="true">{t(result.kind === 'done' ? 'hub.stampDoneShop' : result.kind === 'failed' ? 'hub.stampFailedShop' : 'hub.stampCancelledShop')}</span>
              <h2 className="at-h">{result.title}</h2>
              <p className="at-result-s" role={result.kind === 'failed' ? 'alert' : undefined}>{result.text}</p>
              <div className="hub-actions">{result.actions}</div>
            </div>
          </section>
        )}
      </Panel>
    </div>
  );
};
