import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { installRuntimeGlobals, FakeNamespace } from './fake-do.mjs';
import { pairIdFor } from './nodeimpl.mjs';

installRuntimeGlobals();
const { default: worker, Room, IpLimiter } = await import('../worker/index.js');
const { CLOSE, LIMITS } = await import('../worker/room.js');

const KEY = 'K'.repeat(43);
const PAIR = pairIdFor(KEY);
const PAIR2 = pairIdFor('L'.repeat(43));
const PAGE = 'https://relay.test';
const TOKEN = 'T'.repeat(43);
const TOKEN2 = 'U'.repeat(43);
const hash = (s) => createHash('sha256').update(s).digest('base64url');

const realNow = Date.now;
let now = 1_800_000_000_000;
beforeEach(() => {
  now = 1_800_000_000_000;
  Date.now = () => now;
});
afterEach(() => {
  Date.now = realNow;
});

const ASSET_FILES = {
  '/index.html': '<!doctype html><title>x</title>',
  '/sw.js': 'self.addEventListener("push", () => {});',
  '/manifest.webmanifest': '{}',
};

function makeEnv() {
  const env = {
    ASSETS: {
      fetch: async (req) => {
        const p = new URL(req.url).pathname;
        return p in ASSET_FILES ? new Response(ASSET_FILES[p]) : new Response('nope', { status: 404 });
      },
    },
  };
  env.ROOM = new FakeNamespace(Room, env);
  env.IP_LIMITER = new FakeNamespace(IpLimiter, env);
  return env;
}

// Phone sockets come from the relay's own page; the PC sends no Origin.
async function open(env, role, protocols, { pairId = PAIR, ip = '198.51.100.7', upgrade = true, origin = role === 'pc' ? null : PAGE } = {}) {
  const headers = { 'CF-Connecting-IP': ip, 'Sec-WebSocket-Protocol': protocols.join(', ') };
  if (upgrade) headers.Upgrade = 'websocket';
  if (origin !== null) headers.Origin = origin;
  const res = await worker.fetch(new Request(`https://relay.test/ws/${role}/${pairId}`, { headers }), env);
  return { status: res.status, sock: res.status === 101 ? res.webSocket.peer : null, res };
}
const pcOpen = (env, key = KEY, o) => open(env, 'pc', ['gamachine.v1', 'key.' + key], o);
const phoneOpen = (env, token = TOKEN, o) => open(env, 'phone', ['gamachine.v1', 'tok.' + token], o);
const pairOpen = (env, o) => open(env, 'pair', ['gamachine.v1'], o);
const room = (env, pairId = PAIR) => env.ROOM.object(pairId);
const msg = (env, sock, text, pairId = PAIR) => room(env, pairId).webSocketMessage(sock, text);
async function hangup(env, sock, pairId = PAIR) {
  sock.close(1000, 'client');
  await room(env, pairId).webSocketClose(sock, 1000, 'client', true);
}

// An unknown token is accepted and closed at once with 4009: a refused
// upgrade would reach the page as a bare close.
function assertUnknownToken(r, message) {
  assert.equal(r.status, 101, message);
  assert.deepEqual(r.sock.sent, [], message);
  assert.deepEqual(r.sock.closed, { code: CLOSE.unknownToken, reason: 'unknown_token' }, message);
}

async function pcWithToken(env, tokens = [TOKEN]) {
  const pc = await pcOpen(env);
  await msg(env, pc.sock, JSON.stringify({ type: 'register_tokens', hashes: tokens.map(hash) }));
  pc.sock.take();
  return pc.sock;
}

