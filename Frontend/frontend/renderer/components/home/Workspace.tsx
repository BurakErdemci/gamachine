import React, { useEffect, useRef, useState } from 'react';
import { useLang, type TKey } from '../../lib/i18n';
import { WS_TABS, WS_WIDTHS, type WsTab, type WsWidth } from '../../lib/workspacePanel';
import { lineDelta, type PendingChange } from '../../lib/pendingChange';
import { CheckIcon } from './ApprovalCard';
import { FT_ICON } from './FileTree';

/** Mockup line icons (maket index.html symbols), drawn in the current ink. */
const Ic = ({ children, className = 'ic' }: { children: React.ReactNode; className?: string }) => (
  <svg className={className} viewBox="0 0 20 20" aria-hidden="true">{children}</svg>
);
const WIDTH_ICON: Record<WsWidth, React.ReactNode> = {
  dar: <><rect x="2.5" y="4" width="15" height="12" rx="1" /><path d="M13.5 4v12" /></>,
  yarim: <><rect x="2.5" y="4" width="15" height="12" rx="1" /><path d="M10 4v12" /></>,
  odak: <><rect x="2.5" y="4" width="15" height="12" rx="1" /><path d="M6.5 4v12" /></>,
};
const X_ICON = <path d="M5.5 5.5l9 9M14.5 5.5l-9 9" />;

const TAB_KEY: Record<WsTab, TKey> = { sahne: 'ws.tabScene', dosyalar: 'ws.tabFiles', kod: 'ws.tabCode', onizleme: 'ws.tabPreview' };
// Guide anchors on the panes (lib/guide/anchors.ts). The file tree is anchored here, not in
// FileTree, because the sidebar's Files tab draws the same tree and the guide means this one.
const PANE_ANCHOR: Record<WsTab, string | undefined> = { sahne: undefined, dosyalar: 'file-tree', kod: 'ws-code', onizleme: 'ws-preview' };
const WIDTH_KEY: Record<WsWidth, TKey> = { dar: 'ws.widthNarrow', yarim: 'ws.widthHalf', odak: 'ws.widthFocus' };

interface WorkspaceProps {
  open: boolean;
  tab: WsTab;
  onTab: (tab: WsTab) => void;
  width: WsWidth;
  onWidth: (width: WsWidth) => void;
  onClose: () => void;
  /** Every pane stays mounted: Monaco's buffer and undo, the 3D stage and the file tree
   *  survive a tab switch. Only the visible one is laid out (workspace.css `.ws-pane`). */
  panes: Record<WsTab, React.ReactNode>;
  /** The terminal drawer under the panes. */
  drawer: React.ReactNode;
}

/**
 * The right panel (mockup `aside.workspace`, KARAKTER 13B): head with the three widths, the four
 * tabs, one pane at a time, the terminal drawer at the foot. State lives in home.tsx
 * (useWorkspacePanel), so the chat can open things here.
 */
