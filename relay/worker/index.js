import { Room } from './room.js';
import { IpLimiter } from './limiter.js';
import { PAIR_ID_RE, plain } from './util.js';

export { Room, IpLimiter };

// Only these paths are served; anything else in the assets folder stays private.
const STATIC = {
  '/p': '/index.html',
  '/app.js': '/app.js',
  '/crypto.js': '/crypto.js',
  '/net.js': '/net.js',
  '/store.js': '/store.js',
  '/style.css': '/style.css',
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
};

function csp(url) {
  // Safari has not always matched ws(s): against 'self', so the socket origin is explicit.
  const ws = (url.protocol === 'https:' ? 'wss://' : 'ws://') + url.host;
  return [
    "default-src 'none'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self'",
    `connect-src 'self' ${ws}`,
    "manifest-src 'self'",
    "worker-src 'self'",
    "base-uri 'none'",
    "form-action 'none'",
    "frame-ancestors 'none'",
  ].join('; ');
}

async function serveStatic(request, env, url) {
  const assetPath = STATIC[url.pathname];
  if (!assetPath) return plain(404, 'not found');
  const upstream = await env.ASSETS.fetch(new Request(new URL(assetPath, url.origin), { method: 'GET' }));
  if (!upstream.ok) return plain(404, 'not found');
  const ext = assetPath.slice(assetPath.lastIndexOf('.') + 1);
  const headers = new Headers({
    'Content-Type': TYPES[ext] || 'application/octet-stream',
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer',
    'Cross-Origin-Opener-Policy': 'same-origin',
    'Cache-Control': ext === 'png' ? 'public, max-age=86400' : 'no-cache',
  });
  if (ext === 'html') {
    headers.set('Content-Security-Policy', csp(url));
    headers.set('X-Frame-Options', 'DENY');
  }
  return new Response(request.method === 'HEAD' ? null : upstream.body, { status: 200, headers });
}

async function routeSocket(request, env, role, pairId) {
  if (request.headers.get('Upgrade')?.toLowerCase() !== 'websocket') return plain(426, 'websocket required');
  if (role === 'pair') {
    const ip = request.headers.get('CF-Connecting-IP') || 'unknown';
    const limiter = env.IP_LIMITER.get(env.IP_LIMITER.idFromName(ip));
    const verdict = await limiter.fetch('https://limiter/hit', { method: 'POST' });
    if (verdict.status === 429) return plain(429, 'too many pairing attempts', { 'Retry-After': '3600' });
  }
  const room = env.ROOM.get(env.ROOM.idFromName(pairId));
  return room.fetch(request);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const ws = url.pathname.match(/^\/ws\/(pc|phone|pair)\/([^/]+)$/);
    if (ws) {
      if (!PAIR_ID_RE.test(ws[2])) return plain(404, 'not found');
      return routeSocket(request, env, ws[1], ws[2]);
    }
    if (request.method !== 'GET' && request.method !== 'HEAD') return plain(405, 'method not allowed');
    if (url.pathname === '/') return Response.redirect(url.origin + '/p', 302);
    return serveStatic(request, env, url);
  },
};