test('PC room key must be the one the pairing id is derived from', async () => {
  const env = makeEnv();
  const first = await pcOpen(env);
  assert.equal(first.status, 101);
  assert.equal(first.res.headers.get('Sec-WebSocket-Protocol'), 'gamachine.v1');
  assert.deepEqual(first.sock.json()[0], { type: 'welcome', phones: [], pairs: [], tokens: 0 });
  assert.equal(await room(env).ctx.storage.get('last_pc'), now);

  assert.equal((await pcOpen(env, 'Z'.repeat(43))).status, 403);
  assert.equal((await open(env, 'pc', ['gamachine.v1'])).status, 401);
  assert.equal((await open(env, 'pc', ['gamachine.v1', 'key.short'])).status, 401);
  assert.equal((await open(env, 'pc', ['key.' + KEY])).status, 400, 'subprotocol gamachine.v1 is required');

  const second = await pcOpen(env);
  assert.equal(second.status, 101);
  assert.deepEqual(first.sock.closed, { code: CLOSE.replaced, reason: 'replaced' });
});

test('phones are turned away unless their token hash is registered', async () => {
  const env = makeEnv();
  assert.equal((await phoneOpen(env)).status, 404, 'room does not exist yet');
  const pc = await pcOpen(env);
  assertUnknownToken(await phoneOpen(env), 'token not registered');
  assertUnknownToken(await open(env, 'phone', ['gamachine.v1']), 'no token');
  assertUnknownToken(await phoneOpen(env, 'short'), 'malformed token');
  assert.deepEqual(pc.sock.take().filter((m) => m.type !== 'welcome'), [], 'the PC hears nothing of a refused phone');
  await msg(env, pc.sock, JSON.stringify({ type: 'register_tokens', hashes: [hash(TOKEN)] }));
  assert.deepEqual(pc.sock.last(), { type: 'tokens_ok', count: 1 });
  assertUnknownToken(await phoneOpen(env, TOKEN2), 'other token');
  const phone = await phoneOpen(env);
  assert.equal(phone.status, 101);
  const open1 = pc.sock.last();
  assert.equal(open1.type, 'phone_open');
  assert.equal(open1.token_hash, hash(TOKEN));
  assert.match(open1.conn, /^[A-Za-z0-9_-]{11}$/);
});

test('frames pass through unchanged in both directions', async () => {
  const env = makeEnv();
  const pc = await pcWithToken(env);
  const phone = (await phoneOpen(env)).sock;
  const conn = pc.take()[0].conn;

  const raw = '{"c":7,"d":"q83vEjRWeJA"}';
  await msg(env, phone, raw);
  assert.deepEqual(pc.last(), { type: 'from', conn, data: raw });

  const back = '{"c":1,"d":"AAEC"}';
  await msg(env, pc, JSON.stringify({ type: 'to', conn, data: back }));
  assert.equal(phone.sent[phone.sent.length - 1], back);

  await msg(env, pc, JSON.stringify({ type: 'to', conn: 'nope', data: back }));
  assert.deepEqual(pc.last(), { type: 'gone', conn: 'nope' });
  await msg(env, pc, 'not json');
  assert.deepEqual(pc.last(), { type: 'error', error: 'bad_json' });
  await msg(env, pc, JSON.stringify({ type: 'something_else' }));
  assert.deepEqual(pc.last(), { type: 'error', error: 'unknown_type' });
});

test('pc_offline carries last_seen and online/offline changes reach the phones', async () => {
  const env = makeEnv();
  const pc = await pcWithToken(env);
  const phone = (await phoneOpen(env)).sock;
  now += 1000;
  await hangup(env, pc);
  assert.deepEqual(phone.last(), { type: 'pc_offline', last_seen: now });

  now += 5000;
  await msg(env, phone, '{"c":1,"d":"x"}');
  assert.deepEqual(phone.last(), { type: 'pc_offline', last_seen: now - 5000 });

  const phone2 = (await phoneOpen(env)).sock;
  assert.deepEqual(phone2.last(), { type: 'pc_offline', last_seen: now - 5000 });

  const pc2 = (await pcOpen(env)).sock;
  assert.deepEqual(phone.last(), { type: 'pc_online' });
  const welcome = pc2.json()[0];
  assert.equal(welcome.type, 'welcome');
  assert.equal(welcome.phones.length, 2);
  assert.equal(welcome.tokens, 1);
});

