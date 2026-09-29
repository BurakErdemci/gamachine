// Relay connections for the phone: one-shot pairing socket and the long-lived
// session link. No DOM access, so tests can drive it from node.

import * as C from './crypto.js';

export const PROTOCOL = 'gamachine.v1';
export const CLOSE_TOKEN_DROPPED = 4001;
export const CLOSE_PAIR_DONE = 4002;
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
    let replied = false;
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
      if (replied) return;
      let m;
      try { m = JSON.parse(ev.data); } catch { return; }
      if (m.type === 'no_room') return finish(new Error('no_room'));
      if (m.type === 'pc_offline') return finish(new Error('pc_offline'));
      if (m.type === 'pair_reject') return finish(new Error(typeof m.reason === 'string' ? m.reason : 'rejected'));
      if (m.type !== 'pair_ok') return;
      // The relay closes the socket (pair_done) right behind the PC's reply,
      // so the close arrives while pair_ok is still being decrypted: from here
      // the reply decides the outcome, not the close.
      replied = true;
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
    ws.onclose = (ev) => {
      if (replied) return;
      // pair_done with no reply handled: the PC answered with nothing readable.
      const code = ev.code === CLOSE_NO_ROOM ? 'no_room' : ev.code === CLOSE_PAIR_DONE ? 'bad_reply' : opened ? 'rejected' : 'refused';
      finish(new Error(code));
    };
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
      const ws = this.ws;
      if (!eph || !ws) return;
      let ephPc;
      try {
        ephPc = await C.verifyHelloAck({ kStatic: this.kStatic, ephPhonePubRaw: eph.publicRaw, msg: m });
      } catch {
        return; // forged or stale; the real ack may still come
      }
      const keys = await C.deriveSessionKeys(await C.ecdh(eph.privateKey, ephPc), this.kStatic);
      const channel = await C.Channel.forPhone(keys);
      // A close during the awaits dropped this session; it must not come back as ready.
      if (this.ws !== ws || this.eph !== eph) return;
      this.channel = channel;
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

// The composer note for a failed send_message.
export function sendFailureNote(error) {
  if (error === 'too_large') return 'Mesaj tek seferde gönderilemeyecek kadar uzun; kısaltıp tekrar dene.';
  return 'Gönderilemedi: ' + error;
}

// A `/compact` sent from the phone runs on the PC and gets no reply beyond
// "accepted" (docs/remote-control.md), so the note says where to look.
export function sentNote(text, status) {
  if (status !== 'accepted') return 'Yanıt: ' + (status || 'bilinmiyor');
  return String(text ?? '').trim() === '/compact'
    ? 'Sıkıştırma isteği bilgisayara iletildi; sonuç bilgisayarda görünür.'
    : 'Gönderildi.';
}

// ---- slash commands (list_slash_commands)

export const SLASH_SHOWN_MAX = 60;

// One row per command or skill: the catalog's `commands` first, then `skills`
// the command list lacks (Codex skills are not commands), each with what
// `meta` says of it. `insert` is what a pick writes into the composer.
export function slashItems(catalog) {
  const meta = new Map();
  for (const m of Array.isArray(catalog?.meta) ? catalog.meta : []) {
    if (m && typeof m.name === 'string') meta.set(m.name, m);
  }
  const names = [];
  const seen = new Set();
  for (const list of [catalog?.commands, catalog?.skills]) {
    for (const name of Array.isArray(list) ? list : []) {
      if (typeof name === 'string' && name && !seen.has(name)) {
        seen.add(name);
        names.push(name);
      }
    }
  }
  return names.map((name) => {
    const m = meta.get(name) || {};
    return {
      name,
      insert: typeof m.insert === 'string' && m.insert ? m.insert : '/' + name + ' ',
      description: typeof m.description === 'string' ? m.description : '',
      hint: typeof m.argumentHint === 'string' ? m.argumentHint : '',
    };
  });
}

