/**
 * Desktop side of the web remote's IPC (docs/remote-control-web.md, "Desktop
 * side"). A paired browser's `ui_ipc` reaches this renderer as a `remote_ipc`
 * frame on /wake-stream-all (Backend/app/remote/desktop_channel.py); the
 * renderer holds `window.ipc`, so it runs the call and posts the answer back.
 * Its own copy of the allow-list is checked too: the backend already refused
 * everything else, but a frame that slipped through must not reach main.
 */

import axios from 'axios';
import { toBase64 } from './transport';

const ALLOW = new Set([
  'read-file', 'read-model-file', 'read-image-file', 'write-file',
  'create-file', 'create-folder', 'delete-file', 'delete-entry', 'rename-entry', 'move-entry',
  'read-directory', 'file-exists', 'path-exists', 'git-status', 'workspace-info',
  'backend-workspace-path', 'host-workspace-path',
  'terminal-spawn', 'terminal-write', 'terminal-resize',
  'computer-use-live', 'browser-live', 'computer-use',
]);
const OPS: Record<string, Set<string>> = {
  // `status` answers with the agent's page only; the panel's `panel-status` (every tab) is not on this list.
  // The settings ops are the agent browser's on/off, card switches and browsing data: sites, history, their removal and clear (owner decision 4 Nov 2026).
  'browser-live': new Set(['status', 'halt', 'answer-card', 'settings', 'enable', 'disable', 'cards-off', 'cards-on',
    'sites', 'history', 'clear-browsing-data', 'site-remove', 'history-remove', 'history-clear']),
  // `remote-panel-visible` is what a browser's `panel-visible` becomes (remoteIpcCall); the raw op stays out,
  // so a browser never moves the desktop panel's own flag.
  'computer-use-live': new Set(['status', 'halt', 'halted', 'remote-panel-visible']),
  // Computer use's switches, app grants and driver restart; the ops that open native dialogs on the PC stay out.
  'computer-use': new Set(['status', 'enable', 'disable', 'cards-off', 'cards-on', 'remove-grant', 'restart']),
};
const EVENTS = new Set(['browser-live', 'computer-use-live', 'open-conversation']);
const TERMINAL_EVENT = /^(terminal-data|terminal-exit)-[A-Za-z0-9_-]{1,64}$/;
const REQUEST_ID = /^[0-9a-f]{16}$/;

export function remoteIpcAllowed(channel: string, args: unknown[]): boolean {
  if (!ALLOW.has(channel)) return false;
  const ops = OPS[channel];
  if (!ops) return true;
  const first = args[0] as Record<string, unknown> | undefined;
  return !!first && typeof first === 'object' && typeof first.op === 'string' && ops.has(first.op);
}

const LIVE = 'computer-use-live';

/**
 * The call as main should get it: a browser's Screen tab reports `panel-visible`, which main keeps
 * apart from the desktop panel's (`remote-panel-visible`, its own flag, cleared when the last remote
 * subscription ends). Every other call is unchanged.
 */
export function remoteIpcCall(channel: string, args: unknown[]): unknown[] {
  const first = args[0] as Record<string, unknown> | undefined;
  if (channel !== LIVE || !first || typeof first !== 'object' || first.op !== 'panel-visible') return args;
  return [{ op: 'remote-panel-visible', visible: first.visible }, ...args.slice(1)];
}

/** No browser watches the live view any more: main must not keep capturing for one. */
function clearRemoteVisible(): void {
  try {
    void Promise.resolve(desktopIpc()?.invoke?.(LIVE, { op: 'remote-panel-visible', visible: false })).catch(() => {});
  } catch { /* the desktop bridge is gone */ }
}

export function remoteEventAllowed(channel: string): boolean {
  return EVENTS.has(channel) || TERMINAL_EVENT.test(channel);
}

export interface RemoteIpcCall {
  requestId: string;
  channel: string;
  args: unknown[];
}

export function parseRemoteIpc(data: unknown): RemoteIpcCall | null {
  if (!data || typeof data !== 'object') return null;
  const d = data as Record<string, unknown>;
  if (d.type !== 'remote_ipc') return null;
  if (typeof d.request_id !== 'string' || !REQUEST_ID.test(d.request_id)) return null;
  if (typeof d.channel !== 'string' || !Array.isArray(d.payload)) return null;
  return { requestId: d.request_id, channel: d.channel, args: d.payload };
}

