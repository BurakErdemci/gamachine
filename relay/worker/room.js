// One Durable Object per pairing id. Forwards opaque text frames between the
// single PC socket and the phone sockets. Durable storage holds only the room
// key hash, the phone token hashes, the PC's last-seen time and the pairing
// attempt timestamps - never frame content (docs/remote-control.md, "Parts").
//
// Uses the WebSocket Hibernation API: in-memory fields are lost whenever the
// object sleeps, so every per-socket fact lives in the socket attachment.

import { PROTOCOL, HASH_RE, SECRET_RE, sha256b64u, randomId, equalStrings, parseProtocols, plain } from './util.js';

export const LIMITS = {
  pairPerMinute: 5,
  pairWindowMs: 60_000,
  // Doc: pair_secret lives 5 minutes; one extra minute covers clock skew.
  pairSocketTtlMs: 6 * 60_000,
  phoneFrameMax: 64 * 1024,
  maxTokens: 50,
};

export const CLOSE = {
  replaced: 4000,
  tokenDropped: 4001,
  pairDone: 4002,
  pairTimeout: 4003,
  badFrame: 4004,
  pcOffline: 4005,
  roomReset: 4006,
};

const PING = '{"type":"ping"}';
const PONG = '{"type":"pong"}';
const OPEN = 1;

export class Room {
  constructor(ctx, env) {
    this.ctx = ctx;
    this.env = env;
    this.meta = null;
    // Keepalive pings are answered by the runtime without waking the object.
    if (typeof WebSocketRequestResponsePair === 'function') {
      ctx.setWebSocketAutoResponse(new WebSocketRequestResponsePair(PING, PONG));
    }
  }

  async load() {
    if (!this.meta) {
      const m = await this.ctx.storage.get(['room_hash', 'tokens', 'last_seen', 'pair_hits']);
      this.meta = {
        roomHash: m.get('room_hash') ?? null,
        tokens: m.get('tokens') ?? [],
        lastSeen: m.get('last_seen') ?? null,
        pairHits: m.get('pair_hits') ?? [],
      };
    }
    return this.meta;
  }

  sockets(tag) {
    return this.ctx.getWebSockets(tag).filter((s) => s.readyState === OPEN);
  }

  pcSocket() {
    return this.sockets('pc')[0] ?? null;
  }

  findConn(conn) {
    for (const s of this.sockets()) {
      const a = s.deserializeAttachment();
      if (a && a.conn === conn) return { ws: s, att: a };
    }
    return null;
  }

  offlineFrame() {
    return JSON.stringify({ type: 'pc_offline', last_seen: this.meta ? this.meta.lastSeen : null });
  }

  accept(tags, attachment, request) {
    const pair = new WebSocketPair();
    const client = pair[0];
    const server = pair[1];
    this.ctx.acceptWebSocket(server, tags);
    server.serializeAttachment(attachment);
    const response = new Response(null, {
      status: 101,
      webSocket: client,
      headers: { 'Sec-WebSocket-Protocol': PROTOCOL },
    });
    return { server, response };
  }

  async fetch(request) {
    const url = new URL(request.url);
    const parts = url.pathname.split('/');
    const role = parts[2];
    if (request.headers.get('Upgrade')?.toLowerCase() !== 'websocket') return plain(426, 'websocket required');
    const protocols = parseProtocols(request.headers.get('Sec-WebSocket-Protocol'));
    if (!protocols.includes(PROTOCOL)) return plain(400, 'missing subprotocol');
    if (role === 'pc') return this.connectPc(request, protocols);
    if (role === 'phone') return this.connectPhone(request, protocols);
    if (role === 'pair') return this.connectPair(request);
    return plain(404, 'not found');
  }