export const Workspace: React.FC<WorkspaceProps> = ({ open, tab, onTab, width, onWidth, onClose, panes, drawer }) => {
  const { t } = useLang();
  // #11 pane change: the newly shown pane rises in once (mockup `.ws-pane.is-entering`).
  const [entering, setEntering] = useState<WsTab | null>(null);
  const first = useRef(true);
  useEffect(() => {
    if (first.current) { first.current = false; return; }
    setEntering(tab);
    const id = setTimeout(() => setEntering(null), 260);
    return () => clearTimeout(id);
  }, [tab]);

  // Arrow keys move between the tabs (the tablist pattern), as the mockup's tabs are buttons in a row.
  const onTabKey = (e: React.KeyboardEvent) => {
    const i = WS_TABS.indexOf(tab);
    const next = e.key === 'ArrowRight' ? WS_TABS[(i + 1) % WS_TABS.length]
      : e.key === 'ArrowLeft' ? WS_TABS[(i + WS_TABS.length - 1) % WS_TABS.length] : null;
    if (!next) return;
    e.preventDefault();
    onTab(next);
    requestAnimationFrame(() => document.getElementById(`ws-tab-${next}`)?.focus());
  };

  return (
    <aside
      className="workspace is-entering"
      aria-label={t('ws.title')}
      data-tab={tab}
      data-width={width}
      hidden={!open}
      data-testid="workspace"
      data-guide="workspace"
    >
      <div className="tex tex-panel" aria-hidden="true" />
      <header className="ws-head">
        <span className="ws-title">{t('ws.title')}</span>
        <div className="ws-widths" data-guide="ws-widths" role="group" aria-label={t('ws.widths')}>
          {WS_WIDTHS.map(w => (
            <button
              key={w}
              type="button"
              className="ws-w"
              data-width={w}
              aria-pressed={width === w}
              aria-label={t(WIDTH_KEY[w])}
              title={t(WIDTH_KEY[w])}
              onClick={() => onWidth(w)}
            >
              <Ic>{WIDTH_ICON[w]}</Ic>
            </button>
          ))}
        </div>
        <button type="button" className="icon-btn" aria-label={t('ws.close')} title={t('ws.close')} onClick={onClose}>
          <Ic>{X_ICON}</Ic>
        </button>
      </header>
      <div className="ws-tabs" data-guide="ws-tabs" role="tablist" aria-label={t('ws.tabs')} onKeyDown={onTabKey}>
        {WS_TABS.map(id => (
          <button
            key={id}
            id={`ws-tab-${id}`}
            type="button"
            role="tab"
            className="ws-tab"
            data-tab={id}
            aria-selected={tab === id}
            aria-controls={`ws-pane-${id}`}
            tabIndex={tab === id ? 0 : -1}
            onClick={() => onTab(id)}
          >
            {t(TAB_KEY[id])}
            {id === 'kod' && <span className="ws-tab-mark" role="img" aria-label={t('ws.pendingMark')} />}
          </button>
        ))}
      </div>
      <div className="ws-body">
        {WS_TABS.map(id => (
          <div
            key={id}
            id={`ws-pane-${id}`}
            className={`ws-pane${id === 'kod' ? ' ws-code' : ''}${id === 'onizleme' ? ' ws-preview' : ''}${entering === id ? ' is-entering' : ''}`}
            data-pane={id}
            data-guide={PANE_ANCHOR[id]}
            role="tabpanel"
            aria-labelledby={`ws-tab-${id}`}
          >
            {panes[id]}
          </div>
        ))}
      </div>
      {drawer}
    </aside>
  );
};

/** `dir/` and `name` of a path, relative to the workspace when it lies inside it. */
export const splitPath = (path: string, workspacePath: string | null) => {
  let p = path.replace(/\\/g, '/');
  const ws = workspacePath?.replace(/\\/g, '/').replace(/\/+$/, '');
  if (ws && p.toLowerCase().startsWith(`${ws.toLowerCase()}/`)) p = p.slice(ws.length + 1);
  const cut = p.lastIndexOf('/');
  return { dir: cut >= 0 ? p.slice(0, cut + 1) : '', name: cut >= 0 ? p.slice(cut + 1) : p };
};

const KIND_KEY: Record<string, TKey> = { '.cs': 'ws.kindCsharp', '.unity': 'ws.kindScene', '.prefab': 'ws.kindPrefab' };
const extOf = (p: string) => { const m = /\.[^./\\]+$/.exec(p); return m ? m[0].toLowerCase() : ''; };

export interface WsDiff { name: string; path?: string; original: string; modified: string }

/** A hidden workspace also hides an expanded drawer; the menu must reveal both. */
export function toggleTerminalDrawer(
  workspaceOpen: boolean, drawerOpen: boolean,
  setWorkspaceOpen: (open: boolean) => void, setDrawerOpen: (open: boolean) => void,
) {
  if (!workspaceOpen) setWorkspaceOpen(true);
  setDrawerOpen(!workspaceOpen || !drawerOpen);
}

