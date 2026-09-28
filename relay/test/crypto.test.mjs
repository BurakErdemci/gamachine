// The phone's WebCrypto code (public/crypto.js) against the independent
// node:crypto implementation (test/nodeimpl.mjs) and the shared vectors.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as P from '../public/crypto.js';
import * as N from './nodeimpl.mjs';
import { buildVectors } from './make-vectors.mjs';

const V = JSON.parse(readFileSync(new URL('./vectors.json', import.meta.url), 'utf8'));
const b = (s) => P.fromB64u(s);
const hex = (u8) => Buffer.from(u8).toString('hex');

// Fixed private keys enter WebCrypto the same way the page holds them: non-extractable.
async function importFixed({ d, pub }) {
  const raw = b(pub);
  const jwk = { kty: 'EC', crv: 'P-256', d, x: P.b64u(raw.slice(1, 33)), y: P.b64u(raw.slice(33, 65)) };
  return crypto.subtle.importKey('jwk', jwk, { name: 'ECDH', namedCurve: 'P-256' }, false, ['deriveBits']);
}

test('vectors.json is exactly what the node:crypto builder produces', () => {
  assert.deepEqual(V, JSON.parse(JSON.stringify(buildVectors())));
});

test('pair_id is derived from the room key (relay WebCrypto, node:crypto and the vector agree)', async () => {
  const { pairIdFor, ROOM_LABEL } = await import('../worker/util.js');
  assert.equal(P.b64u(new TextEncoder().encode(ROOM_LABEL + V.relay.room_key)), V.relay.pair_id_input);
  assert.equal(await pairIdFor(V.relay.room_key), V.relay.pair_id);
  assert.equal(N.pairIdFor(V.relay.room_key), V.relay.pair_id);
  assert.equal(V.pairing.pair_id, V.relay.pair_id, 'the QR carries the derived id');
  assert.match(V.relay.pair_id, /^[A-Za-z0-9_-]{22}$/);
});

test('QR fragment parses; malformed fragments are refused', () => {
  const p = P.parsePairFragment('#' + V.pairing.qr_fragment);
  assert.equal(p.pairId, V.pairing.pair_id);
  assert.equal(P.b64u(p.pcPub), V.keys.pc_static.pub);
  assert.equal(P.b64u(p.pairSecret), V.pairing.pair_secret);
  const [id, pub, sec] = V.pairing.qr_fragment.split('.');
  for (const bad of [
    '', 'chat=abc', `${id}.${pub}`, `${id}.${pub}.${sec}x`, `${id}.${'A' + pub.slice(1)}.${sec}`, `${id}.${pub}.${sec.slice(1)}+`,
  ]) {
    assert.equal(P.parsePairFragment(bad), null, bad);
  }
});

test('pairing values match the vectors (mac, K_static both ways, SAS, K_pair, pair_ok)', async () => {
  const phonePriv = await importFixed(V.keys.phone_static);
  const pcPriv = await importFixed(V.keys.pc_static);
  const pairSecret = b(V.pairing.pair_secret);

  assert.equal(P.b64u(P.pairMacInput(b(V.keys.phone_static.pub), V.pairing.device_name)), V.pairing.mac_input);
  const req = await P.buildPairRequest({ phonePubRaw: b(V.keys.phone_static.pub), deviceName: V.pairing.device_name, pairSecret });
  assert.deepEqual(req, V.pairing.pair_request);

  const kStatic = await P.staticSecret(phonePriv, b(V.keys.pc_static.pub));
  assert.equal(P.b64u(kStatic), V.pairing.k_static);
  assert.equal(P.b64u(await P.staticSecret(pcPriv, b(V.keys.phone_static.pub))), V.pairing.k_static);

  assert.equal(P.b64u(await P.hkdf(kStatic, pairSecret, P.INFO.sas, 4)), V.pairing.sas_okm);
  assert.equal(await P.computeSas(kStatic, pairSecret), V.pairing.sas);
  assert.match(V.pairing.sas, /^\d{4}$/);

  const kPair = await P.derivePairKey(kStatic, pairSecret);
  assert.equal(P.b64u(kPair), V.pairing.k_pair);
  assert.deepEqual(await P.openPairOk(kPair, V.pairing.pair_ok), JSON.parse(V.pairing.pair_ok_plaintext));

  const wrong = await P.derivePairKey(kStatic, new Uint8Array(16));
  await assert.rejects(P.openPairOk(wrong, V.pairing.pair_ok));
});

