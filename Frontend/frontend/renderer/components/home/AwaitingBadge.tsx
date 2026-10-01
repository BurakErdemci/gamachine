import React from 'react';
import { useLang } from '../../lib/i18n';

/**
 * Count of chats waiting on a decision; nothing when there are none. The mockup's `.badge` chip:
 * the accent colour comes from the token, and Arena's lexicon swaps the number for "!" in CSS
 * (`.badge-n` / `.badge-bang`), so the count always stays in the label and the DOM.
 */
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
      className={`badge ${className}`.trim()}
    >
      <span className="badge-n">{count}</span>
      <span className="badge-bang" aria-hidden="true">!</span>
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
export const SidebarToggle: React.FC<SidebarToggleProps> = ({ open, onToggle, awaiting }) => {
  const { t } = useLang();
  const label = open ? t('home.sidebarHide') : t('home.sidebarShow');
  return (
    <button
      type="button"
      data-testid="sidebar-toggle"
      onClick={onToggle}
      className="icon-btn"
      aria-label={label}
      title={label}
      aria-pressed={open}
    >
      <svg className="ic" viewBox="0 0 20 20" aria-hidden="true">
        {/* the mockup's panel glyph, divider on the left: "the sidebar" */}
        <rect x="3" y="4" width="14" height="12" rx="1.2" />
        <path d="M7.5 4v12" />
      </svg>
      {!open && <AwaitingBadge count={awaiting} testId="sidebar-toggle-awaiting" />}
    </button>
  );
};
