import React, { useEffect, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { useLang } from '../../lib/i18n';
import { Edit3, Trash2 } from 'lucide-react';
import { Conversation, UserData } from './types';
import { platformKeys } from '../../lib/platformKeys';
import type { ConvStatus } from '../../hooks/home/useChat';
import type { UnityMCPStatus } from '../../hooks/home/useAIConfig';
import { STATUS_DOT, familyRootId, mostUrgent, rootsOf } from '../../lib/convFamily';
import { BrandLogo } from './BrandLogo';
import { useUnityLinkPulse } from './UnityMcpToggle';
import { confirmDialog } from '../ui/ConfirmDialog';
import { displayName } from '../../lib/displayName';
import type { AchievementId } from '../../lib/profileStats';
import type { RemoteStatus } from '../../lib/remoteControl';
import { RemoteBadge } from './RemoteBadge';
import { AwaitingBadge } from './AwaitingBadge';
import { useSceneEditorSetting } from '../../lib/sceneEditor';

interface SidebarProps {
  hierarchy?: React.ReactNode;
  onHierarchyVisible?: (visible: boolean) => void;
  isSidebarOpen: boolean;
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
  user: UserData | null;
  userName?: string;
  setShowSettings: (val: boolean) => void;
  handleLogout: () => void;
  /** Drives the brand head's visor blink when Unity connects (mockup round 7). */
  unityStatus?: UnityMCPStatus;
  /** Remote control state for the phone button in the foot; hidden while remote control is off. */
  remoteStatus?: RemoteStatus | null;
  /** The phone button opens the settings screen on "Uzaktan kontrol" (falls back to Settings). */
  onOpenRemote?: () => void;
  /** Level and XP from GET /profile/stats (all-time whatever the range); null until it answers. */
  profileLevel?: { level: number; xp: number; levelXp: number; levelNeed: number; xp_partial?: boolean; lastAch?: AchievementId | null } | null;
  /** The maker profile is on screen (the card is marked active). */
  profileOpen?: boolean;
  /** The card opens the maker profile. */
  onOpenProfile?: () => void;
  /** The guide (Rehber) is on screen: its `?` button in the foot is marked active. */
  guideOpen?: boolean;
  onOpenGuide?: () => void;
}

/** Sidebar width from the mockup (base.css `.app` first track). */
export const SIDEBAR_WIDTH = 240;

// Lamp per chat status: the mockup's `.status-*` classes, coloured by tokens.
const LAMP: Record<ConvStatus, string> = {
  running: 'status-running',
  awaiting: 'status-pending',
  unread: 'status-ok',
};

const baseName = (p: string | null | undefined) => (p ? p.split(/[\\/]/).filter(Boolean).pop() : undefined);

export const Sidebar: React.FC<SidebarProps> = (props) => {
  const {
    isSidebarOpen, conversations, activeConvId,
    selectConversation, createNewConversation, deleteConversation, editingId,
    setEditingId, tempTitle, setTempTitle, saveRename, workspacePath,
    closeWorkspace, user, userName, setShowSettings,
    convStatus, unityStatus, isDirty, remoteStatus, onOpenRemote,
    profileLevel, profileOpen, onOpenProfile, guideOpen, onOpenGuide,
  } = props;

  const { t, lang } = useLang();
  const linked = useUnityLinkPulse(unityStatus);
  const [sceneEditorSetting] = useSceneEditorSetting();
  const editorOn = sceneEditorSetting && unityStatus !== 'off';
  const [sideTab, setSideTab] = useState<'chats' | 'hierarchy'>('chats');
  useEffect(() => { if (!editorOn) setSideTab('chats'); }, [editorOn]);
  useEffect(() => {
    props.onHierarchyVisible?.(editorOn && isSidebarOpen && sideTab === 'hierarchy');
    return () => props.onHierarchyVisible?.(false);
  }, [editorOn, isSidebarOpen, sideTab, props.onHierarchyVisible]);
  // Which list the row being renamed sat in when its input opened (see `inTasks` below).
  const renameListRef = useRef<{ id: number; task: boolean } | null>(null);

  if (!user) return null;
  const name = displayName(userName ?? user.name);

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
  // A row whose status changes while its rename input is open would jump between the two
  // lists, and the move remounts the input (focus and draft lost). It stays put until the
  // rename ends.
  if (editingId == null) renameListRef.current = null;
  const inTasks = (c: Conversation) => {
    if (editingId !== c.id) return isTask(statusOf(c.id));
    if (renameListRef.current?.id !== c.id) renameListRef.current = { id: c.id, task: isTask(statusOf(c.id)) };
    return renameListRef.current.task;
  };
  const tasks = roots.filter(inTasks);
  const chats = roots.filter(c => !inTasks(c));

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
          data-guide={status === 'running' ? 'chat-running' : undefined}
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
      <div className={`sidebar shell${editorOn ? ' has-scene-editor' : ''}`} aria-label={t('sidebar.chats')}>
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

        {editorOn && <div className="side-tabs" role="tablist" aria-label={t('sceneEditor.sidebarTabs')}>
          {(['chats', 'hierarchy'] as const).map(tab => <button key={tab} type="button" className="side-tab" role="tab"
            id={`side-tab-${tab}`} aria-controls={`side-panel-${tab}`} aria-selected={sideTab === tab} tabIndex={sideTab === tab ? 0 : -1}
            onClick={() => setSideTab(tab)} onKeyDown={event => {
              if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
              event.preventDefault();
              const next = sideTab === 'chats' ? 'hierarchy' : 'chats';
              setSideTab(next); document.getElementById(`side-tab-${next}`)?.focus();
            }}>
            {t(tab === 'chats' ? 'sidebar.chats' : 'sceneEditor.hierarchy')}
            {tab === 'chats' && sideTab === 'hierarchy' && <AwaitingBadge count={roots.filter(c => statusOf(c.id) === 'awaiting').length}
              testId="sidebar-tab-awaiting" className="side-tab-mark" />}
          </button>)}
        </div>}
        {editorOn && <div className="side-hierarchy" id="side-panel-hierarchy" role="tabpanel" hidden={sideTab !== 'hierarchy'} aria-labelledby="side-tab-hierarchy">{props.hierarchy}</div>}
        <div className="side-scroll custom-scrollbar" id={editorOn ? 'side-panel-chats' : undefined}
          role={editorOn ? 'tabpanel' : undefined} aria-labelledby={editorOn ? 'side-tab-chats' : undefined}
          hidden={editorOn && sideTab === 'hierarchy'}>
          <button type="button" className="new-chat" data-guide="new-chat" onClick={() => createNewConversation()}>
            <svg className="ic" viewBox="0 0 20 20" aria-hidden="true"><path d="M10 4v12M4 10h12" /></svg>
            <span>{t('sidebar.newChat')}</span>
            <kbd>{platformKeys(t('sidebar.newChatKey'))}</kbd>
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
              <ul className="chat-list" data-guide="chat-list-active">{tasks.map(c => row(c, true))}</ul>
            </>
          )}

          <div className="side-label"><span>{t('sidebar.chats')}</span><span className="side-count">{chats.length}</span></div>
          <ul className="chat-list" data-guide={tasks.length ? undefined : 'chat-list-active'}>{chats.map(c => row(c, false))}</ul>
        </div>

        {/* Level and XP are the profile's own numbers (GET /profile/stats); before the first answer
            the card shows a dash, not a made-up level. Sade hides both (theme-sade.shell.css). */}
        <button
          type="button"
          className={`side-profile${profileOpen ? ' is-active' : ''}`}
          title={profileLevel?.xp_partial ? t('pf.xpPartial') : profileLevel?.lastAch
            ? t('pf.lastAchShort', { ad: t(`pf.ach.${profileLevel.lastAch}.name`) }) : name || undefined}
          aria-current={profileOpen ? 'page' : undefined}
          onClick={onOpenProfile}
          data-testid="sidebar-profile"
          data-guide="side-profile"
        >
          <span className="lvl level" aria-hidden="true"><span className="lvl-n">{profileLevel ? profileLevel.level : '–'}</span></span>
          <svg className="ic side-profile-ic" viewBox="0 0 20 20" aria-hidden="true">
            <circle cx="10" cy="7" r="3.2" />
            <path d="M3.8 17c.8-3.2 3.3-5 6.2-5s5.4 1.8 6.2 5" />
          </svg>
          <span className="side-profile-text">
            <span className="side-profile-name">{t('sidebar.profile')}</span>
            <span className="side-profile-meta">
              <span className="level">{t('sidebar.level', { seviye: profileLevel ? profileLevel.level : '–' })}</span>
              {profileLevel && (
                <span className="xp"> · <span className="num">{t('sidebar.xp', { xp: profileLevel.xp.toLocaleString(lang === 'tr' ? 'tr-TR' : 'en-GB') })}</span></span>
              )}
              <span className="side-profile-plain">{t('sidebar.profilePlain')}</span>
            </span>
            <span className="xp-mini xp" aria-hidden="true">
              <span style={{ width: `${profileLevel ? Math.max(0, Math.min(100, (profileLevel.levelXp / Math.max(1, profileLevel.levelNeed)) * 100)) : 0}%` }} />
            </span>
          </span>
        </button>

        <div className="side-foot">
          <button type="button" className="foot-btn" onClick={() => setShowSettings(true)}>
            <svg className="ic" viewBox="0 0 20 20" aria-hidden="true">
              <circle cx="10" cy="10" r="2.6" />
              <path d="M10 2.5v2.2M10 15.3v2.2M2.5 10h2.2M15.3 10h2.2M4.7 4.7l1.6 1.6M13.7 13.7l1.6 1.6M4.7 15.3l1.6-1.6M13.7 6.3l1.6-1.6" />
            </svg>
            <span>{t('sidebar.settings')}</span>
          </button>
          {/* The guide (round 12b): also F1, /rehber in the composer, Settings > General. */}
          {onOpenGuide && (
            <button
              type="button"
              className={`foot-btn foot-help${guideOpen ? ' is-active' : ''}`}
              data-testid="sidebar-guide"
              data-guide="guide-entry"
              aria-label={t('guide.entry')}
              aria-current={guideOpen ? 'page' : undefined}
              title={t('guide.entryTitle')}
              onClick={onOpenGuide}
            >
              <svg className="ic" viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7" /><path d="M7.9 8a2.2 2.2 0 114.2.9c-.5 1-2.1 1.3-2.1 2.7" /><path d="M10 14.2v.1" /></svg>
            </button>
          )}
          {name && <span className="foot-user" title={name}>{name}</span>}
          <RemoteBadge status={remoteStatus ?? null} onClick={() => (onOpenRemote ? onOpenRemote() : setShowSettings(true))} />
        </div>
      </div>
    </motion.aside>
  );
};
