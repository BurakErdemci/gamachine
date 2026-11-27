import React, { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { useLang, type TKey } from '../../lib/i18n';

export type SceneMode = 'code' | 'mixed' | 'team';

const MODES: ReadonlyArray<{ id: SceneMode; labelKey: TKey; descKey: TKey }> = [
  { id: 'code', labelKey: 'sceneMode.code', descKey: 'sceneMode.codeDesc' },
  { id: 'mixed', labelKey: 'sceneMode.mixed', descKey: 'sceneMode.mixedDesc' },
  { id: 'team', labelKey: 'sceneMode.team', descKey: 'sceneMode.teamDesc' },
];

const isSceneMode = (v: unknown): v is SceneMode =>
  v === 'code' || v === 'mixed' || v === 'team';

export interface SceneModeChipProps {
  api?: string;
  sessionToken?: string | null;
  workspacePath?: string | null;
  projectName?: string | null;
}

/**
 * Per-project Unity scene writing mode chip (Kod / Karma / Ekip) shown inside the chat title
 * block right after the project name. Hidden until the backend confirms the current workspace
 * is a Unity project; hidden on non-Unity folders, missing credentials, or request failure.
 */
export const SceneModeChip: React.FC<SceneModeChipProps> = ({
  api,
  sessionToken,
  workspacePath,
  projectName,
}) => {
  const { t } = useLang();
  const [visible, setVisible] = useState(false);
  const [mode, setMode] = useState<SceneMode>('mixed');
  const [open, setOpen] = useState(false);
  const [alignEnd, setAlignEnd] = useState(false);

  const wrapRef = useRef<HTMLDivElement>(null);
  const chipRef = useRef<HTMLButtonElement>(null);
  const popRef = useRef<HTMLDivElement>(null);
  const epochRef = useRef(0);
  const saveSeqRef = useRef(0);
  const confirmedModeRef = useRef<SceneMode>('mixed');

  const closePopover = useCallback((returnFocus: boolean) => {
    setOpen(false);
    if (returnFocus) chipRef.current?.focus();
  }, []);

  // Fetch the active workspace's scene mode on mount and whenever workspace/credentials change.
  useEffect(() => {
    const epoch = ++epochRef.current;
    setVisible(false);
    setOpen(false);

    if (!api || !sessionToken || !workspacePath) return;

    let cancelled = false;
    axios
      .get(`${api}/workspace/scene-mode`, {
        headers: { 'X-Session-Token': sessionToken },
      })
      .then((res) => {
        if (cancelled || epochRef.current !== epoch) return;
        const data = res?.data;
        if (data && data.unity === true) {
          const nextMode: SceneMode = isSceneMode(data.mode) ? data.mode : 'mixed';
          confirmedModeRef.current = nextMode;
          setMode(nextMode);
          setVisible(true);
        } else {
          setVisible(false);
        }
      })
      .catch(() => {
        if (cancelled || epochRef.current !== epoch) return;
        setVisible(false);
      });

    return () => {
      cancelled = true;
    };
  }, [api, sessionToken, workspacePath]);

  // Focus the checked radio option when opening and flip alignment if near the stage's right edge.
  useEffect(() => {
    if (!open) {
      setAlignEnd(false);
      return;
    }
    const pop = popRef.current;
    const wrap = wrapRef.current;
    if (pop) {
      const checked = pop.querySelector<HTMLButtonElement>('[role="radio"][aria-checked="true"]');
      const first = pop.querySelector<HTMLButtonElement>('[role="radio"]');
      (checked ?? first)?.focus();
    }
    if (wrap && pop) {
      const stage = wrap.closest('.stage') ?? document.documentElement;
      const stageRect = stage.getBoundingClientRect();
      const wrapRect = wrap.getBoundingClientRect();
      const popWidth = pop.getBoundingClientRect().width || 372;
      setAlignEnd(stageRect.width > 0 && wrapRect.left + popWidth > stageRect.right - 8);
    }
  }, [open]);

  // Close on outside click or document-level Escape while open.
  useEffect(() => {
    if (!open) return;
    const onPointerOutside = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) {
        closePopover(false);
      }
    };
    const onDocKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        closePopover(true);
      }
    };
    document.addEventListener('mousedown', onPointerOutside);
    document.addEventListener('click', onPointerOutside);
    document.addEventListener('keydown', onDocKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerOutside);
      document.removeEventListener('click', onPointerOutside);
      document.removeEventListener('keydown', onDocKeyDown);
    };
  }, [open, closePopover]);

  if (!api || !sessionToken || !workspacePath || !visible) return null;

  const currentMeta = MODES.find((m) => m.id === mode) ?? MODES[1];
  const currentLabel = t(currentMeta.labelKey);
  const chipAriaLabel = t('sceneMode.aria', { mode: currentLabel });
  const scopeName =
    projectName ?? workspacePath?.split(/[\\/]/).filter(Boolean).pop() ?? '';

  const selectMode = (nextMode: SceneMode) => {
    closePopover(true);
    if (nextMode === mode) return;

    const epoch = epochRef.current;
    const saveId = ++saveSeqRef.current;
    const rollbackMode = confirmedModeRef.current;

    setMode(nextMode);
    axios
      .post(
        `${api}/workspace/scene-mode`,
        { mode: nextMode },
        { headers: { 'X-Session-Token': sessionToken } },
      )
      .then((res) => {
        if (epochRef.current !== epoch || saveSeqRef.current !== saveId) return;
        const confirmed = isSceneMode(res?.data?.mode) ? res.data.mode : nextMode;
        confirmedModeRef.current = confirmed;
        setMode(confirmed);
      })
      .catch(() => {
        if (epochRef.current !== epoch || saveSeqRef.current !== saveId) return;
        setMode(rollbackMode);
      });
  };

  const onChipKeyDown = (e: React.KeyboardEvent<HTMLButtonElement>) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      setOpen((prev) => !prev);
    } else if (e.key === 'ArrowDown' && !open) {
      e.preventDefault();
      setOpen(true);
    } else if (e.key === 'Escape' && open) {
      e.preventDefault();
      e.stopPropagation();
      closePopover(true);
    }
  };

  const onPopKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      closePopover(true);
      return;
    }
    if (e.key === 'Tab') {
      closePopover(true);
      return;
    }
    const items = Array.from(
      popRef.current?.querySelectorAll<HTMLButtonElement>('[role="radio"]') ?? [],
    );
    if (!items.length) return;
    const idx = items.indexOf(document.activeElement as HTMLButtonElement);
    let next = -1;
    if (e.key === 'ArrowDown' || e.key === 'ArrowRight') {
      next = (idx + 1) % items.length;
    } else if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') {
      next = idx <= 0 ? items.length - 1 : idx - 1;
    } else if (e.key === 'Home') {
      next = 0;
    } else if (e.key === 'End') {
      next = items.length - 1;
    }
    if (next >= 0) {
      e.preventDefault();
      items[next].focus();
    }
  };

  return (
    <>
      <span className="tb-proj-sep" aria-hidden="true" />
      <div className="scene-mode" ref={wrapRef}>
        <button
          ref={chipRef}
          type="button"
          className="scene-chip"
          aria-haspopup="true"
          aria-expanded={open}
          aria-label={chipAriaLabel}
          title={chipAriaLabel}
          onClick={() => setOpen((prev) => !prev)}
          onKeyDown={onChipKeyDown}
        >
          <svg className="ic scene-ic" viewBox="0 0 20 20" aria-hidden="true">
            <path d="M10 2.5l6.5 3.75v7.5L10 17.5l-6.5-3.75v-7.5zM3.5 6.25L10 10l6.5-3.75M10 10v7.5" />
          </svg>
          <span className="scene-chip-k">{t('sceneMode.prefix')}</span>
          <span className="scene-chip-v">{currentLabel}</span>
          <svg className="ic ic-sm scene-chev" viewBox="0 0 20 20" aria-hidden="true">
            <path d="M6 8l4 4 4-4" />
          </svg>
        </button>

        {open && (
          <div
            ref={popRef}
            className="scene-pop"
            role="radiogroup"
            aria-label={t('sceneMode.title')}
            data-align={alignEnd ? 'end' : undefined}
            onKeyDown={onPopKeyDown}
          >
            <div className="scene-pop-head">
              <span className="scene-pop-k">{t('sceneMode.title')}</span>
              {scopeName && <span className="scene-pop-scope">{scopeName}</span>}
            </div>
            {MODES.map((opt) => {
              const checked = opt.id === mode;
              return (
                <button
                  key={opt.id}
                  type="button"
                  className="scene-opt"
                  role="radio"
                  aria-checked={checked}
                  data-mode={opt.id}
                  onClick={() => selectMode(opt.id)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      selectMode(opt.id);
                    }
                  }}
                >
                  <span className="scene-radio" aria-hidden="true" />
                  <span className="scene-opt-name">{t(opt.labelKey)}</span>
                  <span className="scene-opt-desc">{t(opt.descKey)}</span>
                </button>
              );
            })}
          </div>
        )}
      </div>
    </>
  );
};
