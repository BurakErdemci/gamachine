import React from 'react';

import { useLang, type TKey } from '../../../lib/i18n';
import { clock, joinTarget, templateName, type HubTemplate } from '../../../lib/hub';
import type { Hub, HubView } from './useHub';

export const Ic = ({ children, className = 'ic' }: { children: React.ReactNode; className?: string }) => (
  <svg className={className} viewBox="0 0 20 20" aria-hidden="true">{children}</svg>
);

/** One drawn mark per template kind, stroked in currentColor so every theme inks it its own way. */
export const TemplateGlyph = ({ t }: { t?: HubTemplate }) => {
  const kind = tplKind(t);
  return (
    <svg className="hub-glyph" viewBox="0 0 48 48" aria-hidden="true" data-kind={kind}>
      {kind === '2d' && <><rect x="9" y="9" width="30" height="30" /><path d="M9 31l9-9 7 7 5-5 9 9" /><circle cx="31" cy="17" r="3" /></>}
      {kind === '3d' && <><path d="M24 7l15 8.5v17L24 41 9 32.5v-17z" /><path d="M9 15.5L24 24l15-8.5M24 24v17" /></>}
      {kind === 'urp' && <><path d="M24 7l15 8.5v17L24 41 9 32.5v-17z" /><path d="M24 24l15-8.5M24 24v17M24 24L9 15.5" /><path d="M16 11.2l15 8.6" strokeDasharray="2 3" /></>}
      {kind === 'hdrp' && <><circle cx="24" cy="24" r="8" /><path d="M24 6v6M24 36v6M6 24h6M36 24h6M11.3 11.3l4.2 4.2M32.5 32.5l4.2 4.2M11.3 36.7l4.2-4.2M32.5 15.5l4.2-4.2" /></>}
      {kind === 'none' && <><rect x="10" y="10" width="28" height="28" strokeDasharray="3 3" /></>}
      {kind === 'other' && <><rect x="10" y="12" width="28" height="24" /><path d="M10 19h28" /></>}
    </svg>
  );
};

export const tplKind = (t?: HubTemplate): 'none' | '2d' | '3d' | 'urp' | 'hdrp' | 'other' => {
  if (!t) return 'none';
  if (t.label !== 'other') return t.label;
  return /^URP/.test(templateName(t)) ? 'urp' : 'other';
};

const TPL_TEXT: Record<ReturnType<typeof tplKind>, TKey> = {
  none: 'hub.tplNone', '2d': 'hub.tpl2d', '3d': 'hub.tpl3d', urp: 'hub.tplUrp', hdrp: 'hub.tplHdrp', other: 'hub.tplOther',
};
export const tplText = (t?: HubTemplate): TKey => TPL_TEXT[tplKind(t)];

const PROBLEM: Record<string, TKey> = { empty: 'hub.nameEmpty', chars: 'hub.nameChars', long: 'hub.nameLong', spaces: 'hub.nameSpaces' };

/** The name box; the engine's rule is always in view and turns into the reason once it is broken. */
export const NameInput = ({ h, placeholder, className = 'hub-input' }: { h: Hub; placeholder?: string; className?: string }) => {
  const { t } = useLang();
  const bad = h.nameTouched && !!h.problem;
  return (
    <input id="hub-name" type="text" className={className} value={h.name} autoComplete="off" spellCheck={false}
      placeholder={placeholder ?? t('hub.namePlaceholder')} aria-invalid={bad || undefined} aria-describedby="hub-name-note"
      onChange={e => { h.setName(e.target.value); if (e.target.value) h.setNameTouched(true); }}
      onBlur={() => h.setNameTouched(true)} data-testid="hub-name" />
  );
};
export const NameNote = ({ h }: { h: Hub }) => {
  const { t } = useLang();
  const bad = h.nameTouched && !!h.problem;
  return (
    <span id="hub-name-note" className={`hub-note${bad ? ' is-bad' : ''}`} data-testid="hub-name-note" aria-live="polite">
      {bad ? t(PROBLEM[h.problem as string]) : t('hub.nameRule')}
    </span>
  );
};
export const NameCount = ({ h }: { h: Hub }) => <span className="hub-count num" aria-hidden="true">{String([...h.name].length).padStart(2, '0')}/64</span>;

