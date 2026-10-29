import { Room } from './room.js';
import { IpLimiter } from './limiter.js';
import { PAIR_ID_RE, plain } from './util.js';

export { Room, IpLimiter };

// Only these paths are served; anything else in the assets folder stays private.
const STATIC = {
  '/p': '/index.html',
  '/app.js': '/app.js',
  '/i18n.js': '/i18n.js',
  '/crypto.js': '/crypto.js',
  '/net.js': '/net.js',
  '/store.js': '/store.js',
  '/style.css': '/style.css',
  '/fonts.css': '/fonts.css',
  '/fonts/ChakraPetch-600-latin.woff2': '/fonts/ChakraPetch-600-latin.woff2',
  '/fonts/ChakraPetch-600-latin-ext.woff2': '/fonts/ChakraPetch-600-latin-ext.woff2',
  '/fonts/ChakraPetch-700-latin.woff2': '/fonts/ChakraPetch-700-latin.woff2',
  '/fonts/ChakraPetch-700-latin-ext.woff2': '/fonts/ChakraPetch-700-latin-ext.woff2',
  '/fonts/Figtree-300-900-latin.woff2': '/fonts/Figtree-300-900-latin.woff2',
  '/fonts/Figtree-300-900-latin-ext.woff2': '/fonts/Figtree-300-900-latin-ext.woff2',
  '/fonts/JetBrainsMono-400-700-latin.woff2': '/fonts/JetBrainsMono-400-700-latin.woff2',
  '/fonts/JetBrainsMono-400-700-latin-ext.woff2': '/fonts/JetBrainsMono-400-700-latin-ext.woff2',
  '/sw.js': '/sw.js',
  '/manifest.webmanifest': '/manifest.webmanifest',
  '/icon-192.png': '/icon-192.png',
  '/icon-512.png': '/icon-512.png',
  '/apple-touch-icon.png': '/apple-touch-icon.png',
  '/favicon.ico': '/icon-192.png',
};

const TYPES = {
  html: 'text/html; charset=utf-8',
  js: 'text/javascript; charset=utf-8',
  css: 'text/css; charset=utf-8',
  webmanifest: 'application/manifest+json',
  png: 'image/png',
  woff2: 'font/woff2',
  json: 'application/json',
  svg: 'image/svg+xml',
  ico: 'image/x-icon',
  txt: 'text/plain; charset=utf-8',
};

// Code and markup stay no-cache so relay fixes reach phones at once. A font file never changes
// under its name (a new cut gets a new file), so phones keep it for a year.
const CACHE = {
  png: 'public, max-age=86400',
  woff2: 'public, max-age=31536000, immutable',
};

function csp(url, scriptHashes = []) {
  // Safari has not always matched ws(s): against 'self', so the socket origin is explicit.
  const ws = (url.protocol === 'https:' ? 'wss://' : 'ws://') + url.host;
  return [
    "default-src 'none'",
    ["script-src 'self'", ...scriptHashes.map((h) => `'${h}'`)].join(' '),
    "style-src 'self'",
    "font-src 'self'",
    "img-src 'self'",
    `connect-src 'self' ${ws}`,
    "manifest-src 'self'",
    "worker-src 'self'",
    "base-uri 'none'",
    "form-action 'none'",
    "frame-ancestors 'none'",
  ].join('; ');
}

function staticResponse(request, url, upstream, ext, cache, scriptHashes = []) {
  const headers = new Headers({
    'Content-Type': TYPES[ext] || 'application/octet-stream',
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer',
    'Cross-Origin-Opener-Policy': 'same-origin',
    'Cache-Control': cache,
  });
  if (ext === 'html') {
    headers.set('Content-Security-Policy', csp(url, scriptHashes));
    headers.set('X-Frame-Options', 'DENY');
  }
  return new Response(request.method === 'HEAD' ? null : upstream.body, { status: 200, headers });
}

async function serveStatic(request, env, url) {
  const assetPath = STATIC[url.pathname];
  if (!assetPath) return plain(404, 'not found');
  const upstream = await env.ASSETS.fetch(new Request(new URL(assetPath, url.origin), { method: 'GET' }));
  if (!upstream.ok) return plain(404, 'not found');
  const ext = assetPath.slice(assetPath.lastIndexOf('.') + 1);
  return staticResponse(request, url, upstream, ext, CACHE[ext] || 'no-cache');
}

