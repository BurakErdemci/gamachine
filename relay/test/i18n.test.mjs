import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import { STRINGS, t, setLang, getLang } from '../public/i18n.js';
import * as net from '../public/net.js';

const PUBLIC = new URL('../public/', import.meta.url);
const html = fs.readFileSync(new URL('index.html', PUBLIC), 'utf8');
const source = fs.readFileSync(new URL('app.js', PUBLIC), 'utf8');
const dictionary = fs.readFileSync(new URL('i18n.js', PUBLIC), 'utf8');
const { JSDOM } = createRequire(new URL('../../Frontend/frontend/package.json', import.meta.url))('jsdom');

test('both dictionaries cover every static key and have identical key sets', () => {
  assert.deepEqual(Object.keys(STRINGS.en).sort(), Object.keys(STRINGS.tr).sort());
  const { window } = new JSDOM(html);
  for (const node of window.document.querySelectorAll('[data-i18n], [data-i18n-attr]')) {
    const keys = [node.dataset.i18n, ...(node.dataset.i18nAttr || '').split(';').map(e => e.slice(e.indexOf(':') + 1))].filter(Boolean);
    for (const key of keys) {
      assert.ok(Object.hasOwn(STRINGS.en, key), key);
      assert.ok(Object.hasOwn(STRINGS.tr, key), key);
    }
  }
  window.close();
});

test('default language, interpolation, invalid values, missing keys and persistence', () => {
  const { window } = new JSDOM(html, { url: 'https://phone.invalid/p', runScripts: 'outside-only' });
  window.eval(dictionary.replace(/export /g, ''));
  assert.equal(window.getLang(), 'en');
  assert.equal(window.t('send.tooLong', { max: 20 }), 'Message too long (maximum 20 characters).');
  assert.equal(window.t('missing'), 'missing');
  assert.equal(window.t('constructor'), 'constructor');
  window.setLang('tr');
  assert.equal(window.localStorage.getItem('gm-ui-lang'), 'tr');
  assert.equal(window.t('send.tooLong', { max: 20 }), 'Mesaj çok uzun (en fazla 20 karakter).');
  window.setLang('invalid');
  assert.equal(window.getLang(), 'tr');
  window.close();
});

test('storage errors are harmless and valid stored language is restored', () => {
  for (const saved of ['tr', 'invalid', null]) {
    const { window } = new JSDOM(html, { url: 'https://phone.invalid/p', runScripts: 'outside-only' });
    if (saved) window.localStorage.setItem('gm-ui-lang', saved);
    window.eval(dictionary.replace(/export /g, ''));
    assert.equal(window.getLang(), saved === 'tr' ? 'tr' : 'en');
    window.close();
  }
  const { window } = new JSDOM(html, { runScripts: 'outside-only' });
  window.eval(dictionary.replace(/export /g, ''));
  assert.equal(window.getLang(), 'en');
  window.setLang('tr');
  assert.equal(window.getLang(), 'tr');
  window.close();
});

async function settle(predicate) {
  for (let i = 0; i < 100 && !predicate(); i++) await new Promise(resolve => setTimeout(resolve, 10));
  assert.ok(predicate(), 'page did not settle');
}

test('real page follows get_config and ui_changed, preserves null and rerenders dynamic notes', async () => {
  const { window } = new JSDOM(html, { url: 'https://phone.invalid/p', runScripts: 'outside-only' });
  window.scrollTo = () => {};
  window.matchMedia = () => ({ matches: false });
  window.Element.prototype.scrollIntoView = () => {};
  window.C = { fromB64u: () => new Uint8Array() };
  window.store = { get: async () => ({ pairId: 'p', pcPub: 'AAAA', deviceId: 'd', token: 't', privateKey: {}, pushDone: true }) };
  Object.assign(window, net);
  window.eval(dictionary.replace(/export /g, ''));
  const localSetLang = window.setLang;
  window.setLang = value => { localSetLang(value); setLang(value); };
  setLang('en');
  let link;
  let releaseConfig;
  const pending = new Promise(resolve => { releaseConfig = resolve; });
  window.Link = class {
    constructor(options) { this.options = options; this.ready = false; link = this; }
    start() { queueMicrotask(() => { this.ready = true; this.options.onStatus('ready'); }); }
    stop() {}
    async request(type) {
      if (type === 'get_config') return pending;
      if (type === 'list_chats') return { ok: true, result: { chats: [{ chat_id: '7', status: 'idle' }] } };
      return { ok: true, result: { cards: [] } };
    }
  };
  const $ = id => window.document.getElementById(id);
  try {
    window.eval(source.slice(source.indexOf('const $ = (id) =>')));
    await settle(() => link?.ready && $('chats').children.length === 1);
    assert.equal(window.document.documentElement.lang, 'en');
    assert.equal(window.document.title, 'Gamachine Remote');
    assert.equal($('btn-send').textContent, 'Send');
    assert.equal($('composer-text').placeholder, 'Write a message…');
    assert.match($('chats').textContent, /Untitled chat/);
    releaseConfig({ ok: true, result: { approval_mode: 'step', desktop_ui: { lang: 'tr', theme: 'pafta' } } });
    await settle(() => window.document.documentElement.lang === 'tr');
    assert.equal(window.document.title, 'Gamachine Uzaktan');
    assert.equal($('btn-send').textContent, 'Gönder');
    assert.equal($('mode-select').getAttribute('aria-label'), 'Onay modu');
    assert.equal(window.document.documentElement.dataset.theme, 'pafta');
    assert.equal($('status-text').textContent, 'Bağlı');
    assert.match($('chats').textContent, /Adsız sohbet/);
    link.options.onPush({ type: 'ui_changed', desktop_ui: { lang: 'en', theme: 'arena' } });
    assert.equal(window.document.documentElement.lang, 'en');
    assert.equal($('btn-send').textContent, 'Send');
    assert.equal($('status-text').textContent, 'Connected');
    assert.match($('chats').textContent, /Untitled chat/);
    assert.equal($('mode-note').textContent, 'Shows an approval card for every change.');
    assert.equal(window.document.documentElement.dataset.theme, 'arena');
    link.options.onPush({ type: 'ui_changed', desktop_ui: null });
    assert.equal(window.document.documentElement.lang, 'en');
    assert.equal(window.document.documentElement.dataset.theme, 'arena');
    link.options.onPush({ type: 'ui_changed', desktop_ui: { lang: 'invalid', theme: 'invalid' } });
    assert.equal(window.document.documentElement.lang, 'en');
    assert.equal(window.document.documentElement.dataset.theme, 'arena');
  } finally {
    window.close();
    setLang('en');
  }
});

test('display maps and notes use the language at call time', () => {
  setLang('tr');
  assert.equal(net.modeInfo('step').label, 'Adım Adım');
  assert.equal(net.effortLabel('high'), 'Yüksek');
  assert.equal(net.cardActions({ card_id: '1' }).buttons[0].label, 'Onayla');
  setLang('en');
  assert.equal(getLang(), 'en');
  assert.equal(net.modeInfo('step').label, 'Step by Step');
  assert.equal(net.effortLabel('high'), 'High');
  assert.equal(net.cardActions({ card_id: '1' }).buttons[0].label, 'Approve');
  assert.equal(net.stopLine('no_session'), 'No running turn found.');
});
