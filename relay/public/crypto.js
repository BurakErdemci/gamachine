// Protocol crypto for the phone side of docs/remote-control.md, WebCrypto only.
// Byte layouts are fixed here and mirrored by test/nodeimpl.mjs and
// test/vectors.json; the Python bridge must reproduce the same vectors.

const subtle = globalThis.crypto.subtle;
const te = new TextEncoder();
const td = new TextDecoder();

export const INFO = {
  sas: 'gamachine-remote-v1 sas',
  session: 'gamachine-remote-v1 session',
  pair: 'gamachine-remote-v1 pair',
};
export const DIR = { phoneToPc: 1, pcToPhone: 2 };
export const HELLO_WINDOW_S = 300;

const P256 = { name: 'ECDH', namedCurve: 'P-256' };

export function b64u(bytes) {
  let s = '';
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export function fromB64u(str) {
  if (typeof str !== 'string' || !/^[A-Za-z0-9_-]*$/.test(str)) throw new Error('bad base64url');
  const s = str.replace(/-/g, '+').replace(/_/g, '/');
  const bin = atob(s + '==='.slice((s.length + 3) % 4));
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export function concat(...parts) {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let o = 0;
  for (const p of parts) {
    out.set(p, o);
    o += p.length;
  }
  return out;
}

export function u64be(n) {
  const out = new Uint8Array(8);
  new DataView(out.buffer).setBigUint64(0, BigInt(n));
  return out;
}

export function nonce(direction, counter) {
  const out = new Uint8Array(12);
  const v = new DataView(out.buffer);
  v.setUint32(0, direction);
  v.setBigUint64(4, BigInt(counter));
  return out;
}

export function equalBytes(a, b) {
  if (a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a[i] ^ b[i];
  return d === 0;
}

// `#<pair_id>.<pc_pub>.<pair_secret>`: 16-byte id, 65-byte uncompressed P-256 key, 16-byte secret.
export function parsePairFragment(fragment) {
  const f = fragment.startsWith('#') ? fragment.slice(1) : fragment;
  const m = f.match(/^([A-Za-z0-9_-]{22})\.([A-Za-z0-9_-]{87})\.([A-Za-z0-9_-]{22})$/);
  if (!m) return null;
  const pcPub = fromB64u(m[2]);
  if (pcPub.length !== 65 || pcPub[0] !== 4) return null;
  const pairSecret = fromB64u(m[3]);
  if (pairSecret.length !== 16 || fromB64u(m[1]).length !== 16) return null;
  return { pairId: m[1], pcPub, pairSecret };
}

// Long-term and ephemeral keys alike: the private half can never leave WebCrypto.
export async function generateKeyPair() {
  const kp = await subtle.generateKey(P256, false, ['deriveBits']);
  const pub = new Uint8Array(await subtle.exportKey('raw', kp.publicKey));
  return { privateKey: kp.privateKey, publicRaw: pub };
}

export async function ecdh(privateKey, peerPubRaw) {
  const peer = await subtle.importKey('raw', peerPubRaw, P256, false, []);
  return new Uint8Array(await subtle.deriveBits({ name: 'ECDH', public: peer }, privateKey, 256));
}

export async function hkdf(ikm, salt, info, length) {
  const key = await subtle.importKey('raw', ikm, 'HKDF', false, ['deriveBits']);
  const bits = await subtle.deriveBits({ name: 'HKDF', hash: 'SHA-256', salt, info: te.encode(info) }, key, length * 8);
  return new Uint8Array(bits);
}

export async function hmac(keyBytes, data) {
  const key = await subtle.importKey('raw', keyBytes, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return new Uint8Array(await subtle.sign('HMAC', key, data));
}

export async function sha256(bytes) {
  return new Uint8Array(await subtle.digest('SHA-256', bytes));
}

// --- pairing ------------------------------------------------------------

export function pairMacInput(phonePubRaw, deviceName) {
  return concat(phonePubRaw, te.encode(deviceName));
}

export async function buildPairRequest({ phonePubRaw, deviceName, pairSecret }) {
  const mac = await hmac(pairSecret, pairMacInput(phonePubRaw, deviceName));
  return { type: 'pair_request', phone_pub: b64u(phonePubRaw), device_name: deviceName, mac: b64u(mac) };
}

export const staticSecret = ecdh;

// 4 HKDF bytes as a big-endian uint32, mod 10000, zero-padded.
export async function computeSas(kStatic, pairSecret) {
  const b = await hkdf(kStatic, pairSecret, INFO.sas, 4);
  const n = new DataView(b.buffer).getUint32(0) % 10000;
  return String(n).padStart(4, '0');
}

export async function derivePairKey(kStatic, pairSecret) {
  return hkdf(kStatic, pairSecret, INFO.pair, 32);
}

// pair_ok is the "first encrypted frame" of docs step 4: {type, c, d} under
// K_pair in the PC->phone direction. Its plaintext is {device_id, token, vapid_pub?}.
export async function openPairOk(kPair, msg) {
  const key = await importAesKey(kPair);
  const plain = await openFrameRaw(key, DIR.pcToPhone, msg);
  const obj = JSON.parse(td.decode(plain));
  if (typeof obj.device_id !== 'string' || typeof obj.token !== 'string') throw new Error('bad pair_ok');
  return obj;
}

// --- session handshake ---------------------------------------------------

export function helloTagInput(deviceId, ephPhonePubRaw, t) {
  return concat(te.encode('hello'), te.encode(deviceId), ephPhonePubRaw, u64be(t));
}

export function helloAckTagInput(ephPhonePubRaw, ephPcPubRaw) {
  return concat(te.encode('hello_ack'), ephPhonePubRaw, ephPcPubRaw);
}

export async function buildHello({ kStatic, deviceId, ephPubRaw, t }) {
  const tag = await hmac(kStatic, helloTagInput(deviceId, ephPubRaw, t));
  return { type: 'hello', device_id: deviceId, eph_phone_pub: b64u(ephPubRaw), t, tag: b64u(tag) };
}

export async function verifyHelloAck({ kStatic, ephPhonePubRaw, msg }) {
  if (msg?.type !== 'hello_ack') throw new Error('not hello_ack');
  const ephPc = fromB64u(msg.eph_pc_pub);
  if (ephPc.length !== 65 || ephPc[0] !== 4) throw new Error('bad eph_pc_pub');
  const expected = await hmac(kStatic, helloAckTagInput(ephPhonePubRaw, ephPc));
  if (!equalBytes(expected, fromB64u(msg.tag))) throw new Error('bad hello_ack tag');
  return ephPc;
}

// K_session = HKDF(ECDH(eph,eph) || K_static, salt = empty, info = session), 64 bytes:
// first half phone->PC, second half PC->phone.
export async function deriveSessionKeys(ephShared, kStatic) {
  const okm = await hkdf(concat(ephShared, kStatic), new Uint8Array(0), INFO.session, 64);
  return { phoneToPc: okm.slice(0, 32), pcToPhone: okm.slice(32, 64) };
}

// --- frames ---------------------------------------------------------------

export async function importAesKey(raw) {
  return subtle.importKey('raw', raw, 'AES-GCM', false, ['encrypt', 'decrypt']);
}

export async function sealFrameRaw(key, direction, counter, plainBytes) {
  const ct = await subtle.encrypt({ name: 'AES-GCM', iv: nonce(direction, counter) }, key, plainBytes);
  return { c: counter, d: b64u(new Uint8Array(ct)) };
}

export async function openFrameRaw(key, direction, frame) {
  if (!Number.isSafeInteger(frame?.c) || frame.c < 1 || typeof frame.d !== 'string') throw new Error('bad frame');
  const pt = await subtle.decrypt({ name: 'AES-GCM', iv: nonce(direction, frame.c) }, key, fromB64u(frame.d));
  return new Uint8Array(pt);
}

// One per connection. Counters start at 1; the receiver keeps the last
// accepted counter and drops anything not greater (replay protection).
export class Channel {
  constructor(sendKey, recvKey, sendDir, recvDir) {
    this.sendKey = sendKey;
    this.recvKey = recvKey;
    this.sendDir = sendDir;
    this.recvDir = recvDir;
    this.sendCounter = 0;
    this.lastRecv = 0;
    this.queue = Promise.resolve();
  }

  static async forPhone({ phoneToPc, pcToPhone }) {
    return new Channel(await importAesKey(phoneToPc), await importAesKey(pcToPhone), DIR.phoneToPc, DIR.pcToPhone);
  }

  async seal(obj) {
    this.sendCounter += 1;
    return sealFrameRaw(this.sendKey, this.sendDir, this.sendCounter, te.encode(JSON.stringify(obj)));
  }

  // Returns the decoded object, or null for a replayed/forged frame.
  // Serialized: two concurrent opens must not both pass the counter check.
  open(frame) {
    const run = this.queue.then(() => this.openNow(frame));
    this.queue = run.catch(() => {});
    return run;
  }

  async openNow(frame) {
    if (!Number.isSafeInteger(frame?.c) || frame.c <= this.lastRecv) return null;
    let obj;
    try {
      obj = JSON.parse(td.decode(await openFrameRaw(this.recvKey, this.recvDir, frame)));
    } catch {
      return null;
    }
    this.lastRecv = frame.c;
    return obj;
  }
}
