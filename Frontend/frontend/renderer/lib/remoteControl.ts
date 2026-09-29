/**
 * Renderer side of phone remote control (docs/remote-control.md, step 4).
 *
 * Every /remote/* call goes through the main process (`remote-control` invoke
 * channel, main/helpers/remote-control.ts): four of them need the UI secret,
 * which page JS never holds, and the keep-awake blocker lives there.
 */

import { useEffect, useRef } from 'react';
import axios from 'axios';
import { cevir, type TKey } from './i18n';
import { stripBidi } from './modelText';

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
  /** Chosen on the phone for this message; absent = the page's own level. */
  effort?: RemoteEffort;
}

/** Every level the registry can return (Backend/app/providers/effort_caps.py
 *  EFFORT_LEVELS: the canonical scale plus `none`, OpenAI API models'). */
export const REMOTE_EFFORTS = ['auto', 'off', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'] as const;
export type RemoteEffort = typeof REMOTE_EFFORTS[number];

const REQUEST_ID = /^[A-Za-z0-9_-]{1,64}$/;

export function parseRemoteMessage(data: any): RemoteMessage | null {
  if (!data || data.type !== 'remote_message' || data.source !== 'phone') return null;
  const conversationId = Number(data.conversation_id);
  if (!Number.isSafeInteger(conversationId) || conversationId <= 0) return null;
  if (typeof data.request_id !== 'string' || !REQUEST_ID.test(data.request_id)) return null;
  if (typeof data.text !== 'string' || !data.text.trim()) return null;
  const deviceName = typeof data.device_name === 'string' ? data.device_name.slice(0, 64) : '';
  // Strict: a level the page does not know is no level. The backend has checked
  // it against the chat's model, so a bad one is a broken frame, not a message.
  let effort: RemoteEffort | undefined;
  if (data.effort !== undefined && data.effort !== null) {
    if (typeof data.effort !== 'string' || !(REMOTE_EFFORTS as readonly string[]).includes(data.effort)) return null;
    effort = data.effort as RemoteEffort;
  }
  return { requestId: data.request_id, conversationId, text: data.text, deviceName, ...(effort ? { effort } : {}) };
}

// Every open renderer stream gets the frame, so each window may see it. The
// first to claim a request id sends it; the others drop it. Within a window a
// Set is enough; across windows of the same origin the claim is written to
// localStorage under a Web Lock, so two windows cannot both read "unclaimed".
//
// When the claim cannot be recorded that way (the write throws, or the lock
// request fails), only the primary window claims, so a message is sent at
// most once. The primary is the holder of a lifetime Web Lock; the next
// waiting window takes it over when the holder closes. Without Web Locks
// (never the case in Electron) windows elect one over a BroadcastChannel: an
// existing primary wins, otherwise the lowest random id heard within
// ELECT_MS. With neither API there is nothing to coordinate with and the
// window treats itself as the only one.
const SEEN_KEY = 'gamachine.remoteMessages.claimed';
const SEEN_TTL_MS = 60 * 60 * 1000;
const SEEN_MAX = 200;
const CLAIM_LOCK = 'gamachine-remote-message-claim';
const PRIMARY_LOCK = 'gamachine-remote-primary';
const PRIMARY_CHANNEL = 'gamachine-remote-primary';
const ELECT_MS = 150;

type ClaimOutcome = 'claimed' | 'taken' | 'unrecorded';

function claimInStorage(requestId: string): ClaimOutcome {
  let seen: Record<string, number> = {};
  try {
    const raw = localStorage.getItem(SEEN_KEY);
    if (raw) seen = JSON.parse(raw) || {};
  } catch { seen = {}; }
  if (seen[requestId]) return 'taken';
  const now = Date.now();
  const kept = Object.entries(seen)
    .filter(([, at]) => typeof at === 'number' && now - at < SEEN_TTL_MS)
    .sort((a, b) => b[1] - a[1])
    .slice(0, SEEN_MAX - 1);
  try {
    localStorage.setItem(SEEN_KEY, JSON.stringify(Object.fromEntries([...kept, [requestId, now]])));
  } catch {
    return 'unrecorded';
  }
  return 'claimed';
}

const locksApi = () => (typeof navigator !== 'undefined' ? (navigator as any).locks : undefined);

/** Per renderer window: its claims and its part in the primary election. */
class WindowContext {
  readonly claimed = new Set<string>();
  private primary = false;
  private decided: Promise<void> | null = null;
  private channel: BroadcastChannel | null = null;
  private readonly id = Math.random().toString(36).slice(2) + Date.now().toString(36);

  async isPrimary(): Promise<boolean> {
    if (!this.decided) this.decided = this.elect();
    await this.decided;
    return this.primary;
  }

  private elect(): Promise<void> {
    const locks = locksApi();
    if (!locks?.request) return this.electWithoutLocks();
    return new Promise<void>(resolve => {
      const holdForever = () => { this.primary = true; resolve(); return new Promise<void>(() => {}); };
      locks.request(PRIMARY_LOCK, { ifAvailable: true }, (lock: unknown) => {
        if (lock) return holdForever();
        resolve();
        // Queue up to take over when the current primary window closes.
        locks.request(PRIMARY_LOCK, holdForever).catch(() => {});
        return undefined;
      }).catch(() => { void this.electWithoutLocks().then(resolve); });
    });
  }

  private electWithoutLocks(): Promise<void> {
    if (typeof BroadcastChannel === 'undefined') {
      this.primary = true;
      return Promise.resolve();
    }
    return this.electOverChannel();
  }

  private electOverChannel(): Promise<void> {
    const channel = new BroadcastChannel(PRIMARY_CHANNEL);
    (channel as any).unref?.(); // Node (tests): do not keep the process alive
    this.channel = channel;
    let deciding = true;
    let primaryHeard: string | null = null;
    const candidates = new Set([this.id]);
    channel.onmessage = (event: MessageEvent) => {
      const msg = event.data;
      if (!msg || typeof msg.id !== 'string') return;
      if (msg.type === 'primary') {
        if (this.primary && msg.id < this.id) this.primary = false; // concurrent win: lower id keeps it
        if (deciding) primaryHeard = msg.id;
      } else if (msg.type === 'candidate') {
        if (this.primary) channel.postMessage({ type: 'primary', id: this.id });
        else if (deciding) {
          candidates.add(msg.id);
          if (msg.reply !== false) channel.postMessage({ type: 'candidate', id: this.id, reply: false });
        }
      } else if (msg.type === 'bye' && !this.primary) {
        this.decided = null; // the primary may be gone: elect again on the next claim
        this.close();
      }
    };
    channel.postMessage({ type: 'candidate', id: this.id });
    return new Promise<void>(resolve => {
      setTimeout(() => {
        deciding = false;
        this.primary = primaryHeard === null && [...candidates].sort()[0] === this.id;
        if (this.primary) {
          channel.postMessage({ type: 'primary', id: this.id });
          if (typeof window !== 'undefined') {
            window.addEventListener('pagehide', () => channel.postMessage({ type: 'bye', id: this.id }));
          }
        }
        resolve();
      }, ELECT_MS);
    });
  }

  close(): void {
    this.channel?.close();
    this.channel = null;
  }
}

let here = new WindowContext();

/** True exactly once per request id, across the windows of this app. */
export async function claimRemoteMessage(requestId: string): Promise<boolean> {
  const ctx = here;
  if (ctx.claimed.has(requestId)) return false;
  ctx.claimed.add(requestId);
  const locks = locksApi();
  let outcome: ClaimOutcome = 'unrecorded';
  if (locks?.request) {
    try {
      outcome = await locks.request(CLAIM_LOCK, () => claimInStorage(requestId));
    } catch {
      if (claimInStorage(requestId) === 'taken') outcome = 'taken';
    }
  } else {
    // No Web Locks: a synchronous read-then-write is the best storage can do.
    outcome = claimInStorage(requestId);
  }
  if (outcome === 'taken') return false;
  if (outcome === 'claimed') return true;
  return ctx.isPrimary();
}

/**
 * Test hook: start a fresh window context (in-window claims and election
 * state), as a second renderer window would have. Earlier contexts keep
 * answering on the channel like the windows they model, unless `closeAll`.
 */
const contexts: WindowContext[] = [];
export function resetRemoteClaimsForTests(closeAll = false): void {
  contexts.push(here);
  if (closeAll) contexts.splice(0).forEach(c => c.close());
  here = new WindowContext();
}

/** `phone:<name>` (bridge device label) -> `<name>`; null for anyone else. */
export function phoneDeviceName(by: unknown): string | null {
  return typeof by === 'string' && by.startsWith('phone:') ? by.slice('phone:'.length) : null;
}

// ── cards a phone answered first (`card_closed` frames on /wake-stream-all) ──

export interface CardClosed {
  cardId: string;
  /** `phone:<name>`, the same label `already_answered.by` carries. */
  by: string;
  decision?: string;
}

/** Same bound as CARD_ID_MAX in Backend/app/agentic/cards.py. */
const CARD_ID_MAX = 1024;

export function parseCardClosed(data: unknown): CardClosed | null {
  if (!data || typeof data !== 'object') return null;
  const d = data as Record<string, unknown>;
  if (d.type !== 'card_closed') return null;
  if (typeof d.card_id !== 'string' || !d.card_id || d.card_id.length > CARD_ID_MAX) return null;
  if (typeof d.by !== 'string') return null;
  return { cardId: d.card_id, by: d.by, decision: typeof d.decision === 'string' ? d.decision : undefined };
}

// ── an approval mode a phone changed (`approval_mode_changed` frames) ────

export interface ModeChanged {
  mode: 'auto' | 'balanced' | 'step';
  previous: string;
  /** Cards the backend approved on the switch (0 unless it was to auto or balanced). */
  approvedPending: number;
  /** `phone:<name>`. */
  by: string;
}

/** Strict on purpose: an unknown mode is ignored, never read as step or auto. */
export function parseModeChanged(data: unknown): ModeChanged | null {
  if (!data || typeof data !== 'object') return null;
  const d = data as Record<string, unknown>;
  if (d.type !== 'approval_mode_changed') return null;
  if (d.mode !== 'auto' && d.mode !== 'balanced' && d.mode !== 'step') return null;
  if (typeof d.by !== 'string' || phoneDeviceName(d.by) === null) return null;
  const pending = Number(d.approved_pending);
  return {
    mode: d.mode,
    previous: typeof d.previous === 'string' ? d.previous : '',
    approvedPending: Number.isSafeInteger(pending) && pending > 0 ? pending : 0,
    by: d.by,
  };
}

// ── a chat model a phone changed (`chat_model_changed` frames) ───────────

export interface ChatModelChanged {
  conversationId: number;
  providerType: string;
  modelName: string;
  /** `phone:<name>`. */
  by: string;
}

/** The backend has stored it already; the frame only says which chat to re-read. */
export function parseChatModelChanged(data: unknown): ChatModelChanged | null {
  if (!data || typeof data !== 'object') return null;
  const d = data as Record<string, unknown>;
  if (d.type !== 'chat_model_changed') return null;
  const conversationId = d.conversation_id;
  if (typeof conversationId !== 'number' || !Number.isSafeInteger(conversationId) || conversationId <= 0) return null;
  if (typeof d.provider_type !== 'string' || !d.provider_type) return null;
  if (typeof d.model_name !== 'string') return null;
  if (typeof d.by !== 'string' || phoneDeviceName(d.by) === null) return null;
  return { conversationId, providerType: d.provider_type, modelName: d.model_name, by: d.by };
}

// Open cards live in two hooks (useChat: a turn's command and question cards;
// useMCPApproval: bridge, note and tray cards) but only useChat reads the
// stream, so each store registers a closer here. A closer drops the card if it
// holds it and says whether it did.
type CardCloser = (cardId: string) => boolean;
const cardClosers = new Set<CardCloser>();

export function onCardClosed(closer: CardCloser): () => void {
  cardClosers.add(closer);
  return () => { cardClosers.delete(closer); };
}

/** True when some store held the card. One failing store does not stop the others. */
export function closeAnsweredCard(cardId: string): boolean {
  let held = false;
  for (const closer of [...cardClosers]) {
    try {
      if (closer(cardId)) held = true;
    } catch (err) {
      console.warn('[remote] closing an answered card failed', err);
    }
  }
  return held;
}

// ── the desktop's effort, shown and set from a phone ─────────────────────
// Effort is one page-level state (`thinkingLevel` in pages/home.tsx), not per
// chat. The renderer owns it: it tells the backend what it has (so a phone can
// show it) and applies a level a phone asks for (`remote_effort` frames).

export interface EffortRequest {
  level: RemoteEffort;
  /** `phone:<name>`. */
  by: string;
}

/** Strict on purpose: a level off the registry's scale is no request. */
export function parseEffortRequest(data: unknown): EffortRequest | null {
  if (!data || typeof data !== 'object') return null;
  const d = data as Record<string, unknown>;
  if (d.type !== 'remote_effort') return null;
  if (typeof d.level !== 'string' || !(REMOTE_EFFORTS as readonly string[]).includes(d.level)) return null;
  if (typeof d.by !== 'string' || phoneDeviceName(d.by) === null) return null;
  return { level: d.level as RemoteEffort, by: d.by };
}

// The stream that carries the frame lives in useChat, the page state in
// home.tsx; like answered cards, the page registers and the stream delivers.
type EffortListener = (request: EffortRequest) => void;
const effortListeners = new Set<EffortListener>();

export function onEffortRequest(listener: EffortListener): () => void {
  effortListeners.add(listener);
  return () => { effortListeners.delete(listener); };
}

export function deliverEffortRequest(request: EffortRequest): void {
  for (const listener of [...effortListeners]) {
    try {
      listener(request);
    } catch (err) {
      console.warn('[remote] applying a phone effort request failed', err);
    }
  }
}

/** Also the retry after a backend that was not up yet or restarted, which
 *  forgets the snapshot: the backend ignores a report that changes nothing. */
export const EFFORT_REPORT_EVERY_MS = 30_000;

export interface RemoteEffortOptions {
  api: string | undefined;
  token: string | undefined;
  level: string;
  /** What the active provider and model accept; null until the registry answered. */
  levels: string[] | null;
  setLevel: (level: any) => void;
  showToast: (message: string, type: any) => void;
}

export function useRemoteEffort({ api, token, level, levels, setLevel, showToast }: RemoteEffortOptions): void {
  const levelsKey = levels ? levels.join(',') : '';
  const known = levels?.includes(level) ?? false;
  useEffect(() => {
    if (!api || !token || !known) return;
    const report = () => {
      axios.put(`${api}/remote/desktop-effort`, { level, levels: levelsKey.split(',') },
        { headers: { 'X-Session-Token': token } }).catch(() => {});
    };
    report();
    const timer = setInterval(report, EFFORT_REPORT_EVERY_MS);
    return () => clearInterval(timer);
  }, [api, token, level, levelsKey, known]);

  const live = useRef({ level, levels, setLevel, showToast });
  live.current = { level, levels, setLevel, showToast };
  useEffect(() => onEffortRequest(request => {
    const now = live.current;
    if (!now.levels?.includes(request.level)) {
      console.warn('[remote] a phone asked for an effort the active model does not offer:', request.level);
      return;
    }
    if (request.level === now.level) return;
    now.setLevel(request.level);
    const cihaz = stripBidi(phoneDeviceName(request.by) || '') || cevir('chat.phoneUnnamed');
    now.showToast(cevir('effort.changedByPhone', {
      cihaz, seviye: cevir(`effort.label.${request.level}` as TKey),
    }), 'info');
  }), []);
}
