// The phone page against the shapes the PC bridge sends (Backend/app/remote/):
// the real Link over a fake socket with a node:crypto PC, and the page's
// DOM-free helpers in public/net.js.

import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import * as N from './nodeimpl.mjs';
import * as C from '../public/crypto.js';
import { Link, ReplyParts, mergeParts, PHONE_FRAME_MAX } from '../public/net.js';

class FakeWS {
  static last = null;
  constructor(url, protocols) {
    this.url = url;
    this.protocols = protocols;
    this.sent = [];
    this.closed = false;
    FakeWS.last = this;
    queueMicrotask(() => this.onopen?.());
  }
  send(text) { this.sent.push(text); }
  close() {
    if (this.closed) return;
    this.closed = true;
    this.onclose?.({ code: 1000 });
  }
  deliver(obj) { this.onmessage({ data: JSON.stringify(obj) }); }
}

const tick = () => new Promise((r) => setTimeout(r, 0));
async function until(fn, ms = 2000) {
  const end = Date.now() + ms;
  while (!fn()) {
    if (Date.now() > end) throw new Error('condition not met');
    await new Promise((r) => setTimeout(r, 5));
  }
}

// Every Link keeps a ping interval running; stop them so node can exit.
const links = [];
afterEach(() => {
  for (const l of links.splice(0)) l.stop();
});

// A paired phone whose Link has finished the handshake with a node PC.
async function connected({ timeouts } = {}) {
  const pc = N.keyPair();
  const phone = await C.generateKeyPair();
  const kStatic = N.ecdh(pc.d, Buffer.from(phone.publicRaw));
  const deviceId = N.enc(crypto.getRandomValues(new Uint8Array(16)));
  const pushes = [];
  const statuses = [];
  const link = new Link({
    origin: 'wss://relay.test',
    device: { pairId: 'p', pcPub: new Uint8Array(pc.pub), deviceId, token: 't', privateKey: phone.privateKey },
    onStatus: (s) => statuses.push(s),
    onPush: (m) => pushes.push(m),
    WS: FakeWS,
    timeouts,
  });
  links.push(link);
  await link.start();
  const ws = FakeWS.last;
  await until(() => ws.sent.length === 1);
  const { ack, keys } = N.pcHandleHello(JSON.parse(ws.sent[0]), {
    kStatic, knownDeviceId: deviceId, nowSeconds: Math.floor(Date.now() / 1000),
  });
  ws.deliver(ack);
  await until(() => link.ready);
  let counter = 0;
  const pcSend = (obj) => ws.deliver(N.seal(keys.pcToPhone, N.PC_TO_PHONE, ++counter, JSON.stringify(obj)));
  // The request the phone sent last, decrypted as the PC sees it.
  const lastRequest = () => {
    const frame = JSON.parse(ws.sent[ws.sent.length - 1]);
    return frame.d ? JSON.parse(N.open(keys.phoneToPc, N.PHONE_TO_PC, frame)) : {};
  };
  return { link, ws, pcSend, lastRequest, pushes, statuses };
}

// What Backend/app/remote/session.py split_reply produces for an open_chat reply.
function splitOpenChat(id, parts) {
  return parts.map((piece, i) => ({
    id, ok: true, part: i + 1, parts: parts.length,
    result: { chat_id: '7', gap: false, epoch: 'e1', last_seq: 12, ...piece },
  }));
}

// ---------------------------------------------------------------- split replies

test('split reply: parts in order are joined before the request resolves', async () => {
  const { link, pcSend, lastRequest, pushes } = await connected();
  const reply = link.request('open_chat', { chat_id: '7' });
  await until(() => lastRequest().type === 'open_chat');
  const { id } = lastRequest();
  const frames = splitOpenChat(id, [
    { messages: [{ role: 'user', text: 'a' }, { role: 'assistant', text: 'b' }], events: [] },
    { messages: [{ role: 'user', text: 'c' }], events: [{ type: 'event', kind: 'turn_start', seq: 11 }] },
    { messages: [], events: [{ type: 'event', kind: 'text', seq: 12, text: 'x' }] },
  ]);
  let settled = false;
  reply.then(() => (settled = true));
  pcSend(frames[0]);
  pcSend(frames[1]);
  await tick();
  await tick();
  assert.equal(settled, false, 'not resolved on the first part');
  pcSend(frames[2]);
  const r = await reply;
  assert.equal(r.ok, true);
  assert.equal(r.part, undefined);
  assert.equal(r.parts, undefined);
  assert.deepEqual(r.result.messages.map((m) => m.text), ['a', 'b', 'c']);
  assert.deepEqual(r.result.events.map((e) => e.seq), [11, 12]);
  assert.equal(r.result.chat_id, '7');
  assert.equal(r.result.last_seq, 12);
  assert.deepEqual(pushes, [], 'later parts never reach onPush');
});

test('split reply: parts out of order are joined in part order', () => {
  const frames = splitOpenChat(3, [
    { messages: [{ text: '1' }], events: [] },
    { messages: [{ text: '2' }], events: [] },
    { messages: [{ text: '3' }], events: [{ seq: 1 }] },
  ]);
  const set = new ReplyParts();
  assert.deepEqual(set.add(frames[2], 10), {});
  assert.deepEqual(set.add(frames[0], 10), {});
  const { done } = set.add(frames[1], 10);
  assert.deepEqual(done.result.messages.map((m) => m.text), ['1', '2', '3']);
  assert.deepEqual(done.result.events, [{ seq: 1 }]);
  assert.equal(done.id, 3);
});

