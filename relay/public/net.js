// Relay connections for the phone: one-shot pairing socket and the long-lived
// session link. No DOM access, so tests can drive it from node.

import * as C from './crypto.js';

export const PROTOCOL = 'gamachine.v1';
export const CLOSE_TOKEN_DROPPED = 4001;
export const CLOSE_ROOM_RESET = 4006;
export const CLOSE_NO_ROOM = 4008;
const PAIR_TIMEOUT_MS = 330_000; // pair_secret lives 5 min on the PC
const REQUEST_TIMEOUT_MS = 20_000;
// The bridge cuts replies over ~700 KB of plaintext into parts (docs/remote-control.md,
// "Bridge implementation notes"). Its largest reply (50 messages of up to 100k
// characters plus a 1 MiB event ring) needs ~25 parts; these bounds leave room.
export const MAX_REPLY_PARTS = 64;
export const MAX_REPLY_CHARS = 32 * 1024 * 1024; // sum of the sealed `d` strings
const PART_GAP_MS = 20_000; // an incomplete set fails when no part arrives for this long
// relay/worker/room.js LIMITS.phoneFrameMax: the relay closes a socket that sends more.
export const PHONE_FRAME_MAX = 64 * 1024;
const PING_EVERY_MS = 25_000;
const BACKOFF_MS = [1000, 2000, 5000, 10_000, 30_000];

export function wsOrigin(loc) {
  return (loc.protocol === 'https:' ? 'wss://' : 'ws://') + loc.host;
}

// Resolves {deviceId, token, vapidPub, privateKey, publicRaw}; rejects with
// Error(code) where code is no_room | pc_offline | rejected | refused | timeout | bad_reply | <PC reason>.
export async function pair({ origin, parsed, deviceName, onSas, WS = globalThis.WebSocket }) {
  const phone = await C.generateKeyPair();
  const kStatic = await C.staticSecret(phone.privateKey, parsed.pcPub);
  onSas(await C.computeSas(kStatic, parsed.pairSecret));
  const request = await C.buildPairRequest({ phonePubRaw: phone.publicRaw, deviceName, pairSecret: parsed.pairSecret });
  const kPair = await C.derivePairKey(kStatic, parsed.pairSecret);

  return new Promise((resolve, reject) => {
    let settled = false;
    let opened = false;
    const ws = new WS(`${origin}/ws/pair/${parsed.pairId}`, [PROTOCOL]);
    const timer = setTimeout(() => finish(new Error('timeout')), PAIR_TIMEOUT_MS);
    function finish(err, value) {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      try { ws.close(); } catch {}
      if (err) reject(err);
      else resolve(value);
    }
    ws.onopen = () => {
      opened = true;
      ws.send(JSON.stringify(request));
    };
    ws.onmessage = async (ev) => {
      let m;
      try { m = JSON.parse(ev.data); } catch { return; }
      if (m.type === 'no_room') return finish(new Error('no_room'));
      if (m.type === 'pc_offline') return finish(new Error('pc_offline'));
      if (m.type === 'pair_reject') return finish(new Error(typeof m.reason === 'string' ? m.reason : 'rejected'));
      if (m.type !== 'pair_ok') return;
      try {
        const payload = await C.openPairOk(kPair, m);
        finish(null, {
          deviceId: payload.device_id,
          token: payload.token,
          vapidPub: typeof payload.vapid_pub === 'string' ? payload.vapid_pub : null,
          privateKey: phone.privateKey,
          publicRaw: phone.publicRaw,
        });
      } catch {
        finish(new Error('bad_reply'));
      }
    };
    ws.onclose = (ev) => finish(new Error(ev.code === CLOSE_NO_ROOM ? 'no_room' : opened ? 'rejected' : 'refused'));
  });
}

function isObject(v) {
  return v !== null && typeof v === 'object' && !Array.isArray(v);
}

