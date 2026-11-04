import React, { useEffect, useState } from 'react';
import { useLang, type TKey } from '../../lib/i18n';
import { remoteSession } from '../../lib/remote/bootstrap';
import { RemoteError } from '../../lib/remote/transport';
import type { AvailableModels, GenerationMode } from '../home/types';

/** One v1 RPC over the paired link (Backend/app/remote/rpc.py); resolves with its result. */
export type PhoneRequest = (op: string, params: Record<string, unknown>) => Promise<any>;

const linkRequest: PhoneRequest = (op, params) => {
  const session = remoteSession();
  if (!session) return Promise.reject(new RemoteError('not_ready'));
  return session.transport.request(op, params);
};

const MODES: Array<{ id: GenerationMode; label: TKey; desc: TKey }> = [
  { id: 'auto', label: 'mode.auto', desc: 'mode.autoDesc' },
  { id: 'balanced', label: 'mode.balanced', desc: 'mode.balancedDesc' },
  { id: 'step', label: 'mode.step', desc: 'mode.stepDesc' },
];

type View = 'menu' | 'model' | 'effort' | 'mode' | 'confirm-auto';
type ModelPick = { provider_type: string; model_name: string; label: string };

/** The picker's three sources as the desktop picker saves them (ModelSelector): CLI, local, cloud. */
export function modelPicks(models: AvailableModels): ModelPick[] {
  return [
    ...(models.subscription ?? []).map(m => ({ provider_type: 'subscription', model_name: m.id, label: m.name || m.id })),
    ...(models.local ?? []).map(m => ({ provider_type: 'ollama', model_name: m.id, label: m.name || m.id })),
    // A cloud row the account cannot reach, or one only from OpenRouter's open catalogue, is left out.
    ...(models.cloud ?? []).filter(m => m.available !== false && m.verified !== false)
      .map(m => ({ provider_type: m.provider, model_name: m.id, label: m.name || m.id })),
  ];
}

export interface PhoneMenuSheetProps {
  open: boolean;
  onClose: () => void;
  convId: number | null;
  /** GET /available-models, as the page already holds it. */
  models: AvailableModels;
  currentModel: { provider_type: string; model_name: string };
  /** Re-reads the model list (GET /available-models) when its page opens. */
  onOpenModels?: () => void;
  /** The pick was stored on the PC: re-read the chat's model. */
  onModelChanged?: () => void;
  /** Levels the chat's model offers (/effort-capabilities); null or one level = no control. */
  effortLevels: string[] | null;
  thinkingLevel: string;
  /** The PC took the level: this page sends with it too. */
  onEffortChanged: (level: string) => void;
  mode: GenerationMode;
  loading: boolean;
  isCompacting: boolean;
  onCompact: () => void;
  onStop: () => void;
  showToast?: (msg: string, type: 'success' | 'error' | 'warning' | 'info') => void;
  /** Tests pass their own; the page uses the paired link. */
  request?: PhoneRequest;
}

/**
 * The phone's "⋯" bottom sheet. Model, thinking level and approval mode go through the v1 RPCs
 * (`set_model`, `set_effort`, `set_approval_mode`): the desktop routes that change them
 * (/save-ai-config, /approval-mode, approval-mode-set) stay refused for a browser. Compact and Stop
 * are the page's own handlers. Choosing Auto asks first, as the old phone page did.
 */
