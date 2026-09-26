import React, { useEffect, useRef, useState } from 'react';
import { ChevronDown, GitBranchPlus, Pencil, Trash2, X } from 'lucide-react';
import { useLang } from '../../lib/i18n';
import { STATUS_DOT, familyOf, familyRootId, mostUrgent } from '../../lib/convFamily';
import type { Conversation } from './types';
import type { ConvStatus } from '../../hooks/home/useChat';

const StatusDot: React.FC<{ status?: ConvStatus; testId: string }> = ({ status, testId }) => {
  const { t } = useLang();
  if (!status) return null;
  const dot = STATUS_DOT[status];
  return (
    <span
      data-testid={testId}
      role="img"
      title={t(dot.label)}
      aria-label={t(dot.label)}
      className={`w-1.5 h-1.5 rounded-full shrink-0 ${dot.className}`}
    />
  );
};

// An outside mousedown, Escape, leaving the window or resizing it closes a popup.
const useDismiss = (open: boolean, ref: React.RefObject<HTMLElement | null>, close: () => void) => {
  const closeRef = useRef(close);
  closeRef.current = close;
  useEffect(() => {
    if (!open) return;
    const shut = () => closeRef.current();
    const onDown = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) shut(); };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') shut(); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    window.addEventListener('blur', shut);
    window.addEventListener('resize', shut);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
      window.removeEventListener('blur', shut);
      window.removeEventListener('resize', shut);
    };
  }, [open, ref]);
};

const MENU_WIDTH = 168;

interface BranchButtonProps {
  sourceId: number | null;
  /** The source chat is mid-turn or holds an open card. */
  blocked: boolean;
  onBranch: (sourceId: number) => Promise<unknown>;
}

/** "Branch from now" on the chat on screen. */
export const BranchButton: React.FC<BranchButtonProps> = ({ sourceId, blocked, onBranch }) => {
  const { t } = useLang();
  const [pending, setPending] = useState(false);
  if (sourceId == null) return null;
  const disabled = blocked || pending;
  return (
    <button
      type="button"
      data-testid="branch-new"
      disabled={disabled}
      title={t(blocked ? 'branch.newBlocked' : 'branch.new')}
      aria-label={t('branch.new')}
      onClick={async () => {
        setPending(true);
        try { await onBranch(sourceId); } finally { setPending(false); }
      }}
      className="p-1 rounded transition-all shrink-0 text-slate-500 hover:text-slate-300 hover:bg-white/[0.06] disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-slate-500 disabled:cursor-not-allowed"
    >
      <GitBranchPlus size={14} />
    </button>
  );
};

interface ChatTabsProps {
  conversations: Conversation[];
  activeConvId: number | null;
  convStatus?: Record<number, ConvStatus>;
  branchBlocked: boolean;
  onSelect: (conv: Conversation) => void;
  onBranch: (sourceId: number) => Promise<unknown>;
  onClose: (convId: number) => void;
  /** Resolves false when the rename was not stored. */
  onRename?: (convId: number, title: string) => Promise<boolean>;
  /** Branch only; asks for confirmation itself. */
  onDelete?: (convId: number) => Promise<unknown>;
}

/** True when the chat on screen belongs to a family with any branch, visible or not. */
export const hasBranches = (conversations: Conversation[], activeConvId: number | null) =>
  familyOf(conversations, familyRootId(conversations, activeConvId)).branches.length > 0;

/**
 * Tabs for the family of the chat on screen: its root, then its visible
 * branches in creation order. Drawn only once the family has a branch; until
 * then the header carries the lone BranchButton.
 */