// Joins the parts of one split reply: every list in `result` is concatenated
// in part order, other fields come from the first part that has them.
export function mergeParts(frames) {
  const failed = frames.find((f) => f.ok === false || !isObject(f.result));
  const { part, parts, ...head } = failed || frames[0];
  if (failed) return head;
  const result = {};
  for (const f of frames) {
    for (const [k, v] of Object.entries(f.result)) {
      if (Array.isArray(v)) result[k] = Array.isArray(result[k]) ? result[k].concat(v) : v.slice();
      else if (!(k in result)) result[k] = v;
    }
  }
  return { ...head, result };
}

// Collects the parts of one reply id. add() answers {} (need more),
// {done: reply} or {error: 'bad_reply' | 'too_large'}.
export class ReplyParts {
  constructor({ maxParts = MAX_REPLY_PARTS, maxChars = MAX_REPLY_CHARS } = {}) {
    this.maxParts = maxParts;
    this.maxChars = maxChars;
    this.got = new Map();
    this.total = null;
    this.chars = 0;
  }

  add(obj, size = 0) {
    const { part, parts } = obj;
    if (!Number.isSafeInteger(part) || !Number.isSafeInteger(parts) || part < 1 || part > parts) return { error: 'bad_reply' };
    if (parts > this.maxParts) return { error: 'too_large' };
    if (this.total === null) this.total = parts;
    else if (parts !== this.total) return { error: 'bad_reply' };
    if (this.got.has(part)) return { error: 'bad_reply' };
    this.chars += size;
    if (this.chars > this.maxChars) return { error: 'too_large' };
    this.got.set(part, obj);
    if (this.got.size < this.total) return {};
    return { done: mergeParts([...this.got.keys()].sort((a, b) => a - b).map((k) => this.got.get(k))) };
  }
}

// Status values passed to onStatus: connecting | ready | pc_offline {lastSeen}
// | removed | hello_rejected {reason}.
export class Link {
  constructor({ origin, device, onStatus, onPush, WS = globalThis.WebSocket, timeouts = {} }) {
    this.origin = origin;
    this.requestTimeoutMs = timeouts.request ?? REQUEST_TIMEOUT_MS;
    this.partGapMs = timeouts.partGap ?? PART_GAP_MS;
    this.device = device;
    this.onStatus = onStatus;
    this.onPush = onPush;
    this.WS = WS;
    this.ws = null;
    this.channel = null;
    this.eph = null;
    this.kStatic = null;
    this.stopped = true;
    this.attempt = 0;
    this.seq = 0;
    this.pending = new Map();
    this.inbox = Promise.resolve();
    this.pingTimer = null;
    this.retryTimer = null;
    this.lastPong = 0;
  }

  async start() {
    this.kStatic = await C.staticSecret(this.device.privateKey, this.device.pcPub);
    this.stopped = false;
    this.connect();
  }

  stop() {
    this.stopped = true;
    clearTimeout(this.retryTimer);
    if (this.ws) try { this.ws.close(); } catch {}
  }

  get ready() {
    return this.channel !== null;
  }

  connect() {
    if (this.stopped || this.ws) return;
    this.onStatus('connecting');
    const ws = new this.WS(`${this.origin}/ws/phone/${this.device.pairId}`, [PROTOCOL, 'tok.' + this.device.token]);
    this.ws = ws;
    ws.onopen = () => {
      this.attempt = 0;
      this.lastPong = Date.now();
      this.pingTimer = setInterval(() => this.ping(), PING_EVERY_MS);
      this.inbox = this.inbox.then(() => this.sendHello());
    };
    ws.onmessage = (ev) => {
      let m;
      try { m = JSON.parse(ev.data); } catch { return; }
      // In order: a frame right after hello_ack must see the new channel.
      this.inbox = this.inbox.then(() => this.handle(m)).catch(() => {});
    };
    ws.onclose = (ev) => {
      if (this.ws !== ws) return;
      this.ws = null;
      this.dropSession();
      clearInterval(this.pingTimer);
      if (ev.code === CLOSE_TOKEN_DROPPED || ev.code === CLOSE_ROOM_RESET) {
        this.stopped = true;
        this.onStatus('removed');
        return;
      }
      if (this.stopped) return;
      const delay = BACKOFF_MS[Math.min(this.attempt, BACKOFF_MS.length - 1)];
      this.attempt += 1;
      this.onStatus('connecting');
      this.retryTimer = setTimeout(() => this.connect(), delay);
    };
  }