export function PhoneMenuSheet(props: PhoneMenuSheetProps) {
  const { open, onClose, convId, models, currentModel, effortLevels, thinkingLevel, mode, loading, isCompacting } = props;
  const { t } = useLang();
  const request = props.request ?? linkRequest;
  const [view, setView] = useState<View>('menu');
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (!open) { setView('menu'); setBusy(false); } }, [open]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);
  if (!open) return null;

  const fail = (err: unknown) => {
    const code = err instanceof RemoteError ? err.code : err instanceof Error ? err.message : String(err);
    props.showToast?.(t('phone.sheet.failed', { error: code }), 'error');
  };
  const run = async (op: string, params: Record<string, unknown>, after: (result: any) => void) => {
    if (busy) return;
    setBusy(true);
    try {
      const result = await request(op, params);
      if (result && result.status === 'desktop_not_ready') props.showToast?.(t('phone.sheet.notReady'), 'warning');
      else after(result);
      onClose();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };

  const pickModel = (m: ModelPick) => {
    if (convId == null) return;
    void run('set_model', { chat_id: convId, provider_type: m.provider_type, model_name: m.model_name }, () => props.onModelChanged?.());
  };
  const pickEffort = (level: string) => {
    void run('set_effort', { level }, () => props.onEffortChanged(level));
  };
  const pickMode = (next: GenerationMode) => {
    if (next === mode) { onClose(); return; }
    if (next === 'auto' && view !== 'confirm-auto') { setView('confirm-auto'); return; }
    // The change comes back as an `approval_mode_changed` frame, which moves this page's mode.
    void run('set_approval_mode', { mode: next }, () => {});
  };

  const levels = (effortLevels ?? []).length > 1 ? effortLevels! : [];
  const picks = modelPicks(models);
  const currentPick = picks.find(p => p.provider_type === currentModel.provider_type && p.model_name === currentModel.model_name);
  const currentMode = MODES.find(m => m.id === mode) ?? MODES[2];
  const back = (
    <button type="button" className="phone-sheet-back" onClick={() => setView('menu')} aria-label={t('phone.sheet.backToMenu')}>‹</button>
  );
  const head = (title: string, withBack = true) => (
    <div className="phone-sheet-head">
      {withBack && back}
      <span className="phone-sheet-title">{title}</span>
      <button type="button" className="phone-sheet-x" onClick={onClose} aria-label={t('phone.sheet.close')}>×</button>
    </div>
  );
  const option = (key: string, label: string, selected: boolean, onPick: () => void, desc?: string) => (
    <button key={key} type="button" role="menuitemradio" aria-checked={selected} className="phone-sheet-row" data-row={key}
      disabled={busy} onClick={onPick}>
      <span className="phone-sheet-row-t">{label}{desc && <span className="phone-sheet-row-d">{desc}</span>}</span>
      {selected && <span className="phone-sheet-check" aria-hidden="true">✓</span>}
    </button>
  );

  let body: React.ReactNode;
  if (view === 'model') {
    body = (
      <>
        {head(t('phone.sheet.model'))}
        <div className="phone-sheet-list" role="menu">
          {convId == null
            ? <p className="phone-sheet-note">{t('phone.sheet.noChat')}</p>
            : picks.length === 0
              ? <p className="phone-sheet-note">{t('phone.sheet.noModels')}</p>
              : picks.map(m => option(`${m.provider_type}:${m.model_name}`, m.label,
                m.provider_type === currentModel.provider_type && m.model_name === currentModel.model_name, () => pickModel(m)))}
        </div>
      </>
    );
  } else if (view === 'effort') {
    body = (
      <>
        {head(t('phone.sheet.effort'))}
        <div className="phone-sheet-list" role="menu">
          {levels.map(l => option(l, t(`effort.label.${l}` as TKey), l === thinkingLevel, () => pickEffort(l), t(`effort.desc.${l}` as TKey)))}
        </div>
      </>
    );
  } else if (view === 'mode') {
    body = (
      <>
        {head(t('phone.sheet.mode'))}
        <div className="phone-sheet-list" role="menu">
          {MODES.map(m => option(m.id, t(m.label), m.id === mode, () => pickMode(m.id), t(m.desc)))}
        </div>
      </>
    );
  } else if (view === 'confirm-auto') {
    body = (
      <div className="phone-sheet-confirm" role="alertdialog" aria-labelledby="phone-auto-warning">
        <p id="phone-auto-warning">{t('phone.sheet.autoWarning')}</p>
        <div className="phone-sheet-confirm-acts">
          <button type="button" className="btn btn-ghost" onClick={() => setView('mode')}>{t('confirm.cancel')}</button>
          <button type="button" className="btn btn-primary" data-testid="phone-auto-confirm" disabled={busy}
            onClick={() => pickMode('auto')}>{t('phone.sheet.autoConfirm')}</button>
        </div>
      </div>
    );
  } else {
    const nav = (key: View, label: string, value: string | null, enabled = true) => (
      <button type="button" className="phone-sheet-row" data-row={key} disabled={!enabled}
        onClick={() => { if (key === 'model') props.onOpenModels?.(); setView(key); }}>
        <span className="phone-sheet-row-t">{label}</span>
        {value && <span className="phone-sheet-row-v">{value}</span>}
        <span className="phone-sheet-chev" aria-hidden="true">›</span>
      </button>
    );
    body = (
      <>
        {head(t('phone.menu'), false)}
        <div className="phone-sheet-list">
          {nav('model', t('phone.sheet.model'), currentPick?.label ?? (currentModel.model_name || null), convId != null)}
          {nav('effort', t('phone.sheet.effort'), levels.includes(thinkingLevel) ? t(`effort.label.${thinkingLevel}` as TKey) : null, levels.length > 0)}
          {nav('mode', t('phone.sheet.mode'), t(currentMode.label))}
          <button type="button" className="phone-sheet-row" data-row="compact" disabled={convId == null || isCompacting}
            onClick={() => { onClose(); props.onCompact(); }}>
            <span className="phone-sheet-row-t">{t('phone.sheet.compact')}</span>
            {isCompacting && <span className="phone-sheet-row-v">{t('memory.compacting')}</span>}
          </button>
          <button type="button" className="phone-sheet-row is-stop" data-row="stop" disabled={!loading}
            onClick={() => { onClose(); props.onStop(); }}>
            <span className="phone-sheet-row-t">{t('phone.sheet.stop')}</span>
          </button>
        </div>
      </>
    );
  }

  return (
    <div className="phone-sheet-layer">
      <button type="button" className="phone-sheet-scrim" aria-label={t('phone.sheet.close')} tabIndex={-1} onClick={onClose} />
      <div className="phone-sheet" role="dialog" aria-modal="true" aria-label={t('phone.menu')} data-view={view}>
        {body}
      </div>
    </div>
  );
}