export const FolderButton = ({ h, className = 'hub-folder' }: { h: Hub; className?: string }) => {
  const { t } = useLang();
  return (
    <button type="button" className={className} onClick={() => void h.pickParent()} data-testid="hub-folder">
      <Ic><path d="M2.5 5.5a1 1 0 0 1 1-1h4l2 2h7a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1h-13a1 1 0 0 1-1-1z" /></Ic>
      <span className="hub-folder-path" title={h.parent ?? undefined}>{h.parent ?? t('hub.folderNone')}</span>
      <span className="hub-folder-act">{t(h.parent ? 'hub.folderChange' : 'hub.folderPick')}</span>
    </button>
  );
};

export const Target = ({ h }: { h: Hub }) => {
  const { t } = useLang();
  if (!h.parent || !h.name || h.problem) return null;
  return <span className="hub-target" data-testid="hub-target">{t('hub.target', { path: joinTarget(h.parent, h.name) })}</span>;
};

export const VersionSelect = ({ h, className = 'hub-select' }: { h: Hub; className?: string }) => {
  const { t } = useLang();
  const list = h.editors ?? [];
  return (
    <select id="hub-version" className={className} value={h.version} disabled={!list.length}
      onChange={e => h.setVersion(e.target.value)} data-testid="hub-version">
      {!list.length && <option value="">{t('hub.needEditor')}</option>}
      {list.map((e, i) => <option key={e.version} value={e.version}>{e.version}{i === 0 ? ` · ${t('hub.editorLatest')}` : ''}</option>)}
    </select>
  );
};

/** Template choices as a radio group; `show` picks how much each option says. */
export const TemplateChoices = ({ h, className = 'hub-tpls', show = 'full' }: { h: Hub; className?: string; show?: 'full' | 'short' }) => {
  const { t } = useLang();
  if (h.templates === undefined) return <p className="hub-wait" role="status">{t('hub.templatesLoading')}</p>;
  if (!h.templates.length) return <p className="hub-wait" role="status">{t(h.version ? 'hub.templatesNone' : 'hub.needEditor')}</p>;
  return (
    <div className={className} role="radiogroup" aria-labelledby="hub-tpl-label" data-testid="hub-templates">
      {h.templates.map(x => (
        <button key={x.id} type="button" role="radio" aria-checked={h.templateId === x.id} data-kind={tplKind(x)}
          className={`hub-tpl${h.templateId === x.id ? ' is-on' : ''}`} onClick={() => h.setTemplateId(x.id)} title={x.id}>
          <TemplateGlyph t={x} />
          <span className="hub-tpl-name">{templateName(x)}</span>
          {show === 'full' && <span className="hub-tpl-desc">{t(tplText(x))}</span>}
        </button>
      ))}
    </div>
  );
};

export const LogTail = ({ text, className = 'hub-log' }: { text?: string; className?: string }) => {
  const { t } = useLang();
  const lines = (text ?? '').split(/\r?\n/).map(l => l.trimEnd()).filter(Boolean).slice(-12);
  return (
    <pre className={className} aria-label={t('hub.logTitle')} data-testid="hub-log">
      {lines.length ? lines.join('\n') : t('hub.logEmpty')}
    </pre>
  );
};

export const Elapsed = ({ ms, className = 'hub-clock num' }: { ms: number; className?: string }) => (
  <span className={className} data-testid="hub-elapsed">{clock(ms)}</span>
);

export const CancelButton = ({ h, className = 'btn btn-ghost hub-cancel', children }: { h: Hub; className?: string; children?: React.ReactNode }) => {
  const { t } = useLang();
  const busy = h.run.state === 'creating' && h.run.cancelling;
  return (
    <button type="button" className={className} onClick={() => void h.cancel()} disabled={busy} data-testid="hub-cancel">
      {children}{t(busy ? 'hub.cancelling' : 'hub.cancel')}
    </button>
  );
};

/** The phase line under the clock; 'starting' and 'running' read differently. */
export const phaseKey = (h: Hub): TKey => (h.run.state === 'creating' && h.run.progress.phase === 'starting' ? 'hub.phaseStarting' : 'hub.phaseRunning');

/**
 * The result of a creation (done / failed / cancelled) as plain parts every theme arranges: the
 * title, the text (the engine's own error on failure) and the buttons.
 */
