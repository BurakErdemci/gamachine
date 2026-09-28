// Relay connections for the phone: one-shot pairing socket and the long-lived
// session link. No DOM access, so tests can drive it from node.

import * as C from './crypto.js';

export const PROTOCOL = 'gamachine.v1';
export const CLOSE_TOKEN_DROPPED = 4001;
export const CLOSE_ROOM_RESET = 4006;
const PAIR_TIMEOUT_MS = 330_000; // pair_secret lives 5 min on the PC
const REQUEST_TIMEOUT_MS = 20_000;
const PING_EVERY_MS = 25_000;
const BACKOFF_MS = [1000, 2000, 5000, 10_000, 30_000];

export function wsOrigin(loc) {
  return (loc.protocol === 'https:' ? 'wss://' : 'ws://') + loc.host;
}

// Resolves {deviceId, token, vapidPub, privateKey, publicRaw}; rejects with
// Error(code) where code is pc_offline | rejected | refused | timeout | bad_reply | <PC reason>.
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
    ws.onclose = () => finish(new Error(opened ? 'rejected' : 'refused'));
  });
}

// Status values passed to onStatus: connecting | ready | pc_offline {lastSeen}
// | removed | hello_rejected {reason}.
export class Link {
  constructor({ origin, device, onStatus, onPush, WS = globalThis.WebSocket }) {
    this.origin = origin;
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
      if (!obj) return;
      if (obj.id !== undefined && this.pending.has(obj.id)) {
        const p = this.pending.get(obj.id);
        this.pending.delete(obj.id);
        p.resolve(obj);
      } else {
        this.onPush(obj);
      }
    }
  }

  // Resolves with the PC's reply object ({id, ok, result} or {id, ok:false, error}).
  async request(type, params = {}) {
    const channel = this.channel;
    const ws = this.ws;
    if (!channel || !ws) throw new Error('not_ready');
    const id = ++this.seq;
    const reply = new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error('timeout'));
      }, REQUEST_TIMEOUT_MS);
      this.pending.set(id, {
        resolve: (v) => { clearTimeout(timer); resolve(v); },
        reject: (e) => { clearTimeout(timer); reject(e); },
      });
    });
    // Sealing is async; chaining keeps frames on the wire in counter order,
    // otherwise the PC would drop an earlier counter that arrives late.
    this.outbox = (this.outbox ?? Promise.resolve()).then(async () => {
      const frame = await channel.seal({ ...params, id, type });
      if (this.channel !== channel || this.ws !== ws) throw new Error('disconnected');
      ws.send(JSON.stringify(frame));
    }).catch((err) => {
      const p = this.pending.get(id);
      this.pending.delete(id);
      p?.reject(err);
    });
    return reply;
  }
}
