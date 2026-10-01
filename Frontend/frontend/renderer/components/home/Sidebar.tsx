import React from 'react';
import { motion } from 'framer-motion';
import { useLang } from '../../lib/i18n';
import {
  Plus,
  Edit3,
  Trash2,
  Folder,
  FolderOpen,
  File as FileIcon,
  FolderPlus,
  Pencil,
} from 'lucide-react';
import { Conversation, FileEntry, UserData } from './types';
import { FileTree } from './FileTree';
import type { ConvStatus } from '../../hooks/home/useChat';
import type { UnityMCPStatus } from '../../hooks/home/useAIConfig';
import { STATUS_DOT, awaitingElsewhere, familyRootId, mostUrgent, rootsOf } from '../../lib/convFamily';
import { AwaitingBadge } from './AwaitingBadge';
import { BrandLogo } from './BrandLogo';
import { useUnityLinkPulse } from './UnityMcpToggle';
import { confirmDialog } from '../ui/ConfirmDialog';

interface SidebarProps {
  isSidebarOpen: boolean;
  sidebarTab: 'chats' | 'files';
  setSidebarTab: (tab: 'chats' | 'files') => void;
  conversations: Conversation[];
  activeConvId: number | null;
  convStatus?: Record<number, ConvStatus>;
  selectConversation: (conv: Conversation) => void;
  createNewConversation: () => void;
  deleteConversation: (e: React.MouseEvent, id: number) => void;
  editingId: number | null;
  setEditingId: (id: number | null) => void;
  tempTitle: string;
  setTempTitle: (val: string) => void;
  saveRename: (id: number) => void;
  workspacePath: string | null;
  closeWorkspace: () => void;
  /** The open editor file has unsaved changes: switching project asks first. */
  isDirty?: boolean;
  rootFolderPath: string | null;
  openFolder: () => void;
  openFilePicker: () => void;

  // File System Hooks
  fileTree: FileEntry[];
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
  treeContextMenu: { x: number; y: number; entry: FileEntry } | null;
  setTreeContextMenu: (val: any) => void;
  gitStatus?: { isRepo: boolean; files: Record<string, string>; dirs: Record<string, string> };

  user: UserData | null;
  setShowSettings: (val: boolean) => void;
  handleLogout: () => void;
  /** Drives the brand head's visor blink when Unity connects (mockup round 7). */
  unityStatus?: UnityMCPStatus;
}

/** Sidebar width from the mockup (base.css `.app` first track). */
export const SIDEBAR_WIDTH = 240;

// Lamp per chat status: the mockup's `.status-*` classes, coloured by tokens.
const LAMP: Record<ConvStatus, string> = {
  running: 'status-running',
  awaiting: 'status-pending',
  unread: 'status-ok',
};

// TODO(profile): level and XP have no data source yet (ENTEGRASYON-PLANI "Later": a local usage
// stats source needs its own spec). Until then the card shows a fresh maker; Sade hides both.
const PROFILE_LEVEL = 1;
const PROFILE_XP = 0;

const baseName = (p: string | null | undefined) => (p ? p.split(/[\\/]/).filter(Boolean).pop() : undefined);

