// End-to-end against the real Workers runtime: starts `wrangler dev --local`
// (one process, killed with its tree in `after`), then drives the page's own
// net.js/crypto.js as the phone and test/nodeimpl.mjs as the PC.
// Skipped when wrangler is not installed or RELAY_IT=0.

import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { request as httpRequest } from 'node:http';
import { request as httpsRequest } from 'node:https';
import { randomBytes } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import * as N from './nodeimpl.mjs';
import * as C from '../public/crypto.js';
import { pair, Link } from '../public/net.js';

const PORT = Number(process.env.RELAY_IT_PORT || 8799);
// RELAY_IT_URL=https://... runs the same checks against a deployed relay instead.
const REMOTE = process.env.RELAY_IT_URL || null;
const HTTP = REMOTE || `http://127.0.0.1:${PORT}`;
const WSO = HTTP.replace(/^http/, 'ws');
const RELAY_DIR = fileURLToPath(new URL('..', import.meta.url));
// The relay only accepts phone sockets from its own page, as a browser would
// send them; Node's WebSocket sends no Origin unless told to.
class PageWS extends WebSocket {
  constructor(url, protocols) {
    super(url, { protocols, headers: { Origin: HTTP } });
  }
}
const enabled = !!REMOTE || (process.env.RELAY_IT !== '0' && spawnSync('wrangler --version', { shell: true }).status === 0);

let proc = null;
let stateDir = null;

before(async () => {
  if (!enabled || REMOTE) return;
  // Refuse to run against something already on the port: the test must hit its own instance.
  const busy = await fetch(HTTP + '/p').then(() => true, () => false);
  if (busy) throw new Error(`port ${PORT} is already in use`);
  stateDir = mkdtempSync(join(tmpdir(), 'gm-relay-it-'));
  proc = spawn(`wrangler dev --local --port ${PORT} --ip 127.0.0.1 --persist-to "${stateDir}"`, {
    cwd: RELAY_DIR,
    shell: true,
    stdio: 'ignore',
  });
  for (let i = 0; i < 120; i++) {
    try {
      const r = await fetch(HTTP + '/p');
      if (r.status === 200) return;
    } catch {}
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error('wrangler dev did not come up');
});

after(async () => {
  if (proc) {
    const exited = new Promise((r) => proc.once('exit', r));
    if (process.platform === 'win32') spawnSync('taskkill', ['/pid', String(proc.pid), '/T', '/F']);
    else proc.kill('SIGTERM');
    await exited;
  }
  if (!stateDir) return;
  // workerd releases its files (and the folder handle) a moment after the kill on Windows.
  for (let i = 0; i < 40; i++) {
    try {
      rmSync(stateDir, { recursive: true, force: true });
      return;
    } catch {
      await new Promise((r) => setTimeout(r, 250));
    }
  }
  rmSync(stateDir, { recursive: true, force: true });
});

// Raw upgrade request so the refusal status is visible (a WebSocket client only sees "error").
// Phone paths get the page's Origin unless `origin` says otherwise (null = none).
function upgradeStatus(path, protocols, origin = path.startsWith('/ws/pc/') ? null : HTTP) {
  return new Promise((resolve, reject) => {
    const headers = {
      Connection: 'Upgrade',
      Upgrade: 'websocket',
      'Sec-WebSocket-Version': '13',
      'Sec-WebSocket-Key': randomBytes(16).toString('base64'),
      'Sec-WebSocket-Protocol': protocols.join(', '),
    };
    if (origin !== null) headers.Origin = origin;
    const req = (HTTP.startsWith('https') ? httpsRequest : httpRequest)(HTTP + path, { headers });
    req.on('upgrade', (res, socket) => {
      socket.destroy();
      resolve(res.statusCode);
    });
    req.on('response', (res) => {
      res.resume();
      resolve(res.statusCode);
    });
    req.on('error', reject);
    req.end();
  });
}

// PC side over a real socket: collects relay frames, answers like the bridge would.
function connectPc(pairId, roomKey) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(`${WSO}/ws/pc/${pairId}`, ['gamachine.v1', 'key.' + roomKey]);
    const inbox = [];
    const waiters = [];
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      const i = waiters.findIndex((w) => w.pred(m));
      if (i >= 0) waiters.splice(i, 1)[0].resolve(m);
      else inbox.push(m);
    };
    ws.onerror = () => reject(new Error('pc socket failed'));
    const next = (pred, ms = 10_000) => {
      const i = inbox.findIndex(pred);
      if (i >= 0) return Promise.resolve(inbox.splice(i, 1)[0]);
      return new Promise((res, rej) => {
        const w = { pred, resolve: res };
        waiters.push(w);
        setTimeout(() => rej(new Error('pc wait timeout')), ms);
      });
    };
    const send = (obj) => ws.send(JSON.stringify(obj));
    ws.onopen = () => resolve({ ws, next, send });
  });
}

function waitFor(pred, ms = 10_000) {
  return new Promise((resolve, reject) => {
    const t0 = Date.now();
    const tick = () => {
      const v = pred();
      if (v) return resolve(v);
      if (Date.now() - t0 > ms) return reject(new Error('timeout'));
      setTimeout(tick, 25);
    };
    tick();
  });
}

