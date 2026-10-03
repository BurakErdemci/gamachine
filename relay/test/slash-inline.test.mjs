import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import * as net from '../public/net.js';
import { t, setLang } from '../public/i18n.js';

setLang('tr');
const html = readFileSync(new URL('../public/index.html', import.meta.url), 'utf8');
const source = readFileSync(new URL('../public/app.js', import.meta.url), 'utf8');
const catalog = {
  commands: ['compact', 'usage', 'review'],
  meta: [{ name: 'review', insert: 'Review the current changes. ', argumentHint: '[pr]' }],
};

// Run the real page listeners with a small DOM and a fake PC transport.
function page({ reply = async () => catalog } = {}) {
  const document = { activeElement: null, addEventListener() {} };
  class Node {
    constructor() {
      this.value = '';
      this.hidden = false;
      this.children = [];
      this.attributes = new Map();
      this.listeners = new Map();
      this.isConnected = true;
    }
    setAttribute(key, value) { this.attributes.set(key, String(value)); }
    getAttribute(key) { return this.attributes.get(key) ?? null; }
    removeAttribute(key) { this.attributes.delete(key); }
    get firstChild() { return this.children[0]; }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    addEventListener(type, fn) {
      const listeners = this.listeners.get(type) ?? [];
      listeners.push(fn);
      this.listeners.set(type, listeners);
    }
    dispatch(type, props = {}) {
      const event = { defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...props };
      const done = Promise.all((this.listeners.get(type) ?? []).map((fn) => fn(event)));
      return { event, done };
    }
    focus() { document.activeElement = this; }
    setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; }
    scrollIntoView() { this.scrolled = true; }
  }
  const nodes = new Map([...html.matchAll(/id="([a-z0-9-]+)"/g)].map((m) => [m[1], new Node()]));
  nodes.get('slash-panel').hidden = true;
  document.getElementById = (id) => nodes.get(id);
  document.createElement = () => new Node();
  const calls = [];
  const context = {
    ...net, t, document, navigator: {}, window: { addEventListener() {} },
    setTimeout, clearTimeout,
    transport: { ready: true, request: async (type, params) => {
      calls.push({ type, params });
      return { ok: true, result: type === 'list_slash_commands' ? await reply(params) : { status: 'accepted' } };
    } },
  };
  runInNewContext(source.slice(source.indexOf('const $ = (id) =>'), source.lastIndexOf('boot();'))
    + '\nlink = transport; view.show("7"); wire();'
    + '\nglobalThis.changeChat = (id) => { closeSlash(); view.show(id); };', context);
  const box = nodes.get('composer-text');
  const panel = nodes.get('slash-panel');
  const list = nodes.get('slash-list');
  const shortcut = nodes.get('btn-slash');
  const input = (value) => { box.value = value; return box.dispatch('input').done; };
  const key = (key, props = {}) => box.dispatch('keydown', { key, ...props });
  const buttons = () => list.children.map((row) => row.children[0]);
  const names = () => buttons().map((button) => button.children[0]);
  const catalogCalls = () => calls.filter((call) => call.type === 'list_slash_commands');
  return { nodes, document, box, panel, list, shortcut, input, key, buttons, names, calls, catalogCalls, changeChat: context.changeChat };
}

test('typing filters inline suggestions and whitespace or ordinary text hides them', async () => {
  const p = page();
  await p.input('/');
  assert.equal(p.panel.hidden, false);
  assert.equal(p.shortcut.getAttribute('aria-expanded'), 'true');
  assert.deepEqual(p.names(), ['/compact', '/usage', '/review [pr]']);
  assert.equal(p.buttons()[0].getAttribute('aria-selected'), 'true');
  await p.input('/co');
  assert.deepEqual(p.names(), ['/compact']);
  await p.input('/unknown');
  assert.equal(p.panel.hidden, false);
  assert.equal(p.buttons().length, 0);
  assert.equal(p.nodes.get('slash-note').textContent, t('slash.noMatches'));
  for (const value of ['/compact ', '/co\t', '/co\n', 'hello', ' /co', '']) {
    await p.input(value);
    assert.equal(p.panel.hidden, true, JSON.stringify(value));
    assert.equal(p.shortcut.getAttribute('aria-expanded'), 'false');
  }
});