  async connectPc(request, protocols) {
    const key = protocols.find((p) => p.startsWith('key.'))?.slice(4);
    if (!key || !SECRET_RE.test(key)) return plain(401, 'unauthorized');
    const hash = await sha256b64u(key);
    const meta = await this.load();
    if (!meta.roomHash) {
      // Trust on first use: the first PC to connect owns this pairing id.
      meta.roomHash = hash;
      await this.ctx.storage.put('room_hash', hash);
    } else if (!equalStrings(meta.roomHash, hash)) {
      return plain(403, 'forbidden');
    }
    for (const old of this.sockets('pc')) {
      try { old.close(CLOSE.replaced, 'replaced'); } catch {}
    }
    const conn = randomId();
    const { server, response } = this.accept(['pc'], { role: 'pc', conn }, request);
    const phones = [];
    const pairs = [];
    for (const s of this.sockets()) {
      const a = s.deserializeAttachment();
      if (a?.role === 'phone') phones.push({ conn: a.conn, token_hash: a.tokenHash });
      if (a?.role === 'pair') pairs.push({ conn: a.conn, ip: a.ip });
    }
    server.send(JSON.stringify({ type: 'welcome', phones, pairs, tokens: meta.tokens.length }));
    const online = JSON.stringify({ type: 'pc_online' });
    for (const s of this.sockets('phone')) {
      try { s.send(online); } catch {}
    }
    return response;
  }

  async connectPhone(request, protocols) {
    const token = protocols.find((p) => p.startsWith('tok.'))?.slice(4);
    const hash = token && SECRET_RE.test(token) ? await sha256b64u(token) : null;
    const meta = await this.load();
    if (!meta.roomHash) return plain(404, 'not found');
    if (!hash || !meta.tokens.some((t) => equalStrings(t, hash))) return plain(401, 'unauthorized');
    const conn = randomId();
    const { server, response } = this.accept(['phone', 'tok:' + hash], { role: 'phone', conn, tokenHash: hash }, request);
    const pc = this.pcSocket();
    if (pc) pc.send(JSON.stringify({ type: 'phone_open', conn, token_hash: hash }));
    else server.send(this.offlineFrame());
    return response;
  }

  async connectPair(request) {
    const meta = await this.load();
    if (!meta.roomHash) return plain(404, 'not found');
    const now = Date.now();
    const hits = meta.pairHits.filter((t) => now - t < LIMITS.pairWindowMs);
    if (hits.length >= LIMITS.pairPerMinute) return plain(429, 'too many pairing attempts', { 'Retry-After': '60' });
    hits.push(now);
    meta.pairHits = hits;
    await this.ctx.storage.put('pair_hits', hits);

    const ip = request.headers.get('CF-Connecting-IP') || 'unknown';
    const conn = randomId();
    const { server, response } = this.accept(['pair'], { role: 'pair', conn, ip, opened: now, requested: false }, request);
    const pc = this.pcSocket();
    if (pc) {
      pc.send(JSON.stringify({ type: 'pair_open', conn, ip }));
      if (!(await this.ctx.storage.getAlarm())) await this.ctx.storage.setAlarm(now + LIMITS.pairSocketTtlMs);
    } else {
      server.send(this.offlineFrame());
      server.close(CLOSE.pcOffline, 'pc_offline');
    }
    return response;
  }

  async webSocketMessage(ws, message) {
    const a = ws.deserializeAttachment();
    if (!a) return;
    if (typeof message !== 'string') {
      ws.close(CLOSE.badFrame, 'text frames only');
      return;
    }
    if (message === PING) {
      ws.send(PONG);
      return;
    }
    await this.load();
    if (a.role === 'pc') return this.onPcMessage(ws, message);
    if (message.length > LIMITS.phoneFrameMax) {
      ws.close(CLOSE.badFrame, 'frame too large');
      return;
    }
    if (a.role === 'pair') {
      // One pairing attempt per socket keeps the rate limit meaningful.
      if (a.requested) {
        ws.close(CLOSE.badFrame, 'one request per pairing socket');
        return;
      }
      a.requested = true;
      ws.serializeAttachment(a);
    }
    const pc = this.pcSocket();
    if (!pc) {
      ws.send(this.offlineFrame());
      if (a.role === 'pair') ws.close(CLOSE.pcOffline, 'pc_offline');
      return;
    }
    pc.send(JSON.stringify({ type: 'from', conn: a.conn, data: message }));
  }

