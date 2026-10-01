import { setLang } from '../public/i18n.js';
setLang('tr');

// What the phone page reads when a model pick is announced: the REAL app.js and
// index.html in jsdom, only the transport faked. jsdom is not a relay dependency;
// it is taken from the desktop frontend's install and the tests skip without it.

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
const SETTLE_MS = 400;

// Chat 7 has no model of its own and follows the default; chat 8 has one.
async function boot({ manualSettingsClock = false } = {}) {
  const { window } = new JSDOM(html, { url: 'https://phone.invalid/p', runScripts: 'outside-only' });
  const settingsTimers = new Map();
  if (manualSettingsClock) {
    const set = window.setTimeout.bind(window);
    const clear = window.clearTimeout.bind(window);
    window.setTimeout = (callback, delay, ...args) => {
      if (delay !== 100) return set(callback, delay, ...args);
      const timer = {};
      settingsTimers.set(timer, () => callback(...args));
      return timer;
    };
    window.clearTimeout = (timer) => {
      if (!settingsTimers.delete(timer)) clear(timer);
    };
  }
  window.localStorage.setItem('gm-ui-lang', 'tr');
  window.eval(fs.readFileSync(new URL('i18n.js', PUBLIC), 'utf8').replace(/export /g, ''));
  window.scrollTo = () => {};
  window.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {} });
  window.Element.prototype.scrollIntoView = () => {};
  window.C = { fromB64u: () => new Uint8Array() };
  window.store = { get: async () => ({ pairId: 'p', pcPub: 'AAAA', deviceId: 'd', token: 't', privateKey: {}, pushDone: true }) };
  Object.assign(window, net);
  const pc = { defaultModel: 'claude-opus-5', chat8: 'gpt-6-luna', calls: [], link: null };
  const result = (value) => ({ ok: true, result: value });
  const config = (model) => ({ approval_mode: 'step', desktop_effort: null, provider_type: 'subscription', model_name: model });
  window.Link = class {
    constructor(options) { this.options = options; this.ready = false; pc.link = this; }
    start() { queueMicrotask(() => { this.ready = true; this.options.onStatus('ready'); }); }
    stop() {}
    wake() {}
    async request(type, params) {
      pc.calls.push({ type, params });
      if (type === 'list_chats') return result({ chats: [
        { chat_id: '7', title: 'Follows default', status: 'idle', model: pc.defaultModel },
        { chat_id: '8', title: 'Own model', status: 'idle', model: pc.chat8 },
      ] });
      if (type === 'get_config') return result(config(params?.chat_id === '7' ? pc.defaultModel : pc.chat8));
      if (type === 'list_models') return result({ subscription: ['claude-opus-5', 'gpt-6-luna', 'gpt-6-sol']
        .map((id) => ({ id, name: id, provider: 'subscription' })), cloud: [], local: [] });
      if (type === 'set_model') {
        pc.chat8 = params.model_name;
        pc.defaultModel = params.model_name;
        return result({ provider_type: params.provider_type, model_name: params.model_name });
      }
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
  pc.select = window.document.getElementById('model-select');
  pc.open = async (title, model) => {
    const button = [...window.document.querySelectorAll('#chats button')].find((b) => b.textContent.includes(title));
    button.click();
    assert.ok(await waitFor(() => pc.select.value === `subscription|${model}`), `${title} did not show ${model}`);
  };
  pc.configReads = (chatId) => pc.calls.filter((c) => c.type === 'get_config' && c.params?.chat_id === chatId).length;
  pc.push = (msg) => pc.link.options.onPush(msg);
  pc.pendingSettingsTimers = () => settingsTimers.size;
  pc.advanceSettingsClock = async () => {
    const callbacks = [...settingsTimers.values()];
    settingsTimers.clear();
    callbacks.forEach((callback) => callback());
    await sleep(20);
  };
  pc.close = () => window.close();
  return pc;
}

test('opening another chat cancels the pending settings refresh', { skip }, async () => {
  const pc = await boot({ manualSettingsClock: true });
  try {
    await pc.open('Follows default', 'claude-opus-5');
    pc.push({ type: 'chat_model_changed', chat_id: '7', provider_type: 'subscription', model_name: 'gpt-6-sol' });
    assert.equal(pc.pendingSettingsTimers(), 1);
    const before = pc.configReads('8');
    await pc.open('Own model', 'gpt-6-luna');
    await pc.advanceSettingsClock();
    assert.equal(pc.configReads('8') - before, 1);
  } finally {
    pc.close();
  }
});

test('a pick in another chat moves the default: the open chat that follows it re-reads once', { skip }, async () => {
  const pc = await boot();
  await pc.open('Follows default', 'claude-opus-5');
  const before = pc.configReads('7');
  pc.defaultModel = 'gpt-6-sol';
  pc.push({ type: 'chat_model_changed', chat_id: '8', provider_type: 'subscription', model_name: 'gpt-6-sol' });
  pc.push({ type: 'chat_changed', chat: { chat_id: '8', title: 'Own model', status: 'idle', model: 'gpt-6-sol' } });
  await sleep(SETTLE_MS);
  assert.equal(pc.select.value, 'subscription|gpt-6-sol');
  assert.equal(pc.configReads('7') - before, 1);
  pc.close();
});

test('one pick announced for the open chat costs one config read, not one per frame', { skip }, async () => {
  const pc = await boot();
  await pc.open('Own model', 'gpt-6-luna');
  const before = pc.configReads('8');
  pc.chat8 = 'gpt-6-sol';
  pc.push({ type: 'chat_model_changed', chat_id: '8', provider_type: 'subscription', model_name: 'gpt-6-sol' });
  pc.push({ type: 'chat_changed', chat: { chat_id: '8', title: 'Own model', status: 'idle', model: 'gpt-6-sol' } });
  await sleep(SETTLE_MS);
  assert.equal(pc.select.value, 'subscription|gpt-6-sol');
  assert.equal(pc.configReads('8') - before, 1);
  pc.close();
});

test('a pick made on this phone: the reply and the frames behind it share one read', { skip }, async () => {
  const pc = await boot();
  await pc.open('Own model', 'gpt-6-luna');
  const before = pc.configReads('8');
  pc.select.value = 'subscription|gpt-6-sol';
  pc.select.dispatchEvent(new pc.select.ownerDocument.defaultView.Event('change'));
  await sleep(20);
  pc.push({ type: 'chat_model_changed', chat_id: '8', provider_type: 'subscription', model_name: 'gpt-6-sol' });
  pc.push({ type: 'chat_changed', chat: { chat_id: '8', title: 'Own model', status: 'idle', model: 'gpt-6-sol' } });
  await sleep(SETTLE_MS);
  assert.equal(pc.calls.filter((c) => c.type === 'set_model').length, 1);
  assert.equal(pc.select.value, 'subscription|gpt-6-sol');
  assert.equal(pc.configReads('8') - before, 1);
  pc.close();
});

test('a default moved with no chat picked still refreshes the open chat', { skip }, async () => {
  const pc = await boot();
  await pc.open('Follows default', 'claude-opus-5');
  pc.defaultModel = 'gpt-6-luna';
  pc.push({ type: 'default_model_changed', provider_type: 'subscription', model_name: 'gpt-6-luna' });
  await sleep(SETTLE_MS);
  assert.equal(pc.select.value, 'subscription|gpt-6-luna');
  pc.close();
});