export const Sidebar: React.FC<SidebarProps> = (props) => {
  const {
    isSidebarOpen, sidebarTab, setSidebarTab, conversations, activeConvId,
    selectConversation, createNewConversation, deleteConversation, editingId,
    setEditingId, tempTitle, setTempTitle, saveRename, workspacePath,
    closeWorkspace, rootFolderPath, openFolder, openFilePicker, user,
    setShowSettings, fileTree, treeContextMenu, setTreeContextMenu,
    treeCreating, startTreeCreate, treeCreateValue, setTreeCreateValue, submitTreeCreate, setTreeCreating,
    convStatus, unityStatus, isDirty,
  } = props;

  const { t } = useLang();
  const linked = useUnityLinkPulse(unityStatus);

  if (!user) return null;

  // Closing the workspace drops the editor buffer; a dirty file must not vanish on one click.
  const switchProject = async () => {
    if (isDirty && !(await confirmDialog(t('sidebar.switchProjectDirty'), t('sidebar.switchProjectConfirm'), t('confirm.cancel')))) return;
    closeWorkspace();
  };

  // Branches live in the chat column's tabs; a row stands for its whole family.
  const roots = rootsOf(conversations);
  const activeRootId = familyRootId(conversations, activeConvId);
  const familyIds = (rootId: number) =>
    [rootId, ...conversations.filter(c => c.parent_id === rootId).map(c => c.id)];
  const statusOf = (id: number) => mostUrgent(convStatus, familyIds(id));
  // Mockup: work that is running or waiting on the user is an "active task" with a second line;
  // everything else (unread included) stays in the plain chat list, in the same order as before.
  const isTask = (s: ConvStatus | undefined) => s === 'running' || s === 'awaiting';
  const tasks = roots.filter(c => isTask(statusOf(c.id)));
  const chats = roots.filter(c => !isTask(statusOf(c.id)));

  const row = (conv: Conversation, quest: boolean) => {
    const isActive = activeRootId === conv.id;
    const status = statusOf(conv.id);
    const editing = editingId === conv.id;
    const cls = ['chat', quest ? 'chat-quest' : '', isActive ? 'is-active' : ''].filter(Boolean).join(' ');
    const title = editing ? (
      <input
        autoFocus
        className="chat-rename"
        value={tempTitle}
        onChange={e => setTempTitle(e.target.value)}
        onBlur={() => saveRename(conv.id)}
        onKeyDown={e => e.key === 'Enter' && saveRename(conv.id)}
        onClick={e => e.stopPropagation()}
      />
    ) : (
      <span className="chat-title">{conv.title}</span>
    );
    return (
      <li key={conv.id}>
        <div
          data-testid={`conv-row-${conv.id}`}
          data-active={isActive || undefined}
          aria-current={isActive ? 'page' : undefined}
          onClick={() => selectConversation(conv)}
          className={cls}
        >
          {status ? (
            <span
              data-testid={`conv-status-${conv.id}`}
              role="img"
              title={t(STATUS_DOT[status].label)}
              aria-label={t(STATUS_DOT[status].label)}
              className={`status ${LAMP[status]}`}
            />
          ) : (
            <span className="status status-idle" aria-hidden="true" />
          )}
          {quest && !editing ? (
            <span className="chat-text">
              {title}
              {status && <span className="chat-sub">{t(STATUS_DOT[status].label)}</span>}
            </span>
          ) : title}
          {!editing && (
            <span
              data-testid={`conv-number-${conv.id}`}
              title={t('mention.chatNumber', { no: conv.id })}
              className="chat-meta chat-no"
            >#{conv.id}</span>
          )}
          {/* The waiting chip: one signal per state, the lamp steps aside for it (CSS m5). */}
          {status === 'awaiting' && (
            <span className="badge" aria-hidden="true">
              <span className="badge-n">1</span>
              <span className="badge-bang">!</span>
            </span>
          )}
          {!editing && (
            <span className="chat-actions">
              <button
                type="button"
                className="chat-act"
                title={t('sidebar.rename')}
                aria-label={t('sidebar.rename')}
                onClick={(e) => { e.stopPropagation(); setEditingId(conv.id); setTempTitle(conv.title); }}
              ><Edit3 size={13} /></button>
              <button
                type="button"
                className="chat-act"
                title={t('sidebar.delete')}
                aria-label={t('sidebar.delete')}
                onClick={(e) => deleteConversation(e, conv.id)}
              ><Trash2 size={13} /></button>
            </span>
          )}
        </div>
      </li>
    );
  };

  return (
    <motion.aside
      className="sidebar-slot"
      animate={{ width: isSidebarOpen ? SIDEBAR_WIDTH : 0, opacity: isSidebarOpen ? 1 : 0 }}
      transition={{ duration: 0.2 }}
    >
      <div className="sidebar shell" aria-label={t('sidebar.chats')}>
        <div className="tex tex-shell" aria-hidden="true" />
        <div className={`brand${linked ? ' is-linked' : ''}`}><BrandLogo /></div>

        {/* The project switcher: closing the workspace returns to the project picker, which is
            how a different project is opened today. */}
        <button type="button" className="project" onClick={() => { void switchProject(); }} title={t('sidebar.switchProject')}>
          <svg className="ic" viewBox="0 0 20 20" aria-hidden="true"><path d="M3 5.5h4.5l1.5 1.8h8v8.2H3z" /></svg>
          <span className="project-text">
            <span className="project-name">{baseName(workspacePath) || 'Workspace'}</span>
            <span className="project-meta">{t('sidebar.projectMeta')}</span>
          </span>
          <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><path d="M6 8l4 4 4-4" /></svg>
        </button>

        <div className="side-tabs" role="tablist">
          {([['chats', t('sidebar.chats')], ['files', t('sidebar.files')]] as const).map(([tab, label]) => (
            <button
              key={tab}
              type="button"
              role="tab"
              aria-selected={sidebarTab === tab}
              onClick={() => setSidebarTab(tab)}
              className="side-tab"
            >
              {label}
              {/* The rows that carry the status are hidden behind the Files tab. */}
              {tab === 'chats' && sidebarTab !== 'chats' && (
                <AwaitingBadge count={awaitingElsewhere(convStatus, activeConvId, conversations)} testId="chats-tab-awaiting" />
              )}
            </button>
          ))}
        </div>

        <div className="side-scroll custom-scrollbar">
          {sidebarTab === 'chats' ? (
            <>
              <button type="button" className="new-chat" onClick={() => createNewConversation()}>
                <svg className="ic" viewBox="0 0 20 20" aria-hidden="true"><path d="M10 4v12M4 10h12" /></svg>
                <span>{t('sidebar.newChat')}</span>
                <kbd>{t('sidebar.newChatKey')}</kbd>
              </button>

              {tasks.length > 0 && (
                <>
                  <div className="side-label">
                    <span className="lex">
                      <span className="lex-d">{t('sidebar.now')}</span>
                      <span className="lex-q">{t('sidebar.activeTasks')}</span>
                    </span>
                    <span className="side-count">{tasks.length}</span>
                  </div>
                  <ul className="chat-list">{tasks.map(c => row(c, true))}</ul>
                </>
              )}

              <div className="side-label"><span>{t('sidebar.chats')}</span><span className="side-count">{chats.length}</span></div>
              <ul className="chat-list">{chats.map(c => row(c, false))}</ul>
            </>
          ) : (
            <div className="pt-2" onClick={() => treeContextMenu && setTreeContextMenu(null)}>
              <div className="flex gap-1 mb-2">
                <button onClick={openFolder} className="flex-1 flex items-center justify-center gap-1.5 px-2 py-2 text-[12px] text-blue-500 hover:bg-blue-600/10 rounded-lg transition-all font-semibold"><FolderOpen size={13} /> {t('sidebar.openFolder')}</button>
                <button onClick={openFilePicker} className="flex-1 flex items-center justify-center gap-1.5 px-2 py-2 text-[12px] text-emerald-500 hover:bg-emerald-600/10 rounded-lg transition-all font-semibold"><FileIcon size={13} /> {t('sidebar.openFile')}</button>
              </div>
              {rootFolderPath ? (
                <div>
                  <div className="px-2 py-1.5 flex items-center justify-between mb-1">
                    <span className="text-[12px] font-bold text-slate-500 uppercase tracking-wider truncate flex-1 min-w-0">{baseName(rootFolderPath)}</span>
                    <div className="flex items-center gap-0.5 shrink-0 ml-1">
                      <button onClick={(e) => { e.stopPropagation(); startTreeCreate(rootFolderPath, 'file'); }} className="p-1 text-slate-600 hover:text-white rounded transition-colors"><Plus size={11} /></button>
                      <button onClick={(e) => { e.stopPropagation(); startTreeCreate(rootFolderPath, 'folder'); }} className="p-1 text-slate-600 hover:text-white rounded transition-colors"><FolderPlus size={11} /></button>
                    </div>
                  </div>
                  {treeCreating?.parentPath === rootFolderPath && (
                    <div className="flex items-center gap-1.5 px-2 py-[3px]">
                      <Folder size={13} className="text-emerald-400 shrink-0" />
                      <input autoFocus value={treeCreateValue} onChange={e => setTreeCreateValue(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') submitTreeCreate(); if (e.key === 'Escape') setTreeCreating(null); }} onBlur={() => setTreeCreating(null)} className="chat-rename" />
                    </div>
                  )}
                  <FileTree {...props} entries={fileTree} />
                </div>
              ) : (
                <div className="text-center py-8 text-slate-600">
                  <FolderOpen size={24} className="mx-auto mb-2 opacity-20" />
                  <p className="text-[12px]">{t('sidebar.emptyFolder')}</p>
                </div>
              )}

              {treeContextMenu && (
                // The floating menu takes the shell's menu surface instead of a hard-coded near-black.
                <div className="fixed z-50 rounded-lg shadow-2xl py-1 min-w-[160px] text-[12px]" style={{ left: treeContextMenu.x, top: treeContextMenu.y, background: 'var(--menu-bg)', border: 'var(--line-w) solid var(--menu-line)', color: 'var(--shell-text)' }} onClick={e => e.stopPropagation()}>
                  {treeContextMenu.entry.isDirectory && (
                    <>
                      <button onClick={() => startTreeCreate(treeContextMenu.entry.path, 'file')} className="side-menu-item"><Plus size={13} /> {t('sidebar.newFile')}</button>
                      <button onClick={() => startTreeCreate(treeContextMenu.entry.path, 'folder')} className="side-menu-item"><FolderPlus size={13} /> {t('sidebar.newFolder')}</button>
                      <div className="side-menu-sep" />
                    </>
                  )}
                  <button onClick={() => props.startRename(treeContextMenu.entry)} className="side-menu-item"><Pencil size={13} /> {t('sidebar.rename')}</button>
                  <div className="side-menu-sep" />
                  <button onClick={() => props.handleTreeDelete(treeContextMenu.entry)} className="side-menu-item is-danger"><Trash2 size={13} /> {t('sidebar.delete')}</button>
                </div>
              )}
            </div>
          )}
        </div>

        <div className="side-profile" title={user.name}>
          <span className="lvl level" aria-hidden="true"><span className="lvl-n">{PROFILE_LEVEL}</span></span>
          <svg className="ic side-profile-ic" viewBox="0 0 20 20" aria-hidden="true">
            <circle cx="10" cy="7" r="3.2" />
            <path d="M3.8 17c.8-3.2 3.3-5 6.2-5s5.4 1.8 6.2 5" />
          </svg>
          <span className="side-profile-text">
            <span className="side-profile-name">{t('sidebar.profile')}</span>
            <span className="side-profile-meta">
              <span className="level">{t('sidebar.level', { seviye: PROFILE_LEVEL })}</span>
              <span className="xp"> · <span className="num">{t('sidebar.xp', { xp: PROFILE_XP })}</span></span>
              <span className="side-profile-plain">{t('sidebar.profilePlain')}</span>
            </span>
            <span className="xp-mini xp" aria-hidden="true"><span style={{ width: '0%' }} /></span>
          </span>
        </div>

        <div className="side-foot">
          <button type="button" className="foot-btn" onClick={() => setShowSettings(true)}>
            <svg className="ic" viewBox="0 0 20 20" aria-hidden="true">
              <circle cx="10" cy="10" r="2.6" />
              <path d="M10 2.5v2.2M10 15.3v2.2M2.5 10h2.2M15.3 10h2.2M4.7 4.7l1.6 1.6M13.7 13.7l1.6 1.6M4.7 15.3l1.6-1.6M13.7 6.3l1.6-1.6" />
            </svg>
            <span>{t('sidebar.settings')}</span>
          </button>
          <span className="foot-user" title={user.name}>{user.name}</span>
        </div>
      </div>
    </motion.aside>
  );
};