interface KodPaneProps {
  workspacePath: string | null;
  openedFilePath: string | null;
  isDirty: boolean;
  onSave: () => void;
  onCloseFile: () => void;
  /** The change on screen (the card's diff), or null. */
  diff: WsDiff | null;
  /** The card's handlers for that change, when a card is waiting on it. */
  change: PendingChange | null;
  /** The file editor. Always mounted, so its buffer, undo and cursor survive a diff review. */
  fileEditor: React.ReactNode;
  /** The diff editor for `diff`. */
  diffEditor: React.ReactNode;
  /** A line over the editor (the C# project hint), or null. */
  hint?: React.ReactNode;
}

/**
 * Kod (mockup `.ws-code`): file tabs, the crumb with the path, the editor; a pending change is a
 * second tab with the "waiting for your approval" strip over its diff. The strip's buttons call
 * the card's own handlers (lib/pendingChange.ts), so the card and the strip are one decision.
 */
export const KodPane: React.FC<KodPaneProps> = ({
  workspacePath, openedFilePath, isDirty, onSave, onCloseFile, diff, change, fileEditor, diffEditor, hint,
}) => {
  const { t } = useLang();
  const [view, setView] = useState<'file' | 'diff'>(diff ? 'diff' : 'file');
  const diffId = diff ? `${diff.path ?? ''}|${diff.name}` : null;
  // A change that arrives is what the user is being asked about: show it. When it is decided the
  // open file comes back.
  useEffect(() => { setView(diffId ? 'diff' : 'file'); }, [diffId]);

  const file = openedFilePath ? splitPath(openedFilePath, workspacePath) : null;
  const shown = view === 'diff' && diff ? 'diff' : file ? 'file' : diff ? 'diff' : 'none';
  const dPath = diff ? splitPath(diff.path || diff.name, workspacePath) : null;
  const delta = diff ? lineDelta(diff.original, diff.modified) : null;
  const fresh = !!diff && !diff.original;

  return (
    <>
      {(file || diff) && (
        <div className="ed-tabs" data-guide="ws-code-tabs" role="tablist" aria-label={t('ws.openFiles')}>
          {file && (
            <div className="ed-tab" role="tab" aria-selected={shown === 'file'} data-file="file">
              <button type="button" className="ed-tab-pick" onClick={() => setView('file')}>
                <Ic className="ic ic-sm">{FT_ICON.file}</Ic>{file.name}
                {isDirty && <span className="ed-dirty" role="img" aria-label={t('ws.unsaved')} />}
              </button>
              <button type="button" className="ed-tab-x" aria-label={t('ws.closeFile', { ad: file.name })} title={t('ws.closeFile', { ad: file.name })} onClick={onCloseFile}>
                <Ic>{X_ICON}</Ic>
              </button>
            </div>
          )}
          {diff && dPath && (
            <div className="ed-tab" role="tab" aria-selected={shown === 'diff'} data-file="diff">
              <button type="button" className="ed-tab-pick" onClick={() => setView('diff')}>
                <Ic className="ic ic-sm">{FT_ICON.file}</Ic>{dPath.name}
                <span className="ws-tab-mark" aria-hidden="true" />
              </button>
            </div>
          )}
        </div>
      )}

      {/* The file editor: mounted while a file is open, hidden behind the diff tab. */}
      <div className="ed-file" data-file="file" hidden={shown !== 'file'} style={shown !== 'file' ? { display: 'none' } : undefined}>
        {file && (
          <div className="ed-crumb">
            <span className="ed-path">{file.dir}<b>{file.name}</b></span>
            {KIND_KEY[extOf(file.name)] && <span className="ed-kind">{t(KIND_KEY[extOf(file.name)])}</span>}
            <button type="button" className="ed-save" onClick={onSave} disabled={!isDirty} title={t('ws.save')}>
              <Ic className="ic"><path d="M4 3.5h9.5l2.5 2.5v10.5H4z" /><path d="M7 3.5v4h6v-4M7 16.5v-5h6v5" /></Ic>
              {t('ws.save')}
            </button>
          </div>
        )}
        {hint}
        <div className="ed-host">{fileEditor}</div>
      </div>

      {diff && dPath && delta && (
        <div
          className="ed-file ws-change"
          data-file="diff"
          data-state={change ? 'pending' : 'shown'}
          style={shown !== 'diff' ? { display: 'none' } : undefined}
        >
          {change && (
            <div className="ws-pending" role="group" aria-label={t('ws.pendingGroup')} data-testid="ws-pending">
              <span className="ws-pending-mark" aria-hidden="true" />
              <span className="ws-pending-text">
                <span className="ws-pending-k">{t('ws.pendingK')}</span>
                <span className="ws-pending-s">
                  {fresh
                    ? t('ws.pendingNew', { ad: dPath.name, ekle: delta.add })
                    : t('ws.pendingEdit', { ad: dPath.name, ekle: delta.add, sil: delta.del })}
                </span>
              </span>
              <span className="ws-pending-act">
                <button type="button" className="btn btn-primary" disabled={change.busy} onClick={change.accept}>
                  <CheckIcon /><span>{t('ws.accept')}</span>
                </button>
                <button type="button" className="btn btn-ghost" disabled={change.busy} onClick={change.reject}>
                  {t('ws.reject')}
                </button>
              </span>
            </div>
          )}
          <div className="ed-crumb">
            <span className="ed-path">{dPath.dir}<b>{dPath.name}</b></span>
            <span className="ed-kind">{t(fresh ? 'ws.kindNew' : 'ws.kindChange')}</span>
            <span className="diff">
              {delta.add > 0 && <span className="add">+{delta.add}</span>}
              {delta.del > 0 && <span className="del">−{delta.del}</span>}
            </span>
          </div>
          <div className="ed-host">{diffEditor}</div>
        </div>
      )}

      {shown === 'none' && (
        <div className="ws-empty"><p>{t('ws.codeEmpty')}</p></div>
      )}
    </>
  );
};