function desktopIpc(): any {
  return typeof window !== 'undefined' ? (window as any).ipc : null;
}

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

// One posted answer stays under the relay's reply room (ui_dispatch.py REPLY_ROOM is 695_904 bytes).
const REPLY_MAX = 600_000;
const FAVICON_MAX = 2048;

/** A remote `history`/`sites` reply without big favicon data URLs and, if still too large, with the oldest rows cut (`truncated: true`). */
export function fitBrowsingReply(result: unknown): unknown {
  if (!result || typeof result !== 'object') return result;
  const reply = { ...(result as Record<string, unknown>) };
  const key = Array.isArray(reply.entries) ? 'entries' : Array.isArray(reply.sites) ? 'sites' : null;
  if (!key) return result;
  const rows = (reply[key] as unknown[]).map(row => {
    const favicon = (row as any)?.favicon;
    return typeof favicon === 'string' && favicon.startsWith('data:') && favicon.length > FAVICON_MAX ? { ...(row as object), favicon: '' } : row;
  });
  const fits = (n: number) => byteLength(JSON.stringify({ ok: true, result: { ...reply, [key]: rows.slice(0, n), ...(n < rows.length ? { truncated: true } : {}) } })) <= REPLY_MAX;
  let lo = 0;
  let hi = rows.length;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if (fits(mid)) lo = mid; else hi = mid - 1;
  }
  reply[key] = rows.slice(0, lo);
  if (lo < rows.length) reply.truncated = true;
  return reply;
}

export async function runRemoteIpc(call: RemoteIpcCall, api: string, token: string): Promise<void> {
  let answer: { ok: boolean; result?: unknown; error?: string };
  const ipc = desktopIpc();
  const args = remoteIpcCall(call.channel, call.args);
  if (!remoteIpcAllowed(call.channel, args)) {
    answer = { ok: false, error: 'remote_unavailable' };
  } else if (!ipc) {
    answer = { ok: false, error: 'desktop_not_ready' };
  } else {
    try {
      const result = await ipc.invoke(call.channel, ...args) ?? null;
      const op = (call.args[0] as Record<string, unknown>)?.op;
      answer = { ok: true, result: call.channel === 'browser-live' && (op === 'history' || op === 'sites') ? fitBrowsingReply(result) : result };
    } catch (err) {
      answer = { ok: false, error: errorText(err) };
    }
  }
  try {
    await axios.post(`${api}/remote/ui-ipc-result/${call.requestId}`, answer,
      { headers: { 'X-Session-Token': token } });
  } catch (err) {
    console.warn('[remote] posting an IPC result failed', err);
  }
}

// channel -> the listener's unsubscribe. One listener per channel however
// many browsers asked; the backend fans the events out.
const subscriptions = new Map<string, () => void>();

function unsubscribe(channel: string): void {
  const off = subscriptions.get(channel);
  subscriptions.delete(channel);
  off?.();
  if (off && channel === LIVE) clearRemoteVisible();
}

/** The phone sees the agent's page only, as in `status`: the panel's other tabs (their addresses, titles, icons) stay on the desktop. */
export function remoteEventArgs(channel: string, args: unknown[]): unknown[] {
  if (channel !== 'browser-live') return args;
  const live = (args[0] ?? {}) as Record<string, any>;
  const agent = Array.isArray(live.tabs) ? live.tabs.find((tab: any) => tab?.agent) : null;
  const reduced: Record<string, unknown> = {
    active: !!agent, conversationId: live.conversationId, url: agent?.url ?? '', title: agent?.title ?? '',
    loading: agent?.loading ?? false, userControl: live.userControl ?? false,
  };
  if (typeof live.error === 'string') reduced.error = live.error;
  return [reduced];
}

