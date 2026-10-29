// The web remote bundle under /app/: prefix serving, client-route fallback, cache headers, own budget.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { installRuntimeGlobals } from './fake-do.mjs';

installRuntimeGlobals();
const { default: worker } = await import('../worker/index.js');

const PAGE = 'https://relay.test';
const FILES = {
  '/app/index.html': ['<html>app</html>', 200],
  '/app/_next/static/chunks/main-abc.js': ['console.log(1)', 200],
  '/app/_next/static/media/f.woff2': ['font', 200],
  '/app/favicon.ico': ['ico', 200],
  '/worker/index.js': ['SECRET', 200],
};

function makeEnv(extra = {}, files = FILES) {
  const fetched = [];
  const ASSETS = {
    fetch: async (req) => {
      const path = new URL(req.url).pathname;
      fetched.push(path);
      const f = files[path];
      return f ? new Response(f[0], { status: f[1] }) : new Response('nope', { status: 404 });
    },
  };
  return { ASSETS, fetched, ...extra };
}

const get = (path, env, init) => worker.fetch(new Request(`${PAGE}${path}`, init), env);
const counting = (success = true) => { const keys = []; return { keys, limit: async ({ key }) => { keys.push(key); return { success }; } }; };

test('/app/ and /app serve index.html with no-cache and the page security headers', async () => {
  for (const p of ['/app/', '/app']) {
    const res = await get(p, makeEnv());
    assert.equal(res.status, 200);
    assert.equal(await res.text(), '<html>app</html>');
    assert.equal(res.headers.get('Cache-Control'), 'no-cache');
    assert.match(res.headers.get('Content-Type'), /^text\/html/);
    assert.match(res.headers.get('Content-Security-Policy'), /default-src 'none'/);
    assert.equal(res.headers.get('X-Frame-Options'), 'DENY');
    assert.equal(res.headers.get('X-Content-Type-Options'), 'nosniff');
  }
});

test('hashed files under /app/_next/static/ are immutable, other files are not', async () => {
  const env = makeEnv();
  const js = await get('/app/_next/static/chunks/main-abc.js', env);
  assert.equal(js.status, 200);
  assert.equal(js.headers.get('Cache-Control'), 'public, max-age=31536000, immutable');
  assert.match(js.headers.get('Content-Type'), /javascript/);
  const font = await get('/app/_next/static/media/f.woff2', env);
  assert.equal(font.headers.get('Cache-Control'), 'public, max-age=31536000, immutable');
  const ico = await get('/app/favicon.ico', env);
  assert.equal(ico.headers.get('Cache-Control'), 'no-cache');
});

test('an unknown deep route falls back to index.html, an unknown file does not', async () => {
  const env = makeEnv();
  const route = await get('/app/chat/session/3', env);
  assert.equal(route.status, 200);
  assert.equal(await route.text(), '<html>app</html>');
  assert.equal(route.headers.get('Cache-Control'), 'no-cache');
  assert.equal((await get('/app/_next/static/chunks/missing.js', env)).status, 404);
});

test('HEAD is served without a body, other methods are refused', async () => {
  const env = makeEnv();
  const head = await get('/app/', env, { method: 'HEAD' });
  assert.equal(head.status, 200);
  assert.equal(await head.text(), '');
  for (const method of ['POST', 'PUT', 'DELETE']) {
    assert.equal((await get('/app/', env, { method })).status, 405);
  }
});

test('path traversal never reaches files outside /app/', async () => {
  const env = makeEnv();
  for (const p of ['/app/../worker/index.js', '/app/%2e%2e/worker/index.js', '/app/..%2fworker/index.js', '/app/%2E%2E/worker/index.js', '/app/..\\worker/index.js']) {
    const res = await get(p, env);
    assert.notEqual(res.status, 200, p);
    assert.doesNotMatch(await res.text(), /SECRET/, p);
  }
  assert.ok(!env.fetched.includes('/worker/index.js'));
});