test('a replaced PC socket closing does not mark the room offline', async () => {
  const env = makeEnv();
  const pcA = await pcWithToken(env);
  const phone = (await phoneOpen(env)).sock;
  const pcB = (await pcOpen(env)).sock;
  phone.take();
  await room(env).webSocketClose(pcA, CLOSE.replaced, 'replaced', true);
  assert.deepEqual(phone.take(), []);
  assert.equal(await room(env).ctx.storage.get('last_seen'), undefined);
  await msg(env, phone, 'hi');
  assert.deepEqual(pcB.last().data, 'hi');
});

test('drop_token and replace close the dropped phones and refuse them afterwards', async () => {
  const env = makeEnv();
  const pc = await pcWithToken(env, [TOKEN, TOKEN2]);
  const p1 = (await phoneOpen(env)).sock;
  const p1b = (await phoneOpen(env)).sock;
  const p2 = (await phoneOpen(env, TOKEN2)).sock;

  await msg(env, pc, JSON.stringify({ type: 'drop_token', hash: hash(TOKEN) }));
  assert.deepEqual(pc.last(), { type: 'tokens_ok', count: 1 });
  assert.equal(p1.closed.code, CLOSE.tokenDropped);
  assert.equal(p1b.closed.code, CLOSE.tokenDropped);
  assert.equal(p2.closed, null);
  assertUnknownToken(await phoneOpen(env));

  await msg(env, pc, JSON.stringify({ type: 'register_tokens', hashes: [hash(TOKEN)], replace: true }));
  assert.deepEqual(pc.last(), { type: 'tokens_ok', count: 1 });
  assert.equal(p2.closed.code, CLOSE.tokenDropped, 'replace drops tokens missing from the new list');
  assertUnknownToken(await phoneOpen(env, TOKEN2));
  assert.equal((await phoneOpen(env)).status, 101);

  await msg(env, pc, JSON.stringify({ type: 'register_tokens', hashes: ['bad'] }));
  assert.deepEqual(pc.last(), { type: 'error', error: 'bad_hashes' });
  const many = Array.from({ length: LIMITS.maxTokens + 1 }, (_, i) => hash('t' + i));
  await msg(env, pc, JSON.stringify({ type: 'register_tokens', hashes: many, replace: true }));
  assert.deepEqual(pc.last(), { type: 'error', error: 'too_many_tokens' });
});

test('pairing: one request per socket, forwarded with the IP, closed after the PC reply', async () => {
  const env = makeEnv();
  const pc = (await pcOpen(env)).sock;
  pc.take();
  const pair = (await pairOpen(env, { ip: '203.0.113.9' })).sock;
  const opened = pc.last();
  assert.equal(opened.type, 'pair_open');
  assert.equal(opened.ip, '203.0.113.9');

  const req = '{"type":"pair_request","phone_pub":"x","device_name":"iPhone","mac":"y"}';
  await msg(env, pair, req);
  assert.deepEqual(pc.last(), { type: 'from', conn: opened.conn, data: req });

  await msg(env, pc, JSON.stringify({ type: 'to', conn: opened.conn, data: '{"type":"pair_ok","c":1,"d":"z"}' }));
  assert.equal(pair.sent.at(-1), '{"type":"pair_ok","c":1,"d":"z"}');
  assert.equal(pair.closed.code, CLOSE.pairDone);

  const pairB = (await pairOpen(env)).sock;
  await msg(env, pairB, req);
  await msg(env, pairB, req);
  assert.equal(pairB.closed.code, CLOSE.badFrame);
});