// A forwarded computer-use-live event stays under the relay's frame cap (session.py FRAME_PLAINTEXT_MAX is
// 700_000 bytes); the desktop's own window keeps the full PNG.
const LIVE_EVENT_MAX = 400_000;
const LIVE_SIDES = [1280, 960, 720];
const LIVE_QUALITIES = [0.75, 0.6];
const LIVE_FRAME_GAP_MS = 500;
const LIVE_ENCODE_TIMEOUT_MS = 5000;
const TIMED_OUT = Symbol('encode timed out');
const liveNow = (): number => (typeof performance !== 'undefined' ? performance.now() : Date.now());

const byteLength = (text: string): number => (typeof TextEncoder !== 'undefined' ? new TextEncoder().encode(text).length : text.length);

function dataUrlToBlob(dataUrl: string): Blob | null {
  const m = /^data:([^;,]+);base64,([\s\S]*)$/.exec(dataUrl);
  if (!m) return null;
  try {
    const bin = atob(m[2]);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new Blob([bytes], { type: m[1] });
  } catch {
    return null;
  }
}

async function blobToDataUrl(blob: Blob): Promise<string> {
  return `data:${blob.type || 'image/jpeg'};base64,${toBase64(new Uint8Array(await blob.arrayBuffer()))}`;
}

/** The frame as a JPEG data URL that fits `LIVE_EVENT_MAX` once `wrap` builds the whole posted body around it, or null when no size/quality does. */
export async function downscaleLiveFrame(frame: string, wrap: (frame: string) => unknown): Promise<string | null> {
  const blob = dataUrlToBlob(frame);
  if (!blob || typeof createImageBitmap !== 'function') return null;
  let bitmap: any = null;
  try {
    bitmap = await createImageBitmap(blob);
    const longest = Math.max(bitmap.width, bitmap.height);
    if (!(longest > 0)) return null;
    for (const side of LIVE_SIDES) {
      const scale = Math.min(1, side / longest);
      const w = Math.max(1, Math.round(bitmap.width * scale));
      const h = Math.max(1, Math.round(bitmap.height * scale));
      const canvas: any = typeof OffscreenCanvas !== 'undefined' ? new OffscreenCanvas(w, h) : document.createElement('canvas');
      canvas.width = w;
      canvas.height = h;
      const ctx = canvas.getContext('2d');
      if (!ctx) return null;
      ctx.drawImage(bitmap, 0, 0, w, h);
      for (const quality of LIVE_QUALITIES) {
        const url: string = typeof canvas.convertToBlob === 'function'
          ? await blobToDataUrl(await canvas.convertToBlob({ type: 'image/jpeg', quality }))
          : canvas.toDataURL('image/jpeg', quality);
        if (byteLength(JSON.stringify(wrap(url))) <= LIVE_EVENT_MAX) return url;
      }
    }
    return null;
  } catch {
    return null;
  } finally {
    try { bitmap?.close?.(); } catch { /* already closed */ }
  }
}

/**
 * Forwards computer-use-live events with a downscaled JPEG frame. Per subscription at most one encode
 * runs; a newer frame only replaces the single waiting slot. A finished encode is sent (it is recent)
 * unless the subscription ended, carrying the state of the newest event seen (only `frame` comes from the
 * encode; a newest event with `active: false` drops the frame), and the next waiting frame starts once
 * `LIVE_FRAME_GAP_MS` have passed since the last send (monotonic clock). A state-only event (frame null)
 * goes at once, drops the waiting frame and leaves the encode running. An encode that does not settle in
 * `LIVE_ENCODE_TIMEOUT_MS` counts as failed and sends nothing.
 */
