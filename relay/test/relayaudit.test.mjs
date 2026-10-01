// Regression tests for the four relay findings of Codex relayaudit, 28 Sep 2026
// (room pre-claim, unbounded rooms, character-counted frame cap, cross-origin
// quota drain). Each started as a probe that reproduced the defect.

import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { installRuntimeGlobals, FakeNamespace } from './fake-do.mjs';
import { pairIdFor, sha256, enc } from './nodeimpl.mjs';

installRuntimeGlobals();
const { default: worker, Room, IpLimiter } = await import('../worker/index.js');
const { CLOSE, LIMITS } = await import('../worker/room.js');
const { pair } = await import('../public/net.js');
const C = await import('../public/crypto.js');

const PAGE = 'https://relay.test';
const IP = '198.51.100.7';
const hash = (s) => enc(sha256(Buffer.from(s)));

const realNow = Date.now;
let now = 1_800_000_000_000;
beforeEach(() => {
  now = 1_800_000_000_000;
  Date.now = () => now;
});
afterEach(() => {
  Date.now = realNow;
});

function makeEnv() {
  const env = { ASSETS: { fetch: async () => new Response('', { status: 404 }) } };
  env.ROOM = new FakeNamespace(Room, env);
  env.IP_LIMITER = new FakeNamespace(IpLimiter, env);
  return env;
}

async function open(env, role, pairId, { credential = null, origin = role === 'pc' ? null : PAGE, ip = IP } = {}) {
  const headers = { Upgrade: 'websocket', 'CF-Connecting-IP': ip,
    'Sec-WebSocket-Protocol': credential ? `gamachine.v1, ${credential}` : 'gamachine.v1' };
  if (origin !== null) headers.Origin = origin;
  const res = await worker.fetch(new Request(`${PAGE}/ws/${role}/${pairId}`, { headers }), env);
  return { status: res.status, sock: res.status === 101 ? res.webSocket.peer : null };
}
const pcOpen = (env, key, o = {}) => open(env, 'pc', pairIdFor(key), { credential: 'key.' + key, ...o });
const keyN = (i) => enc(sha256(Buffer.from('room key ' + i)));

// --- 1. room pre-claim -------------------------------------------------------

test('a stranger who knows the pairing id cannot claim it before the owner', async () => {
  const env = makeEnv();
  const ownerKey = 'B'.repeat(43);
  const id = pairIdFor(ownerKey);
  const stranger = await open(env, 'pc', id, { credential: 'key.' + 'A'.repeat(43) });
  assert.equal(stranger.status, 403);
  assert.equal(env.ROOM.state(id).storage.map.size, 0, 'a refused key leaves nothing behind');
  const owner = await open(env, 'pc', id, { credential: 'key.' + ownerKey });
  assert.equal(owner.status, 101);
  assert.equal((await open(env, 'pc', id, { credential: 'key.' + 'A'.repeat(43) })).status, 403);
});

// --- 2. unbounded rooms ------------------------------------------------------

test('one IP can open at most 10 new rooms per hour; reconnects are free', async () => {
  const env = makeEnv();
  let accepted = 0;
  for (let i = 0; i < 64; i++) if ((await pcOpen(env, keyN(i))).status === 101) accepted++;
  assert.equal(accepted, 10);
  const stored = [...env.ROOM.states.values()].filter((st) => st.storage.map.size > 0).length;
  assert.equal(stored, 10, 'refused rooms store nothing');

  assert.equal((await pcOpen(env, keyN(0))).status, 101, 'reconnecting to an existing room is not counted');
  assert.equal((await pcOpen(env, keyN(64), { ip: '198.51.100.8' })).status, 101, 'other IPs are unaffected');
  now += 3_600_000 + 1;
  assert.equal((await pcOpen(env, keyN(65))).status, 101, 'the window slides');
});

test('a room is deleted 30 days after its PC was last there; any PC connect pushes it back', async () => {
  const env = makeEnv();
  const key = keyN(1);
  const id = pairIdFor(key);
  const r = () => env.ROOM.object(id);
  const pc = (await pcOpen(env, key)).sock;
  await r().webSocketMessage(pc, JSON.stringify({ type: 'register_tokens', hashes: [hash('T'.repeat(43))] }));
  assert.equal(r().ctx.storage.alarm, now + LIMITS.roomIdleMs);

  // Still connected when the alarm fires: the room stays.
  now += LIMITS.roomIdleMs;
  await r().alarm();
  assert.ok(r().ctx.storage.map.size > 0);
  assert.equal(r().ctx.storage.alarm, now + LIMITS.roomIdleMs);

  pc.close(1000, 'client');
  await r().webSocketClose(pc, 1000, 'client', true);
  const left = now;
  const phone = (await open(env, 'phone', id, { credential: 'tok.' + 'T'.repeat(43) })).sock;

  now = left + LIMITS.roomIdleMs - 1;
  await r().alarm();
  assert.ok(r().ctx.storage.map.size > 0, 'not yet');

  // A reconnect in between pushes the deadline forward.
  const pc2 = (await pcOpen(env, key)).sock;
  const back = now;
  pc2.close(1000, 'client');
  await r().webSocketClose(pc2, 1000, 'client', true);
  now = left + LIMITS.roomIdleMs + 1;
  await r().alarm();
  assert.ok(r().ctx.storage.map.size > 0, 'deadline moved by the reconnect');
  assert.equal(r().ctx.storage.alarm, back + LIMITS.roomIdleMs);

  now = back + LIMITS.roomIdleMs;
  await r().alarm();
  assert.equal(r().ctx.storage.map.size, 0, 'storage deleted');
  assert.equal(r().ctx.storage.alarm, null);
  assert.equal(phone.closed.code, CLOSE.roomExpired);
  assert.equal((await open(env, 'phone', id, { credential: 'tok.' + 'T'.repeat(43) })).status, 404);
});

