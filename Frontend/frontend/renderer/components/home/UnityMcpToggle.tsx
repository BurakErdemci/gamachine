import React, { useEffect, useId, useRef, useState } from 'react';

import { useLang, type LangContextValue } from '../../lib/i18n';
import type { UnityMCPStatus } from '../../hooks/home/useAIConfig';

type Anahtar = Parameters<LangContextValue['t']>[0];

/** The mockup's five looks (maket/base.css `.unity[data-unity]`). */
export type UnityVisual = 'off' | 'connecting' | 'on' | 'closed' | 'blocked';

/**
 * Real status -> look, words and tooltip.
 *
 * `Record<UnityMCPStatus, …>` on purpose: a new status in the type is a COMPILE error here. The
 * guard has a measured limit, and it is why this component exists: `SettingsModal` used the
 * same pattern and still crashed on `blocked`, because the hook cast `res.data.status as
 * UnityMCPStatus` and let a value outside the type in. `Record` only protects a truthful type.
 *
 * `unknown` takes the "closed" look (hollow knob, no light, no motion): the light means "we know
 * it is live", and we do not. Leaving it green was finding I-2: a failed poll kept the switch on
 * `connected` forever and the user could not see they were attached to a foreign server.
 */
const STATE: Record<UnityMCPStatus, { visual: UnityVisual; word: Anahtar; hint: Anahtar | null; title: Anahtar }> = {
  off: { visual: 'off', word: 'unity.wordOff', hint: 'unity.hintOff', title: 'home.unityOpen' },
  // `blocked` is a stop that needs the user, not a wait, so it never shows the connecting chase.
  blocked: { visual: 'blocked', word: 'unity.wordBlocked', hint: null, title: 'home.unityBlocked' },
  starting: { visual: 'connecting', word: 'unity.wordConnecting', hint: null, title: 'home.unityStarting' },
  running: { visual: 'connecting', word: 'unity.wordConnecting', hint: null, title: 'home.unityConnecting' },
  connected: { visual: 'on', word: 'unity.wordOn', hint: null, title: 'home.unityConnected' },
  unknown: { visual: 'closed', word: 'unity.wordUnknown', hint: 'unity.hintUnknown', title: 'home.unityUnknown' },
};

export const unityVisual = (status: UnityMCPStatus): UnityVisual => STATE[status].visual;

/** How long the "link landed" moment stays on (mockup: replay(..., 'is-linked', 700)). */
export const LINK_PULSE_MS = 700;

/**
 * True for a moment when the status turns `connected` from another KNOWN status. The first
 * status after mount is not a link landing (the app opened onto an existing connection), so it
 * does not pulse. The sidebar's visor blink and the knob's pop both read this, each with its
 * own instance: same input, same timing. Reduced motion is CSS's job (the animation is removed),
 * the class itself is harmless.
 */
export function useUnityLinkPulse(status: UnityMCPStatus | undefined): boolean {
  const [linked, setLinked] = useState(false);
  const previous = useRef<UnityMCPStatus | undefined>(undefined);
  useEffect(() => {
    const before = previous.current;
    previous.current = status;
    if (status !== 'connected' || before === undefined || before === 'connected') return;
    setLinked(true);
    const id = setTimeout(() => setLinked(false), LINK_PULSE_MS);
    return () => clearTimeout(id);
  }, [status]);
  return linked;
}

interface UnityMcpToggleProps {
  status: UnityMCPStatus;
  toggling: boolean;
  /** Reason for `blocked`: comes from the backend and lives with the status. */
  reason: string | null;
  /** A failed toggle attempt; the caller clears it after 6 s. */
  error: string | null;
  onToggle: () => void;
  /** Shown after "Connected"; the backend reports no scene yet, so this is the project name. */
  projectName?: string | null;
}

const PlugGlyph = () => (
  <svg className="ic us-plug" viewBox="0 0 20 20" aria-hidden="true">
    <path d="M7.5 2.5v4M12.5 2.5v4M5 6.5h10v3.2a5 5 0 01-10 0zM10 14.7v2.8" />
  </svg>
);
const LockGlyph = () => (
  <svg className="ic us-lock" viewBox="0 0 20 20" aria-hidden="true">
    <rect x="4.5" y="9" width="11" height="8" rx="1" />
    <path d="M7 9V6.5a3 3 0 016 0V9" />
  </svg>
);

