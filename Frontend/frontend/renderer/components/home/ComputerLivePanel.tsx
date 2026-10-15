import React, { useEffect, useRef, useState } from 'react';
import { useLang, type TKey } from '../../lib/i18n';

/** Main-process event and invoke channel (main/helpers/computer-use-live.ts). */
export const LIVE_CHANNEL = 'computer-use-live';

export interface LiveAction {
  tool: string | null;
  bundle_id: string | null;
  app_name: string | null;
  pid: number | null;
  window_id: number | null;
  at: number;
  decision: string;
}

export interface LiveState {
  active: boolean;
  running: boolean;
  conversationId: number | null;
  startedAt: number | null;
  last: LiveAction | null;
  target: Pick<LiveAction, 'bundle_id' | 'app_name' | 'pid' | 'window_id'> | null;
  appName: string | null;
  /** Main's Stop did not reach the backend (indicator button or shortcut); the run is still live. */
  haltError: boolean;
}

const EMPTY: LiveState = { active: false, running: false, conversationId: null, startedAt: null, last: null, target: null, appName: null, haltError: false };

const ipcOf = (): any => (typeof window !== 'undefined' ? (window as any).ipc : null);

const isMac = () => typeof navigator !== 'undefined' && /Mac/i.test(navigator.platform || navigator.userAgent || '');
export const haltKeys = () => (isMac() ? '⌘⇧Esc' : 'Ctrl+Shift+Esc');

function toState(raw: any): LiveState {
  if (!raw || typeof raw !== 'object') return EMPTY;
  const last = raw.last && typeof raw.last === 'object' ? (raw.last as LiveAction) : null;
  const target = raw.target && typeof raw.target === 'object' ? raw.target : null;
  const name = (v: unknown) => (typeof v === 'string' && v ? v : null);
  return {
    active: raw.active === true,
    running: raw.running === true,
    conversationId: Number.isInteger(raw.conversationId) ? raw.conversationId : null,
    startedAt: typeof raw.startedAt === 'number' ? raw.startedAt : null,
    last,
    target,
    haltError: raw.haltError === true,
    appName: name(target?.app_name) ?? name(last?.app_name) ?? name(target?.bundle_id) ?? name(last?.bundle_id),
  };
}

/** 83 s -> "01:23"; an hour and more gets an hour field. */
export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const two = (n: number) => String(n).padStart(2, '0');
  return h ? `${h}:${two(m)}:${two(s)}` : `${two(m)}:${two(s)}`;
}

/** A plain-language verb for the driver tool behind the last step. */
export function actionKey(tool: string | null): TKey {
  const name = (tool ?? '').replace(/^computer_/, '');
  if (name.includes('click')) return 'cu.live.act.click';
  if (name === 'type_text' || name === 'set_value') return 'cu.live.act.type';
  if (name === 'scroll') return 'cu.live.act.scroll';
  if (name === 'press_key' || name === 'hotkey') return 'cu.live.act.key';
  if (name === 'drag') return 'cu.live.act.drag';
  if (name === 'launch_app') return 'cu.live.act.launch';
  if (name === 'window_state' || name === 'list_windows' || name === 'zoom' || name === 'list_apps') return 'cu.live.act.look';
  return 'cu.live.act.other';
}

/** A clock that ticks once a second, only while something is live. */
export function useNow(running: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [running]);
  return now;
}

/**
 * Live state from main: one status read on mount, then the event stream. With `frames`, each
 * new frame is decoded off-screen first and swapped in only once it is ready, so the picture
 * never blanks between frames for the same conversation and target.
 */
export function useComputerLive({ frames = false }: { frames?: boolean } = {}) {
  const [state, setState] = useState<LiveState>(EMPTY);
  const [frame, setFrame] = useState<string | null>(null);
  const latest = useRef(0);

  useEffect(() => {
    const ipc = ipcOf();
    if (!ipc) return;
    let alive = true;
    let current = EMPTY;
    const present = (src: string) => {
      const n = ++latest.current;
      const img = typeof Image !== 'undefined' ? new Image() : null;
      if (!img || typeof img.decode !== 'function') { setFrame(src); return; }
      img.src = src;
      img.decode().then(() => { if (alive && n === latest.current) setFrame(src); }, () => {});
    };
    const receive = (raw: any) => {
      if (!alive || !raw || typeof raw !== 'object') return;
      const next = toState(raw);
      const changed = next.conversationId !== current.conversationId
        || next.target?.pid !== current.target?.pid
        || next.target?.window_id !== current.target?.window_id
        || next.target?.app_name !== current.target?.app_name
        || next.target?.bundle_id !== current.target?.bundle_id
        || next.appName !== current.appName;
      current = next;
      setState(next);
      // Clear the old picture and invalidate its pending decode before accepting a new identity's frame.
      if (!next.active || changed) { latest.current++; setFrame(null); }
      if (next.active && frames && typeof raw.frame === 'string' && raw.frame.startsWith('data:image/')) present(raw.frame);
    };
    Promise.resolve(ipc.invoke?.(LIVE_CHANNEL, { op: 'status' }))
      .then((raw: any) => { if (raw && !raw.error) receive(raw); })
      .catch(() => {});
    const off = ipc.on?.(LIVE_CHANNEL, receive);
    return () => { alive = false; if (typeof off === 'function') off(); };
  }, [frames]);

  return { state, frame };
}

