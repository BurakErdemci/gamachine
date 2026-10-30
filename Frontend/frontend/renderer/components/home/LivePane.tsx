import React from 'react';
import { useLang, type TKey } from '../../lib/i18n';
import type { LiveSide } from '../../lib/workspacePanel';

const SIDES: LiveSide[] = ['bilgisayar', 'tarayici'];
const SIDE_KEY: Record<LiveSide, TKey> = { bilgisayar: 'ws.tabComputer', tarayici: 'ws.tabBrowser' };

interface LivePaneProps {
  side: LiveSide;
  onPick: (side: LiveSide) => void;
  running: Record<LiveSide, boolean>;
  /** Sides drawn at all (feature on or running). One side draws no sub-tab bar. */
  shown: Record<LiveSide, boolean>;
  computer: React.ReactNode;
  browser: React.ReactNode;
}

/**
 * The Canlı tab: the computer-use mirror and the agent browser as two sub-tabs, each with a
 * light while it runs. A shown side stays mounted when it is not the visible one (the browser's
 * native view and the mirror keep their state); a side that is not shown is not rendered.
 */
export const LivePane: React.FC<LivePaneProps> = ({ side, onPick, running, shown, computer, browser }) => {
  const { t } = useLang();
  const sides = SIDES.filter(s => shown[s]);
  const content: Record<LiveSide, React.ReactNode> = { bilgisayar: computer, tarayici: browser };

  const onKey = (e: React.KeyboardEvent) => {
    const i = sides.indexOf(side);
    const next = e.key === 'ArrowRight' ? sides[(i + 1) % sides.length]
      : e.key === 'ArrowLeft' ? sides[(i + sides.length - 1) % sides.length] : null;
    if (!next) return;
    e.preventDefault();
    onPick(next);
    requestAnimationFrame(() => document.getElementById(`live-tab-${next}`)?.focus());
  };

  return (
    <div className="live-pane">
      {sides.length > 1 && (
        <div className="live-tabs" role="tablist" aria-label={t('ws.tabLive')} onKeyDown={onKey}>
          {sides.map(s => (
            <button
              key={s}
              id={`live-tab-${s}`}
              type="button"
              role="tab"
              className="live-tab"
              data-side={s}
              aria-selected={side === s}
              aria-controls={`live-pane-${s}`}
              tabIndex={side === s ? 0 : -1}
              onClick={() => onPick(s)}
            >
              {t(SIDE_KEY[s])}
              {running[s] && <span className="live-dot" data-on="true" role="img" aria-label={t('ws.liveRunning')} />}
            </button>
          ))}
        </div>
      )}
      {sides.map(s => (
        <div key={s} id={`live-pane-${s}`} className="live-side" data-side={s} role={sides.length > 1 ? 'tabpanel' : undefined} hidden={side !== s}>
          {content[s]}
        </div>
      ))}
    </div>
  );
};