  // Called when the page becomes visible again: iOS can leave a dead socket
  // behind after backgrounding, so probe it and reconnect if it stays silent.
  wake() {
    if (this.stopped) return;
    if (!this.ws) {
      clearTimeout(this.retryTimer);
      this.attempt = 0;
      this.connect();
      return;
    }
    const sentAt = Date.now();
    this.ping();
    setTimeout(() => {
      if (this.ws && this.lastPong < sentAt) try { this.ws.close(); } catch {}
    }, 5000);
  }

  ping() {
    try { this.ws?.send('{"type":"ping"}'); } catch {}
  }

  dropSession() {
    this.channel = null;
    this.eph = null;
    for (const p of this.pending.values()) p.reject(new Error('disconnected'));
    this.pending.clear();
  }

  async sendHello() {
    const ws = this.ws;
    if (!ws) return;
    this.dropSession();
    const eph = await C.generateKeyPair();
    this.eph = eph;
    const hello = await C.buildHello({
      kStatic: this.kStatic,
      deviceId: this.device.deviceId,
      ephPubRaw: eph.publicRaw,
      t: Math.floor(Date.now() / 1000),
    });
    if (this.ws === ws) ws.send(JSON.stringify(hello));
  }

  async handle(m) {
    if (m.type === 'pong') {
      this.lastPong = Date.now();
      return;
    }
    if (m.type === 'pc_offline') {
      this.dropSession();
      this.onStatus('pc_offline', { lastSeen: m.last_seen ?? null });
      return;
    }
    if (m.type === 'pc_online') {
      // The PC lost every session when it disconnected; start a fresh one.
      await this.sendHello();
      return;
    }
    if (m.type === 'hello_reject') {
      this.dropSession();
      this.onStatus('hello_rejected', { reason: m.reason ?? 'unknown' });
      return;
    }
    if (m.type === 'hello_ack') {
      const eph = this.eph;
      if (!eph) return;
      let ephPc;
      try {
        ephPc = await C.verifyHelloAck({ kStatic: this.kStatic, ephPhonePubRaw: eph.publicRaw, msg: m });
      } catch {
        return; // forged or stale; the real ack may still come
      }
      const keys = await C.deriveSessionKeys(await C.ecdh(eph.privateKey, ephPc), this.kStatic);
      this.channel = await C.Channel.forPhone(keys);
      this.eph = null;
      this.onStatus('ready');
      return;
    }
    if (typeof m.c === 'number' && typeof m.d === 'string' && this.channel) {
      const obj = await this.channel.open(m);
      if (!isObject(obj)) return;
      // Replies carry `ok`, pushes never do. A reply nobody waits for (timed
      // out, or a part of an abandoned set) is dropped, not taken for a push.
      if ('ok' in obj) this.onReply(obj, m.d.length);
      else this.onPush(obj);
    }
  }

  onReply(obj, size) {
    const p = this.pending.get(obj.id);
    if (!p) return;
    if (obj.part === undefined && obj.parts === undefined) return p.resolve(obj);
    p.parts ??= new ReplyParts();
    const r = p.parts.add(obj, size);
    if (r.error) return p.reject(new Error(r.error));
    if (r.done) return p.resolve(r.done);
    p.arm(this.partGapMs);
  }

