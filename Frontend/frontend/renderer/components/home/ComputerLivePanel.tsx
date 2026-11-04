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
const isWindows = () => typeof navigator !== 'undefined' && /Win/i.test(navigator.platform || navigator.userAgent || '');
// Must match haltShortcut() in main/helpers/computer-use-live.ts.
export const haltKeys = () => (isMac() ? '⌘⇧Esc' : isWindows() ? 'Ctrl+Alt+Esc' : 'Ctrl+Shift+Esc');

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
    let heard = false;
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
      // An event that beat this reply is newer than it; a late status must not roll the panel back.
      .then((raw: any) => { if (!heard && raw && !raw.error) receive(raw); })
      .catch(() => {});
    const off = ipc.on?.(LIVE_CHANNEL, (raw: any) => { heard = true; receive(raw); });
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

const MAX_ZOOM = 4;
const TAP_ZOOM = 2.5;
const DOUBLE_TAP_MS = 300;
// A finger that moved further than this between down and up was a drag, not a tap.
const TAP_SLOP = 10;

type Pt = { x: number; y: number };
const pointOf = (e: React.PointerEvent): Pt => ({ x: Number(e.clientX) || 0, y: Number(e.clientY) || 0 });
const gap = (a: Pt, b: Pt) => Math.hypot(a.x - b.x, a.y - b.y);

/**
 * Pinch and double-tap zoom for the frame (the phone's Screen tab): a CSS transform on the picture,
 * never an input to the desktop. Two fingers scale between 1x and MAX_ZOOM, one finger pans a zoomed
 * picture, a double tap toggles between 1x and TAP_ZOOM.
 */
function useFrameZoom(enabled: boolean) {
  const [view, setView] = useState({ scale: 1, x: 0, y: 0 });
  const viewRef = useRef(view);
  viewRef.current = view;
  const pointers = useRef(new Map<number, Pt>());
  const pinch = useRef<{ dist: number; scale: number } | null>(null);
  const drag = useRef<{ start: Pt; x: number; y: number; moved: boolean } | null>(null);
  const lastTap = useRef(0);

  const down = (e: React.PointerEvent) => {
    pointers.current.set(e.pointerId, pointOf(e));
    const pts = [...pointers.current.values()];
    if (pts.length === 2) {
      pinch.current = { dist: Math.max(1, gap(pts[0], pts[1])), scale: viewRef.current.scale };
      drag.current = null;
    } else if (pts.length === 1) {
      drag.current = { start: pts[0], x: viewRef.current.x, y: viewRef.current.y, moved: false };
    }
  };
  const move = (e: React.PointerEvent) => {
    if (!pointers.current.has(e.pointerId)) return;
    pointers.current.set(e.pointerId, pointOf(e));
    const pts = [...pointers.current.values()];
    if (pinch.current && pts.length >= 2) {
      const scale = Math.min(MAX_ZOOM, Math.max(1, pinch.current.scale * gap(pts[0], pts[1]) / pinch.current.dist));
      setView(v => (scale === 1 ? { scale: 1, x: 0, y: 0 } : { ...v, scale }));
      return;
    }
    const d = drag.current;
    if (!d) return;
    const p = pointOf(e);
    if (gap(p, d.start) > TAP_SLOP) d.moved = true;
    if (viewRef.current.scale > 1) setView(v => ({ ...v, x: d.x + p.x - d.start.x, y: d.y + p.y - d.start.y }));
  };
  const up = (e: React.PointerEvent) => {
    if (!pointers.current.delete(e.pointerId)) return;
    if (pointers.current.size < 2) pinch.current = null;
    const d = drag.current;
    if (pointers.current.size > 0 || !d) return;
    drag.current = null;
    if (d.moved) { lastTap.current = 0; return; }
    const now = Date.now();
    if (now - lastTap.current <= DOUBLE_TAP_MS) {
      lastTap.current = 0;
      setView(v => (v.scale > 1 ? { scale: 1, x: 0, y: 0 } : { scale: TAP_ZOOM, x: 0, y: 0 }));
    } else {
      lastTap.current = now;
    }
  };
  const cancel = (e: React.PointerEvent) => {
    pointers.current.delete(e.pointerId);
    if (pointers.current.size < 2) pinch.current = null;
    if (pointers.current.size === 0) drag.current = null;
  };

  if (!enabled) return null;
  return {
    zoomed: view.scale > 1,
    screen: {
      onPointerDown: down, onPointerMove: move, onPointerUp: up, onPointerCancel: cancel,
      // The page must not scroll or zoom itself under the fingers.
      style: { touchAction: 'none' } as React.CSSProperties,
    },
    frame: { transform: `translate(${view.x}px, ${view.y}px) scale(${view.scale})`, transformOrigin: 'center center' } as React.CSSProperties,
  };
}

const STOP_ICON = <svg className="cul-stop-ic" viewBox="0 0 12 12" aria-hidden="true"><rect x="2" y="2" width="8" height="8" rx="1" /></svg>;

/**
 * The right panel's "Bilgisayar" tab: the driven window, live, and a Stop button. `zoomable` (the
 * phone's Screen tab) adds pinch and double-tap zoom; `onFrame` hears each new picture shown; a new
 * `resend` value reports the visibility again (the link to the PC came back).
 */
export function ComputerLivePanel({ visible, zoomable = false, onFrame, resend = 0 }: { visible: boolean; zoomable?: boolean; onFrame?: () => void; resend?: number }) {
  const { t } = useLang();
  const { state, frame } = useComputerLive({ frames: true });
  const now = useNow(state.active);
  const zoom = useFrameZoom(zoomable);
  const onFrameRef = useRef(onFrame);
  onFrameRef.current = onFrame;
  useEffect(() => { if (frame) onFrameRef.current?.(); }, [frame]);
  const [stopping, setStopping] = useState(false);
  const [stopFailed, setStopFailed] = useState(false);

  useEffect(() => {
    const ipc = ipcOf();
    Promise.resolve(ipc?.invoke?.(LIVE_CHANNEL, { op: 'panel-visible', visible })).catch(() => {});
  }, [visible, resend]);
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
      <div className="cul-screen" {...(zoom ? { ...zoom.screen, 'data-zoomable': '', 'data-zoomed': zoom.zoomed || undefined } : {})}>
        {frame
          ? <img className="cul-frame" src={frame} alt={t('cu.live.frameAlt', { app })} draggable={false} style={zoom?.frame} />
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