// --- 3. frame caps in bytes ----------------------------------------------------

async function pcAndPhone(env) {
  const key = keyN(2);
  const id = pairIdFor(key);
  const pc = (await pcOpen(env, key)).sock;
  const r = env.ROOM.object(id);
  await r.webSocketMessage(pc, JSON.stringify({ type: 'register_tokens', hashes: [hash('T'.repeat(43))] }));
  const phone = (await open(env, 'phone', id, { credential: 'tok.' + 'T'.repeat(43) })).sock;
  const conn = pc.take().find((m) => m.type === 'phone_open').conn;
  return { r, pc, phone, conn };
}

test('phone frame cap counts UTF-8 bytes, not characters', async () => {
  const env = makeEnv();
  const { r, pc, phone } = await pcAndPhone(env);
  const fits = 'é'.repeat(LIMITS.phoneFrameMax / 2);
  await r.webSocketMessage(phone, fits);
  assert.equal(pc.last().data, fits, 'exactly the cap in bytes is forwarded');

  const over = 'é'.repeat(LIMITS.phoneFrameMax);
  await r.webSocketMessage(phone, over);
  assert.equal(phone.closed.code, CLOSE.badFrame);
  assert.ok(!pc.sent.some((d) => JSON.parse(d).data === over), 'not forwarded');
});

test('PC frame cap counts UTF-8 bytes too', async () => {
  const env = makeEnv();
  const { r, pc, phone, conn } = await pcAndPhone(env);
  const big = 'é'.repeat(LIMITS.pcFrameMax / 2);
  await r.webSocketMessage(pc, JSON.stringify({ type: 'to', conn, data: big }));
  assert.deepEqual(pc.last(), { type: 'error', error: 'too_large' });
  assert.ok(!phone.sent.includes(big));
  assert.equal(pc.closed, null, 'the PC stays connected');
});

// --- 4. cross-origin quota drain ---------------------------------------------

test('cross-origin pairing attempts are refused before any quota is spent', async () => {
  const env = makeEnv();
  const key = keyN(3);
  const id = pairIdFor(key);
  assert.equal((await pcOpen(env, key)).status, 101);
  for (let i = 0; i < 25; i++) {
    assert.equal((await open(env, 'pair', pairIdFor(keyN(100 + i)), { origin: 'https://evil.test' })).status, 403);
    assert.equal((await open(env, 'pair', id, { origin: 'https://evil.test' })).status, 403);
  }
  assert.equal((await open(env, 'pair', id, { origin: null })).status, 403, 'no Origin is not the page either');
  assert.equal((await open(env, 'pair', id)).status, 101, 'the real page still pairs');
});

test('phone route needs the page origin; PC route refuses any browser origin', async () => {
  const env = makeEnv();
  const key = keyN(4);
  const id = pairIdFor(key);
  for (const origin of [PAGE, 'null', 'https://evil.test']) {
    assert.equal((await pcOpen(env, key, { origin })).status, 403, origin);
  }
  assert.equal(env.ROOM.state(id).storage.map.size, 0, 'refused before the room object is touched');
  assert.equal((await pcOpen(env, key)).status, 101);
  const tok = 'tok.' + 'T'.repeat(43);
  assert.equal((await open(env, 'phone', id, { credential: tok, origin: 'https://evil.test' })).status, 403);
  assert.equal((await open(env, 'phone', id, { credential: tok, origin: 'http://relay.test' })).status, 403, 'scheme is part of the origin');
  const reached = await open(env, 'phone', id, { credential: tok });
  assert.equal(reached.status, 101, 'right origin reaches the token check');
  assert.equal(reached.sock.closed.code, CLOSE.tokensPending, 'the PC has not sent its token list');
});

test('pairing into a room that does not exist: told so, closed, no quota spent', async () => {
  const env = makeEnv();
  for (let i = 0; i < 25; i++) {
    const r = await open(env, 'pair', pairIdFor(keyN(200 + i)));
    assert.equal(r.status, 101);
    assert.deepEqual(r.sock.json(), [{ type: 'no_room' }]);
    assert.equal(r.sock.closed.code, CLOSE.noRoom);
  }
  assert.equal(env.IP_LIMITER.state(IP).storage.map.size, 0, 'IP budget untouched');
  const key = keyN(5);
  await pcOpen(env, key);
  assert.equal((await open(env, 'pair', pairIdFor(key))).status, 101);
});

class FakeWS {
  constructor(script) {
    this.script = script;
    setTimeout(() => this.script(this), 0);
  }
  send() {}
  close() {}
}

async function pairWith(script) {
  const pc = C.b64u(new Uint8Array(await crypto.subtle.exportKey('raw', (await crypto.subtle.generateKey({ name: 'ECDH', namedCurve: 'P-256' }, true, ['deriveBits'])).publicKey)));
  const parsed = C.parsePairFragment(`#${pairIdFor(keyN(6))}.${pc}.${enc(Buffer.alloc(16, 1))}`);
  const WS = class extends FakeWS { constructor() { super(script); } };
  return pair({ origin: 'wss://relay.test', parsed, deviceName: 'iPhone', onSas: () => {}, WS });
}

test('the phone page reports no_room separately from refusals', async () => {
  await assert.rejects(pairWith((ws) => {
    ws.onopen();
    ws.onmessage({ data: '{"type":"no_room"}' });
    ws.onclose({ code: 4008 });
  }), { message: 'no_room' });
  await assert.rejects(pairWith((ws) => { ws.onopen(); ws.onclose({ code: 4008 }); }), { message: 'no_room' });
  await assert.rejects(pairWith((ws) => ws.onclose({ code: 1006 })), { message: 'refused' });
});