test('pairing without the PC online answers pc_offline and closes', async () => {
  const env = makeEnv();
  const pc = (await pcOpen(env)).sock;
  await hangup(env, pc);
  const pair = (await pairOpen(env)).sock;
  assert.deepEqual(pair.last(), { type: 'pc_offline', last_seen: now });
  assert.equal(pair.closed.code, CLOSE.pcOffline);
});

test('rate limit: 5 pairing attempts per minute per pairing id', async () => {
  const env = makeEnv();
  await pcOpen(env);
  for (let i = 0; i < LIMITS.pairPerMinute; i++) {
    assert.equal((await pairOpen(env, { ip: '10.0.0.' + i })).status, 101);
  }
  assert.equal((await pairOpen(env, { ip: '10.0.1.1' })).status, 429);
  now += LIMITS.pairWindowMs + 1;
  assert.equal((await pairOpen(env, { ip: '10.0.1.2' })).status, 101);
});

test('rate limit: 20 pairing attempts per hour per IP, across pairing ids', async () => {
  const env = makeEnv();
  const keys = Array.from({ length: 5 }, (_, i) => String.fromCharCode(65 + i).repeat(43));
  const ids = keys.map(pairIdFor);
  for (let i = 0; i < 5; i++) assert.equal((await pcOpen(env, keys[i], { pairId: ids[i] })).status, 101);
  let ok = 0;
  for (let i = 0; i < 20; i++) {
    const r = await pairOpen(env, { pairId: ids[i % 5], ip: '192.0.2.1' });
    if (r.status === 101) ok++;
  }
  assert.equal(ok, 20);
  assert.equal((await pairOpen(env, { pairId: ids[0], ip: '192.0.2.1' })).status, 429);
  assert.equal((await pairOpen(env, { pairId: ids[0], ip: '192.0.2.2' })).status, 101, 'other IPs unaffected');
  now += 3_600_000 + 1;
  assert.equal((await pairOpen(env, { pairId: ids[1], ip: '192.0.2.1' })).status, 101);

  const limiter = env.IP_LIMITER.object('192.0.2.1');
  assert.ok(limiter.ctx.storage.alarm > now, 'cleanup alarm scheduled');
  await limiter.alarm();
  assert.equal(limiter.ctx.storage.map.size, 0, 'alarm wipes the IP record');
});

test('stale pairing sockets are closed by the alarm', async () => {
  const env = makeEnv();
  await pcOpen(env);
  const pair = (await pairOpen(env)).sock;
  const r = room(env);
  assert.equal(r.ctx.storage.alarm, now + LIMITS.pairSocketTtlMs);
  now += 60_000;
  const pair2 = (await pairOpen(env)).sock;
  now += LIMITS.pairSocketTtlMs - 60_000;
  await r.alarm();
  assert.equal(pair.closed.code, CLOSE.pairTimeout);
  assert.equal(pair2.closed, null);
  assert.equal(r.ctx.storage.alarm, now + 60_000, 'rescheduled for the remaining socket');
});

test('frame limits: binary and oversized phone frames are refused', async () => {
  const env = makeEnv();
  await pcWithToken(env);
  const phone = (await phoneOpen(env)).sock;
  await msg(env, phone, new ArrayBuffer(4));
  assert.equal(phone.closed.code, CLOSE.badFrame);
  const phone2 = (await phoneOpen(env)).sock;
  await msg(env, phone2, 'x'.repeat(LIMITS.phoneFrameMax + 1));
  assert.equal(phone2.closed.code, CLOSE.badFrame);
});

test('storage never holds frame content', async () => {
  const env = makeEnv();
  const pc = await pcWithToken(env);
  const phone = (await phoneOpen(env)).sock;
  const conn = pc.take()[0].conn;
  const secretish = 'CONTENT-MARKER-' + 'q'.repeat(20);
  await msg(env, phone, secretish);
  await msg(env, pc, JSON.stringify({ type: 'to', conn, data: secretish }));
  const pair = (await pairOpen(env)).sock;
  await msg(env, pair, secretish);
  await hangup(env, pc);
  const map = room(env).ctx.storage.map;
  assert.deepEqual([...map.keys()].sort(), ['last_pc', 'last_seen', 'pair_hits', 'tokens']);
  assert.ok(!JSON.stringify([...map.values()]).includes('CONTENT-MARKER'));
});