  // Resolves with the PC's reply object ({id, ok, result} or {id, ok:false, error}).
  async request(type, params = {}) {
    const channel = this.channel;
    const ws = this.ws;
    if (!channel || !ws) throw new Error('not_ready');
    const id = ++this.seq;
    const reply = new Promise((resolve, reject) => {
      let timer = null;
      const entry = {
        parts: null,
        arm: (ms) => {
          clearTimeout(timer);
          timer = setTimeout(() => entry.reject(new Error('timeout')), ms);
        },
        resolve: (v) => { clearTimeout(timer); this.pending.delete(id); resolve(v); },
        reject: (e) => { clearTimeout(timer); this.pending.delete(id); reject(e); },
      };
      entry.arm(this.requestTimeoutMs);
      this.pending.set(id, entry);
    });
    // Sealing is async; chaining keeps frames on the wire in counter order,
    // otherwise the PC would drop an earlier counter that arrives late.
    this.outbox = (this.outbox ?? Promise.resolve()).then(async () => {
      const frame = await channel.seal({ ...params, id, type });
      if (this.channel !== channel || this.ws !== ws) throw new Error('disconnected');
      // The frame is ASCII (a number and base64url), so length is its byte count.
      // Unsent, its counter is skipped, which the PC accepts (only greater counters count).
      const text = JSON.stringify(frame);
      if (text.length > PHONE_FRAME_MAX) throw new Error('too_large');
      ws.send(text);
    }).catch((err) => {
      this.pending.get(id)?.reject(err);
    });
    return reply;
  }
}

// ---------------------------------------------------------------- page logic
// DOM-free so node can test it; app.js only renders what these return.

// Backend/app/remote/rpc.py TEXT_MAX: longer send_message texts are refused.
export const SEND_TEXT_MAX = 20_000;
export const ASK_ON_PC = 'Bu soruyu bilgisayardan cevaplayın';

// turn_end.status comes from the PC's turn-event ring: done | error | stopped.
export function turnEndLine(status) {
  if (!status || status === 'done') return { text: 'Tur bitti', error: false };
  if (status === 'error') return { text: 'Tur hatayla bitti', error: true };
  if (status === 'stopped') return { text: 'Tur durduruldu', error: false };
  return { text: 'Tur bitti (' + status + ')', error: false };
}

// One log line for a non-text event.
export function eventLine(ev) {
  switch (ev.kind) {
    case 'tool_call': return { text: 'Araç: ' + (ev.tool || '?') + (ev.summary ? ' - ' + ev.summary : ''), error: false };
    case 'turn_start': return { text: 'Tur başladı', error: false };
    case 'turn_end': return turnEndLine(ev.status);
    case 'card_opened': return { text: (ev.card_kind === 'question' ? 'Soru kartı açıldı' : 'Onay kartı açıldı') + (ev.tool ? ': ' + ev.tool : ''), error: false };
    case 'card_closed': return { text: 'Kart kapandı', error: false };
    case undefined:
      // split_reply puts {truncated:true} where one item alone would not fit a frame.
      if (ev.truncated) return { text: 'Çok büyük bir olay gösterilemedi', error: false };
  }
  return { text: String(ev.kind || 'olay'), error: false };
}

export function messageText(m) {
  if (typeof m.text !== 'string') return m.truncated ? '(Mesaj telefonda gösterilemeyecek kadar uzun.)' : '';
  return m.truncated ? m.text + ' … (kısaltıldı)' : m.text;
}

// The buttons a card gets, each with the exact answer_card payload the bridge
// accepts (Backend/app/remote/rpc.py answer_card): question cards take
// decision "choice" with the choice id (the option label) or "reject"; a
// question the phone cannot answer (several questions, multi-select) comes
// without `choices` and keeps only Reject. Other cards take approve / reject.
export function cardActions(card) {
  const buttons = [];
  let note = null;
  const pay = (decision, choice) => (choice === undefined ? { card_id: card.card_id, decision } : { card_id: card.card_id, decision, choice });
  if (card.kind === 'question') {
    const choices = Array.isArray(card.choices) ? card.choices : [];
    for (const ch of choices) {
      const id = typeof ch === 'string' ? ch : ch?.id;
      if (typeof id !== 'string') continue;
      const label = typeof ch === 'string' ? ch : ch.label || ch.id;
      buttons.push({ label, payload: pay('choice', id) });
    }
    if (!buttons.length) note = ASK_ON_PC;
  } else {
    buttons.push({ label: 'Onayla', payload: pay('approve') });
  }
  buttons.push({ label: 'Reddet', secondary: true, payload: pay('reject') });
  return { buttons, note };
}

// `by` of a card answer: desktop | phone:<device name> | system (timeout, Stop).
export function answeredBy(by) {
  if (by === 'desktop') return 'bilgisayar';
  if (typeof by === 'string' && by.startsWith('phone:')) return by.slice(6) || 'telefon';
  return typeof by === 'string' && by ? by : 'bilinmiyor';
}