const APP_INDEX = '/app/index.html';
// The sha256 of every inline script of each exported page, keyed by its path under /app/,
// written by scripts/build-remote-web.sh when it copies the export. It sits outside /app/, so
// only the worker reads it.
const APP_CSP = '/app-csp.json';
const CSP_HASH_RE = /^sha256-[A-Za-z0-9+/]{43}=$/;

// The page's own inline scripts and nothing else; a missing, broken or foreign entry adds none.
async function appScriptHashes(env, url, assetPath) {
  try {
    const res = await env.ASSETS.fetch(new Request(new URL(APP_CSP, url.origin), { method: 'GET' }));
    if (!res.ok) return [];
    const hashes = (await res.json())?.[assetPath.slice('/app/'.length)];
    return Array.isArray(hashes) ? hashes.filter((h) => typeof h === 'string' && CSP_HASH_RE.test(h)) : [];
  } catch {
    return [];
  }
}

// The web remote bundle (renderer export) lives under /app/ and is served by prefix. A path
// without a file extension is a client route and gets the index page; a missing file does not.
async function serveApp(request, env, url) {
  const path = url.pathname;
  if (path.includes('\\') || /%2e|%2f|%5c/i.test(path) || path.split('/').includes('..')) return plain(404, 'not found');
  const last = path.slice(path.lastIndexOf('/') + 1);
  const hasExt = last.includes('.');
  let assetPath = path === '/app' || path.endsWith('/') ? APP_INDEX : path;
  let upstream = await env.ASSETS.fetch(new Request(new URL(assetPath, url.origin), { method: 'GET' }));
  if (!upstream.ok && !hasExt && assetPath !== APP_INDEX) {
    assetPath = APP_INDEX;
    upstream = await env.ASSETS.fetch(new Request(new URL(assetPath, url.origin), { method: 'GET' }));
  }
  if (!upstream.ok) return plain(404, 'not found');
  const ext = assetPath.slice(assetPath.lastIndexOf('.') + 1);
  const cache = assetPath.startsWith('/app/_next/static/') ? 'public, max-age=31536000, immutable' : 'no-cache';
  const hashes = ext === 'html' ? await appScriptHashes(env, url, assetPath) : [];
  return staticResponse(request, url, upstream, ext, cache, hashes);
}

// Browsers always send Origin on a WebSocket upgrade and a page cannot change it.
// Phone routes accept only the relay's own page; the PC route accepts no
// browser at all (the bridge sends no Origin). Checked before any quota is
// touched, so another site cannot spend a visitor's pairing budget
// (Codex relayaudit, 28 Sep 2026).
function originAllowed(request, url, role) {
  const origin = request.headers.get('Origin');
  return role === 'pc' ? origin === null : origin === url.origin;
}

async function routeSocket(request, env, url, role, pairId) {
  if (request.headers.get('Upgrade')?.toLowerCase() !== 'websocket') return plain(426, 'websocket required');
  if (!originAllowed(request, url, role)) return plain(403, 'origin not allowed');
  const room = env.ROOM.get(env.ROOM.idFromName(pairId));
  return room.fetch(request);
}

// Per-IP budget checked before routing, so a flood never reaches static serving or a
// Durable Object. A missing binding or a failing call lets the request through.
async function overBudget(request, env, binding = env.RELAY_RATE_LIMIT) {
  if (!binding) return false;
  try {
    const { success } = await binding.limit({ key: request.headers.get('CF-Connecting-IP') || 'unknown' });
    return !success;
  } catch {
    return false;
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const isApp = url.pathname === '/app' || url.pathname.startsWith('/app/');
    // The web client loads a bundle of files, so /app/ has a larger budget of its own.
    if (await overBudget(request, env, isApp ? env.RELAY_APP_RATE_LIMIT : env.RELAY_RATE_LIMIT)) {
      return plain(429, 'too many requests', { 'Retry-After': '60' });
    }
    const ws = url.pathname.match(/^\/ws\/(pc|phone|pair)\/([^/]+)$/);
    if (ws) {
      if (!PAIR_ID_RE.test(ws[2])) return plain(404, 'not found');
      return routeSocket(request, env, url, ws[1], ws[2]);
    }
    if (request.method !== 'GET' && request.method !== 'HEAD') return plain(405, 'method not allowed');
    if (isApp) return serveApp(request, env, url);
    if (url.pathname === '/') return Response.redirect(url.origin + '/p', 302);
    return serveStatic(request, env, url);
  },
};
