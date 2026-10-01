import React, { useEffect, useRef, useState } from 'react';

import { useLang, type TKey } from '../../lib/i18n';
import type { GenerationMode } from './types';

// Same three modes, labels and order as GenerationModeSelector (the composer's control), so the
// top bar and the composer can never name a mode differently.
const MODES: Array<{ id: GenerationMode; label: TKey; desc: TKey }> = [
  { id: 'auto', label: 'mode.auto', desc: 'mode.autoDesc' },
  { id: 'balanced', label: 'mode.balanced', desc: 'mode.balancedDesc' },
  { id: 'step', label: 'mode.step', desc: 'mode.stepDesc' },
];

interface ModeChipProps {
  value: GenerationMode;
  onChange: (mode: GenerationMode) => void;
}

/**
 * The top bar's safety-mode chip (mockup `.pick.mode-pick`, "Güvenli otomatik"): shows the
 * approval mode and switches it through the same setter the composer uses.
 */
export const ModeChip: React.FC<ModeChipProps> = ({ value, onChange }) => {
  const { t } = useLang();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const current = MODES.find(m => m.id === value) ?? MODES[0];

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey); };
  }, [open]);

  return (
    <div className="mode-wrap" ref={ref}>
      <button
        type="button"
        className="pick mode-pick"
        data-testid="mode-chip"
        data-mode={current.id}
        aria-haspopup="listbox"
        aria-expanded={open}
        title={t(current.desc)}
        onClick={() => setOpen(o => !o)}
      >
        <svg className="ic" viewBox="0 0 20 20" aria-hidden="true">
          <path d="M10 2.8l5.6 2.2v4.4c0 3.6-2.4 6.2-5.6 7.6-3.2-1.4-5.6-4-5.6-7.6V5z" />
          <path d="M7.6 10l1.7 1.7 3.2-3.4" />
        </svg>
        <span>{t(current.label)}</span>
        <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><path d="M6 8l4 4 4-4" /></svg>
      </button>
      {open && (
        <div className="model-menu mode-menu" role="listbox" aria-label={t('mode.menuTitle')}>
          <div className="model-menu-k">{t('mode.menuTitle')}</div>
          {MODES.map(m => (
            <button
              key={m.id}
              type="button"
              role="option"
              aria-selected={m.id === value}
              className="model-opt"
              onClick={() => { setOpen(false); if (m.id !== value) onChange(m.id); }}
            >
              <span className="model-opt-text">
                <span className="model-opt-name">{t(m.label)}</span>
                <span className="model-opt-sub">{t(m.desc)}</span>
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
};
