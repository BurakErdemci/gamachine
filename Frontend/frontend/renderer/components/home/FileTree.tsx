import React from 'react';
import { FileEntry } from './types';
import { useLang } from '../../lib/i18n';
import { routeForFile } from '../model-viewer/extensions';

interface FileTreeProps {
  entries: FileEntry[];
  depth?: number;
  openedFilePath: string | null;
  expandedDirs: Set<string>;
  dirContents: Record<string, FileEntry[]>;
  toggleDir: (path: string) => void;
  openFile: (path: string) => void;
  openPreview: (path: string) => void;
  treeDragSource: FileEntry | null;
  treeDragTarget: string | null;
  renamingPath: string | null;
  renameValue: string;
  setRenameValue: (val: string) => void;
  submitRename: () => void;
  setRenamingPath: (path: string | null) => void;
  handleTreeDragStart: (e: React.DragEvent, entry: FileEntry) => void;
  handleTreeDragOver: (e: React.DragEvent, entry: FileEntry) => void;
  handleTreeDragLeave: (e: React.DragEvent) => void;
  handleTreeDrop: (e: React.DragEvent, entry: FileEntry) => void;
  handleTreeContextMenu: (e: React.MouseEvent, entry: FileEntry) => void;
  startTreeCreate: (parentPath: string, type: 'file' | 'folder') => void;
  startRename: (entry: FileEntry) => void;
  handleTreeDelete: (entry: FileEntry) => void;
  treeCreating: { parentPath: string; type: 'file' | 'folder' } | null;
  treeCreateValue: string;
  setTreeCreateValue: (val: string) => void;
  submitTreeCreate: () => void;
  setTreeCreating: (val: any) => void;
  gitStatus?: { isRepo: boolean; files: Record<string, string>; dirs: Record<string, string> };
}

/** Git status letters, as the old VS Code style badges had them; colour comes from the row's data-git. */
const GIT_LETTER: Record<string, string> = { modified: 'M', untracked: 'U', added: 'A', deleted: 'D' };

/** Line icons in the mockup's set (maket index.html symbols), one ink colour, glyph by kind. */
const Glyph = ({ d, className = 'ic' }: { d: React.ReactNode; className?: string }) => (
  <svg className={className} viewBox="0 0 20 20" aria-hidden="true">{d}</svg>
);
export const FT_ICON = {
  file: <><path d="M5.5 2.8h6l3 3v11.4h-9z" /><path d="M11.5 2.8v3h3" /></>,
  scene: <><path d="M3 15.5l4.5-6 3 4 2-2.5 4.5 4.5z" /><circle cx="13.5" cy="6" r="1.6" /></>,
  cube: <><path d="M10 3l6 3.3v7.4L10 17l-6-3.3V6.3z" /><path d="M4 6.3l6 3.3 6-3.3M10 9.6V17" /></>,
  film: <><rect x="3" y="4" width="14" height="12" rx="1" /><path d="M6.5 4v12M13.5 4v12M3 8h3.5M3 12h3.5M13.5 8H17M13.5 12H17" /></>,
  image: <><rect x="3" y="4" width="14" height="12" rx="1" /><path d="M3.5 14l4-4 3 3 2-2 4 4" /><circle cx="13" cy="7.6" r="1.3" /></>,
  folder: <path d="M3 5.5h4.5l1.5 1.8h8v8.2H3z" />,
  chev: <path d="M8 6l4 4-4 4" />,
  plus: <path d="M10 4v12M4 10h12" />,
  folderPlus: <><path d="M3 5.5h4.5l1.5 1.8h8v8.2H3z" /><path d="M10 9.2v4.4M7.8 11.4h4.4" /></>,
  pencil: <path d="M12.5 4.5l3 3-8 8H4.5v-3z" />,
  trash: <><path d="M4.5 6h11M8 6V4.5h4V6M6 6l.7 10h6.6L14 6" /></>,
};

