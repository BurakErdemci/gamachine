// Writes public/icon-192.png, icon-512.png and apple-touch-icon.png: a blocky
// "G" as a hand-encoded PNG (IHDR/IDAT/IEND + CRC32) compressed with
// node:zlib - the approach of the push test that worked on the owner's iPhone.
// Run: node tools/gen-icons.mjs

import { deflateSync } from 'node:zlib';
import { writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const GLYPH = [
  '01111100',
  '10000010',
  '10000000',
  '10000000',
  '10001110',
  '10000010',
  '10000010',
  '01111100',
];
const BG = [0x10, 0x12, 0x14];
const FG = [0xd9, 0xb3, 0x6c];
// Glyph fills the middle 50% so iOS corner rounding never clips it.
const MARGIN = 0.25;

const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();

function crc32(buf) {
  let c = 0xffffffff;
  for (let i = 0; i < buf.length; i++) c = CRC_TABLE[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, 'ascii'), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body));
  return Buffer.concat([len, body, crc]);
}

function png(size) {
  const row = 1 + size * 3;
  const raw = Buffer.alloc(row * size);
  const inner = size * (1 - 2 * MARGIN);
  const start = size * MARGIN;
  for (let y = 0; y < size; y++) {
    raw[y * row] = 0;
    for (let x = 0; x < size; x++) {
      const gx = Math.floor(((x - start) * 8) / inner);
      const gy = Math.floor(((y - start) * 8) / inner);
      const on = gx >= 0 && gx < 8 && gy >= 0 && gy < 8 && GLYPH[gy][gx] === '1';
      const [r, g, b] = on ? FG : BG;
      const o = y * row + 1 + x * 3;
      raw[o] = r;
      raw[o + 1] = g;
      raw[o + 2] = b;
    }
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 2; // truecolor RGB
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(raw)),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

const out = (name) => fileURLToPath(new URL('../public/' + name, import.meta.url));
for (const [size, name] of [[180, 'apple-touch-icon.png'], [192, 'icon-192.png'], [512, 'icon-512.png']]) {
  const bytes = png(size);
  writeFileSync(out(name), bytes);
  console.log(name, bytes.length, 'bytes');
}