function newRoom() {
  const roomKey = N.enc(randomBytes(32));
  return { roomKey, pairId: N.pairIdFor(roomKey) };
}

// Leaves nothing stored for the test's pairing id; the relay closes the socket itself.
async function resetRoom(pc) {
  const closed = new Promise((r) => pc.ws.addEventListener('close', (ev) => r(ev.code)));
  pc.send({ type: 'reset_room' });
  assert.equal(await closed, 4006);
}

test('static routes on the real runtime', { skip: !enabled }, async () => {
  const page = await fetch(HTTP + '/p');
  assert.equal(page.status, 200);
  assert.match(page.headers.get('content-security-policy'), /connect-src 'self' wss?:\/\//);
  assert.match(await page.text(), /rel="manifest"/);
  for (const p of ['/sw.js', '/manifest.webmanifest', '/app.js', '/crypto.js', '/net.js', '/store.js', '/style.css', '/icon-192.png', '/icon-512.png', '/apple-touch-icon.png']) {
    assert.equal((await fetch(HTTP + p)).status, 200, p);
  }
  assert.equal((await fetch(HTTP + '/index.html')).status, 404);
  assert.equal((await fetch(HTTP + '/../wrangler.toml')).status, 404);
});

test('socket refusals happen before the upgrade', { skip: !enabled }, async () => {
  const { pairId, roomKey } = newRoom();
  assert.equal(await upgradeStatus(`/ws/phone/${pairId}`, ['gamachine.v1', 'tok.' + N.enc(randomBytes(32))]), 404);
  const pc = await connectPc(pairId, roomKey);
  await pc.next((m) => m.type === 'welcome');
  assert.equal(await upgradeStatus(`/ws/phone/${pairId}`, ['gamachine.v1', 'tok.' + N.enc(randomBytes(32))]), 401);
  assert.equal(await upgradeStatus(`/ws/phone/${pairId}`, ['gamachine.v1']), 401);
  assert.equal(await upgradeStatus(`/ws/pc/${pairId}`, ['gamachine.v1', 'key.' + N.enc(randomBytes(32))]), 403, 'key does not derive this id');
  assert.equal(await upgradeStatus(`/ws/pc/${pairId}`, ['gamachine.v1', 'key.' + roomKey], HTTP), 403, 'browser origin on the PC route');
  assert.equal(await upgradeStatus(`/ws/pc/${pairId}`, ['key.' + roomKey]), 400);
  assert.equal(await upgradeStatus(`/ws/pair/${pairId}`, ['gamachine.v1'], 'https://evil.example'), 403, 'other site');
  assert.equal(await upgradeStatus(`/ws/pair/${pairId}`, ['gamachine.v1'], null), 403, 'no origin');
  assert.equal(await upgradeStatus(`/ws/pair/${newRoom().pairId}`, ['gamachine.v1']), 101, 'no room: accepted, told, closed');
  const ghost = C.parsePairFragment(`#${newRoom().pairId}.${N.enc(N.keyPair().pub)}.${N.enc(randomBytes(16))}`);
  await assert.rejects(pair({ origin: WSO, parsed: ghost, deviceName: 'iPhone', onSas: () => {}, WS: PageWS }), { message: 'no_room' });
  for (let i = 0; i < 5; i++) assert.equal(await upgradeStatus(`/ws/pair/${pairId}`, ['gamachine.v1']), 101);
  assert.equal(await upgradeStatus(`/ws/pair/${pairId}`, ['gamachine.v1']), 429);
  await resetRoom(pc);
});

test('pairing, handshake, encrypted RPC and token drop through the real relay', { skip: !enabled }, async () => {
  const { pairId, roomKey } = newRoom();
  const pcStatic = N.keyPair();
  const pairSecret = randomBytes(16);
  const pc = await connectPc(pairId, roomKey);
  await pc.next((m) => m.type === 'welcome');

  // Phone: exactly what the page does after scanning the QR.
  const parsed = C.parsePairFragment(`#${pairId}.${N.enc(pcStatic.pub)}.${N.enc(pairSecret)}`);
  let phoneSas = null;
  const pairing = pair({ origin: WSO, parsed, deviceName: 'iPhone', onSas: (s) => (phoneSas = s), WS: PageWS });

  // PC: verify the request, compare the code, register the token, answer.
  const opened = await pc.next((m) => m.type === 'pair_open');
  const from = await pc.next((m) => m.type === 'from' && m.conn === opened.conn);
  const req = JSON.parse(from.data);
  const verified = N.pcHandlePairRequest(req, { pcD: pcStatic.d, pairSecret });
  assert.equal(verified.sas, phoneSas, 'both screens show the same code');
  const deviceId = N.enc(randomBytes(16));
  const token = N.enc(randomBytes(32));
  pc.send({ type: 'register_tokens', hashes: [N.enc(N.sha256(Buffer.from(token)))] });
  await pc.next((m) => m.type === 'tokens_ok');
  pc.send({ type: 'to', conn: opened.conn, data: JSON.stringify(N.pcPairOk(verified.kPair, { device_id: deviceId, token, vapid_pub: 'x' })) });
  const paired = await pairing;
  assert.equal(paired.deviceId, deviceId);
  assert.equal(paired.token, token);

  // Session: the page's Link against the node PC.
  const statuses = [];
  const pushes = [];
  const link = new Link({
    origin: WSO,
    device: { pairId, pcPub: parsed.pcPub, deviceId, token, privateKey: paired.privateKey },
    onStatus: (s, info) => statuses.push([s, info]),
    onPush: (m) => pushes.push(m),
    WS: PageWS,
  });
  await link.start();
  const phoneOpen = await pc.next((m) => m.type === 'phone_open');
  assert.equal(phoneOpen.token_hash, N.enc(N.sha256(Buffer.from(token))));
  const helloFrom = await pc.next((m) => m.type === 'from' && m.conn === phoneOpen.conn);
  const session = N.pcHandleHello(JSON.parse(helloFrom.data), {
    kStatic: verified.kStatic,
    knownDeviceId: deviceId,
    nowSeconds: Math.floor(Date.now() / 1000),
  });
  pc.send({ type: 'to', conn: phoneOpen.conn, data: JSON.stringify(session.ack) });
  await waitFor(() => link.ready);

  const reply = link.request('list_chats');
  const reqFrame = await pc.next((m) => m.type === 'from' && m.conn === phoneOpen.conn);
  const inner = JSON.parse(N.open(session.keys.phoneToPc, N.PHONE_TO_PC, JSON.parse(reqFrame.data)));
  assert.equal(inner.type, 'list_chats');
  const chats = { chats: [{ chat_id: 'c1', title: 'Arena ğüş', status: 'idle' }] };
  pc.send({ type: 'to', conn: phoneOpen.conn, data: JSON.stringify(N.seal(session.keys.pcToPhone, N.PC_TO_PHONE, 1, JSON.stringify({ id: inner.id, ok: true, result: chats }))) });
  assert.deepEqual((await reply).result, chats);

  const ev = { type: 'event', chat_id: 'c1', seq: 4, kind: 'text', text: 'merhaba' };
  pc.send({ type: 'to', conn: phoneOpen.conn, data: JSON.stringify(N.seal(session.keys.pcToPhone, N.PC_TO_PHONE, 2, JSON.stringify(ev))) });
  // A replay of counter 2 must be dropped by the phone.
  pc.send({ type: 'to', conn: phoneOpen.conn, data: JSON.stringify(N.seal(session.keys.pcToPhone, N.PC_TO_PHONE, 2, JSON.stringify({ ...ev, text: 'replay' }))) });
  await waitFor(() => pushes.length >= 1);
  await new Promise((r) => setTimeout(r, 300));
  assert.deepEqual(pushes, [ev]);

  pc.send({ type: 'drop_token', hash: N.enc(N.sha256(Buffer.from(token))) });
  await waitFor(() => statuses.some(([s]) => s === 'removed'));
  assert.equal(await upgradeStatus(`/ws/phone/${pairId}`, ['gamachine.v1', 'tok.' + token]), 401);
  link.stop();
  await resetRoom(pc);
});

test('phone sees pc_offline with last_seen, then re-handshakes when the PC returns', { skip: !enabled }, async () => {
  const { pairId, roomKey } = newRoom();
  const pcStatic = N.keyPair();
  const phone = await C.generateKeyPair();
  const kStatic = await C.staticSecret(phone.privateKey, pcStatic.pub);
  const token = N.enc(randomBytes(32));
  const deviceId = N.enc(randomBytes(16));

  let pc = await connectPc(pairId, roomKey);
  await pc.next((m) => m.type === 'welcome');
  pc.send({ type: 'register_tokens', hashes: [N.enc(N.sha256(Buffer.from(token)))] });
  await pc.next((m) => m.type === 'tokens_ok');
  pc.ws.close();

  const statuses = [];
  const link = new Link({
    origin: WSO,
    device: { pairId, pcPub: pcStatic.pub, deviceId, token, privateKey: phone.privateKey },
    onStatus: (s, info) => statuses.push([s, info]),
    onPush: () => {},
    WS: PageWS,
  });
  await link.start();
  const offline = await waitFor(() => statuses.find(([s]) => s === 'pc_offline'));
  assert.equal(typeof offline[1].lastSeen, 'number');

  pc = await connectPc(pairId, roomKey);
  const welcome = await pc.next((m) => m.type === 'welcome');
  assert.equal(welcome.phones.length, 1);
  const hello = await pc.next((m) => m.type === 'from' && JSON.parse(m.data).type === 'hello');
  const session = N.pcHandleHello(JSON.parse(hello.data), { kStatic: Buffer.from(kStatic), knownDeviceId: deviceId, nowSeconds: Math.floor(Date.now() / 1000) });
  pc.send({ type: 'to', conn: hello.conn, data: JSON.stringify(session.ack) });
  await waitFor(() => link.ready);
  link.stop();
  await resetRoom(pc);
});