interface PreviewPaneProps {
  file: { path: string; name: string } | null;
  kind: 'image' | 'model' | null;
  onClose: () => void;
  /** The viewer for `file` (the lazily loaded image or 3D panel). */
  viewer: React.ReactNode;
}

/** Önizleme (mockup `.ws-preview`): what kind of preview, then the app's real viewer. */
export const PreviewPane: React.FC<PreviewPaneProps> = ({ file, kind, onClose, viewer }) => {
  const { t } = useLang();
  if (!file || !kind) return <div className="ws-empty"><p>{t('ws.previewEmpty')}</p></div>;
  return (
    <>
      <div className="pv-switch" role="tablist" aria-label={t('ws.previewKind')}>
        <span className="pv-pick" role="tab" aria-selected="true">
          <Ic className="ic ic-sm">{kind === 'image' ? FT_ICON.image : FT_ICON.cube}</Ic>
          {t(kind === 'image' ? 'ws.previewImage' : 'ws.previewModel')}
        </span>
        <button type="button" className="icon-btn" aria-label={t('ws.previewClose')} title={t('ws.previewClose')} onClick={onClose}>
          <Ic>{X_ICON}</Ic>
        </button>
      </div>
      {viewer}
      {/* The image panel writes its own meta line (size, bytes, fit); a model gets its name here. */}
      {kind === 'model' && <p className="pv-meta"><span className="pv-name">{file.name}</span></p>}
    </>
  );
};

export interface ChangedFile { path: string; rel: string; status: string }

interface ScenePaneProps {
  change: PendingChange | null;
  changed: ChangedFile[];
  /** Total before the list was capped. */
  changedTotal: number;
  seenHidden?: number;
  onAck?: () => void;
  onShowAll?: () => void;
  isRepo: boolean;
  workspacePath: string | null;
  onShowChange: () => void;
  onOpen: (path: string) => void;
}

