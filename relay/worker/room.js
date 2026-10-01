// One Durable Object per pairing id. Forwards opaque text frames between the
// single PC socket and the phone sockets. Durable storage holds only the time
// of the last PC connect, the phone token hashes, the PC's last-seen time and
// the pairing attempt timestamps - never frame content (docs/remote-control.md, "Parts").
//
// Uses the WebSocket Hibernation API: in-memory fields are lost whenever the
// object sleeps, so every per-socket fact lives in the socket attachment.

import { PROTOCOL, HASH_RE, SECRET_RE, sha256b64u, pairIdFor, frameBytes, randomId, equalStrings, parseProtocols, plain } from './util.js';

export const LIMITS = {
  pairPerMinute: 5,
  pairWindowMs: 60_000,
  // Doc: pair_secret lives 5 minutes; one extra minute covers clock skew.
  pairSocketTtlMs: 6 * 60_000,
  phoneFrameMax: 64 * 1024,
  // docs/remote-control.md "Relay limits": the bridge chunks anything larger.
  pcFrameMax: 1024 * 1024,
  maxTokens: 50,
  // Rooms are persistent; one whose PC stays away this long is deleted
  // (Codex relayaudit, 28 Sep 2026: rooms were unbounded).
  roomIdleMs: 30 * 24 * 3_600_000,
};

