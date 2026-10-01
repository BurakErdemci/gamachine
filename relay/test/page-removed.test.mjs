import { setLang } from '../public/i18n.js';
setLang('tr');

// What the phone page does when the PC no longer knows it: the REAL app.js and
// index.html in jsdom, only the transport and storage faked. jsdom is taken
// from the desktop frontend's install, as in page-model-refresh.test.mjs.

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
const REMOVED_TEXT = 'Bu telefon bilgisayardan kaldırıldı. Yeniden eşleştirmek için bilgisayarda Gamachine\'de AI Yapılandırması > Uzaktan kontrol > Telefon eşleştir ile QR kodunu aç ve telefonun kamerasıyla okut.';

async function boot({ paired = true } = {}) {
  const { window } = new JSDOM(html, { url: 'https://phone.invalid/p', runScripts: 'outside-only' });
  window.localStorage.setItem('gm-ui-lang', 'tr');
  window.eval(fs.readFileSync(new URL('i18n.js', PUBLIC), 'utf8').replace(/export /g, ''));
  window.scrollTo = () => {};
  window.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {} });
  window.Element.prototype.scrollIntoView = () => {};
  window.confirm = () => { throw new Error('a removal must not ask'); };
  window.C = { fromB64u: () => new Uint8Array() };
  const page = { link: null, stops: 0, stored: { pairId: 'p', pcPub: 'AAAA', deviceId: 'd', token: 't', privateKey: {}, pushDone: true } };
  if (!paired) page.stored = null;
  window.store = {
    get: async (k) => (k === 'device' ? page.stored : null),
    put: async (k, v) => { if (k === 'device') page.stored = v; },
    del: async (k) => { if (k === 'device') page.stored = null; },
  };
  Object.assign(window, net);
  window.Link = class {
    constructor(options) { this.options = options; this.ready = false; page.link = this; }
    start() { queueMicrotask(() => { this.ready = true; this.options.onStatus('ready'); }); }
    stop() { page.stops += 1; this.ready = false; }
    wake() {}
    async request(type) {
      if (type === 'list_chats') return { ok: true, result: { chats: [] } };
      if (type === 'pending_cards') return { ok: true, result: { cards: [] } };
      return { ok: true, result: {} };
    }
  };
  window.eval(source.slice(source.indexOf('const $ = (id) =>')));
  const $ = (id) => window.document.getElementById(id);
  if (paired) {
    for (let i = 0; i < 100 && !page.link?.ready; i++) await sleep(10);
    assert.ok(page.link?.ready, 'the page did not connect');
    assert.equal($('screen-main').hidden, false);
  } else {
    for (let i = 0; i < 100 && $('screen-welcome').hidden; i++) await sleep(10);
    assert.equal($('screen-welcome').hidden, false);
    assert.equal(page.link, null);
  }
  page.$ = $;
  page.showWelcome = () => window.eval('showWelcome()');
  page.status = (s, info) => page.link.options.onStatus(s, info);
  page.close = () => window.close();
  return page;
}

function assertForgotten(page) {
  assert.equal(page.stored, null, 'the stored device is deleted');
  assert.equal(page.stops, 1, 'the link is stopped');
  assert.equal(page.$('screen-welcome').hidden, false, 'the welcome screen shows');
  assert.equal(page.$('screen-main').hidden, true);
  assert.equal(page.$('welcome-text').textContent, REMOVED_TEXT);
}

test('removed: the page forgets the pairing and shows the welcome screen', { skip }, async () => {
  const page = await boot();
  page.status('removed');
  await sleep(50);
  assertForgotten(page);
  page.close();
});

test('hello_rejected unknown_device: the page forgets the pairing', { skip }, async () => {
  const page = await boot();
  page.status('hello_rejected', { reason: 'unknown_device' });
  await sleep(50);
  assertForgotten(page);
  page.close();
});

test('hello_rejected clock: the pairing is kept', { skip }, async () => {
  const page = await boot();
  page.status('hello_rejected', { reason: 'clock' });
  await sleep(50);
  assert.notEqual(page.stored, null);
  assert.equal(page.stops, 0);
  assert.equal(page.$('screen-main').hidden, false);
  assert.match(page.$('main-note').textContent, /saati/);
  page.close();
});

test('connecting and pc_offline keep the pairing', { skip }, async () => {
  const page = await boot();
  page.status('connecting');
  page.status('pc_offline', { lastSeen: null });
  await sleep(50);
  assert.notEqual(page.stored, null);
  assert.equal(page.$('screen-main').hidden, false);
  page.close();
});

test('welcome: fresh visits are clear, invalid submissions show an error, reopening clears it', { skip }, async () => {
  const page = await boot({ paired: false });
  try {
    assert.equal(page.$('paste-link').value, '');
    assert.equal(page.$('welcome-error').textContent, '');
    page.$('paste-link').value = 'https://phone.invalid/p';
    page.$('btn-paste-pair').click();
    assert.equal(page.$('welcome-error').textContent, 'Bağlantıda # işaretinden sonraki kısım yok.');
    page.showWelcome();
    assert.equal(page.$('welcome-error').textContent, '');
  } finally {
    page.close();
  }
});

test('removal clears a previous link error and explains where to open the QR', { skip }, async () => {
  for (const [status, info] of [['removed', {}], ['hello_rejected', { reason: 'unknown_device' }]]) {
    const page = await boot();
    try {
      page.$('paste-link').value = 'https://phone.invalid/p';
      page.$('btn-paste-pair').click();
      assert.equal(page.$('welcome-error').textContent, 'Bağlantıda # işaretinden sonraki kısım yok.');
      page.status(status, info);
      await sleep(50);
      assertForgotten(page);
      assert.equal(page.$('welcome-error').textContent, '');
    } finally {
      page.close();
    }
  }
});