function liveForwarder(channel: string, send: (args: unknown[]) => void): { push: (args: unknown[]) => void; stop: () => void } {
  let alive = true;
  let encoding = false;
  let latest: Record<string, any> = {};
  let lastSent = -Infinity;
  let pending: unknown[] | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const startEncode = async (args: unknown[]) => {
    encoding = true;
    const live = (args[0] ?? {}) as Record<string, any>;
    let jpeg: string | null | typeof TIMED_OUT = null;
    let guard: ReturnType<typeof setTimeout> | undefined;
    try {
      jpeg = await Promise.race([
        downscaleLiveFrame(live.frame, frame => ({ channel, args: [{ ...live, frame }] })),
        new Promise<typeof TIMED_OUT>(resolve => { guard = setTimeout(() => resolve(TIMED_OUT), LIVE_ENCODE_TIMEOUT_MS); }),
      ]);
    } finally {
      clearTimeout(guard);
      encoding = false;
    }
    if (alive && jpeg !== TIMED_OUT && latest.active !== false) {
      lastSent = liveNow();
      send([{ ...latest, frame: jpeg }]);
    }
    schedule();
  };
  const schedule = () => {
    if (!alive || encoding || !pending || timer) return;
    const wait = Math.min(LIVE_FRAME_GAP_MS, Math.max(0, lastSent + LIVE_FRAME_GAP_MS - liveNow()));
    if (wait > 0) {
      timer = setTimeout(() => { timer = null; schedule(); }, wait);
      return;
    }
    const args = pending;
    pending = null;
    void startEncode(args);
  };

  return {
    push(args) {
      if (!alive) return;
      const live = args[0] as Record<string, any> | undefined;
      if (live && typeof live === 'object') latest = live;
      if (!live || typeof live.frame !== 'string') {
        pending = null;
        send(args);
        return;
      }
      pending = args;
      schedule();
    },
    stop() {
      alive = false;
      pending = null;
      if (timer) { clearTimeout(timer); timer = null; }
    },
  };
}

// A reply to an older subscription of the channel must not end the newer one.
let generation = 0;
const generations = new Map<string, number>();

export function subscribeRemoteEvents(channel: string, api: string, token: string): void {
  const ipc = desktopIpc();
  if (!remoteEventAllowed(channel) || subscriptions.has(channel) || !ipc?.on) return;
  const mine = ++generation;
  generations.set(channel, mine);
  let stopped = false;
  const send = (args: unknown[]) => {
    if (stopped) return;
    axios.post(`${api}/remote/ui-ipc-event`, { channel, args }, { headers: { 'X-Session-Token': token } })
      // No browser wants it any more (its session ended): stop listening.
      .then(res => { if (res?.data?.subscribed === false && generations.get(channel) === mine) unsubscribe(channel); })
      .catch(err => console.warn('[remote] forwarding an IPC event failed', err));
  };
  const live = channel === 'computer-use-live' ? liveForwarder(channel, send) : null;
  const off = ipc.on(channel, (...args: unknown[]) => {
    if (live) live.push(args);
    else send(remoteEventArgs(channel, args));
  });
  subscriptions.set(channel, () => { stopped = true; live?.stop(); if (typeof off === 'function') off(); });
}

/** Listens again to the channels browsers still want. The backend asks for it
 *  (`remote_ipc_resync`) when this renderer's wake stream starts: a reload
 *  dropped the listeners, the browsers' subscriptions live on. */
export async function restoreRemoteSubscriptions(api: string, token: string): Promise<void> {
  try {
    const res = await axios.get(`${api}/remote/ui-ipc-subscriptions`, { headers: { 'X-Session-Token': token } });
    const channels = res?.data?.channels;
    if (!Array.isArray(channels)) return;
    // A reload lost the listener that would have cleared it; no browser still watching means none is.
    if (!channels.includes(LIVE)) clearRemoteVisible();
    for (const channel of channels) {
      if (typeof channel === 'string') subscribeRemoteEvents(channel, api, token);
    }
  } catch (err) {
    console.warn('[remote] restoring IPC subscriptions failed', err);
  }
}

/** Handles the remote IPC frames of the wake stream; false for any other. */
export function handleRemoteIpcFrame(data: any, api: string, token: string): boolean {
  if (data?.type === 'remote_ipc') {
    const call = parseRemoteIpc(data);
    if (call) void runRemoteIpc(call, api, token);
    return true;
  }
  if (data?.type === 'remote_ipc_subscribe') {
    if (typeof data.channel === 'string') subscribeRemoteEvents(data.channel, api, token);
    return true;
  }
  if (data?.type === 'remote_ipc_resync') {
    void restoreRemoteSubscriptions(api, token);
    return true;
  }
  return false;
}

export function resetRemoteSubscriptionsForTests(): void {
  for (const channel of [...subscriptions.keys()]) unsubscribe(channel);
}
