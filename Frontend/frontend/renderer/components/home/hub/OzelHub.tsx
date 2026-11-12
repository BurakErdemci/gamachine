import React from 'react';

import { useLang } from '../../../lib/i18n';
import { joinTarget, templateName } from '../../../lib/hub';
import { MascotHead } from '../BrandLogo';
import {
  CancelButton, Elapsed, EditorsEmpty, FolderButton, FolderNote, LogTail, NameInput, NameNote, Panel, Tabs,
  TemplateChoices, TemplateGlyph, VersionSelect, createGate, onSubmit, phaseKey, useResult, type HubLayoutProps,
} from './parts';
import { InstallFlow, type InstallWords } from './InstallFlow';
import { estimate } from './useHub';

const INSTALL_WORDS: InstallWords = { title: 'hub.install.title', start: 'hub.install.go', done: 'hub.install.done' };

/**
 * Özel: built only from the user's three colours and font (the --u-* variables). The view names
 * are set large in the user's own font, and a new project gets a live card of the game being made
 * with the mascot drawn in those colours (Özel keeps the brand mark and mascot). While Unity works
 * the card fills with the user's accent from the bottom.
 */
export const OzelHub = ({ h, recent, onGetUnity }: HubLayoutProps) => {
  const { t } = useLang();
  const result = useResult(h);
  const r = h.run;
  // A run describes itself; only the idle card follows the form.
  const shownName = r.state === 'idle' ? h.name.trim() : r.name;
  const cardTemplate = r.state === 'idle' ? h.template : r.template;
  const cardVersion = r.state === 'idle' ? h.version : r.req.version;
  const cardParent = r.state === 'idle' ? h.parent : r.req.parentDir;
  const fill = r.state === 'creating' ? estimate(r.progress.elapsedMs) : r.state === 'done' ? 1 : 0;
  const card = (
    <figure className={`oz-card is-${r.state}`} style={{ ['--fill' as any]: fill }} data-testid="hub-ozel-card">
      <span className="oz-card-fill" aria-hidden="true" />
      <MascotHead className="mascot oz-card-head" />
      <span className="oz-card-glyph"><TemplateGlyph t={cardTemplate} /></span>
      <figcaption>
        <b className="oz-card-name">{shownName || t('hub.untitled')}</b>
        <span className="oz-card-meta">
          {cardTemplate ? templateName(cardTemplate) : '—'}<span aria-hidden="true"> · </span><span className="num">{cardVersion || '—'}</span>
        </span>
        <span className="oz-card-path" title={cardParent ?? undefined}>
          {cardParent ? joinTarget(cardParent, shownName || '…') : t('hub.folderNone')}
        </span>
      </figcaption>
    </figure>
  );
  return (
    <div className="hub" data-hub="ozel">
      <Tabs h={h} className="hub-tabs oz-words" labels={{ projects: t('hub.tabProjects'), editors: t('hub.tabEditors'), new: t('hub.tabNew') }} />
      <Panel h={h}>
        {h.view === 'projects' && recent}

        {h.view === 'editors' && (
          <section className="oz-editors">
            {h.installShown && <InstallFlow h={h} words={INSTALL_WORDS} onGetUnity={onGetUnity} className="hub-inst oz-inst" />}
            {!h.installShown && h.editors === undefined && <p className="hub-wait" role="status">{t('hub.editorsLoading')}</p>}
            {!h.installShown && h.editors !== undefined && !h.editors?.length && <EditorsEmpty h={h} />}
            {!h.installShown && !!h.editors?.length && (
              <ul className="oz-chips" data-testid="hub-editors">
                {h.editors.map((e, i) => (
                  <li key={e.version} className={`oz-chip${i === 0 ? ' is-top' : ''}`}>
                    <b className="num">{e.version}</b>
                    <span>{i === 0 ? `${t('hub.editorLatest')} · ` : ''}{t('hub.editorUsedBy', { n: h.usage.get(e.version) ?? 0 })}</span>
                    <span className="oz-chip-p" title={e.path}>{e.path}</span>
                    <button type="button" className="set-link oz-chip-add" onClick={() => h.openInstall(e.version)} data-testid={`hub-add-modules-${e.version}`}>{t('hub.install.addModules')}</button>
                  </li>
                ))}
              </ul>
            )}
            {!h.installShown && (
              <div className="hub-head-acts oz-editors-acts">
                {!!h.editors?.length && <button type="button" className="btn btn-primary hub-install-entry" onClick={() => h.openInstall()} data-testid="hub-install-entry">{t('hub.install.entry')}</button>}
                <button type="button" className="set-link" onClick={() => void h.refreshEditors()}>{t('hub.refresh')}</button>
              </div>
            )}
          </section>
        )}

        {h.view === 'new' && r.state === 'idle' && (
          <form className="oz-make" onSubmit={onSubmit(h)} noValidate>
            {card}
            <div className="oz-fields">
              <label className="oz-k" htmlFor="hub-name">{t('hub.fieldName')}</label>
              <NameInput h={h} className="hub-input oz-in oz-name" />
              <NameNote h={h} />
              <span className="oz-k" id="hub-tpl-label">{t('hub.fieldTemplate')}</span>
              <TemplateChoices h={h} className="hub-tpls oz-tpls" show="short" />
              <div className="oz-pair">
                <span className="oz-pair-c">
                  <label className="oz-k" htmlFor="hub-version">{t('hub.fieldVersion')}</label>
                  <VersionSelect h={h} className="hub-select oz-in" />
                </span>
                <span className="oz-pair-c">
                  <span className="oz-k">{t('hub.fieldFolder')}</span>
                  <FolderButton h={h} className="hub-folder oz-in" />
                </span>
              </div>
              <FolderNote h={h} />
              <button type="submit" className="btn btn-primary oz-go" disabled={h.templates === null} {...createGate(h)} data-testid="hub-create">{t('hub.create')}</button>
              <p className="hub-hint">{t('hub.createHint')}</p>
            </div>
          </form>
        )}

        {h.view === 'new' && r.state === 'creating' && (
          <section className="oz-make oz-run" data-testid="hub-progress" aria-busy="true">
            {card}
            <div className="oz-fields">
              <span className="oz-k">{t(phaseKey(h))}</span>
              <Elapsed ms={r.progress.elapsedMs} className="hub-clock num oz-clock" />
              <span role="progressbar" className="oz-sr" aria-label={t('hub.estimate')} aria-valuemin={0} aria-valuemax={100}
                aria-valuenow={Math.round(fill * 100)} />
              <p className="hub-hint">{t('hub.estimate')}</p>
              <LogTail text={r.progress.logTail} className="hub-log oz-log" />
              <CancelButton h={h} />
            </div>
          </section>
        )}

        {h.view === 'new' && result && (
          <section className={`oz-make oz-result is-${result.kind}`} data-testid={`hub-result-${result.kind}`}>
            {card}
            <div className="oz-fields">
              <h2 className="oz-result-t">{result.title}</h2>
              <p className="oz-result-s" role={result.kind === 'failed' ? 'alert' : undefined}>{result.text}</p>
              <div className="hub-actions">{result.actions}</div>
            </div>
          </section>
        )}
      </Panel>
    </div>
  );
};