test('session handshake matches the vectors (hello, hello_ack, K_session)', async () => {
  const kStatic = b(V.pairing.k_static);
  const s = V.session;
  assert.equal(P.b64u(P.helloTagInput(s.device_id, b(V.keys.phone_ephemeral.pub), s.t)), s.hello_tag_input);
  const hello = await P.buildHello({ kStatic, deviceId: s.device_id, ephPubRaw: b(V.keys.phone_ephemeral.pub), t: s.t });
  assert.deepEqual(hello, s.hello);

  assert.equal(P.b64u(P.helloAckTagInput(b(V.keys.phone_ephemeral.pub), b(V.keys.pc_ephemeral.pub))), s.hello_ack_tag_input);
  const ephPc = await P.verifyHelloAck({ kStatic, ephPhonePubRaw: b(V.keys.phone_ephemeral.pub), msg: s.hello_ack });
  assert.equal(P.b64u(ephPc), V.keys.pc_ephemeral.pub);

  const ephPriv = await importFixed(V.keys.phone_ephemeral);
  const ephShared = await P.ecdh(ephPriv, ephPc);
  assert.equal(P.b64u(ephShared), s.eph_shared);
  const keys = await P.deriveSessionKeys(ephShared, kStatic);
  assert.equal(P.b64u(keys.phoneToPc), s.key_phone_to_pc);
  assert.equal(P.b64u(keys.pcToPhone), s.key_pc_to_phone);

  const forged = { ...s.hello_ack, tag: P.b64u(new Uint8Array(32)) };
  await assert.rejects(P.verifyHelloAck({ kStatic, ephPhonePubRaw: b(V.keys.phone_ephemeral.pub), msg: forged }), /tag/);
  // A hello_ack bound to a different phone ephemeral must fail too.
  await assert.rejects(P.verifyHelloAck({ kStatic, ephPhonePubRaw: b(V.keys.pc_ephemeral.pub), msg: s.hello_ack }), /tag/);
});

test('frames match the vectors: nonce layout, sealing and opening', async () => {
  const keys = { phoneToPc: b(V.session.key_phone_to_pc), pcToPhone: b(V.session.key_pc_to_phone) };
  const phone = await P.Channel.forPhone(keys);
  const aesP2C = await P.importAesKey(keys.phoneToPc);
  for (const f of V.frames) {
    assert.equal(P.b64u(P.nonce(f.direction, f.c)), f.nonce);
    if (f.direction === P.DIR.phoneToPc) {
      const sealed = await phone.seal(JSON.parse(f.plaintext));
      assert.deepEqual(sealed, f.frame, 'phone seals byte-identical frames');
      const back = new TextDecoder().decode(await P.openFrameRaw(aesP2C, P.DIR.phoneToPc, f.frame));
      assert.equal(back, f.plaintext);
    } else {
      assert.deepEqual(await phone.open(f.frame), JSON.parse(f.plaintext));
    }
  }
  assert.equal(hex(P.nonce(2, 1)), '000000020000000000000001');
});

test('channel refuses replays, reordering, tampering and the wrong direction', async () => {
  const keys = { phoneToPc: b(V.session.key_phone_to_pc), pcToPhone: b(V.session.key_pc_to_phone) };
  const [f1, f2] = V.frames.filter((f) => f.direction === P.DIR.pcToPhone).map((f) => f.frame);
  const phone = await P.Channel.forPhone(keys);
  assert.notEqual(await phone.open(f2), null);
  assert.equal(await phone.open(f2), null, 'replay');
  assert.equal(await phone.open(f1), null, 'older counter');

  const fresh = await P.Channel.forPhone(keys);
  const d = b(f1.d);
  d[0] ^= 1;
  assert.equal(await fresh.open({ c: f1.c, d: P.b64u(d) }), null, 'tampered ciphertext');
  assert.equal(await fresh.open({ c: f1.c + 5, d: f1.d }), null, 'counter changed -> nonce changed');
  assert.equal(fresh.lastRecv, 0, 'failed frames do not advance the counter');
  const own = V.frames.find((f) => f.direction === P.DIR.phoneToPc).frame;
  assert.equal(await fresh.open(own), null, 'own direction reflected back');
  assert.equal(await fresh.open({ c: 0, d: f1.d }), null);
  assert.equal(await fresh.open({ c: '1', d: f1.d }), null);
  assert.notEqual(await fresh.open(f1), null, 'the genuine frame still opens afterwards');

  const racing = await P.Channel.forPhone(keys);
  const results = await Promise.all([racing.open(f1), racing.open(f1)]);
  assert.equal(results.filter((r) => r !== null).length, 1, 'concurrent duplicate accepted once');
});