test('a pending catalog is requested once and cached separately per chat', async () => {
  let resolve;
  const p = page({ reply: () => new Promise((r) => { resolve = r; }) });
  const first = p.input('/');
  assert.equal(p.panel.hidden, false);
  assert.equal(p.nodes.get('slash-note').textContent, t('slash.loading'));
  const second = p.input('/co');
  assert.equal(p.catalogCalls().length, 1);
  resolve(catalog);
  await Promise.all([first, second]);
  assert.deepEqual(p.names(), ['/compact']);
  await p.input('hello');
  await p.input('/');
  assert.equal(p.catalogCalls().length, 1);
  p.changeChat('8');
  const other = p.input('/');
  assert.equal(p.catalogCalls().length, 2);
  assert.equal(p.catalogCalls()[1].params.chat_id, '8');
  resolve(catalog);
  await other;
  p.changeChat('7');
  await p.input('/re');
  assert.equal(p.catalogCalls().length, 2);
  assert.deepEqual(p.names(), ['/review [pr]']);
});

test('tap selects a command with one trailing space and retains composer focus', async () => {
  const p = page();
  await p.input('/re');
  await p.buttons()[0].dispatch('click').done;
  assert.equal(p.box.value, '/review ');
  assert.equal(p.panel.hidden, true);
  assert.equal(p.document.activeElement, p.box);
  assert.equal(p.box.selectionStart, p.box.value.length);
  assert.equal(p.box.selectionEnd, p.box.value.length);
});

test('arrow keys wrap the active item; Enter and Tab select without sending', async () => {
  const p = page();
  await p.input('/');
  assert.equal(p.key('ArrowUp').event.defaultPrevented, true);
  assert.equal(p.buttons()[2].getAttribute('aria-selected'), 'true');
  p.key('ArrowDown');
  assert.equal(p.buttons()[0].getAttribute('aria-selected'), 'true');
  p.key('ArrowDown');
  assert.equal(p.buttons()[1].getAttribute('aria-selected'), 'true');
  assert.equal(p.key('Enter').event.defaultPrevented, true);
  assert.equal(p.box.value, '/usage ');
  assert.equal(p.panel.hidden, true);
  assert.equal(p.calls.some((call) => call.type === 'send_message'), false);
  await p.input('/re');
  assert.equal(p.key('Tab').event.defaultPrevented, true);
  assert.equal(p.box.value, '/review ');
  assert.equal(p.panel.hidden, true);
  await p.input('/unknown');
  assert.equal(p.key('Enter').event.defaultPrevented, false);
});

test('Escape dismisses suggestions until input or the slash shortcut reopens them', async () => {
  const p = page();
  await p.input('/');
  assert.equal(p.key('Escape').event.defaultPrevented, true);
  assert.equal(p.panel.hidden, true);
  assert.equal(p.shortcut.getAttribute('aria-expanded'), 'false');
  assert.equal(p.key('Enter').event.defaultPrevented, false);
  await p.shortcut.dispatch('click').done;
  assert.equal(p.panel.hidden, false);
  assert.equal(p.box.value, '/');
  assert.equal(p.document.activeElement, p.box);
});

test('the slash shortcut inserts into an empty box and preserves existing text', async () => {
  const p = page();
  await p.shortcut.dispatch('click').done;
  assert.equal(p.box.value, '/');
  assert.equal(p.panel.hidden, false);
  await p.input('/co');
  await p.shortcut.dispatch('click').done;
  assert.equal(p.box.value, '/co');
  assert.deepEqual(p.names(), ['/compact']);
  await p.input('draft');
  await p.shortcut.dispatch('click').done;
  assert.equal(p.box.value, 'draft');
  assert.equal(p.panel.hidden, true);
  assert.equal(p.document.activeElement, p.box);
});

test('catalog failures keep the existing failure note', async () => {
  const p = page({ reply: async () => { throw new Error('unavailable'); } });
  await p.input('/');
  assert.equal(p.panel.hidden, false);
  assert.equal(p.buttons().length, 0);
  assert.equal(p.nodes.get('slash-note').textContent, net.slashFailureNote('unavailable'));
});

test('late catalogs do not reopen a dismissed list or display another chat catalog', async () => {
  const resolvers = new Map();
  const p = page({ reply: ({ chat_id }) => new Promise((resolve) => resolvers.set(chat_id, resolve)) });
  const first = p.input('/');
  p.key('Escape');
  resolvers.get('7')(catalog);
  await first;
  assert.equal(p.panel.hidden, true);
  p.changeChat('8');
  const second = p.input('/');
  p.changeChat('9');
  const third = p.input('/');
  resolvers.get('8')({ commands: ['other'] });
  await second;
  assert.equal(p.buttons().length, 0);
  resolvers.get('9')({ commands: ['current'] });
  await third;
  assert.deepEqual(p.names(), ['/current']);
});

test('composition Enter does not select a slash suggestion', async () => {
  const p = page();
  await p.input('/');
  assert.equal(p.key('Enter', { isComposing: true }).event.defaultPrevented, false);
  assert.equal(p.box.value, '/');
  assert.equal(p.panel.hidden, false);
});
