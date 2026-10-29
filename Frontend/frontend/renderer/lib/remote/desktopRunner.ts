/**
 * Desktop side of the web remote's IPC (docs/remote-control-web.md, "Desktop
 * side"). A paired browser's `ui_ipc` reaches this renderer as a `remote_ipc`
 * frame on /wake-stream-all (Backend/app/remote/desktop_channel.py); the
 * renderer holds `window.ipc`, so it runs the call and posts the answer back.
 * Its own copy of the allow-list is checked too: the backend already refused
 * everything else, but a frame that slipped through must not reach main.
 */

import axios from 'axios';

const ALLOW = new Set([
  'read-file', 'read-model-file', 'read-image-file', 'write-file',
  'create-file', 'create-folder', 'delete-file', 'delete-entry', 'rename-entry', 'move-entry',
  'read-directory', 'file-exists', 'path-exists', 'git-status', 'workspace-info',
  'backend-workspace-path', 'host-workspace-path',
  'terminal-spawn', 'terminal-write', 'terminal-resize',
  'computer-use-live', 'browser-live',
]);
const OPS: Record<string, Set<string>> = {
  'browser-live': new Set(['status', 'halt', 'answer-card']),
  'computer-use-live': new Set(['status', 'halt', 'halted']),
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

export async function runRemoteIpc(call: RemoteIpcCall, api: string, token: string): Promise<void> {
  let answer: { ok: boolean; result?: unknown; error?: string };
  const ipc = desktopIpc();
  if (!remoteIpcAllowed(call.channel, call.args)) {
    answer = { ok: false, error: 'remote_unavailable' };
  } else if (!ipc) {
    answer = { ok: false, error: 'desktop_not_ready' };
  } else {
    try {
      answer = { ok: true, result: await ipc.invoke(call.channel, ...call.args) ?? null };
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
}

export function subscribeRemoteEvents(channel: string, api: string, token: string): void {
  const ipc = desktopIpc();
  if (!remoteEventAllowed(channel) || subscriptions.has(channel) || !ipc?.on) return;
  const off = ipc.on(channel, (...args: unknown[]) => {
    axios.post(`${api}/remote/ui-ipc-event`, { channel, args }, { headers: { 'X-Session-Token': token } })
      // No browser wants it any more (its session ended): stop listening.
      .then(res => { if (res?.data?.subscribed === false) unsubscribe(channel); })
      .catch(err => console.warn('[remote] forwarding an IPC event failed', err));
  });
  subscriptions.set(channel, typeof off === 'function' ? off : () => {});
}

/** Listens again to the channels browsers still want. The backend asks for it
 *  (`remote_ipc_resync`) when this renderer's wake stream starts: a reload
 *  dropped the listeners, the browsers' subscriptions live on. */
export async function restoreRemoteSubscriptions(api: string, token: string): Promise<void> {
  try {
    const res = await axios.get(`${api}/remote/ui-ipc-subscriptions`, { headers: { 'X-Session-Token': token } });
    const channels = res?.data?.channels;
    if (!Array.isArray(channels)) return;
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