export const CLOSE = {
  replaced: 4000,
  tokenDropped: 4001,
  pairDone: 4002,
  pairTimeout: 4003,
  badFrame: 4004,
  pcOffline: 4005,
  roomReset: 4006,
  roomExpired: 4007,
  noRoom: 4008,
  unknownToken: 4009,
  tokensPending: 4010,
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
      const m = await this.ctx.storage.get(['last_pc', 'tokens', 'last_seen', 'pair_hits']);
      this.meta = {
        // Also the "room exists" marker: set by every PC connect, gone after reset or expiry.
        lastPc: m.get('last_pc') ?? null,
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
    if (role === 'pc') return this.connectPc(request, protocols, parts[3]);
    if (role === 'phone') return this.connectPhone(request, protocols);
    if (role === 'pair') return this.connectPair(request);
    return plain(404, 'not found');
  }

  async ipAllows(request, bucket) {
    const ip = request.headers.get('CF-Connecting-IP') || 'unknown';
    const limiter = this.env.IP_LIMITER.get(this.env.IP_LIMITER.idFromName(ip));
    const verdict = await limiter.fetch('https://limiter/' + bucket, { method: 'POST' });
    return verdict.status !== 429;
  }

  async connectPc(request, protocols, pairId) {
    const key = protocols.find((p) => p.startsWith('key.'))?.slice(4);
    if (!key || !SECRET_RE.test(key)) return plain(401, 'unauthorized');
    if (!equalStrings(await pairIdFor(key), pairId)) return plain(403, 'forbidden');
    const meta = await this.load();
    const now = Date.now();
    if (meta.lastPc === null && !(await this.ipAllows(request, 'room'))) {
      return plain(429, 'too many new rooms', { 'Retry-After': '3600' });
    }
    meta.lastPc = now;
    await this.ctx.storage.put('last_pc', now);
    for (const old of this.sockets('pc')) {
      try { old.close(CLOSE.replaced, 'replaced'); } catch {}
    }
    const conn = randomId();
    // synced: this PC has sent register_tokens with replace:true, so the
    // token list is its own and an unknown token is really unknown.
    const { server, response } = this.accept(['pc'], { role: 'pc', conn, synced: false }, request);
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
    await this.schedule(now);
    return response;
  }

  async connectPhone(request, protocols) {
    const token = protocols.find((p) => p.startsWith('tok.'))?.slice(4);
    const hash = token && SECRET_RE.test(token) ? await sha256b64u(token) : null;
    const meta = await this.load();
    if (meta.lastPc === null) return plain(404, 'not found');
    if (!hash || !meta.tokens.some((t) => equalStrings(t, hash))) {
      const pc = this.pcSocket();
      if (hash && pc && !pc.deserializeAttachment()?.synced) return this.refuseTokensPending(request);
      return this.refuseUnknownToken(request);
    }
    const conn = randomId();
    const { server, response } = this.accept(['phone', 'tok:' + hash], { role: 'phone', conn, tokenHash: hash }, request);
    const pc = this.pcSocket();
    if (pc) pc.send(JSON.stringify({ type: 'phone_open', conn, token_hash: hash }));
    else server.send(this.offlineFrame());
    return response;
  }

  async connectPair(request) {
    const meta = await this.load();
    if (meta.lastPc === null) return this.refuseNoRoom(request);
    // After the existence check: a stale or mistyped code costs no quota.
    if (!(await this.ipAllows(request, 'pair'))) return plain(429, 'too many pairing attempts', { 'Retry-After': '3600' });
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
      await this.schedule(now);
    } else {
      server.send(this.offlineFrame());
      server.close(CLOSE.pcOffline, 'pc_offline');
    }
    return response;
  }

  // A browser cannot read the status of a refused upgrade, so the page could
  // not tell "no such room" from "rate limited". This one is accepted, told,
  // and closed; nothing is stored.
  refuseNoRoom(request) {
    const { server, response } = this.accept(['none'], { role: 'none' }, request);
    server.send(JSON.stringify({ type: 'no_room' }));
    server.close(CLOSE.noRoom, 'no_room');
    return response;
  }

  // Same reason as refuseNoRoom: a refused upgrade reaches the page as a bare
  // close, so a phone revoked while offline could not tell it from a network
  // drop and retried forever. Sent when the connected PC has replaced the
  // list, or no PC is connected; the page still waits for several in a row
  // (net.js), since with the PC away the stored list may be a stale one.
  refuseUnknownToken(request) {
    const { server, response } = this.accept(['none'], { role: 'none' }, request);
    server.close(CLOSE.unknownToken, 'unknown_token');
    return response;
  }

  // The connected PC has not replaced the token list yet (recreated room, or
  // its registration failed or is late), so the token may still be valid:
  // a non-final close the phone just retries.
  refuseTokensPending(request) {
    const { server, response } = this.accept(['none'], { role: 'none' }, request);
    server.close(CLOSE.tokensPending, 'tokens_pending');
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
    if (a.role === 'pc') {
      if (frameBytes(message) > LIMITS.pcFrameMax) {
        ws.send(JSON.stringify({ type: 'error', error: 'too_large' }));
        return;
      }
      return this.onPcMessage(ws, message);
    }
    if (a.role !== 'phone' && a.role !== 'pair') return;
    if (frameBytes(message) > LIMITS.phoneFrameMax) {
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
          const att = pc.deserializeAttachment();
          if (att && !att.synced) pc.serializeAttachment({ ...att, synced: true });
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
        await this.wipe(CLOSE.roomReset, 'room_reset');
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
    if (!a || a.role === 'none') return;
    const meta = await this.load();
    if (a.role === 'pc') {
      // A replaced socket closing must not mark a live PC offline.
      const others = this.sockets('pc').filter((s) => s !== ws && s.deserializeAttachment()?.conn !== a.conn);
      if (others.length) return;
      if (meta.lastPc === null) return; // room was reset or expired
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

  async wipe(code, reason) {
    await this.ctx.storage.deleteAll();
    await this.ctx.storage.deleteAlarm();
    this.meta = null;
    for (const s of this.sockets()) {
      try { s.close(code, reason); } catch {}
    }
  }

  idleSince(meta) {
    return Math.max(meta.lastPc, meta.lastSeen ?? 0);
  }

  // One alarm serves both jobs: closing stale pairing sockets and deleting a
  // room whose PC has been away for LIMITS.roomIdleMs.
  async schedule(now) {
    const meta = await this.load();
    let next = meta.lastPc === null ? null : this.idleSince(meta) + LIMITS.roomIdleMs;
    for (const s of this.sockets('pair')) {
      const due = s.deserializeAttachment().opened + LIMITS.pairSocketTtlMs;
      if (due > now && (next === null || due < next)) next = due;
    }
    if (next === null) await this.ctx.storage.deleteAlarm();
    else await this.ctx.storage.setAlarm(next);
  }

  async alarm() {
    const now = Date.now();
    const meta = await this.load();
    if (meta.lastPc !== null) {
      if (this.pcSocket()) {
        // A PC that stays connected keeps its room alive.
        meta.lastPc = now;
        await this.ctx.storage.put('last_pc', now);
      } else if (this.idleSince(meta) + LIMITS.roomIdleMs <= now) {
        // Phones get a non-final code: if the PC comes back and registers
        // their tokens again, they reconnect on their own.
        await this.wipe(CLOSE.roomExpired, 'room_expired');
        return;
      }
    }
    for (const s of this.sockets('pair')) {
      if (s.deserializeAttachment().opened + LIMITS.pairSocketTtlMs <= now) {
        try { s.close(CLOSE.pairTimeout, 'pair_timeout'); } catch {}
      }
    }
    await this.schedule(now);
  }
}