test('live run: WebCrypto phone with random keys against the node:crypto PC', async () => {
  const pc = N.keyPair();
  const pairSecret = crypto.getRandomValues(new Uint8Array(16));
  const pairId = P.b64u(crypto.getRandomValues(new Uint8Array(16)));
  const parsed = P.parsePairFragment(`${pairId}.${N.enc(pc.pub)}.${P.b64u(pairSecret)}`);

  const phone = await P.generateKeyPair();
  await assert.rejects(crypto.subtle.exportKey('pkcs8', phone.privateKey), 'phone private key is non-extractable');
  await assert.rejects(crypto.subtle.exportKey('jwk', phone.privateKey));

  const req = await P.buildPairRequest({ phonePubRaw: phone.publicRaw, deviceName: 'iPhone', pairSecret: parsed.pairSecret });
  const pcSide = N.pcHandlePairRequest(req, { pcD: pc.d, pairSecret: Buffer.from(pairSecret) });
  assert.throws(() => N.pcHandlePairRequest({ ...req, device_name: 'Evil' }, { pcD: pc.d, pairSecret: Buffer.from(pairSecret) }), /mac/);

  const kStatic = await P.staticSecret(phone.privateKey, parsed.pcPub);
  assert.equal(hex(kStatic), pcSide.kStatic.toString('hex'));
  assert.equal(await P.computeSas(kStatic, parsed.pairSecret), pcSide.sas);

  const deviceId = N.enc(crypto.getRandomValues(new Uint8Array(16)));
  const token = N.enc(crypto.getRandomValues(new Uint8Array(32)));
  const got = await P.openPairOk(await P.derivePairKey(kStatic, parsed.pairSecret), N.pcPairOk(pcSide.kPair, { device_id: deviceId, token }));
  assert.equal(got.device_id, deviceId);
  assert.equal(got.token, token);

  const eph = await P.generateKeyPair();
  const now = Math.floor(Date.now() / 1000);
  const hello = await P.buildHello({ kStatic, deviceId, ephPubRaw: eph.publicRaw, t: now });
  assert.throws(() => N.pcHandleHello(hello, { kStatic: pcSide.kStatic, knownDeviceId: deviceId, nowSeconds: now + 301 }), /clock/);
  assert.throws(() => N.pcHandleHello(hello, { kStatic: pcSide.kStatic, knownDeviceId: 'other', nowSeconds: now }), /unknown/);
  assert.throws(() => N.pcHandleHello({ ...hello, t: now + 1 }, { kStatic: pcSide.kStatic, knownDeviceId: deviceId, nowSeconds: now }), /tag/);
  const { ack, keys: pcKeys } = N.pcHandleHello(hello, { kStatic: pcSide.kStatic, knownDeviceId: deviceId, nowSeconds: now - 299 });

  const ephPc = await P.verifyHelloAck({ kStatic, ephPhonePubRaw: eph.publicRaw, msg: ack });
  const keys = await P.deriveSessionKeys(await P.ecdh(eph.privateKey, ephPc), kStatic);
  assert.equal(hex(keys.phoneToPc), pcKeys.phoneToPc.toString('hex'));
  assert.equal(hex(keys.pcToPhone), pcKeys.pcToPhone.toString('hex'));

  const ch = await P.Channel.forPhone(keys);
  let pcLast = 0;
  for (let i = 1; i <= 50; i++) {
    const up = await ch.seal({ id: i, type: 'send_message', text: 'mesaj ' + i + ' ğüşıöç' });
    assert.ok(up.c > pcLast);
    pcLast = up.c;
    assert.equal(JSON.parse(N.open(pcKeys.phoneToPc, N.PHONE_TO_PC, up)).id, i);
    const down = N.seal(pcKeys.pcToPhone, N.PC_TO_PHONE, i, JSON.stringify({ id: i, ok: true }));
    assert.deepEqual(await ch.open(down), { id: i, ok: true });
  }
});