  async onPcMessage(pc, message) {
    let m;
    try {
      m = JSON.parse(message);
    } catch {
      pc.send(JSON.stringify({ type: 'error', error: 'bad_json' }));
      return;
    }
    const meta = this.meta;
    switch (m?.type) {
      case 'to': {
        const target = typeof m.conn === 'string' ? this.findConn(m.conn) : null;
        if (!target || target.att.role === 'pc' || typeof m.data !== 'string') {
          pc.send(JSON.stringify({ type: 'gone', conn: m.conn ?? null }));
          return;
        }
        target.ws.send(m.data);
        if (target.att.role === 'pair') target.ws.close(CLOSE.pairDone, 'pair_done');
        return;
      }
      case 'register_tokens': {
        const hashes = Array.isArray(m.hashes) ? m.hashes : null;
        if (!hashes || !hashes.every((h) => typeof h === 'string' && HASH_RE.test(h))) {
          pc.send(JSON.stringify({ type: 'error', error: 'bad_hashes' }));
          return;
        }
        const next = m.replace ? [...new Set(hashes)] : [...new Set([...meta.tokens, ...hashes])];
        if (next.length > LIMITS.maxTokens) {
          pc.send(JSON.stringify({ type: 'error', error: 'too_many_tokens' }));
          return;
        }
        meta.tokens = next;
        await this.ctx.storage.put('tokens', next);
        if (m.replace) {
          for (const s of this.sockets('phone')) {
            const a = s.deserializeAttachment();
            if (!next.includes(a.tokenHash)) s.close(CLOSE.tokenDropped, 'token_dropped');
          }
        }
        pc.send(JSON.stringify({ type: 'tokens_ok', count: next.length }));
        return;
      }
      case 'drop_token': {
        if (typeof m.hash !== 'string' || !HASH_RE.test(m.hash)) {
          pc.send(JSON.stringify({ type: 'error', error: 'bad_hash' }));
          return;
        }
        meta.tokens = meta.tokens.filter((t) => t !== m.hash);
        await this.ctx.storage.put('tokens', meta.tokens);
        for (const s of this.sockets('tok:' + m.hash)) s.close(CLOSE.tokenDropped, 'token_dropped');
        pc.send(JSON.stringify({ type: 'tokens_ok', count: meta.tokens.length }));
        return;
      }
      case 'reset_room': {
        // Lets the PC erase everything the relay holds for this pairing id.
        await this.ctx.storage.deleteAll();
        this.meta = null;
        for (const s of this.sockets()) {
          try { s.close(CLOSE.roomReset, 'room_reset'); } catch {}
        }
        return;
      }
      default:
        pc.send(JSON.stringify({ type: 'error', error: 'unknown_type' }));
    }
  }

  async webSocketClose(ws, code, reason) {
    try { ws.close(1000, 'bye'); } catch {}
    await this.onGone(ws);
  }

  async webSocketError(ws) {
    await this.onGone(ws);
  }

  async onGone(ws) {
    const a = ws.deserializeAttachment();
    if (!a) return;
    const meta = await this.load();
    if (a.role === 'pc') {
      // A replaced socket closing must not mark a live PC offline.
      const others = this.sockets('pc').filter((s) => s !== ws && s.deserializeAttachment()?.conn !== a.conn);
      if (others.length) return;
      if (!meta.roomHash) return; // room was reset
      meta.lastSeen = Date.now();
      await this.ctx.storage.put('last_seen', meta.lastSeen);
      const offline = this.offlineFrame();
      for (const s of this.sockets('phone')) {
        try { s.send(offline); } catch {}
      }
      for (const s of this.sockets('pair')) {
        try { s.send(offline); s.close(CLOSE.pcOffline, 'pc_offline'); } catch {}
      }
      return;
    }
    const pc = this.pcSocket();
    if (pc) pc.send(JSON.stringify({ type: a.role === 'pair' ? 'pair_close' : 'phone_close', conn: a.conn }));
  }

  async alarm() {
    const now = Date.now();
    let next = null;
    for (const s of this.sockets('pair')) {
      const a = s.deserializeAttachment();
      const due = a.opened + LIMITS.pairSocketTtlMs;
      if (due <= now) {
        try { s.close(CLOSE.pairTimeout, 'pair_timeout'); } catch {}
      } else if (next === null || due < next) {
        next = due;
      }
    }
    if (next !== null) await this.ctx.storage.setAlarm(next);
  }
}
