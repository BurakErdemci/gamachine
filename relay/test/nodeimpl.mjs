// Independent implementation of the protocol crypto with node:crypto only
// (createECDH / hkdfSync / createHmac / createCipheriv). It deliberately
// shares no code with public/crypto.js, so agreement between the two is a
// real cross-check. It also plays the PC's role in tests.

import { createECDH, createHash, createHmac, createCipheriv, createDecipheriv, hkdfSync, timingSafeEqual } from 'node:crypto';

export const enc = (b) => Buffer.from(b).toString('base64url');
export const dec = (s) => Buffer.from(s, 'base64url');
const utf8 = (s) => Buffer.from(s, 'utf8');

export const INFO_SAS = 'gamachine-remote-v1 sas';
export const INFO_SESSION = 'gamachine-remote-v1 session';
export const INFO_PAIR = 'gamachine-remote-v1 pair';
export const ROOM_LABEL = 'gamachine-remote-v1 room';
export const PHONE_TO_PC = 1;
export const PC_TO_PHONE = 2;

// pair_id = first 16 bytes of SHA-256(label || room_key), room_key as its ASCII text.
export const pairIdInput = (roomKey) => Buffer.concat([utf8(ROOM_LABEL), utf8(roomKey)]);
export const pairIdFor = (roomKey) => enc(sha256(pairIdInput(roomKey)).subarray(0, 16));

export function keyPair(dBuf) {
  const e = createECDH('prime256v1');
  if (dBuf) e.setPrivateKey(dBuf);
  else e.generateKeys();
  // getPrivateKey() drops leading zero bytes; scalars are always 32 bytes on the wire.
  const raw = e.getPrivateKey();
  const d = Buffer.concat([Buffer.alloc(32 - raw.length), raw]);
  return { d, pub: e.getPublicKey(null, 'uncompressed') };
}

export function ecdh(dBuf, peerPub) {
  const e = createECDH('prime256v1');
  e.setPrivateKey(dBuf);
  return e.computeSecret(peerPub);
}

export const hkdf = (ikm, salt, info, len) => Buffer.from(hkdfSync('sha256', ikm, salt, utf8(info), len));
export const hmac = (key, data) => createHmac('sha256', key).update(data).digest();
export const sha256 = (data) => createHash('sha256').update(data).digest();

export function nonce(direction, counter) {
  const n = Buffer.alloc(12);
  n.writeUInt32BE(direction, 0);
  n.writeBigUInt64BE(BigInt(counter), 4);
  return n;
}

export function seal(key, direction, counter, plaintext) {
  const c = createCipheriv('aes-256-gcm', key, nonce(direction, counter));
  const body = Buffer.concat([c.update(utf8(plaintext)), c.final(), c.getAuthTag()]);
  return { c: counter, d: enc(body) };
}

export function open(key, direction, frame) {
  const body = dec(frame.d);
  const d = createDecipheriv('aes-256-gcm', key, nonce(direction, frame.c));
  d.setAuthTag(body.subarray(body.length - 16));
  return Buffer.concat([d.update(body.subarray(0, body.length - 16)), d.final()]).toString('utf8');
}

export const pairMac = (pairSecret, phonePub, deviceName) => hmac(pairSecret, Buffer.concat([phonePub, utf8(deviceName)]));

export function sas(kStatic, pairSecret) {
  const okm = hkdf(kStatic, pairSecret, INFO_SAS, 4);
  return { okm, code: String(okm.readUInt32BE(0) % 10000).padStart(4, '0') };
}

export const pairKey = (kStatic, pairSecret) => hkdf(kStatic, pairSecret, INFO_PAIR, 32);

export function u64(t) {
  const b = Buffer.alloc(8);
  b.writeBigUInt64BE(BigInt(t));
  return b;
}

export const helloInput = (deviceId, ephPhonePub, t) => Buffer.concat([utf8('hello'), utf8(deviceId), ephPhonePub, u64(t)]);
export const helloAckInput = (ephPhonePub, ephPcPub) => Buffer.concat([utf8('hello_ack'), ephPhonePub, ephPcPub]);

export function sessionKeys(ephShared, kStatic) {
  const ikm = Buffer.concat([ephShared, kStatic]);
  const okm = hkdf(ikm, Buffer.alloc(0), INFO_SESSION, 64);
  return { ikm, okm, phoneToPc: okm.subarray(0, 32), pcToPhone: okm.subarray(32, 64) };
}

// --- the PC's side, as a reference for the Python bridge -------------------

export function pcHandlePairRequest(msg, { pcD, pairSecret }) {
  const phonePub = dec(msg.phone_pub);
  const expected = pairMac(pairSecret, phonePub, msg.device_name);
  const got = dec(msg.mac);
  if (got.length !== expected.length || !timingSafeEqual(got, expected)) throw new Error('bad mac');
  const kStatic = ecdh(pcD, phonePub);
  return { phonePub, kStatic, sas: sas(kStatic, pairSecret).code, kPair: pairKey(kStatic, pairSecret) };
}

export function pcPairOk(kPair, payload) {
  return { type: 'pair_ok', ...seal(kPair, PC_TO_PHONE, 1, JSON.stringify(payload)) };
}

export function pcHandleHello(msg, { kStatic, knownDeviceId, nowSeconds, ephD }) {
  if (msg.device_id !== knownDeviceId) throw new Error('unknown device');
  if (!Number.isSafeInteger(msg.t) || Math.abs(msg.t - nowSeconds) > 300) throw new Error('clock');
  const ephPhone = dec(msg.eph_phone_pub);
  const expected = hmac(kStatic, helloInput(msg.device_id, ephPhone, msg.t));
  const got = dec(msg.tag);
  if (got.length !== 32 || !timingSafeEqual(got, expected)) throw new Error('bad tag');
  const eph = keyPair(ephD);
  const tag = hmac(kStatic, helloAckInput(ephPhone, eph.pub));
  const keys = sessionKeys(ecdh(eph.d, ephPhone), kStatic);
  return { ack: { type: 'hello_ack', eph_pc_pub: enc(eph.pub), tag: enc(tag) }, keys, eph };
}
