// Builds test/vectors.json from test/nodeimpl.mjs (node:crypto only).
// Every input is fixed, so the output is byte-for-byte reproducible;
// crypto.test.mjs fails if the committed file drifts from this builder.
// Run: node test/make-vectors.mjs

import { writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import * as N from './nodeimpl.mjs';

const P256_ORDER = BigInt('0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551');

function fixedKey(label) {
  const d = N.sha256(Buffer.from('gamachine test vector ' + label));
  const v = BigInt('0x' + d.toString('hex'));
  if (v === 0n || v >= P256_ORDER) throw new Error('label gives an invalid scalar: ' + label);
  return N.keyPair(d);
}

const fixed = (label, n) => N.sha256(Buffer.from('gamachine test vector ' + label)).subarray(0, n);

export function buildVectors() {
  const phone = fixedKey('phone static');
  const pc = fixedKey('pc static');
  const phoneEph = fixedKey('phone ephemeral');
  const pcEph = fixedKey('pc ephemeral');

  const pairId = N.enc(fixed('pair id', 16));
  const pairSecret = fixed('pair secret', 16);
  const deviceName = 'iPhone (Burak)';
  const macInput = Buffer.concat([phone.pub, Buffer.from(deviceName, 'utf8')]);
  const mac = N.pairMac(pairSecret, phone.pub, deviceName);
  const pairRequest = { type: 'pair_request', phone_pub: N.enc(phone.pub), device_name: deviceName, mac: N.enc(mac) };

  const kStatic = N.ecdh(phone.d, pc.pub);
  const kStaticFromPc = N.ecdh(pc.d, phone.pub);
  if (!kStatic.equals(kStaticFromPc)) throw new Error('ECDH asymmetry');
  const sas = N.sas(kStatic, pairSecret);
  const kPair = N.pairKey(kStatic, pairSecret);

  const roomKey = N.enc(fixed('room key', 32));
  const token = N.enc(fixed('phone token', 32));
  const deviceId = N.enc(fixed('device id', 16));
  const vapidPub = N.enc(fixedKey('vapid').pub);
  const pairOkPlain = JSON.stringify({ device_id: deviceId, token, vapid_pub: vapidPub });
  const pairOk = N.pcPairOk(kPair, JSON.parse(pairOkPlain));

  const t = 1790000000;
  const helloInput = N.helloInput(deviceId, phoneEph.pub, t);
  const hello = {
    type: 'hello',
    device_id: deviceId,
    eph_phone_pub: N.enc(phoneEph.pub),
    t,
    tag: N.enc(N.hmac(kStatic, helloInput)),
  };
  const { ack, keys } = N.pcHandleHello(hello, { kStatic, knownDeviceId: deviceId, nowSeconds: t, ephD: pcEph.d });
  const ephShared = N.ecdh(phoneEph.d, pcEph.pub);

  const plains = [
    [N.PHONE_TO_PC, 1, { id: 1, type: 'list_chats' }],
    [N.PHONE_TO_PC, 2, { id: 2, type: 'answer_card', card_id: 'card-7', decision: 'approve' }],
    [N.PHONE_TO_PC, 3, { id: 3, type: 'send_message', chat_id: 'c1', text: 'Şu testi çalıştır, sonra özetle 🙂' }],
    [N.PC_TO_PHONE, 1, { id: 1, ok: true, result: { chats: [] } }],
    [N.PC_TO_PHONE, 2, { type: 'card_opened', card: { card_id: 'card-7', chat_id: 'c1', title: 'Onay bekliyor - Codex (Arena): git commit -m "düzeltme"' } }],
  ];
  const frames = plains.map(([direction, c, obj]) => {
    const plaintext = JSON.stringify(obj);
    const key = direction === N.PHONE_TO_PC ? keys.phoneToPc : keys.pcToPhone;
    return {
      direction,
      c,
      nonce: N.enc(N.nonce(direction, c)),
      plaintext,
      frame: N.seal(key, direction, c, plaintext),
    };
  });

  return {
    version: 1,
    spec: 'docs/remote-control.md + relay/README.md (byte layouts)',
    encoding: 'byte strings are unpadded base64url; plaintexts are UTF-8 JSON exactly as written here',
    keys: {
      phone_static: { d: N.enc(phone.d), pub: N.enc(phone.pub) },
      pc_static: { d: N.enc(pc.d), pub: N.enc(pc.pub) },
      phone_ephemeral: { d: N.enc(phoneEph.d), pub: N.enc(phoneEph.pub) },
      pc_ephemeral: { d: N.enc(pcEph.d), pub: N.enc(pcEph.pub) },
    },
    relay: {
      room_key: roomKey,
      room_key_hash: N.enc(N.sha256(Buffer.from(roomKey, 'utf8'))),
      token,
      token_hash: N.enc(N.sha256(Buffer.from(token, 'utf8'))),
    },
    pairing: {
      pair_id: pairId,
      pair_secret: N.enc(pairSecret),
      qr_fragment: `${pairId}.${N.enc(pc.pub)}.${N.enc(pairSecret)}`,
      device_name: deviceName,
      mac_input: N.enc(macInput),
      mac: N.enc(mac),
      pair_request: pairRequest,
      k_static: N.enc(kStatic),
      sas_okm: N.enc(sas.okm),
      sas: sas.code,
      k_pair: N.enc(kPair),
      pair_ok_plaintext: pairOkPlain,
      pair_ok: pairOk,
    },
    session: {
      device_id: deviceId,
      t,
      hello_tag_input: N.enc(helloInput),
      hello,
      hello_ack_tag_input: N.enc(N.helloAckInput(phoneEph.pub, pcEph.pub)),
      hello_ack: ack,
      eph_shared: N.enc(ephShared),
      session_ikm: N.enc(keys.ikm),
      session_okm: N.enc(keys.okm),
      key_phone_to_pc: N.enc(keys.phoneToPc),
      key_pc_to_phone: N.enc(keys.pcToPhone),
    },
    frames,
  };
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const out = fileURLToPath(new URL('./vectors.json', import.meta.url));
  writeFileSync(out, JSON.stringify(buildVectors(), null, 2) + '\n');
  console.log('wrote', out);
}
