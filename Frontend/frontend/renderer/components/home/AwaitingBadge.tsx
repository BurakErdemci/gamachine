import React from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useLang } from '../../lib/i18n';

/** Amber count of chats waiting on a decision; nothing when there are none. */
export const AwaitingBadge: React.FC<{ count: number; testId: string; className?: string }> = ({ count, testId, className = '' }) => {
  const { t } = useLang();
  if (count <= 0) return null;
  const label = t('sidebar.awaitingCount', { sayi: count });
  return (
    <span
      data-testid={testId}
      role="img"
      title={label}
      aria-label={label}
      className={`min-w-[14px] h-[14px] px-[3px] rounded-full bg-amber-400 text-slate-950 text-[9px] font-bold leading-[14px] text-center shrink-0 ${className}`}
    >
      {count}
    </span>
  );
};

interface SidebarToggleProps {
  open: boolean;
  onToggle: () => void;
  /** Chats awaiting approval off screen; shown only while the sidebar is collapsed. */
  awaiting: number;
}

// Collapsed, the sidebar rows are the only other place a waiting chat shows.
export const SidebarToggle: React.FC<SidebarToggleProps> = ({ open, onToggle, awaiting }) => (
  <button
    type="button"
    data-testid="sidebar-toggle"
    onClick={onToggle}
    className="relative p-1.5 hover:bg-white/[0.06] rounded-lg text-slate-500 hover:text-slate-300 transition-all shrink-0"
  >
    {open ? <ChevronLeft size={16} /> : <ChevronRight size={16} />}
    {!open && <AwaitingBadge count={awaiting} testId="sidebar-toggle-awaiting" className="absolute -top-0.5 -right-0.5" />}
  </button>
);