test('/app/ uses its own budget binding and leaves the main one alone', async () => {
  const main = counting();
  const app = counting();
  const env = makeEnv({ RELAY_RATE_LIMIT: main, RELAY_APP_RATE_LIMIT: app });
  await get('/app/', env, { headers: { 'CF-Connecting-IP': '198.51.100.7' } });
  assert.deepEqual(app.keys, ['198.51.100.7']);
  assert.deepEqual(main.keys, []);
  await get('/app.js', env, { headers: { 'CF-Connecting-IP': '198.51.100.7' } });
  assert.deepEqual(app.keys, ['198.51.100.7']);
  assert.deepEqual(main.keys, ['198.51.100.7']);
});

test('a denying app budget answers 429 and an exhausted main budget does not block /app/', async () => {
  const denied = await get('/app/', makeEnv({ RELAY_APP_RATE_LIMIT: counting(false) }));
  assert.equal(denied.status, 429);
  assert.equal(denied.headers.get('Retry-After'), '60');
  const ok = await get('/app/', makeEnv({ RELAY_RATE_LIMIT: counting(false), RELAY_APP_RATE_LIMIT: counting(true) }));
  assert.equal(ok.status, 200);
});

test('the app budget fails open when missing or throwing', async () => {
  assert.equal((await get('/app/', makeEnv())).status, 200);
  const throwing = { limit: async () => { throw new Error('down'); } };
  assert.equal((await get('/app/', makeEnv({ RELAY_APP_RATE_LIMIT: throwing }))).status, 200);
});

const sha = (text) => 'sha256-' + createHash('sha256').update(text, 'utf8').digest('base64');
const scriptSrc = (res) => res.headers.get('Content-Security-Policy').split('; ').find((d) => d.startsWith('script-src'));

test('each /app/ page allows exactly its own inline scripts by hash, never unsafe-inline', async () => {
  const boot = sha('self.__boot=1');
  const other = sha('self.__other=1');
  const files = {
    ...FILES,
    '/app/home/index.html': ['<html>home</html>', 200],
    '/app-csp.json': [JSON.stringify({ 'index.html': [boot], 'home/index.html': [other] }), 200],
  };
  for (const p of ['/app/', '/app/chat/3']) {
    const res = await get(p, makeEnv({}, files));
    assert.equal(scriptSrc(res), `script-src 'self' '${boot}'`, p);
    assert.doesNotMatch(res.headers.get('Content-Security-Policy'), /unsafe-inline/);
    assert.match(res.headers.get('Content-Security-Policy'), /connect-src 'self' wss:\/\/relay\.test/);
  }
  assert.equal(scriptSrc(await get('/app/home/index.html', makeEnv({}, files))), `script-src 'self' '${other}'`);
  // Code files carry no page policy and need no list.
  const env = makeEnv({}, files);
  const js = await get('/app/_next/static/chunks/main-abc.js', env);
  assert.equal(js.headers.get('Content-Security-Policy'), null);
  assert.ok(!env.fetched.includes('/app-csp.json'));
});

test('a missing, broken or foreign hash list leaves script-src at self', async () => {
  const cases = [
    {},
    { '/app-csp.json': ['{not json', 200] },
    { '/app-csp.json': [JSON.stringify(['x']), 200] },
    { '/app-csp.json': [JSON.stringify({ 'index.html': "'unsafe-inline'" }), 200] },
    { '/app-csp.json': [JSON.stringify({ 'index.html': ["'unsafe-inline'", 'sha256-short', `${sha('a')}; script-src *`, 7] }), 200] },
  ];
  for (const extra of cases) {
    const res = await get('/app/', makeEnv({}, { ...FILES, ...extra }));
    assert.equal(res.status, 200);
    assert.equal(scriptSrc(res), "script-src 'self'", JSON.stringify(extra));
  }
});

test('the hash list is not served to visitors', async () => {
  const files = { ...FILES, '/app-csp.json': ['{}', 200] };
  assert.equal((await get('/app-csp.json', makeEnv({}, files))).status, 404);
  assert.equal((await get('/app/../app-csp.json', makeEnv({}, files))).status, 404);
});