export const useResult = (h: Hub) => {
  const { t } = useLang();
  const r = h.run;
  if (r.state === 'done') {
    return {
      kind: 'done' as const, title: t('hub.doneTitle', { name: r.name }), text: t('hub.doneText'),
      actions: (
        <>
          <button type="button" className="btn btn-primary hub-go" onClick={() => void h.openCreated()} data-testid="hub-open-created">{t('hub.openHere')}</button>
          <button type="button" className="btn btn-ghost" onClick={() => void h.launch(r.path, r.version)} data-testid="hub-launch-created">{t('hub.launchUnity')}</button>
          <button type="button" className="set-link" onClick={h.another}>{t('hub.another')}</button>
        </>
      ),
    };
  }
  if (r.state === 'failed' || r.state === 'cancelled') {
    return {
      kind: r.state, title: t(r.state === 'failed' ? 'hub.failedTitle' : 'hub.cancelledTitle', { name: r.name }),
      text: r.state === 'failed' ? r.error : t('hub.cancelledText'),
      actions: <button type="button" className="btn btn-primary hub-go" onClick={h.back} data-testid="hub-back">{t('hub.back')}</button>,
    };
  }
  return null;
};

/** The three views as tabs; each theme passes its own words and decorates the buttons with CSS. */
export const Tabs = ({ h, labels, className = 'hub-tabs', badge = true }: {
  h: Hub; labels: Record<HubView, string>; className?: string; badge?: boolean;
}) => {
  const { t } = useLang();
  const count: Partial<Record<HubView, number | undefined>> = { projects: h.recentCount, editors: h.editors?.length };
  const busy = h.run.state === 'creating';
  return (
    <div className={className} role="tablist" aria-label={t('hub.aria')}>
      {(['projects', 'editors', 'new'] as HubView[]).map(v => (
        <button key={v} type="button" role="tab" id={`hub-tab-${v}`} aria-selected={h.view === v} aria-controls="hub-panel"
          className={`hub-tab hub-tab-${v}${h.view === v ? ' is-on' : ''}`} onClick={() => h.setView(v)} data-testid={`hub-tab-${v}`}>
          {v === 'new' && <Ic className="ic ic-sm"><path d="M10 4v12M4 10h12" /></Ic>}
          <span className="hub-tab-t">{labels[v]}</span>
          {badge && v !== 'new' && count[v] !== undefined && <span className="hub-tab-n num">{count[v]}</span>}
          {v === 'new' && busy && <span className="hub-tab-busy" aria-hidden="true" />}
        </button>
      ))}
    </div>
  );
};

/** Editors: the empty and failed states say how to get Unity; shared words, themed frames. */
export const EditorsEmpty = ({ h, onGetUnity }: { h: Hub; onGetUnity: () => void }) => {
  const { t } = useLang();
  return (
    <div className="hub-empty" data-testid="hub-editors-empty">
      <b className="hub-empty-t">{t(h.editors === null ? 'hub.editorsFailed' : 'hub.editorsEmptyTitle')}</b>
      <p className="hub-empty-s">{t('hub.editorsEmpty')}</p>
      <div className="hub-empty-act">
        <button type="button" className="btn btn-primary" onClick={onGetUnity}>{t('hub.getUnity')}</button>
        <button type="button" className="btn btn-ghost" onClick={() => void h.refreshEditors()}>{t('hub.refresh')}</button>
      </div>
    </div>
  );
};

export interface HubLayoutProps {
  h: Hub;
  /** The recent projects (cards, search, drop zones) as WorkspaceScreen draws them. */
  recent: React.ReactNode;
  /** Opens Unity's download page (the editors' empty state). */
  onGetUnity: () => void;
}

export const Panel = ({ h, className = 'hub-panel', children }: { h: Hub; className?: string; children: React.ReactNode }) => (
  <div id="hub-panel" role="tabpanel" aria-labelledby={`hub-tab-${h.view}`} className={className} data-view={h.view}>{children}</div>
);

/** Submits the form through the hook; the button stays enabled so a press shows what is missing. */
export const onSubmit = (h: Hub) => (e: React.FormEvent) => { e.preventDefault(); void h.create(); };

/** Shown after a press on Create while no parent folder is chosen. */
export const FolderNote = ({ h }: { h: Hub }) => {
  const { t } = useLang();
  return h.tried && !h.parent ? <span className="hub-note is-bad" data-testid="hub-folder-note">{t('hub.folderMissing')}</span> : null;
};
