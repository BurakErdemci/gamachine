import React from 'react';
import { motion } from 'framer-motion';
import { useLang } from '../../lib/i18n';
import { 
  Plus, 
  MessageSquare, 
  Edit3, 
  Trash2, 
  Folder, 
  X, 
  FolderOpen, 
  File as FileIcon, 
  FolderPlus, 
  Pencil, 
  Settings, 
  LogOut,
} from 'lucide-react';
import { Conversation, FileEntry, UserData } from './types';
import { FileTree } from './FileTree';
import type { ConvStatus } from '../../hooks/home/useChat';
import { STATUS_DOT, awaitingElsewhere, familyRootId, mostUrgent, rootsOf } from '../../lib/convFamily';
import { AwaitingBadge } from './AwaitingBadge';

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
}

export const Sidebar: React.FC<SidebarProps> = (props) => {
  const {
    isSidebarOpen, sidebarTab, setSidebarTab, conversations, activeConvId,
    selectConversation, createNewConversation, deleteConversation, editingId,
    setEditingId, tempTitle, setTempTitle, saveRename, workspacePath,
    closeWorkspace, rootFolderPath, openFolder, openFilePicker, user,
    setShowSettings, handleLogout, fileTree, treeContextMenu, setTreeContextMenu,
    treeCreating, startTreeCreate, treeCreateValue, setTreeCreateValue, submitTreeCreate, setTreeCreating,
    convStatus,
  } = props;

  const { t } = useLang();

  if (!user) return null;

  // Branches live in the chat column's tabs; a row stands for its whole family.
  const roots = rootsOf(conversations);
  const activeRootId = familyRootId(conversations, activeConvId);
  const familyIds = (rootId: number) =>
    [rootId, ...conversations.filter(c => c.parent_id === rootId).map(c => c.id)];

  return (
    <motion.aside
      animate={{ width: isSidebarOpen ? 260 : 0, opacity: isSidebarOpen ? 1 : 0 }}
      transition={{ duration: 0.2 }}
      className="bg-white/[0.015] border-r border-white/[0.06] flex flex-col overflow-hidden z-20 shrink-0"
    >
      <div className="flex items-center justify-between px-3 h-12 border-b border-white/[0.06] min-w-[260px] shrink-0">
        <div className="flex items-center gap-2 flex-1 min-w-0">
          <Folder size={13} className="text-blue-500 shrink-0" />
          <span className="text-[11px] text-slate-300 font-medium truncate">
            {workspacePath?.split('/').pop() || 'Workspace'}
          </span>
        </div>
        <button onClick={closeWorkspace} className="p-1 hover:bg-red-900/30 rounded text-slate-600 hover:text-red-400 transition-all">
          <X size={13} />
        </button>
      </div>

      <div className="flex gap-1 p-1.5 border-b border-white/[0.06] min-w-[260px]">
        {([['chats', t('sidebar.chats')], ['files', t('sidebar.files')]] as const).map(([tab, label]) => (
          <button
            key={tab}
            onClick={() => setSidebarTab(tab)}
            className={`flex-1 flex items-center justify-center gap-1.5 py-1.5 rounded-lg text-[10px] font-semibold tracking-wider uppercase transition-colors ${
              sidebarTab === tab
                ? 'bg-white/[0.06] text-slate-100'
                : 'text-slate-500 hover:text-slate-300 hover:bg-white/[0.03]'
            }`}
          >
            {label}
            {/* The rows that carry the status are hidden behind the Files tab. */}
            {tab === 'chats' && sidebarTab !== 'chats' && (
              <AwaitingBadge count={awaitingElsewhere(convStatus, activeConvId, conversations)} testId="chats-tab-awaiting" />
            )}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto custom-scrollbar min-w-[260px]">
        {sidebarTab === 'chats' ? (
          <div className="p-1.5 space-y-0.5">
            <button onClick={() => createNewConversation()} className="w-full flex items-center gap-2 px-3 py-2 text-[11px] text-blue-500 hover:bg-blue-600/10 rounded-lg transition-all font-medium"><Plus size={14} /> {t('sidebar.newChat')}</button>
            {roots.map((conv) => {
              const isActive = activeRootId === conv.id;
              const status = mostUrgent(convStatus, familyIds(conv.id));
              return (
              <div key={conv.id} data-testid={`conv-row-${conv.id}`} data-active={isActive || undefined} onClick={() => selectConversation(conv)} className={`group relative px-3 py-2.5 rounded-lg transition-all cursor-pointer ${isActive ? 'bg-white/[0.06] text-slate-100' : 'text-slate-400 hover:bg-white/[0.03] hover:text-slate-200'}`}>
                {isActive && (
                  <span className="absolute left-0 top-2 bottom-2 w-[2px] rounded-full bg-blue-400/80" />
                )}
                <div className="flex items-center gap-2.5">
                  <MessageSquare size={14} className={isActive ? "text-blue-400" : "text-slate-600"} />
                  <div className="flex-1 overflow-hidden pr-6">
                    {editingId === conv.id ? (
                      <input autoFocus className="bg-[#000000] text-white text-xs w-full px-2 py-1 rounded border border-blue-500 outline-none" value={tempTitle} onChange={e => setTempTitle(e.target.value)} onBlur={() => saveRename(conv.id)} onKeyDown={e => e.key === 'Enter' && saveRename(conv.id)} onClick={e => e.stopPropagation()} />
                    ) : (
                      <div className="flex items-center gap-1.5 min-w-0">
                        <div className="text-[13px] font-medium truncate">{conv.title}</div>
                        {status && (
                          <span
                            data-testid={`conv-status-${conv.id}`}
                            role="img"
                            title={t(STATUS_DOT[status].label)}
                            aria-label={t(STATUS_DOT[status].label)}
                            className={`w-1.5 h-1.5 rounded-full shrink-0 ${STATUS_DOT[status].className}`}
                          />
                        )}
                      </div>
                    )}
                  </div>
                </div>
                {editingId !== conv.id && (
                  <div className="absolute right-1.5 top-2 flex gap-0.5 opacity-0 group-hover:opacity-100 transition-all">
                    <button onClick={(e) => { e.stopPropagation(); setEditingId(conv.id); setTempTitle(conv.title); }} className="p-1 hover:bg-slate-700 rounded text-slate-500 hover:text-slate-300"><Edit3 size={11} /></button>
                    <button onClick={(e) => deleteConversation(e, conv.id)} className="p-1 hover:bg-red-900/30 rounded text-slate-500 hover:text-red-400"><Trash2 size={11} /></button>
                  </div>
                )}
              </div>
              );
            })}
          </div>
        ) : (
          <div className="p-1.5" onClick={() => treeContextMenu && setTreeContextMenu(null)}>
            <div className="flex gap-1 mb-2">
              <button onClick={openFolder} className="flex-1 flex items-center justify-center gap-1.5 px-2 py-2 text-[10px] text-blue-500 hover:bg-blue-600/10 rounded-lg transition-all font-semibold"><FolderOpen size={13} /> {t('sidebar.openFolder')}</button>
              <button onClick={openFilePicker} className="flex-1 flex items-center justify-center gap-1.5 px-2 py-2 text-[10px] text-emerald-500 hover:bg-emerald-600/10 rounded-lg transition-all font-semibold"><FileIcon size={13} /> {t('sidebar.openFile')}</button>
            </div>
            {rootFolderPath ? (
              <div>
                <div className="px-2 py-1.5 flex items-center justify-between mb-1">
                  <span className="text-[9px] font-bold text-slate-500 uppercase tracking-wider truncate flex-1 min-w-0">{rootFolderPath.split('/').pop()}</span>
                  <div className="flex items-center gap-0.5 shrink-0 ml-1">
                    <button onClick={(e) => { e.stopPropagation(); startTreeCreate(rootFolderPath, 'file'); }} className="p-1 text-slate-600 hover:text-white rounded transition-colors"><Plus size={11} /></button>
                    <button onClick={(e) => { e.stopPropagation(); startTreeCreate(rootFolderPath, 'folder'); }} className="p-1 text-slate-600 hover:text-white rounded transition-colors"><FolderPlus size={11} /></button>
                  </div>
                </div>
                {treeCreating?.parentPath === rootFolderPath && (
                  <div className="flex items-center gap-1.5 px-2 py-[3px]">
                    <Folder size={13} className="text-emerald-400 shrink-0" />
                    <input autoFocus value={treeCreateValue} onChange={e => setTreeCreateValue(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') submitTreeCreate(); if (e.key === 'Escape') setTreeCreating(null); }} onBlur={() => setTreeCreating(null)} className="flex-1 bg-slate-800 text-white text-[12px] px-1.5 py-0.5 rounded border border-emerald-500 outline-none min-w-0" />
                  </div>
                )}
                <FileTree {...props} entries={fileTree} />
              </div>
            ) : (
              <div className="text-center py-8 text-slate-600">
                <FolderOpen size={24} className="mx-auto mb-2 opacity-20" />
                <p className="text-[11px]">{t('sidebar.emptyFolder')}</p>
              </div>
            )}

            {treeContextMenu && (
              <div className="fixed z-50 bg-[#111111] border border-slate-700 rounded-lg shadow-2xl py-1 min-w-[160px] text-[12px]" style={{ left: treeContextMenu.x, top: treeContextMenu.y }} onClick={e => e.stopPropagation()}>
                {treeContextMenu.entry.isDirectory && (
                  <>
                    <button onClick={() => startTreeCreate(treeContextMenu.entry.path, 'file')} className="w-full flex items-center gap-2 px-3 py-1.5 hover:bg-slate-800 text-slate-300 hover:text-white transition-colors"><Plus size={13} /> {t('sidebar.newFile')}</button>
                    <button onClick={() => startTreeCreate(treeContextMenu.entry.path, 'folder')} className="w-full flex items-center gap-2 px-3 py-1.5 hover:bg-slate-800 text-slate-300 hover:text-white transition-colors"><FolderPlus size={13} /> {t('sidebar.newFolder')}</button>
                    <div className="border-t border-slate-700/50 my-1" />
                  </>
                )}
                <button onClick={() => props.startRename(treeContextMenu.entry)} className="w-full flex items-center gap-2 px-3 py-1.5 hover:bg-slate-800 text-slate-300 hover:text-white transition-colors"><Pencil size={13} /> {t('sidebar.rename')}</button>
                <div className="border-t border-slate-700/50 my-1" />
                <button onClick={() => props.handleTreeDelete(treeContextMenu.entry)} className="w-full flex items-center gap-2 px-3 py-1.5 hover:bg-slate-800 text-red-400 hover:text-red-300 transition-colors"><Trash2 size={13} /> {t('sidebar.delete')}</button>
              </div>
            )}
          </div>
        )}
      </div>

      <div className="px-3 py-2.5 border-t border-white/[0.06] flex items-center justify-between min-w-[260px]">
        <div className="flex items-center gap-2 min-w-0">
          <div className="w-6 h-6 rounded-full bg-white/[0.06] border border-white/[0.08] flex items-center justify-center text-[10px] font-bold text-slate-300 shrink-0">
            {(user.name || '?').charAt(0).toUpperCase()}
          </div>
          <span className="text-[11px] text-slate-500 truncate">{user.name}</span>
        </div>
        <button onClick={() => setShowSettings(true)} className="p-2 hover:bg-white/[0.06] text-slate-500 hover:text-slate-200 rounded-lg transition-all shrink-0"><Settings size={14} /></button>
      </div>
    </motion.aside>
  );
};