test('state survives hibernation (object rebuilt, sockets kept)', async () => {
  const env = makeEnv();
  const pc = await pcWithToken(env);
  const phone = (await phoneOpen(env)).sock;
  const conn = pc.take()[0].conn;
  env.ROOM.hibernate(PAIR);
  await msg(env, phone, 'after-sleep');
  assert.deepEqual(pc.last(), { type: 'from', conn, data: 'after-sleep' });
  env.ROOM.hibernate(PAIR);
  assert.equal((await phoneOpen(env)).status, 101, 'token list reloaded from storage');
});

test('reset_room wipes storage and closes every socket', async () => {
  const env = makeEnv();
  const pc = await pcWithToken(env);
  const phone = (await phoneOpen(env)).sock;
  await msg(env, pc, JSON.stringify({ type: 'reset_room' }));
  assert.equal(room(env).ctx.storage.map.size, 0);
  assert.equal(phone.closed.code, CLOSE.roomReset);
  assert.equal(pc.closed.code, CLOSE.roomReset);
  await room(env).webSocketClose(pc, CLOSE.roomReset, '', true);
  assert.equal(room(env).ctx.storage.map.size, 0, 'closing after reset writes nothing');
  assert.equal((await phoneOpen(env)).status, 404);
});

test('ping is answered without reaching the PC', async () => {
  const env = makeEnv();
  const pc = await pcWithToken(env);
  const phone = (await phoneOpen(env)).sock;
  pc.take();
  await msg(env, phone, '{"type":"ping"}');
  assert.equal(phone.sent.at(-1), '{"type":"pong"}');
  assert.deepEqual(pc.take(), []);
});

test('HTTP routes: page with CSP, whitelist only, sockets need an upgrade and a valid id', async () => {
  const env = makeEnv();
  const page = await worker.fetch(new Request('https://relay.test/p'), env);
  assert.equal(page.status, 200);
  assert.equal(page.headers.get('Content-Type'), 'text/html; charset=utf-8');
  const policy = page.headers.get('Content-Security-Policy');
  assert.match(policy, /script-src 'self'/);
  assert.match(policy, /connect-src 'self' wss:\/\/relay\.test/);
  assert.doesNotMatch(policy, /unsafe-inline/);
  assert.equal(page.headers.get('Referrer-Policy'), 'no-referrer');

  const sw = await worker.fetch(new Request('https://relay.test/sw.js'), env);
  assert.equal(sw.headers.get('Content-Type'), 'text/javascript; charset=utf-8');
  const mf = await worker.fetch(new Request('https://relay.test/manifest.webmanifest'), env);
  assert.equal(mf.headers.get('Content-Type'), 'application/manifest+json');

  assert.equal((await worker.fetch(new Request('https://relay.test/index.html'), env)).status, 404);
  assert.equal((await worker.fetch(new Request('https://relay.test/wrangler.toml'), env)).status, 404);
  const root = await worker.fetch(new Request('https://relay.test/'), env);
  assert.equal(root.status, 302);
  assert.equal(root.headers.get('Location'), 'https://relay.test/p');
  assert.equal((await worker.fetch(new Request('https://relay.test/p', { method: 'POST' }), env)).status, 405);

  assert.equal((await open(env, 'phone', ['gamachine.v1', 'tok.' + TOKEN], { upgrade: false })).status, 426);
  assert.equal((await open(env, 'phone', ['gamachine.v1'], { pairId: 'short' })).status, 404);
  assert.equal((await open(env, 'admin', ['gamachine.v1'])).status, 404);
});
