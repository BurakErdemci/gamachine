import { setLang } from '../public/i18n.js';
setLang('tr');

// Hold-to-confirm on a risky approval card: the REAL app.js and index.html in
// jsdom, only the transport and storage faked (same harness as page-removed.test.mjs).

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

const RISKY = { card_id: 'c1', chat_id: null, kind: 'mcp', tool: 'computer_click', risk: 'Kritik islem dugmesi: Sil', hold_to_confirm: true, detail: 'Finder' };
const PLAIN = { card_id: 'c2', chat_id: null, kind: 'mcp', tool: 'read_file', risk: null, hold_to_confirm: false, detail: 'x' };

async function boot(cards) {
  const { window } = new JSDOM(html, { url: 'https://phone.invalid/p', runScripts: 'outside-only' });
  window.localStorage.setItem('gm-ui-lang', 'tr');
  window.eval(fs.readFileSync(new URL('i18n.js', PUBLIC), 'utf8').replace(/export /g, ''));
  window.scrollTo = () => {};
  window.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {} });
  window.Element.prototype.scrollIntoView = () => {};
  window.C = { fromB64u: () => new Uint8Array() };
  const page = { link: null, answers: [] };
  const stored = { pairId: 'p', pcPub: 'AAAA', deviceId: 'd', token: 't', privateKey: {}, pushDone: true };
  window.store = { get: async (k) => (k === 'device' ? stored : null), put: async () => {}, del: async () => {} };
  Object.assign(window, net);
  window.Link = class {
    constructor(options) { this.options = options; this.ready = false; page.link = this; }
    start() { queueMicrotask(() => { this.ready = true; this.options.onStatus('ready'); }); }
    stop() { this.ready = false; }
    wake() {}
    async request(type, payload) {
      if (type === 'list_chats') return { ok: true, result: { chats: [] } };
      if (type === 'pending_cards') return { ok: true, result: { cards } };
      if (type === 'answer_card') { page.answers.push(payload); return { ok: true, result: {} }; }
      return { ok: true, result: {} };
    }
  };
  window.eval(source.slice(source.indexOf('const $ = (id) =>')));
  for (let i = 0; i < 100 && !page.link?.ready; i++) await sleep(10);
  assert.ok(page.link?.ready, 'the page did not connect');
  for (let i = 0; i < 100 && !window.document.querySelector('#cards .card'); i++) await sleep(10);
  page.window = window;
  page.card = (id) => [...window.document.querySelectorAll('#cards .card')].find((n) => n.dataset.cardId === id);
  page.fire = (node, type, init = {}) => node.dispatchEvent(new window.Event(type, { bubbles: true, ...init }));
  return page;
}

test('a risky card shows the risk and a hold button; early release cancels', { skip }, async () => {
  const page = await boot([RISKY]);
  const card = page.card('c1');
  assert.equal(card.querySelector('.risk').textContent, RISKY.risk);
  const hold = card.querySelector('button.hold');
  assert.ok(hold, 'Approve is a hold button');
  assert.match(hold.textContent, /Onaylamak için basılı tut/);
  page.fire(hold, 'pointerdown');
  assert.ok(hold.classList.contains('holding'));
  await sleep(300);
  page.fire(hold, 'pointerup');
  assert.ok(!hold.classList.contains('holding'));
  await sleep(1200);
  assert.deepEqual(page.answers, [], 'a released hold answers nothing');
  page.window.close();
});

test('holding Approve to the end answers like a tap; Reject is a single tap', { skip }, async () => {
  const page = await boot([RISKY]);
  const hold = page.card('c1').querySelector('button.hold');
  page.fire(hold, 'pointerdown');
  await sleep(1400);
  assert.deepEqual(page.answers, [{ card_id: 'c1', decision: 'approve' }]);
  page.window.close();

  const again = await boot([RISKY]);
  again.card('c1').querySelector('button[data-decision="reject"]').click();
  await sleep(50);
  assert.deepEqual(again.answers, [{ card_id: 'c1', decision: 'reject' }]);
  again.window.close();
});

test('keyboard hold with Enter works and key release cancels', { skip }, async () => {
  const page = await boot([RISKY]);
  const hold = page.card('c1').querySelector('button.hold');
  const key = (type, k) => hold.dispatchEvent(new page.window.KeyboardEvent(type, { key: k, bubbles: true, cancelable: true }));
  key('keydown', ' ');
  await sleep(200);
  key('keyup', ' ');
  await sleep(1200);
  assert.deepEqual(page.answers, []);
  key('keydown', 'Enter');
  await sleep(1400);
  assert.deepEqual(page.answers, [{ card_id: 'c1', decision: 'approve' }]);
  page.window.close();
});

test('a plain card keeps the single-tap Approve and shows no risk', { skip }, async () => {
  const page = await boot([PLAIN]);
  const card = page.card('c2');
  assert.equal(card.querySelector('.risk'), null);
  assert.equal(card.querySelector('button.hold'), null);
  card.querySelector('button[data-decision="approve"]').click();
  await sleep(50);
  assert.deepEqual(page.answers, [{ card_id: 'c2', decision: 'approve' }]);
  page.window.close();
});
