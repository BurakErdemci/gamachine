import React from 'react';

import { useLang, type TKey } from '../../lib/i18n';
import type { GenerationMode } from './types';

// Same three modes and labels as GenerationModeSelector (the composer's control), so the
// top bar and the composer can never name a mode differently.
const MODES: Array<{ id: GenerationMode; label: TKey; desc: TKey }> = [
  { id: 'auto', label: 'mode.auto', desc: 'mode.autoDesc' },
  { id: 'balanced', label: 'mode.balanced', desc: 'mode.balancedDesc' },
  { id: 'step', label: 'mode.step', desc: 'mode.stepDesc' },
];

interface ModeChipProps {
  value: GenerationMode;
  /** Opens the settings screen on its "Onay modu" page, where the mode is chosen. */
  onOpen: () => void;
}

/**
 * The top bar's approval-mode shield (mockup `.pick.mode-pick[data-set-link="onay"]`): names the
 * current mode; a click opens the settings screen's "Onay modu" page (round 11: the chip's own
 * dropdown went away, the page explains each mode).
 */
export const ModeChip: React.FC<ModeChipProps> = ({ value, onOpen }) => {
  const { t } = useLang();
  const current = MODES.find(m => m.id === value) ?? MODES[0];
  return (
    <button
      type="button"
      className="pick mode-pick"
      data-testid="mode-chip"
      data-guide="mode-chip"
      data-mode={current.id}
      data-set-link="onay"
      title={t(current.desc)}
      onClick={onOpen}
    >
      <svg className="ic" viewBox="0 0 20 20" aria-hidden="true">
        <path d="M10 2.8l5.6 2.2v4.4c0 3.6-2.4 6.2-5.6 7.6-3.2-1.4-5.6-4-5.6-7.6V5z" />
        <path d="M7.6 10l1.7 1.7 3.2-3.4" />
      </svg>
      <span>{t(current.label)}</span>
      <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><path d="M6 8l4 4 4-4" /></svg>
    </button>
  );
};