const STATUS_KEY: Record<string, TKey> = {
  modified: 'ws.statusModified', untracked: 'ws.statusNew', added: 'ws.statusNew', deleted: 'ws.statusDeleted',
};
const STATUS_TAG: Record<string, string> = { modified: 'tag-mod', untracked: 'tag-new', added: 'tag-new', deleted: 'tag-del' };

/**
 * Sahne (mockup `.ws-pane[data-pane=sahne]`). The app has no source for the Unity scene tree
 * (no hook or backend call reads the hierarchy), so the scene section says so in one line; the
 * changed files come from the git status the file tree already polls, with the change a card is
 * waiting on at the top.
 */
export const ScenePane: React.FC<ScenePaneProps> = ({ change, changed, changedTotal, seenHidden = 0, onAck, onShowAll, isRepo, workspacePath, onShowChange, onOpen }) => {
  const { t } = useLang();
  const count = changedTotal + (change ? 1 : 0);
  return (
    <>
      <section className="ws-sec">
        <h3 className="ws-label"><span>{t('ws.scene')}</span></h3>
        <p className="ws-note">{t('ws.sceneEmpty')}</p>
      </section>
      <section className="ws-sec" data-guide="changed-files">
        <h3 className="ws-label">
          <span>{t('ws.changed')}</span> <span className="ws-count">{count}</span>
          {changedTotal > 0 && onAck && (
            <button type="button" className="ws-ack" title={t('ws.ackTitle')} aria-label={t('ws.ackTitle')} onClick={onAck}>{t('ws.ack')}</button>
          )}
        </h3>
        {count === 0 ? (
          seenHidden > 0 ? (
            <p className="ws-note">
              <span>{t('ws.changedSeenNote', { sayi: seenHidden })}</span>{' '}
              <button type="button" className="ws-ack" onClick={onShowAll}>{t('ws.showAll')}</button>
            </p>
          ) : <p className="ws-note">{t(isRepo ? 'ws.changedEmpty' : 'ws.changedNoRepo')}</p>
        ) : (
          <ul className="files">
            {change && (() => {
              const p = splitPath(change.path || change.name, workspacePath);
              const d = lineDelta(change.original, change.modified);
              return (
                <li>
                  <button type="button" className="file-row" data-state="pending" onClick={onShowChange}>
                    <Ic>{FT_ICON.file}</Ic>
                    <span className="file-text">
                      <span className="file-name">{p.name}</span>
                      <span className="file-dir">{p.dir || './'}</span>
                    </span>
                    <span className="diff">
                      {d.add > 0 && <span className="add">+{d.add}</span>}
                      {d.del > 0 && <span className="del">−{d.del}</span>}
                    </span>
                    <span className="tag tag-wait">{t('ws.statusWaiting')}</span>
                  </button>
                </li>
              );
            })()}
            {changed.map(f => {
              const p = splitPath(f.rel, null);
              const body = (
                <>
                  <Ic>{FT_ICON.file}</Ic>
                  <span className="file-text">
                    <span className="file-name">{p.name}</span>
                    <span className="file-dir">{p.dir || './'}</span>
                  </span>
                  <span className={`tag ${STATUS_TAG[f.status] ?? 'tag-mod'}`}>{t(STATUS_KEY[f.status] ?? 'ws.statusModified')}</span>
                </>
              );
              return (
                <li key={f.path}>
                  {/* A deleted file has nothing to open. */}
                  {f.status === 'deleted'
                    ? <div className="file-row">{body}</div>
                    : <button type="button" className="file-row" onClick={() => onOpen(f.path)}>{body}</button>}
                </li>
              );
            })}
          </ul>
        )}
        {changedTotal > changed.length && (
          <p className="ws-note">{t('ws.changedMore', { sayi: changedTotal - changed.length })}</p>
        )}
        {count > 0 && seenHidden > 0 && (
          <p className="ws-note">
            <span>{t('ws.changedSeenMore', { sayi: seenHidden })}</span>{' '}
            <button type="button" className="ws-ack" onClick={onShowAll}>{t('ws.showAll')}</button>
          </p>
        )}
      </section>
    </>
  );
};