/**
 * The Unity connection switch, the top bar's signature control (mockup round 7-8 `.unity` bay):
 * the switch position is the user's intent, the light is the real state.
 *
 * Kept as its own component because `home.tsx` cannot be rendered in jsdom (Monaco, electron
 * IPC, six hooks): inline, none of these states could be tested in the DOM.
 */
export const UnityMcpToggle: React.FC<UnityMcpToggleProps> = ({
  status,
  toggling,
  reason,
  error,
  onToggle,
  projectName = null,
}) => {
  const { t } = useLang();
  const state = STATE[status];
  const ids = useId();
  const stateId = `${ids}-state`;
  const whyId = `${ids}-why`;
  const bayRef = useRef<HTMLDivElement>(null);
  const linked = useUnityLinkPulse(status);
  // The reason belongs to `blocked` only: one left over from an earlier state is stale.
  const sebep = status === 'blocked' ? reason : null;

  // The reason opens by itself when the block arrives (the old strip showed it without a click,
  // and the user must act on it); "Why?" then closes and reopens it, as in the mockup.
  const [whyOpen, setWhyOpen] = useState(status === 'blocked');
  useEffect(() => { setWhyOpen(status === 'blocked'); }, [status]);
  useEffect(() => {
    if (!whyOpen) return;
    const onDown = (e: MouseEvent) => {
      if (bayRef.current && !bayRef.current.contains(e.target as Node)) setWhyOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setWhyOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [whyOpen]);

  const cls = ['unity', linked ? 'is-linked' : '', whyOpen && status === 'blocked' ? 'why-open' : '']
    .filter(Boolean).join(' ');

  return (
    <div ref={bayRef} className={cls} data-unity={state.visual} data-status={status} data-testid="unity-bay" data-guide="unity-switch">
      <button
        className="unity-switch"
        data-guide="unity-light"
        type="button"
        role="switch"
        // Checked = clicking turns it off. `blocked` is not: a click there retries the start
        // (useAIConfig treats off and blocked alike), so it reads as off to assistive tech.
        aria-checked={status !== 'off' && status !== 'blocked'}
        aria-describedby={sebep ? `${stateId} ${whyId}` : stateId}
        title={t(state.title)}
        onClick={onToggle}
        // `blocked` stays PRESSABLE: after the user closes the conflicting server, this button
        // is their only way to retry.
        disabled={toggling || status === 'starting'}
      >
        <span className="sr-only">{t('unity.switchLabel')}</span>
        <span className="us-track" aria-hidden="true">
          <span className="us-knob"><PlugGlyph /><LockGlyph /></span>
        </span>
        <span className="us-leds" aria-hidden="true"><i /><i /><i /></span>
      </button>
      <span className="unity-read">
        <span className="unity-text" id={stateId} aria-live="polite">
          <span className="unity-k" lang="en">{t('unity.kicker')}</span>{' '}
          <span className="unity-word">{t(state.word)}</span>
        </span>
        {state.visual === 'on' && projectName && <span className="unity-path">{projectName}</span>}
        {state.hint && <span className="unity-hint">{t(state.hint)}</span>}
        {status === 'blocked' && (
          <button
            className="unity-why-btn"
            type="button"
            aria-expanded={whyOpen}
            aria-controls={whyId}
            onClick={(e) => { e.stopPropagation(); setWhyOpen(o => !o); }}
          >
            {t('unity.why')}
          </button>
        )}
      </span>
      {(status === 'blocked' || error) && (
        // Both can be up at once (a toggle on a blocked port gets a 500), so they stack in one
        // column under the bay instead of covering each other.
        <span className="unity-pops">
          {status === 'blocked' && (
            // In the DOM for the whole blocked state, shown by CSS when open / hovered / focused:
            // it follows the status, not a timer.
            <span className="unity-why" id={whyId} role="alert">
              <b>{t('unity.whyTitle')}</b>{' '}
              {/* No nowrap: the reason carries a process name and PID, so it is long. */}
              {sebep || t('home.unityBlocked')}
            </span>
          )}
          {error && <span className="unity-err" role="alert">{error}</span>}
        </span>
      )}
    </div>
  );
};
