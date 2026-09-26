import React, { useEffect, useRef, useState } from 'react';
import { ChevronDown, GitBranchPlus, X } from 'lucide-react';
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
  conversations, activeConvId, convStatus, branchBlocked, onSelect, onBranch, onClose,
}) => {
  const { t } = useLang();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const fam = familyOf(conversations, familyRootId(conversations, activeConvId), activeConvId);

  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setMenuOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [menuOpen]);

  useEffect(() => { if (fam.hidden.length === 0) setMenuOpen(false); }, [fam.hidden.length]);

  if (!fam.root || fam.branches.length === 0) return null;
  const tabs = [fam.root, ...fam.visible];

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
              <button
                type="button"
                role="tab"
                aria-selected={active}
                title={conv.title}
                onClick={() => { if (!active) onSelect(conv); }}
                className="flex-1 min-w-0 flex items-center gap-1.5 text-left"
              >
                <StatusDot status={convStatus?.[conv.id]} testId={`tab-status-${conv.id}`} />
                <span className="text-[11px] font-medium truncate">{conv.title}</span>
              </button>
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
    </div>
  );
};