export const ChatTabs: React.FC<ChatTabsProps> = ({
  conversations, activeConvId, convStatus, branchBlocked, onSelect, onBranch, onClose, onRename, onDelete,
}) => {
  const { t } = useLang();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const [tabMenu, setTabMenu] = useState<{ id: number; x: number; y: number } | null>(null);
  const tabMenuRef = useRef<HTMLDivElement>(null);
  const [renaming, setRenaming] = useState<{ id: number; value: string } | null>(null);
  // Enter commits and the unmounting input then blurs; only the first counts.
  const renamingRef = useRef(renaming);
  renamingRef.current = renaming;
  const fam = familyOf(conversations, familyRootId(conversations, activeConvId), activeConvId);

  useDismiss(menuOpen, menuRef, () => setMenuOpen(false));
  useDismiss(!!tabMenu, tabMenuRef, () => setTabMenu(null));

  useEffect(() => { if (fam.hidden.length === 0) setMenuOpen(false); }, [fam.hidden.length]);

  // Keyboard users land on the first item.
  useEffect(() => {
    if (tabMenu) tabMenuRef.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
  }, [tabMenu]);

  if (!fam.root || fam.branches.length === 0) return null;
  const root = fam.root;
  const tabs = [root, ...fam.visible];
  const menuConv = tabMenu ? tabs.find(c => c.id === tabMenu.id) ?? null : null;
  const menuIsBranch = !!menuConv && menuConv.id !== root.id;

  const openTabMenu = (e: React.MouseEvent<HTMLElement>, convId: number) => {
    e.preventDefault();
    let { clientX: x, clientY: y } = e;
    // The context-menu key reports no pointer position; anchor under the tab.
    if (x === 0 && y === 0) {
      const r = e.currentTarget.getBoundingClientRect();
      x = r.left; y = r.bottom;
    }
    setTabMenu({ id: convId, x: Math.max(4, Math.min(x, window.innerWidth - MENU_WIDTH - 4)), y });
  };

  const onMenuKey = (e: React.KeyboardEvent) => {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    e.preventDefault();
    const items = Array.from(tabMenuRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);
    const at = items.indexOf(document.activeElement as HTMLElement);
    items[(at + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length]?.focus();
  };

  const commitRename = async () => {
    const r = renamingRef.current;
    if (!r) return;
    renamingRef.current = null;
    setRenaming(null);
    const original = conversations.find(c => c.id === r.id)?.title;
    if (!r.value.trim() || r.value === original || !onRename) return;
    // Refused: the editor comes back with what was typed.
    if (!(await onRename(r.id, r.value))) setRenaming(r);
  };

  return (
    <div className="h-9 border-b border-white/[0.06] flex items-center gap-1 px-2 shrink-0 relative">
      <div role="tablist" aria-label={t('branch.tabs')} className="flex-1 min-w-0 flex items-center gap-0.5 overflow-x-auto no-scrollbar">
        {tabs.map(conv => {
          const active = conv.id === activeConvId;
          const isBranch = conv.id !== fam.root?.id;
          return (
            <div
              key={conv.id}
              data-testid={`chat-tab-${conv.id}`}
              className={`group relative flex items-center gap-1 h-7 pl-2.5 ${isBranch ? 'pr-1' : 'pr-2.5'} rounded-md min-w-[64px] flex-[0_1_150px] transition-colors ${
                active ? 'bg-white/[0.06] text-slate-100' : 'text-slate-500 hover:text-slate-300 hover:bg-white/[0.03]'
              }`}
            >
              {active && <span className="absolute left-2 right-2 bottom-0 h-[2px] rounded-full bg-blue-400/80" />}
              {renaming?.id === conv.id ? (
                <input
                  autoFocus
                  data-testid={`chat-tab-rename-${conv.id}`}
                  aria-label={t('branch.menuRename')}
                  value={renaming.value}
                  onChange={e => setRenaming({ id: conv.id, value: e.target.value })}
                  onFocus={e => e.currentTarget.select()}
                  onKeyDown={e => {
                    if (e.key === 'Enter') void commitRename();
                    if (e.key === 'Escape') { renamingRef.current = null; setRenaming(null); }
                  }}
                  onBlur={() => { void commitRename(); }}
                  className="flex-1 min-w-0 bg-[#000000] text-white text-[11px] px-1.5 py-0.5 rounded border border-blue-500 outline-none"
                />
              ) : (
                <button
                  type="button"
                  role="tab"
                  aria-selected={active}
                  aria-haspopup="menu"
                  title={conv.title}
                  onClick={() => { if (!active) onSelect(conv); }}
                  onContextMenu={e => openTabMenu(e, conv.id)}
                  className="flex-1 min-w-0 flex items-center gap-1.5 text-left"
                >
                  <StatusDot status={convStatus?.[conv.id]} testId={`tab-status-${conv.id}`} />
                  <span className="text-[11px] font-medium truncate">{conv.title}</span>
                </button>
              )}
              {isBranch && (
                <button
                  type="button"
                  data-testid={`chat-tab-close-${conv.id}`}
                  title={t('branch.close')}
                  aria-label={t('branch.close')}
                  onClick={(e) => { e.stopPropagation(); onClose(conv.id); }}
                  className={`p-0.5 rounded shrink-0 text-slate-600 hover:text-slate-300 hover:bg-white/[0.06] transition-opacity ${
                    active ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 focus:opacity-100'
                  }`}
                >
                  <X size={11} />
                </button>
              )}
            </div>
          );
        })}
      </div>

      <BranchButton sourceId={activeConvId} blocked={branchBlocked} onBranch={onBranch} />

      {fam.hidden.length > 0 && (
        <div ref={menuRef} className="relative shrink-0">
          <button
            type="button"
            data-testid="closed-branches"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen(v => !v)}
            className={`flex items-center gap-1 h-7 px-2 rounded-md text-[10px] font-medium whitespace-nowrap transition-colors ${
              menuOpen ? 'bg-white/[0.06] text-slate-200' : 'text-slate-500 hover:text-slate-300 hover:bg-white/[0.03]'
            }`}
          >
            <StatusDot status={mostUrgent(convStatus, fam.hidden.map(c => c.id))} testId="closed-branches-status" />
            {t('branch.closed', { sayi: fam.hidden.length })}
            <ChevronDown size={11} />
          </button>
          {menuOpen && (
            <div role="menu" className="absolute right-0 top-full mt-1 z-30 bg-[#111111] border border-slate-700 rounded-lg shadow-2xl py-1 min-w-[180px] max-w-[260px] max-h-64 overflow-y-auto custom-scrollbar text-[12px]">
              {fam.hidden.map(conv => (
                <button
                  key={conv.id}
                  type="button"
                  role="menuitem"
                  data-testid={`closed-branch-${conv.id}`}
                  title={conv.title}
                  onClick={() => { setMenuOpen(false); onSelect(conv); }}
                  className="w-full flex items-center gap-2 px-3 py-1.5 hover:bg-slate-800 text-slate-300 hover:text-white transition-colors text-left"
                >
                  <StatusDot status={convStatus?.[conv.id]} testId={`closed-status-${conv.id}`} />
                  <span className="truncate">{conv.title}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {tabMenu && menuConv && (
        <div
          ref={tabMenuRef}
          role="menu"
          data-testid="tab-menu"
          aria-label={menuConv.title}
          onKeyDown={onMenuKey}
          onContextMenu={e => e.preventDefault()}
          style={{ left: tabMenu.x, top: tabMenu.y }}
          className="fixed z-50 bg-[#111111] border border-slate-700 rounded-lg shadow-2xl py-1 min-w-[160px] text-[12px]"
        >
          {onRename && (
            <button
              type="button"
              role="menuitem"
              data-testid="tab-menu-rename"
              onClick={() => { setTabMenu(null); setRenaming({ id: menuConv.id, value: menuConv.title }); }}
              className="w-full flex items-center gap-2 px-3 py-1.5 hover:bg-slate-800 focus:bg-slate-800 outline-none text-slate-300 hover:text-white transition-colors"
            >
              <Pencil size={13} /> {t('branch.menuRename')}
            </button>
          )}
          {menuIsBranch && (
            <button
              type="button"
              role="menuitem"
              data-testid="tab-menu-close"
              onClick={() => { setTabMenu(null); onClose(menuConv.id); }}
              className="w-full flex items-center gap-2 px-3 py-1.5 hover:bg-slate-800 focus:bg-slate-800 outline-none text-slate-300 hover:text-white transition-colors"
            >
              <X size={13} /> {t('branch.menuClose')}
            </button>
          )}
          {menuIsBranch && onDelete && (
            <>
              <div className="border-t border-slate-700/50 my-1" />
              <button
                type="button"
                role="menuitem"
                data-testid="tab-menu-delete"
                onClick={() => { setTabMenu(null); void onDelete(menuConv.id); }}
                className="w-full flex items-center gap-2 px-3 py-1.5 hover:bg-slate-800 focus:bg-slate-800 outline-none text-red-400 hover:text-red-300 transition-colors"
              >
                <Trash2 size={13} /> {t('branch.menuDelete')}
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
};