// What the card shows after a failed answer_card; `close` removes the card.
export function answerFailure(error, reply = {}) {
  if (error === 'already_answered') {
    if (reply.by === 'system') return { close: true, note: 'Bu kart zaten kapanmış (süre doldu ya da tur durduruldu).' };
    return { close: true, note: 'Başka cihaz (' + answeredBy(reply.by) + ') cevapladı.' };
  }
  if (error === 'not_found') return { close: true, note: 'Bu kart artık açık değil.' };
  if (error === 'unsupported_on_phone') return { close: false, onlyReject: true, note: ASK_ON_PC };
  return { close: false, note: 'Gönderilemedi: ' + error };
}

// stop replies {status: ok | no_session | error}.
export function stopLine(status) {
  if (status === 'no_session') return 'Çalışan bir tur bulunamadı.';
  if (status === 'error') return 'Durdurulamadı.';
  return 'Durdurma isteği gönderildi.';
}

// chat_changed carries a summary; list_chats leaves out hidden idle chats, so this does too.
export function mergeChat(chats, chat) {
  const rest = chats.filter((c) => c.chat_id !== chat.chat_id);
  return chat.hidden && chat.status === 'idle' ? rest : rest.concat(chat);
}

// Which chat the page shows, and what to do with open_chat replies and
// pushes for it. The bridge registers an open_chat listener only after an
// await, so a close_chat sent while that open is pending can arrive first
// and leave a listener pushing a chat the page no longer shows: such a late
// reply (verdict 'orphan') and pushes for chats not shown ask for close_chat
// again. Every show/hide/load bumps `gen`, so only the newest load renders.
export class ChatView {
  constructor({ now = Date.now, strayEveryMs = 10_000 } = {}) {
    this.shown = null;
    this.gen = 0;
    this.loading = null;
    this.now = now;
    this.strayEveryMs = strayEveryMs;
    this.strayAt = new Map();
    this.reloadAfter = false;
  }

  // Returns the chat that was shown before, if it differs (the caller closes it).
  show(id) {
    const prev = this.shown;
    this.shown = id;
    this.gen += 1;
    this.loading = null;
    this.reloadAfter = false;
    this.strayAt.delete(id);
    return prev !== null && prev !== id ? prev : null;
  }

  hide() {
    const prev = this.shown;
    this.shown = null;
    this.gen += 1;
    this.loading = null;
    this.reloadAfter = false;
    return prev;
  }

  beginLoad() {
    if (this.shown === null) return null;
    this.gen += 1;
    this.loading = { chatId: this.shown, gen: this.gen };
    return this.loading;
  }

  // 'apply' (render it), 'stale' (a newer load of the same chat is coming)
  // or 'orphan' (the chat is no longer shown: send close_chat for it).
  endLoad(token) {
    if (this.loading === token) this.loading = null;
    if (token.gen === this.gen) return 'apply';
    return this.shown === token.chatId ? 'stale' : 'orphan';
  }

  // A push for a chat that is not shown: true when close_chat should be sent
  // (at most once per chat per strayEveryMs).
  stray(chatId) {
    if (chatId === this.shown || typeof chatId !== 'string') return false;
    const t = this.now();
    const last = this.strayAt.get(chatId);
    if (last !== undefined && t - last < this.strayEveryMs) return false;
    if (this.strayAt.size > 200) this.strayAt.clear();
    this.strayAt.set(chatId, t);
    return true;
  }

  // {type:"gap", chat_id}: live events were lost; 'reload' re-opens the chat.
  // During a load the gap may come from the listener that load replaces or
  // from the new one, so the chat is reloaded once more after it (takeReload).
  onGap(chatId) {
    if (chatId !== this.shown) return this.stray(chatId) ? 'close' : 'ignore';
    if (!this.loading) return 'reload';
    this.reloadAfter = true;
    return 'ignore';
  }

  takeReload() {
    const again = !!this.reloadAfter;
    this.reloadAfter = false;
    return again;
  }
}
