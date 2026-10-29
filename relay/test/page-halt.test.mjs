import { setLang } from '../public/i18n.js';
setLang('tr');

// The chat view's "stop computer use" button: the REAL app.js and index.html in
// jsdom, only the transport faked (same harness as page-model-refresh.test.mjs).

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const PUBLIC = new URL('../public/', import.meta.url);
let JSDOM = null;
try {
  ({ JSDOM } = createRequire(new URL('../../Frontend/frontend/package.json', import.meta.url))('jsdom'));
} catch { /* skipped below */ }
const skip = JSDOM ? false : 'jsdom is not installed (Frontend/frontend/node_modules)';

const net = await import(new URL('net.js', PUBLIC).href);
const html = fs.readFileSync(fileURLToPath(new URL('index.html', PUBLIC)), 'utf8');
const source = fs.readFileSync(fileURLToPath(new URL('app.js', PUBLIC)), 'utf8');
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function boot({ haltError = null } = {}) {
  const { window } = new JSDOM(html, { url: 'https://phone.invalid/p', runScripts: 'outside-only' });
  window.localStorage.setItem('gm-ui-lang', 'tr');
  window.eval(fs.readFileSync(new URL('i18n.js', PUBLIC), 'utf8').replace(/export /g, ''));
  window.scrollTo = () => {};
  window.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {} });
  window.Element.prototype.scrollIntoView = () => {};
  window.C = { fromB64u: () => new Uint8Array() };
  window.store = { get: async () => ({ pairId: 'p', pcPub: 'AAAA', deviceId: 'd', token: 't', privateKey: {}, pushDone: true }) };
  Object.assign(window, net);
  const pc = { calls: [], link: null };
  const result = (value) => ({ ok: true, result: value });
  window.Link = class {
    constructor(options) { this.options = options; this.ready = false; pc.link = this; }
    start() { queueMicrotask(() => { this.ready = true; this.options.onStatus('ready'); }); }
    stop() {}
    wake() {}
    async request(type, params) {
      pc.calls.push({ type, params });
      if (type === 'list_chats') return result({ chats: [
        { chat_id: '7', title: 'Running chat', status: 'running', model: 'claude-opus-5' },
        { chat_id: '8', title: 'Idle chat', status: 'idle', model: 'claude-opus-5' },
      ] });
      if (type === 'get_config') return result({ approval_mode: 'step', desktop_effort: null });
      if (type === 'halt_computer' && haltError) return { ok: false, error: haltError };
      if (type === 'pending_cards') return result({ cards: [] });
      if (type === 'open_chat') return result({ messages: [], events: [] });
      return result({});
    }
  };
  window.eval(source.slice(source.indexOf('const $ = (id) =>')));
  const waitFor = async (predicate) => {
    for (let i = 0; i < 100; i++) {
      if (predicate()) return true;
      await sleep(10);
    }
    return false;
  };
  assert.ok(await waitFor(() => pc.link?.ready && window.document.querySelector('#chats button')), 'chat list did not load');
  pc.window = window;
  pc.open = (title) => [...window.document.querySelectorAll('#chats button')].find((b) => b.textContent.includes(title)).click();
  pc.button = () => window.document.getElementById('btn-halt-computer');
  pc.status = () => window.document.getElementById('chat-status').textContent;
  pc.waitFor = waitFor;
  return pc;
}

test('the halt button sends halt_computer for the shown chat', { skip }, async () => {
  const pc = await boot();
  try {
    pc.open('Running chat');
    await pc.waitFor(() => pc.button() && !pc.button().hidden);
    assert.equal(pc.button().hidden, false);
    assert.equal(pc.button().textContent, 'Bilgisayar kullanımını durdur');
    pc.button().click();
    assert.ok(await pc.waitFor(() => pc.calls.some((c) => c.type === 'halt_computer')));
    assert.equal(JSON.stringify(pc.calls.filter((c) => c.type === 'halt_computer').map((c) => c.params)), '[{"chat_id":"7"}]');
    assert.ok(await pc.waitFor(() => pc.status() === 'Bilgisayar kullanımı durduruldu.'));
    assert.equal(pc.button().disabled, false);
  } finally {
    pc.window.close();
  }
});

test('the halt button is hidden for an idle chat', { skip }, async () => {
  const pc = await boot();
  try {
    pc.open('Idle chat');
    await sleep(100);
    assert.equal(pc.button().hidden, true);
  } finally {
    pc.window.close();
  }
});

test('a refused halt shows the error on the status line', { skip }, async () => {
  const pc = await boot({ haltError: 'unsupported' });
  try {
    pc.open('Running chat');
    await pc.waitFor(() => pc.button() && !pc.button().hidden);
    pc.button().click();
    assert.ok(await pc.waitFor(() => pc.status().includes('unsupported')));
  } finally {
    pc.window.close();
  }
});
