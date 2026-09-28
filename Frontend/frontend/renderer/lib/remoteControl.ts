/**
 * Renderer side of phone remote control (docs/remote-control.md, step 4).
 *
 * Every /remote/* call goes through the main process (`remote-control` invoke
 * channel, main/helpers/remote-control.ts): four of them need the UI secret,
 * which page JS never holds, and the keep-awake blocker lives there.
 */

import type { TKey } from './i18n';

export interface RemoteStatus {
  enabled: boolean;
  connected: boolean;
  relay_url: string;
  default_relay_url: string;
  custom_relay: boolean;
  last_error: string | null;
  retry_at: number | null;
  gave_up: boolean;
  devices: number;
  phones_online: number;
  keep_awake: boolean;
  keep_awake_active: boolean;
  pairing?: { offer_expires_at: number | null; pending: boolean };
}

export interface RemoteDevice {
  device_id: string;
  name: string;
  created: number;
  last_seen: number | null;
  online?: boolean;
}

export interface PairOffer { qr_url: string; expires_at: number }
export interface PendingPair { device_name: string; sas: string; source: string; expires_at: number }

export type RemoteResult<T = any> =
  | { ok: true; data: T }
  | { ok: false; status?: number; code: string; message?: string };

export async function remoteCall<T = any>(action: string, arg?: unknown): Promise<RemoteResult<T>> {
  const ipc = typeof window !== 'undefined' ? (window as any).ipc : null;
  if (!ipc?.invoke) return { ok: false, code: 'no_desktop' };
  try {
    return await ipc.invoke('remote-control', action, arg);
  } catch (err) {
    return { ok: false, code: 'ipc_failed', message: (err as Error)?.message };
  }
}

const ERROR_KEYS: Record<string, TKey> = {
  no_desktop: 'remote.err.noDesktop',
  backend_not_ready: 'remote.err.unreachable',
  unreachable: 'remote.err.unreachable',
  remote_off: 'remote.err.remoteOff',
  too_many_devices: 'remote.err.tooManyDevices',
  relay_unreachable: 'remote.err.relayUnreachable',
  no_pending_pairing: 'remote.err.noPending',
  phone_left: 'remote.err.phoneLeft',
  bad_relay_url: 'remote.err.badRelayUrl',
  unknown_device: 'remote.err.unknownDevice',
};

/** The i18n key for a refusal code; relay token failures share one text. */
export function remoteErrorKey(code: string): TKey {
  if (ERROR_KEYS[code]) return ERROR_KEYS[code];
  if (code.startsWith('relay_')) return 'remote.err.relayUnreachable';
  return 'remote.err.generic';
}

// ── phone messages (`remote_message` frames on /wake-stream-all) ─────────

export interface RemoteMessage {
  requestId: string;
  conversationId: number;
  text: string;
  deviceName: string;
}

const REQUEST_ID = /^[A-Za-z0-9_-]{1,64}$/;

export function parseRemoteMessage(data: any): RemoteMessage | null {
  if (!data || data.type !== 'remote_message' || data.source !== 'phone') return null;
  const conversationId = Number(data.conversation_id);
  if (!Number.isSafeInteger(conversationId) || conversationId <= 0) return null;
  if (typeof data.request_id !== 'string' || !REQUEST_ID.test(data.request_id)) return null;
  if (typeof data.text !== 'string' || !data.text.trim()) return null;
  const deviceName = typeof data.device_name === 'string' ? data.device_name.slice(0, 64) : '';
  return { requestId: data.request_id, conversationId, text: data.text, deviceName };
}

// Every open renderer stream gets the frame, so each window may see it. The
// first to claim a request id sends it; the others drop it. Within a window a
// Set is enough; across windows of the same origin the claim is written to
// localStorage under a Web Lock, so two windows cannot both read "unclaimed".
const SEEN_KEY = 'gamachine.remoteMessages.claimed';
const SEEN_TTL_MS = 60 * 60 * 1000;
const SEEN_MAX = 200;
const claimedHere = new Set<string>();

function claimInStorage(requestId: string): boolean {
  let seen: Record<string, number> = {};
  try {
    const raw = localStorage.getItem(SEEN_KEY);
    if (raw) seen = JSON.parse(raw) || {};
  } catch { seen = {}; }
  if (seen[requestId]) return false;
  const now = Date.now();
  const kept = Object.entries(seen)
    .filter(([, at]) => typeof at === 'number' && now - at < SEEN_TTL_MS)
    .sort((a, b) => b[1] - a[1])
    .slice(0, SEEN_MAX - 1);
  try {
    localStorage.setItem(SEEN_KEY, JSON.stringify(Object.fromEntries([...kept, [requestId, now]])));
  } catch {
    // No storage: the in-window Set still holds.
  }
  return true;
}

/** True exactly once per request id, across the windows of this app. */
export async function claimRemoteMessage(requestId: string): Promise<boolean> {
  if (claimedHere.has(requestId)) return false;
  claimedHere.add(requestId);
  const locks = typeof navigator !== 'undefined' ? (navigator as any).locks : undefined;
  if (!locks?.request) return claimInStorage(requestId);
  try {
    return await locks.request('gamachine-remote-message-claim', () => claimInStorage(requestId));
  } catch {
    return claimInStorage(requestId);
  }
}

/** Test hook: forget in-window claims. */
export function resetRemoteClaimsForTests(): void {
  claimedHere.clear();
}

/** `phone:<name>` (bridge device label) -> `<name>`; null for anyone else. */
export function phoneDeviceName(by: unknown): string | null {
  return typeof by === 'string' && by.startsWith('phone:') ? by.slice('phone:'.length) : null;
}
