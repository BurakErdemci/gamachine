// Per-IP request budget in front of the relay worker (RELAY_RATE_LIMIT binding).

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { installRuntimeGlobals, FakeNamespace } from './fake-do.mjs';
import { pairIdFor } from './nodeimpl.mjs';

installRuntimeGlobals();
const { default: worker, Room, IpLimiter } = await import('../worker/index.js');

const PAGE = 'https://relay.test';
const IP = '198.51.100.7';

function makeEnv(binding) {
  const env = { ASSETS: { fetch: async () => new Response('asset', { status: 200 }) }, calls: [] };
  env.ROOM = new FakeNamespace(Room, env);
  env.IP_LIMITER = new FakeNamespace(IpLimiter, env);
  const roomGet = env.ROOM.get.bind(env.ROOM);
  env.ROOM.get = (id) => { env.calls.push('ROOM.get'); return roomGet(id); };
  const limiterGet = env.IP_LIMITER.get.bind(env.IP_LIMITER);
  env.IP_LIMITER.get = (id) => { env.calls.push('IP_LIMITER.get'); return limiterGet(id); };
  if (binding) env.RELAY_RATE_LIMIT = binding;
  return env;
}

const staticReq = () => new Request(`${PAGE}/app.js`, { headers: { 'CF-Connecting-IP': IP } });
const pairReq = async () => new Request(`${PAGE}/ws/pair/${await pairIdFor('K'.repeat(43))}`,
  { headers: { Upgrade: 'websocket', Origin: PAGE, 'CF-Connecting-IP': IP, 'Sec-WebSocket-Protocol': 'gamachine.v1' } });

const deny = () => { const keys = []; return { keys, limit: async ({ key }) => { keys.push(key); return { success: false }; } }; };

test('without the binding the worker answers as before', async () => {
  const env = makeEnv();
  assert.equal((await worker.fetch(staticReq(), env)).status, 200);
  assert.equal((await worker.fetch(new Request(`${PAGE}/nope`), env)).status, 404);
});

test('a denying binding answers 429 with Retry-After 60 for a static path', async () => {
  const b = deny();
  const env = makeEnv(b);
  const res = await worker.fetch(staticReq(), env);
  assert.equal(res.status, 429);
  assert.equal(res.headers.get('Retry-After'), '60');
  assert.deepEqual(b.keys, [IP]);
});

test('a denying binding stops a pair socket before any Durable Object is touched', async () => {
  const env = makeEnv(deny());
  const res = await worker.fetch(await pairReq(), env);
  assert.equal(res.status, 429);
  assert.equal(res.headers.get('Retry-After'), '60');
  assert.deepEqual(env.calls, []);
});

test('a request without CF-Connecting-IP is keyed as unknown', async () => {
  const b = deny();
  await worker.fetch(new Request(`${PAGE}/app.js`), makeEnv(b));
  assert.deepEqual(b.keys, ['unknown']);
});

test('an allowing binding leaves responses unchanged', async () => {
  const env = makeEnv({ limit: async () => ({ success: true }) });
  assert.equal((await worker.fetch(staticReq(), env)).status, 200);
  const res = await worker.fetch(await pairReq(), env);
  assert.equal(res.status, 101);
  assert.ok(env.calls.includes('ROOM.get'));
});

test('a binding that throws never blocks the request', async () => {
  const env = makeEnv({ limit: async () => { throw new Error('binding down'); } });
  assert.equal((await worker.fetch(staticReq(), env)).status, 200);
});