test('split reply: a single-frame reply resolves unchanged', async () => {
  const { link, pcSend, lastRequest } = await connected();
  const reply = link.request('list_chats');
  await until(() => lastRequest().type === 'list_chats');
  pcSend({ id: lastRequest().id, ok: true, result: { chats: [{ chat_id: '1' }] } });
  assert.deepEqual((await reply).result, { chats: [{ chat_id: '1' }] });
});

test('split reply: bounds on part count and size, and malformed sets', () => {
  const tooMany = new ReplyParts({ maxParts: 4 });
  assert.deepEqual(tooMany.add({ id: 1, ok: true, part: 1, parts: 5, result: {} }), { error: 'too_large' });

  const tooBig = new ReplyParts({ maxChars: 100 });
  assert.deepEqual(tooBig.add({ id: 1, ok: true, part: 1, parts: 2, result: {} }, 60), {});
  assert.deepEqual(tooBig.add({ id: 1, ok: true, part: 2, parts: 2, result: {} }, 60), { error: 'too_large' });

  const bad = new ReplyParts();
  assert.deepEqual(bad.add({ id: 1, ok: true, part: 0, parts: 2, result: {} }), { error: 'bad_reply' });
  assert.deepEqual(bad.add({ id: 1, ok: true, part: 3, parts: 2, result: {} }), { error: 'bad_reply' });
  assert.deepEqual(bad.add({ id: 1, ok: true, part: '1', parts: 2, result: {} }), { error: 'bad_reply' });
  const dup = new ReplyParts();
  dup.add({ id: 1, ok: true, part: 1, parts: 3, result: {} });
  assert.deepEqual(dup.add({ id: 1, ok: true, part: 1, parts: 3, result: {} }), { error: 'bad_reply' });
  assert.deepEqual(dup.add({ id: 1, ok: true, part: 2, parts: 4, result: {} }), { error: 'bad_reply' }, 'parts must not change');
});

test('split reply: an oversized set rejects the request and its remaining parts are dropped', async () => {
  const { link, pcSend, lastRequest, pushes } = await connected();
  const reply = link.request('open_chat', { chat_id: '7' });
  await until(() => lastRequest().type === 'open_chat');
  const { id } = lastRequest();
  pcSend({ id, ok: true, part: 1, parts: 1000, result: { messages: [] } });
  await assert.rejects(reply, /too_large/);
  pcSend({ id, ok: true, part: 2, parts: 1000, result: { messages: [] } });
  await tick();
  await tick();
  assert.deepEqual(pushes, []);
  assert.equal(link.pending.size, 0);
});

test('split reply: an incomplete set times out once parts stop arriving', async () => {
  const { link, pcSend, lastRequest, pushes } = await connected({ timeouts: { request: 5000, partGap: 60 } });
  const reply = link.request('open_chat', { chat_id: '7' });
  await until(() => lastRequest().type === 'open_chat');
  const { id } = lastRequest();
  const frames = splitOpenChat(id, [{ messages: [] }, { messages: [] }, { messages: [] }]);
  pcSend(frames[0]);
  await new Promise((r) => setTimeout(r, 40));
  pcSend(frames[1]); // re-arms the gap timer
  await new Promise((r) => setTimeout(r, 40));
  assert.equal(link.pending.size, 1, 'each part extends the deadline');
  await assert.rejects(reply, /timeout/);
  pcSend(frames[2]);
  await tick();
  await tick();
  assert.deepEqual(pushes, [], 'a late part is dropped, not shown as a push');
});

test('split reply: an error part wins over the others', () => {
  const merged = mergeParts([
    { id: 1, ok: true, part: 1, parts: 2, result: { messages: [1] } },
    { id: 1, ok: false, part: 2, parts: 2, error: 'internal' },
  ]);
  assert.deepEqual(merged, { id: 1, ok: false, error: 'internal' });
});

test('replies nobody waits for are dropped; pushes still arrive', async () => {
  const { pcSend, pushes } = await connected();
  pcSend({ id: 99, ok: true, result: {} });
  pcSend({ id: null, ok: false, error: 'busy' });
  pcSend({ type: 'card_closed', card_id: 'k' });
  await until(() => pushes.length === 1);
  await tick();
  assert.deepEqual(pushes, [{ type: 'card_closed', card_id: 'k' }]);
});

test('a request whose frame would exceed the relay phone cap is refused before sending', async () => {
  const { link, ws } = await connected();
  const before = ws.sent.length;
  // 3-byte UTF-8 characters: under the 20 000-character bridge limit, over 64 KiB once sealed.
  await assert.rejects(link.request('send_message', { chat_id: '1', text: '日'.repeat(19_000) }), /too_large/);
  assert.equal(ws.sent.length, before, 'nothing reached the socket (the relay would close it)');
  assert.ok(!ws.closed);
  const ok = link.request('send_message', { chat_id: '1', text: 'kısa' });
  await until(() => ws.sent.length === before + 1);
  assert.ok(ws.sent[ws.sent.length - 1].length <= PHONE_FRAME_MAX);
  link.stop();
  await assert.rejects(ok, /disconnected/);
});
