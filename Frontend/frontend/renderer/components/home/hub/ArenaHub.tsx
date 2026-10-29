import React from 'react';

import { useLang } from '../../../lib/i18n';
import { templateName } from '../../../lib/hub';
import {
  CancelButton, Elapsed, EditorsEmpty, FolderButton, FolderNote, LogTail, NameCount, NameInput, NameNote, Panel, Tabs,
  TemplateChoices, TemplateGlyph, Target, VersionSelect, onSubmit, phaseKey, useResult, type HubLayoutProps,
} from './parts';
import { estimate } from './useHub';

const SEGMENTS = 24;

/**
 * Arena: the Hub is a game's main menu. Tabs are skewed HUD plates, a new project is a
 * character-creation screen (pick a class = template, then fill the save slot), and creating is a
 * loading screen with a segmented bar, a big clock and a console under it.
 */
export const ArenaHub = ({ h, recent, onGetUnity }: HubLayoutProps) => {
  const { t } = useLang();
  const result = useResult(h);
  const r = h.run;
  return (
    <div className="hub" data-hub="arena">
      <Tabs h={h} labels={{ projects: t('hub.tabProjectsQuest'), editors: t('hub.tabEditorsQuest'), new: t('hub.tabNewQuest') }} />
      <Panel h={h}>
        {h.view === 'projects' && recent}

        {h.view === 'editors' && (
          <section className="ar-engines">
            <h2 className="ar-kick">{t('hub.editorsHeadQuest')}</h2>
            {h.editors === undefined && <p className="hub-wait" role="status">{t('hub.editorsLoading')}</p>}
            {h.editors !== undefined && !h.editors?.length && <EditorsEmpty h={h} onGetUnity={onGetUnity} />}
            {!!h.editors?.length && (
              <ul className="ar-engine-list" data-testid="hub-editors">
                {h.editors.map((e, i) => {
                  const [major, ...rest] = e.version.split('.');
                  return (
                    <li key={e.version} className={`ar-engine${i === 0 ? ' is-top' : ''}`}>
                      <span className="ar-engine-lv num"><b>{major}</b>.{rest.join('.')}</span>
                      {i === 0 && <span className="ar-plate">{t('hub.editorLatest')}</span>}
                      <span className="ar-engine-use">{t('hub.editorUsedBy', { n: h.usage.get(e.version) ?? 0 })}</span>
                      <span className="ar-engine-path" title={e.path}>{e.path}</span>
                    </li>
                  );
                })}
              </ul>
            )}
            {!!h.editors?.length && (
              <button type="button" className="btn btn-ghost ar-refresh" onClick={() => void h.refreshEditors()}>{t('hub.refresh')}</button>
            )}
          </section>
        )}

        {h.view === 'new' && r.state === 'idle' && (
          <form className="ar-create" onSubmit={onSubmit(h)} noValidate>
            <section className="ar-class">
              <h2 className="ar-kick" id="hub-tpl-label"><span className="ar-step num">1</span>{t('hub.stepTemplateQuest')}</h2>
              <TemplateChoices h={h} className="hub-tpls ar-tiles" />
            </section>
            <section className="ar-slot">
              <h2 className="ar-kick"><span className="ar-step num">2</span>{t('hub.stepDetailsQuest')}</h2>
              <div className="ar-hero">
                <span className="ar-hero-glyph"><TemplateGlyph t={h.template} /></span>
                <span className="ar-hero-cls">{h.template ? templateName(h.template) : '—'}</span>
              </div>
              <label className="ar-field" htmlFor="hub-name">
                <span className="ar-lab">{t('hub.fieldName')}<NameCount h={h} /></span>
              </label>
              <NameInput h={h} className="hub-input ar-name" />
              <NameNote h={h} />
              <span className="ar-lab">{t('hub.fieldFolder')}</span>
              <FolderButton h={h} className="hub-folder ar-folder" />
              <FolderNote h={h} />
              <label className="ar-lab" htmlFor="hub-version">{t('hub.fieldVersion')}</label>
              <VersionSelect h={h} className="hub-select ar-select" />
              <Target h={h} />
              <button type="submit" className="btn btn-primary ar-start" disabled={h.templates === null} data-testid="hub-create">
                {t('hub.createQuest')}
                <Ic16 />
              </button>
              <p className="hub-hint">{t('hub.createHint')}</p>
            </section>
          </form>
        )}

        {h.view === 'new' && r.state === 'creating' && (
          <section className="ar-load" data-testid="hub-progress" aria-busy="true">
            <p className="ar-kick">{t(phaseKey(h))}</p>
            <h2 className="ar-load-name">{r.name}</h2>
            <div className="ar-segs" role="progressbar" aria-label={t('hub.estimate')}
              aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(estimate(r.progress.elapsedMs) * 100)}>
              {Array.from({ length: SEGMENTS }, (_, i) => (
                <i key={i} className={i < Math.round(estimate(r.progress.elapsedMs) * SEGMENTS) ? 'is-on' : ''} />
              ))}
            </div>
            <div className="ar-load-row">
              <Elapsed ms={r.progress.elapsedMs} className="hub-clock num ar-clock" />
              <span className="ar-load-est">{t('hub.estimate')}</span>
              <CancelButton h={h} className="btn btn-ghost hub-cancel ar-esc" />
            </div>
            <p className="ar-tip"><b>{t('hub.tipLabel')}</b> {t('hub.tip')}</p>
            <LogTail text={r.progress.logTail} className="hub-log ar-console" />
          </section>
        )}

        {h.view === 'new' && result && (
          <section className={`ar-result is-${result.kind}`} data-testid={`hub-result-${result.kind}`}>
            <p className="ar-banner">{t(result.kind === 'done' ? 'hub.bannerDoneQuest' : result.kind === 'failed' ? 'hub.bannerFailedQuest' : 'hub.bannerCancelledQuest')}</p>
            <h2 className="ar-result-t">{result.title}</h2>
            <p className="ar-result-s" role={result.kind === 'failed' ? 'alert' : undefined}>{result.text}</p>
            <div className="hub-actions">{result.actions}</div>
          </section>
        )}
      </Panel>
    </div>
  );
};

const Ic16 = () => (
  <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><path d="M6 4l9 6-9 6z" /></svg>
);