/** Stop from the panel: main's single halt coordinator, shared with the indicator and the shortcut. */
async function haltFromRenderer(): Promise<boolean> {
  const ipc = ipcOf();
  if (!ipc) return false;
  try {
    const result: any = await ipc.invoke(LIVE_CHANNEL, { op: 'halt' });
    return !!result && typeof result === 'object' && !result.error && result.halted === true;
  } catch {
    return false;
  }
}

const STOP_ICON = <svg className="cul-stop-ic" viewBox="0 0 12 12" aria-hidden="true"><rect x="2" y="2" width="8" height="8" rx="1" /></svg>;

/** The right panel's "Bilgisayar" tab: the driven window, live, and a Stop button. */
export function ComputerLivePanel({ visible }: { visible: boolean }) {
  const { t } = useLang();
  const { state, frame } = useComputerLive({ frames: true });
  const now = useNow(state.active);
  const [stopping, setStopping] = useState(false);
  const [stopFailed, setStopFailed] = useState(false);

  useEffect(() => {
    const ipc = ipcOf();
    Promise.resolve(ipc?.invoke?.(LIVE_CHANNEL, { op: 'panel-visible', visible })).catch(() => {});
  }, [visible]);
  useEffect(() => () => {
    Promise.resolve(ipcOf()?.invoke?.(LIVE_CHANNEL, { op: 'panel-visible', visible: false })).catch(() => {});
  }, []);
  useEffect(() => { setStopping(false); setStopFailed(false); }, [state.active, state.conversationId]);

  const current = useRef(state.conversationId);
  current.current = state.conversationId;
  const stop = async () => {
    const conversationId = state.conversationId;
    if (conversationId === null || stopping) return;
    setStopping(true);
    setStopFailed(false);
    const halted = await haltFromRenderer();
    // A result for a run that is no longer shown must not touch the new run's Stop.
    if (current.current !== conversationId) return;
    if (!halted) {
      setStopping(false);
      setStopFailed(true);
    }
  };

  if (!state.active) {
    const off = !state.running;
    return (
      <section className="cul-panel" data-state={off ? 'off' : 'idle'} aria-label={t('ws.tabComputer')}>
        <div className="cul-screen cul-screen-empty">
          <svg className="cul-empty-ic" viewBox="0 0 40 32" aria-hidden="true">
            <rect x="3" y="3" width="34" height="21" rx="1.5" /><path d="M14 29h12M20 24v5" />
            {off && <path d="M8 7l24 13" />}
          </svg>
        </div>
        <p className="cul-empty-title">{t(off ? 'cu.live.off' : 'cu.live.idle')}</p>
        <p className="cul-empty-hint">{t(off ? 'cu.live.offHint' : 'cu.live.idleHint')}</p>
      </section>
    );
  }

  const app = state.appName ?? t('cu.live.unknownApp');
  const elapsed = state.startedAt !== null ? formatElapsed(now - state.startedAt) : '00:00';
  return (
    <section className="cul-panel" data-state="active" aria-label={t('ws.tabComputer')}>
      <header className="cul-head">
        <span className="cul-dot" aria-hidden="true" />
        <span className="cul-kicker">{t('cu.live.kicker')}</span>
        <span className="cul-app" title={app}>{app}</span>
        <span className="cul-time" aria-label={t('cu.live.elapsed')}>{elapsed}</span>
        <button type="button" className="cul-stop" onClick={stop} disabled={stopping}
          title={t('cu.live.stopTitle', { keys: haltKeys() })}>
          {STOP_ICON}<span>{t(stopping ? 'cu.live.stopping' : 'cu.live.stop')}</span>
        </button>
      </header>
      <div className="cul-screen">
        {frame
          ? <img className="cul-frame" src={frame} alt={t('cu.live.frameAlt', { app })} draggable={false} />
          : <span className="cul-waiting">{t('cu.live.waiting')}</span>}
      </div>
      {state.last && (
        <p className="cul-last">
          <span className="cul-last-k">{t('cu.live.lastAction')}</span>
          <span className="cul-last-v">{t(actionKey(state.last.tool))}</span>
        </p>
      )}
      {(stopFailed || state.haltError) && <p className="cul-error" role="alert">{t('cu.live.stopFailed')}</p>}
      <p className="cul-note">{t('cu.live.stopNote')}</p>
    </section>
  );
}
