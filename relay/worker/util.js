export const PROTOCOL = 'gamachine.v1';

// 128-bit ids encoded as unpadded base64url are exactly 22 characters.
export const PAIR_ID_RE = /^[A-Za-z0-9_-]{22}$/;
// SHA-256 digests encoded as unpadded base64url are exactly 43 characters.
export const HASH_RE = /^[A-Za-z0-9_-]{43}$/;
// Room keys and phone tokens: at least 256 bits of base64url.
export const SECRET_RE = /^[A-Za-z0-9_-]{43,128}$/;

export function b64u(bytes) {
  let s = '';
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export async function sha256b64u(text) {
  const d = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return b64u(new Uint8Array(d));
}

export function randomId(bytes = 8) {
  return b64u(crypto.getRandomValues(new Uint8Array(bytes)));
}

// Both inputs are digests of equal public length, so only content timing matters.
export function equalStrings(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string' || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export function parseProtocols(header) {
  if (!header) return [];
  return header.split(',').map((p) => p.trim()).filter(Boolean);
}

export function plain(status, text, extra = {}) {
  return new Response(text, {
    status,
    headers: { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'no-store', ...extra },
  });
}