// Names starting with the query come first; `total` says how many matched
// when more than SLASH_SHOWN_MAX did.
export function filterSlash(items, query) {
  const q = String(query ?? '').trim().replace(/^\//, '').toLowerCase();
  if (!q) return { shown: items.slice(0, SLASH_SHOWN_MAX), total: items.length };
  const hits = items.filter((i) => i.name.toLowerCase().includes(q) || i.description.toLowerCase().includes(q));
  const first = hits.filter((i) => i.name.toLowerCase().startsWith(q));
  const rest = hits.filter((i) => !i.name.toLowerCase().startsWith(q));
  return { shown: [...first, ...rest].slice(0, SLASH_SHOWN_MAX), total: hits.length };
}

// Where a picked command goes: an empty composer, or a half-typed `/word`,
// is replaced by it; any other text stays and the command follows it.
export function withCommand(current, insert) {
  const text = String(current ?? '');
  if (!text.trim() || /^\s*\/\S*$/.test(text)) return insert;
  return text.replace(/\s+$/, '') + ' ' + insert;
}

export function slashFailureNote(error) {
  if (error === 'not_ready') return 'Önce bilgisayara bağlanmalı.';
  if (error === 'unknown_chat') return 'Bu sohbet artık yok.';
  if (error === 'unavailable') return 'Bilgisayardaki uygulama komut listesini veremedi.';
  return 'Komutlar alınamadı: ' + error;
}

// ---- approval mode (get_config, set_approval_mode)

// Labels and descriptions as the desktop shows them (mode.* in i18n.tsx).
export const APPROVAL_MODES = [
  { id: 'auto', label: 'Otomatik', desc: 'Onay kartı yok — dış AI istemcileri (Claude Code vb.) dahil.' },
  { id: 'balanced', label: 'Güvenli Otomatik', desc: 'Kendi başına çalışır; yalnız kritik işlemlerde onay sorar (önerilen).' },
  { id: 'step', label: 'Adım Adım', desc: 'Her değişiklik için onay kartı çıkar.' },
];
export const AUTO_MODE_WARNING = 'Otomatik modda yapay zekâ dosya yazma, silme, komut çalıştırma ve Unity\'deki her değişikliği sana sormadan yapar; onay kartı hiç çıkmaz. Otomatik moda geçilsin mi?';

export function modeInfo(id) {
  return APPROVAL_MODES.find((m) => m.id === id) || null;
}

export function modeChangedNote(result) {
  const info = modeInfo(result?.mode);
  const n = Number(result?.approved_pending) || 0;
  return 'Mod değişti: ' + (info ? info.label : String(result?.mode)) + '.'
    + (n > 0 ? ' Bekleyen ' + n + ' onay otomatik onaylandı.' : '');
}

// What the mode note says when set_approval_mode failed; the mode did not change.
export function modeFailureNote(error, reply = {}, wanted = '') {
  if (error === 'agy_step_refused') {
    const target = wanted === 'balanced' ? 'Güvenli Otomatik moda' : 'Adım adım onay moduna';
    const pids = reply?.params?.pids || '?';
    return target + ' geçilemedi: agy\'nin onay kapısı güncellenemedi ve çalışan agy süreci durdurulamadı (pid ' + pids
      + '). Mod değişmedi. Bilgisayarda o agy sürecini kapat ya da uygulamayı yeniden başlat, sonra yeniden dene.';
  }
  if (error === 'not_ready') return 'Önce bilgisayara bağlanmalı.';
  if (error === 'bad_mode') return 'Bilinmeyen mod; hiçbir şey değişmedi.';
  if (error === 'unavailable') return 'Bilgisayardaki uygulama modu değiştiremedi.';
  return 'Mod değiştirilemedi: ' + error;
}

// ---- model and effort (get_config, list_models, set_model, set_effort)

// Labels as the desktop shows them (effort.label.* in i18n.tsx), in the
// registry's canonical order.
export const EFFORT_LABELS = {
  auto: 'Auto', off: 'Kapalı', none: 'None', minimal: 'Minimal', low: 'Düşük', medium: 'Orta', high: 'Yüksek', xhigh: 'XHigh', max: 'Max',
};

const knownEffort = (level) => Object.prototype.hasOwnProperty.call(EFFORT_LABELS, level);

export function effortLabel(level) {
  return knownEffort(level) ? EFFORT_LABELS[level] : String(level);
}

// The desktop's own effort as get_config / effort_changed carry it:
// {level, levels, ultracode}. Anything the page cannot show honestly is null,
// which the page words as "unknown". `ultracode` is on only when the PC says so.
export function desktopEffort(value) {
  if (!value || typeof value !== 'object' || typeof value.level !== 'string' || !Array.isArray(value.levels)) return null;
  const levels = value.levels.filter((l) => typeof l === 'string' && knownEffort(l));
  if (!levels.length || !levels.includes(value.level)) return null;
  return { level: value.level, levels, ultracode: value.ultracode === true };
}

// A model is one <option> value: provider and model id, split at the first `|`.
export function modelValue(providerType, modelName) {
  return String(providerType) + '|' + String(modelName ?? '');
}

export function parseModelValue(value) {
  const at = typeof value === 'string' ? value.indexOf('|') : -1;
  if (at < 1) return null;
  return { provider_type: value.slice(0, at), model_name: value.slice(at + 1) };
}

const MODEL_GROUPS = [
  { key: 'subscription', label: 'Abonelik (komut satırı)' },
  { key: 'cloud', label: 'Bulut (API)' },
  { key: 'local', label: 'Yerel' },
];

// The picker's catalog as option groups. The chat's current model is always
// there, even when the catalog does not list it (a typed id, a model gone from
// a live list): the select must show what the chat has. Cloud models the
// account cannot call (`available` is not true) are left out; they could only
// be refused with `not_ready`.
export function modelGroups(catalog, current) {
  const groups = [];
  const seen = new Set();
  const currentValue = current ? modelValue(current.provider_type, current.model_name) : null;
  for (const { key, label } of MODEL_GROUPS) {
    const items = [];
    for (const m of Array.isArray(catalog?.[key]) ? catalog[key] : []) {
      if (!m || typeof m.id !== 'string' || !m.id || typeof m.provider !== 'string' || !m.provider) continue;
      if (key === 'cloud' && m.available !== true) continue;
      const value = modelValue(m.provider, m.id);
      if (seen.has(value)) continue;
      seen.add(value);
      const name = typeof m.name === 'string' && m.name ? m.name : m.id;
      // The desktop's plan lock (`disabled`): shown, but not pickable.
      items.push(m.disabled === true
        ? { value, label: name + ' (planında kilitli)', disabled: true }
        : { value, label: name });
    }
    if (items.length) groups.push({ label, items });
  }
  if (currentValue && !seen.has(currentValue)) {
    const name = current.model_name || 'sağlayıcının varsayılanı';
    groups.unshift({ label: 'Bu sohbetteki', items: [{ value: currentValue, label: current.provider_type + ' · ' + name }] });
  }
  return { groups, currentValue };
}

// Refusals every request can meet, worded once.
function commonFailure(error) {
  switch (error) {
    case 'not_ready': return 'Önce bilgisayara bağlanmalı.';
    case 'busy': return 'Bilgisayar şu an meşgul; biraz sonra tekrar dene.';
    case 'timeout': return 'Bilgisayardan yanıt gelmedi.';
    case 'disconnected': return 'Bağlantı koptu; bağlanınca tekrar dene.';
    case 'internal': return 'Bilgisayarda beklenmeyen bir hata oldu.';
    case 'too_large': return 'İstek gönderilemedi.';
    case 'unknown_type': return 'Bilgisayardaki Gamachine bu isteği tanımıyor; bilgisayardaki uygulamayı güncelle.';
    case 'bad_request': return 'İstek anlaşılamadı.';
    default: return null;
  }
}

// What the provider lacks, from not_ready's `needs`.
const MODEL_NEEDS = {
  apikey: 'Bu sağlayıcı için bilgisayarda API anahtarı girilmemiş.',
  install: 'Bu modelin komut satırı aracı bilgisayarda kurulu değil.',
  login: 'Bu modelin komut satırı aracında bilgisayarda oturum açılmamış.',
  service: 'Yerel model servisi (Ollama) bilgisayarda çalışmıyor.',
};

// The model note when set_model failed; the chat's model did not change.
// `not_ready` is both the link's own error (no `needs`) and the PC's refusal.
export function modelFailureNote(error, reply = {}) {
  if (error === 'not_ready' && reply?.needs !== undefined) {
    return 'Model değişmedi. ' + (MODEL_NEEDS[reply.needs] || 'Sağlayıcı şu an hazır değil.');
  }
  const why = commonFailure(error)
    ?? (error === 'unknown_chat' ? 'Bu sohbet artık yok.'
      : error === 'bad_chat_id' ? 'Sohbet numarası geçersiz.'
      : error === 'unknown_provider' ? 'Bilinmeyen sağlayıcı.'
      : error === 'bad_model' ? 'Model adı geçersiz.'
      : error === 'plan_locked' ? 'Aboneliğin bu modeli desteklemiyor; Auto modelini kullanabilirsin.'
      : null);
  return why ? 'Model değişmedi. ' + why : 'Model değiştirilemedi: ' + error;
}

// list_models failing leaves the select with the chat's current model only.
export function modelListFailureNote(error) {
  if (error === 'unavailable') return 'Bilgisayardaki uygulama model listesini veremedi.';
  return 'Model listesi alınamadı: ' + (commonFailure(error) ?? error);
}

export function modelChangedNote(result) {
  const name = result?.model_name || 'sağlayıcının varsayılanı';
  return 'Model değişti: ' + name + '. Çalışan bir tur başladığı modelle biter; yeni model sonraki mesajdan itibaren geçerli.';
}

// The effort note when set_effort failed or was not delivered; nothing changed.
export function effortFailureNote(error) {
  const why = commonFailure(error)
    ?? (error === 'bad_effort' ? 'Bu düşünme seviyesi geçersiz.' : null);
  return why ? 'Düşünme seviyesi değişmedi. ' + why : 'Düşünme seviyesi değiştirilemedi: ' + error;
}

// set_effort replies {status: accepted | desktop_not_ready}. `accepted` only
// says the desktop app got the request: it applies the level when the active
// model offers it, and the page learns the real value from the PC afterwards.
export function effortSetNote(status, level) {
  if (status === 'accepted') return 'Bilgisayara iletildi: ' + effortLabel(level) + '.';
  if (status === 'desktop_not_ready') return 'Düşünme seviyesi değişmedi. Bilgisayardaki uygulama hazır değil.';
  return 'Düşünme seviyesi değişmedi. Yanıt: ' + (status || 'bilinmiyor');
}

// What the PC really has after a request the desktop accepted; it may not
// offer the level (the model the desktop shows differs from the chat's).
export function effortOutcomeNote(requested, actual, ultracode = false) {
  if (requested === actual && !ultracode) return 'Bilgisayarda değişti: ' + effortLabel(actual) + '.';
  return 'Bilgisayar ' + effortLabel(requested) + ' seviyesini uygulamadı (açık sohbetin modeli desteklemiyor olabilir); şu an: '
    + (ultracode ? 'Ultracode' : effortLabel(actual)) + '.';
}

// Ultracode is on at the PC: its panel then shows "Ultracode" and no level,
// and choosing a level (even the one it sits over) switches it off. The select
// shows the same, so that choosing that level is a change the page can send.
export const ULTRACODE_OPTION = 'ultracode';

export const EFFORT_UNKNOWN_NOTE = 'Bilgisayardaki düşünme seviyesi bilinmiyor: uygulama açık değil ya da henüz bildirmedi.';

export function configFailureNote(error) {
  if (error === 'unknown_chat') return 'Bu sohbet artık yok.';
  return 'Bilgisayardaki ayarlar okunamadı: ' + (commonFailure(error) ?? error);
}

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

// True when turn events name a card still open that `cards` does not hold:
// its card_opened push never arrived (socket replaced, page asleep), so the
// caller asks pending_cards again rather than leave the turn with no buttons.
export function cardsMissing(cards, events) {
  const held = new Set(cards.map((c) => c.card_id));
  const open = new Set();
  for (const ev of events) {
    if (ev?.kind === 'card_opened' && typeof ev.card_id === 'string') open.add(ev.card_id);
    else if (ev?.kind === 'card_closed') open.delete(ev.card_id);
  }
  for (const id of open) if (!held.has(id)) return true;
  return false;
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

export const GAP_RELOADS_NOW = 3;
export const GAP_DELAY_BASE_MS = 1000;
export const GAP_DELAY_MAX_MS = 30_000;

// Which chat the page shows, and what to do with open_chat replies and
// pushes for it. The bridge registers an open_chat listener only after an
// await, so a close_chat sent while that open is pending can arrive first
// and leave a listener pushing a chat the page no longer shows: such a late
// reply (verdict 'orphan') and pushes for chats not shown ask for close_chat
// again. Every show/hide/load bumps `gen`, so only the newest load renders.
export class ChatView {
  constructor({ now = Date.now, strayEveryMs = 10_000, gapQuietMs = 60_000 } = {}) {
    this.shown = null;
    this.gen = 0;
    this.loading = null;
    this.now = now;
    this.strayEveryMs = strayEveryMs;
    this.strayAt = new Map();
    this.reloadAfter = false;
    // Gap reloads are bounded: GAP_RELOADS_NOW immediate ones, then a doubling
    // delay up to GAP_DELAY_MAX_MS; a gap-free gapQuietMs resets the streak.
    this.gapQuietMs = gapQuietMs;
    this.gapStreak = 0;
    this.lastGapAt = -Infinity;
    this.scheduled = null;
  }

  resetGaps() {
    this.reloadAfter = false;
    this.gapStreak = 0;
    this.lastGapAt = -Infinity;
    this.scheduled = null;
  }

  // Returns the chat that was shown before, if it differs (the caller closes it).
  show(id) {
    const prev = this.shown;
    this.shown = id;
    this.gen += 1;
    this.loading = null;
    this.resetGaps();
    this.strayAt.delete(id);
    return prev !== null && prev !== id ? prev : null;
  }

  hide() {
    const prev = this.shown;
    this.shown = null;
    this.gen += 1;
    this.loading = null;
    this.resetGaps();
    return prev;
  }

  beginLoad() {
    if (this.shown === null) return null;
    this.gen += 1;
    this.scheduled = null; // this load supersedes a delayed reload
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

  // {type:"gap", chat_id}: live events were lost. 'reload' re-opens the chat
  // now, 'later' means a delayed reload was planned (takeSchedule), 'ignore'
  // means a load or a planned reload already covers it. During a load the gap
  // may come from the listener that load replaces or from the new one, so the
  // chat is reloaded once more after it (takeReload).
  onGap(chatId) {
    if (chatId !== this.shown) return this.stray(chatId) ? 'close' : 'ignore';
    const t = this.now();
    if (t - this.lastGapAt > this.gapQuietMs) this.gapStreak = 0;
    this.lastGapAt = t;
    if (this.loading) {
      this.reloadAfter = true;
      return 'ignore';
    }
    if (this.scheduled) return 'ignore';
    return this.planReload() ? 'reload' : 'later';
  }

  // True: reload now. False with a gap pending: a delayed reload was planned.
  takeReload() {
    const again = !!this.reloadAfter;
    this.reloadAfter = false;
    return again && this.planReload();
  }

  // True when the next gap reload may run now; otherwise records a delayed one.
  planReload() {
    this.gapStreak += 1;
    if (this.gapStreak <= GAP_RELOADS_NOW) return true;
    const delayMs = Math.min(GAP_DELAY_MAX_MS, GAP_DELAY_BASE_MS * 2 ** (this.gapStreak - GAP_RELOADS_NOW - 1));
    this.scheduled = { chatId: this.shown, delayMs, armed: false };
    return false;
  }

  // The planned delayed reload, once: the caller arms a timer and later calls
  // runScheduled(s), which is true only if nothing superseded it.
  takeSchedule() {
    const s = this.scheduled;
    if (!s || s.armed) return null;
    s.armed = true;
    return s;
  }

  runScheduled(s) {
    if (this.scheduled !== s || this.shown !== s.chatId) return false;
    this.scheduled = null;
    return true;
  }
}