const fileGlyph = (ext: string) => {
  const e = ext.toLowerCase();
  if (e === '.unity' || e === '.prefab') return FT_ICON.scene;
  if (['.fbx', '.obj', '.blend', '.dae', '.3ds', '.gltf', '.glb', '.stl'].includes(e)) return FT_ICON.cube;
  if (['.anim', '.controller', '.overridecontroller', '.playable', '.mask'].includes(e)) return FT_ICON.film;
  if (['.png', '.jpg', '.jpeg', '.tga', '.psd', '.tif', '.tiff', '.exr', '.hdr', '.gif', '.bmp', '.webp', '.svg'].includes(e)) return FT_ICON.image;
  return FT_ICON.file;
};

/**
 * The project tree. One component for both hosts: the sidebar's Files tab (shell tokens) and the
 * workspace's Dosyalar tab (paper tokens); workspace.css colours the mockup's `.ft-row` hooks
 * for each. All state lives in useFileSystem, so the two views always agree.
 */
export const FileTree: React.FC<FileTreeProps> = (props) => {
  const { t } = useLang();
  const {
    entries, depth = 0, openedFilePath, expandedDirs, dirContents,
    toggleDir, openFile, openPreview, treeDragSource, treeDragTarget,
    renamingPath, renameValue, setRenameValue, submitRename, setRenamingPath,
    handleTreeDragStart, handleTreeDragOver, handleTreeDragLeave, handleTreeDrop,
    handleTreeContextMenu, startTreeCreate, startRename, handleTreeDelete,
    treeCreating, treeCreateValue, setTreeCreateValue, submitTreeCreate, setTreeCreating,
    gitStatus
  } = props;

  // Girdinin git durumu: dosya → files haritasından; klasör → içinde değişiklik
  // varsa 'contains' (nokta rozeti). Harita anahtarları LOWERCASE tutulur
  // (Windows sürücü-harfi/case farkları), arama da lowercase yapılır.
  const gitInfo = (entry: FileEntry): string | null => {
    if (!gitStatus?.isRepo) return null;
    const key = entry.path.toLowerCase();
    if (!entry.isDirectory) return gitStatus.files[key] || null;
    return gitStatus.dirs[key] ? 'contains' : null;
  };
  const gitTitle = (gs: string) => (gs === 'untracked' ? t('git.untracked') : gs === 'modified' ? t('git.modified')
    : gs === 'added' ? t('git.added') : gs === 'contains' ? t('git.dirDirty') : t('git.deleted'));

  return (
    <>
      {entries.map(entry => {
        const lv = { '--lv': depth } as React.CSSProperties;
        const open = entry.isDirectory && expandedDirs.has(entry.path);
        const gs = gitInfo(entry);
        return (
          <div
            key={entry.path}
            onDragOver={(e) => handleTreeDragOver(e, entry)}
            onDragLeave={handleTreeDragLeave}
            onDrop={(e) => handleTreeDrop(e, entry)}
          >
            {renamingPath === entry.path ? (
              <div className="ft-row" style={lv}>
                <Glyph d={entry.isDirectory ? FT_ICON.folder : fileGlyph(entry.extension)} />
                <input
                  autoFocus value={renameValue} onChange={e => setRenameValue(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') submitRename(); if (e.key === 'Escape') setRenamingPath(null); }}
                  onBlur={() => setRenamingPath(null)}
                  className="ft-input"
                />
              </div>
            ) : (
              <div
                draggable onDragStart={(e) => handleTreeDragStart(e, entry)}
                onClick={() => {
                  if (entry.isDirectory) { toggleDir(entry.path); return; }
                  // Blocked formats also go to the preview panel: it is the
                  // surface that can explain why they cannot be shown.
                  const route = routeForFile(entry.path);
                  if (route !== 'text') openPreview(entry.path);
                  else openFile(entry.path);
                }}
                onContextMenu={(e) => handleTreeContextMenu(e, entry)}
                className={`ft-row ${entry.isDirectory ? 'ft-dir' : 'ft-file'}${treeDragTarget === entry.path ? ' is-drop' : ''}${treeDragSource?.path === entry.path ? ' is-dragging' : ''}`}
                aria-expanded={entry.isDirectory ? open : undefined}
                aria-current={openedFilePath === entry.path ? 'true' : undefined}
                data-git={gs || undefined}
                style={lv}
              >
                {entry.isDirectory
                  ? <Glyph d={FT_ICON.chev} className="ic ft-chev" />
                  : <Glyph d={fileGlyph(entry.extension)} />}
                <span className="ft-name">{entry.name}</span>
                {gs && (
                  <span className="ft-note" title={gitTitle(gs)}>{gs === 'contains' ? '●' : GIT_LETTER[gs]}</span>
                )}
                <span className="ft-acts">
                  {entry.isDirectory && (
                    <>
                      <button type="button" className="ft-act" aria-label={t('sidebar.newFile')} title={t('sidebar.newFile')}
                        onClick={(e) => { e.stopPropagation(); startTreeCreate(entry.path, 'file'); }}><Glyph d={FT_ICON.plus} /></button>
                      <button type="button" className="ft-act" aria-label={t('sidebar.newFolder')} title={t('sidebar.newFolder')}
                        onClick={(e) => { e.stopPropagation(); startTreeCreate(entry.path, 'folder'); }}><Glyph d={FT_ICON.folderPlus} /></button>
                    </>
                  )}
                  <button type="button" className="ft-act" aria-label={t('sidebar.rename')} title={t('sidebar.rename')}
                    onClick={(e) => { e.stopPropagation(); startRename(entry); }}><Glyph d={FT_ICON.pencil} /></button>
                  <button type="button" className="ft-act is-danger" aria-label={t('sidebar.delete')} title={t('sidebar.delete')}
                    onClick={(e) => { e.stopPropagation(); handleTreeDelete(entry); }}><Glyph d={FT_ICON.trash} /></button>
                </span>
              </div>
            )}
            {open && (
              <div>
                {treeCreating?.parentPath === entry.path && (
                  <div className="ft-row" style={{ '--lv': depth + 1 } as React.CSSProperties}>
                    <Glyph d={treeCreating.type === 'file' ? FT_ICON.file : FT_ICON.folder} />
                    <input autoFocus value={treeCreateValue} onChange={e => setTreeCreateValue(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') submitTreeCreate(); if (e.key === 'Escape') setTreeCreating(null); }} onBlur={() => setTreeCreating(null)} className="ft-input" />
                  </div>
                )}
                {dirContents[entry.path] && <FileTree {...props} entries={dirContents[entry.path]} depth={depth + 1} />}
              </div>
            )}
          </div>
        );
      })}
    </>
  );
};

interface TreeContextMenuProps {
  menu: { x: number; y: number; entry: FileEntry } | null;
  startTreeCreate: (parentPath: string, type: 'file' | 'folder') => void;
  startRename: (entry: FileEntry) => void;
  handleTreeDelete: (entry: FileEntry) => void;
}

/**
 * The tree's right-click menu on the shell's floating menu surface. Fixed-positioned at the click,
 * so whichever tree host draws it, it opens where the user clicked; home.tsx makes sure exactly
 * one host draws it.
 */
export const TreeContextMenu: React.FC<TreeContextMenuProps> = ({ menu, startTreeCreate, startRename, handleTreeDelete }) => {
  const { t } = useLang();
  if (!menu) return null;
  return (
    <div className="fixed z-50 rounded-lg shadow-2xl py-1 min-w-[160px] text-[13px]" style={{ left: menu.x, top: menu.y, background: 'var(--menu-bg)', border: 'var(--line-w) solid var(--menu-line)', color: 'var(--shell-text)' }} onClick={e => e.stopPropagation()}>
      {menu.entry.isDirectory && (
        <>
          <button onClick={() => startTreeCreate(menu.entry.path, 'file')} className="side-menu-item"><Glyph d={FT_ICON.plus} className="ic ic-sm" /> {t('sidebar.newFile')}</button>
          <button onClick={() => startTreeCreate(menu.entry.path, 'folder')} className="side-menu-item"><Glyph d={FT_ICON.folderPlus} className="ic ic-sm" /> {t('sidebar.newFolder')}</button>
          <div className="side-menu-sep" />
        </>
      )}
      <button onClick={() => startRename(menu.entry)} className="side-menu-item"><Glyph d={FT_ICON.pencil} className="ic ic-sm" /> {t('sidebar.rename')}</button>
      <div className="side-menu-sep" />
      <button onClick={() => handleTreeDelete(menu.entry)} className="side-menu-item is-danger"><Glyph d={FT_ICON.trash} className="ic ic-sm" /> {t('sidebar.delete')}</button>
    </div>
  );
};

export type ProjectFilesProps = Omit<FileTreeProps, 'entries'> & {
  rootFolderPath: string | null;
  fileTree: FileEntry[];
  openFolder: () => void;
  openFilePicker: () => void;
  treeContextMenu: { x: number; y: number; entry: FileEntry } | null;
  setTreeContextMenu: (val: any) => void;
  /** Draw the right-click menu here. Only one host may draw it (both would stack two copies). */
  showMenu: boolean;
};

const baseName = (p: string | null | undefined) => (p ? p.split(/[\/]/).filter(Boolean).pop() : undefined);

/**
 * The Files block: open folder / open file, the project root with its create buttons, the tree
 * and its menu. The sidebar's Files tab and the workspace's Dosyalar tab both draw this, so the
 * two cannot drift apart.
 */
export const ProjectFiles: React.FC<ProjectFilesProps> = (props) => {
  const { t } = useLang();
  const {
    rootFolderPath, fileTree, openFolder, openFilePicker, treeContextMenu, setTreeContextMenu, showMenu,
    treeCreating, treeCreateValue, setTreeCreateValue, submitTreeCreate, setTreeCreating, startTreeCreate,
  } = props;
  return (
    <div className="ftree" onClick={() => treeContextMenu && setTreeContextMenu(null)}>
      <div className="ft-tools">
        <button type="button" onClick={openFolder} className="ft-tool"><Glyph d={FT_ICON.folder} className="ic ic-sm" /> {t('sidebar.openFolder')}</button>
        <button type="button" onClick={openFilePicker} className="ft-tool"><Glyph d={FT_ICON.file} className="ic ic-sm" /> {t('sidebar.openFile')}</button>
      </div>
      {rootFolderPath ? (
        <div>
          <div className="ft-head">
            <h3 className="ws-label"><span>{baseName(rootFolderPath)}</span></h3>
            <button type="button" className="ft-act" aria-label={t('sidebar.newFile')} title={t('sidebar.newFile')}
              onClick={(e) => { e.stopPropagation(); startTreeCreate(rootFolderPath, 'file'); }}><Glyph d={FT_ICON.plus} /></button>
            <button type="button" className="ft-act" aria-label={t('sidebar.newFolder')} title={t('sidebar.newFolder')}
              onClick={(e) => { e.stopPropagation(); startTreeCreate(rootFolderPath, 'folder'); }}><Glyph d={FT_ICON.folderPlus} /></button>
          </div>
          {treeCreating?.parentPath === rootFolderPath && (
            <div className="ft-row">
              <Glyph d={treeCreating.type === 'file' ? FT_ICON.file : FT_ICON.folder} />
              <input autoFocus value={treeCreateValue} onChange={e => setTreeCreateValue(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') submitTreeCreate(); if (e.key === 'Escape') setTreeCreating(null); }} onBlur={() => setTreeCreating(null)} className="ft-input" />
            </div>
          )}
          <FileTree {...props} entries={fileTree} />
        </div>
      ) : (
        <p className="ft-empty">{t('sidebar.emptyFolder')}</p>
      )}
      {showMenu && (
        <TreeContextMenu menu={treeContextMenu} startTreeCreate={startTreeCreate} startRename={props.startRename} handleTreeDelete={props.handleTreeDelete} />
      )}
    </div>
  );
};
