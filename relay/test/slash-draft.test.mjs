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
  commands: ['compact'],
  skills: ['review', 'inspect'],
  meta: [
    { name: 'review', insert: '$review ' },
    { name: 'inspect', insert: 'Review the current changes.' },
  ],
};

// Exercise the real page listeners with a small DOM and fake PC transport.
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
    ...net, t, document, navigator: {}, window: { addEventListener() {}, scrollTo() {} },
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
  const shortcut = nodes.get('btn-slash');
  const input = (value) => { box.value = value; return box.dispatch('input').done; };
  const key = (key, props = {}) => box.dispatch('keydown', { key, ...props });
  const buttons = () => nodes.get('slash-list').children.map((row) => row.children[0]);
  const names = () => buttons().map((button) => button.children[0]);
  const catalogCalls = () => calls.filter((call) => call.type === 'list_slash_commands');
  return { nodes, document, box, panel, shortcut, input, key, buttons, names, calls, catalogCalls, changeChat: context.changeChat };
}

test('picking from an empty composer writes the catalog insert or fallback exactly', async () => {
  for (const [name, insert] of [['review', '$review '], ['inspect', 'Review the current changes.'], ['compact', '/compact ']]) {
    const p = page();
    await p.shortcut.dispatch('click').done;
    await p.input('/' + name);
    await p.buttons()[0].dispatch('click').done;
    assert.equal(p.box.value, insert);
    assert.equal(p.panel.hidden, true);
    assert.equal(p.document.activeElement, p.box);
    assert.equal(p.box.selectionStart, insert.length);
    assert.equal(p.box.selectionEnd, insert.length);
  }
});

test('the shortcut trims a draft, filters the trailing command and preserves the prefix on pick', async () => {
  const p = page();
  await p.input('please check  \t\n');
  await p.shortcut.dispatch('click').done;
  assert.equal(p.box.value, 'please check /');
  assert.equal(p.panel.hidden, false);
  assert.equal(p.shortcut.getAttribute('aria-expanded'), 'true');
  assert.equal(p.box.getAttribute('aria-expanded'), 'true');
  assert.equal(p.box.selectionStart, p.box.value.length);
  assert.equal(p.box.selectionEnd, p.box.value.length);
  await p.input('please check /re');
  assert.deepEqual(p.names(), ['/review']);
  await p.buttons()[0].dispatch('click').done;
  assert.equal(p.box.value, 'please check $review ');
  assert.equal(p.panel.hidden, true);
  assert.equal(p.box.getAttribute('aria-activedescendant'), null);
  assert.equal(p.document.activeElement, p.box);
  assert.equal(p.box.selectionStart, p.box.value.length);
  assert.equal(p.box.selectionEnd, p.box.value.length);
  await p.input('please check $review /re');
  assert.equal(p.panel.hidden, true);

  await p.input('first  line\nsecond\tline');
  await p.shortcut.dispatch('click').done;
  await p.input('first  line\nsecond\tline /inspect');
  await p.buttons()[0].dispatch('click').done;
  assert.equal(p.box.value, 'first  line\nsecond\tline Review the current changes.');
  assert.equal(p.catalogCalls().length, 1);
});

test('Escape leaves the draft as typed and ends draft mode', async () => {
  const p = page();
  await p.input('please check');
  await p.shortcut.dispatch('click').done;
  await p.input('please check /re');
  assert.equal(p.key('Escape').event.defaultPrevented, true);
  assert.equal(p.box.value, 'please check /re');
  assert.equal(p.panel.hidden, true);
  assert.equal(p.box.getAttribute('aria-expanded'), 'false');
  await p.input('please check /rev');
  assert.equal(p.panel.hidden, true);
});

test('ordinary text containing a slash never enters draft mode by typing', async () => {
  const p = page();
  const path = 'see ' + '/' + 'Users' + '/' + 'x';
  for (const value of [path, 'please check /', 'please check /re']) {
    await p.input(value);
    assert.equal(p.panel.hidden, true);
  }
  assert.equal(p.catalogCalls().length, 0);
});

test('whitespace in the trailing segment or deleting it ends draft mode', async () => {
  for (const suffix of ['/re ', '/re\t', '/re\n', '', 're']) {
    const p = page();
    await p.input('please check');
    await p.shortcut.dispatch('click').done;
    assert.equal(p.panel.hidden, false);
    await p.input('please check ' + suffix);
    assert.equal(p.panel.hidden, true, JSON.stringify(suffix));
    await p.input('please check /re');
    assert.equal(p.panel.hidden, true);
  }
  const p = page();
  await p.input('see /earlier');
  await p.shortcut.dispatch('click').done;
  await p.input('see /earlier');
  assert.equal(p.panel.hidden, true);
});

test('draft keyboard navigation wraps, ignores composition and picks without sending', async () => {
  for (const key of ['Enter', 'Tab']) {
    const p = page();
    await p.input('please check');
    await p.shortcut.dispatch('click').done;
    assert.equal(p.key('ArrowUp').event.defaultPrevented, true);
    assert.equal(p.buttons()[2].getAttribute('aria-selected'), 'true');
    p.key('ArrowDown');
    assert.equal(p.buttons()[0].getAttribute('aria-selected'), 'true');
    p.key('ArrowDown');
    assert.equal(p.key(key, { isComposing: true }).event.defaultPrevented, false);
    assert.equal(p.box.value, 'please check /');
    assert.equal(p.key(key).event.defaultPrevented, true);
    assert.equal(p.box.value, 'please check $review ');
    assert.equal(p.panel.hidden, true);
    assert.equal(p.calls.some((call) => call.type === 'send_message'), false);
  }
});

test('sending or switching chats ends draft mode', async () => {
  for (const action of ['send', 'switch', 'close']) {
    const p = page();
    await p.input('please check');
    await p.shortcut.dispatch('click').done;
    if (action === 'send') await p.nodes.get('composer').dispatch('submit').done;
    else if (action === 'switch') p.changeChat('8');
    else await p.nodes.get('btn-back').dispatch('click').done;
    assert.equal(p.panel.hidden, true);
    await p.input('please check /re');
    assert.equal(p.panel.hidden, true);
  }
});

test('draft loading and late results respect filtering and dismissal', async () => {
  let resolve;
  const p = page({ reply: () => new Promise((r) => { resolve = r; }) });
  await p.input('please check');
  const first = p.shortcut.dispatch('click').done;
  assert.equal(p.nodes.get('slash-note').textContent, t('slash.loading'));
  const second = p.input('please check /re');
  assert.equal(p.catalogCalls().length, 1);
  resolve(catalog);
  await Promise.all([first, second]);
  assert.deepEqual(p.names(), ['/review']);

  const dismissed = page({ reply: () => new Promise((r) => { resolve = r; }) });
  await dismissed.input('please check');
  const pending = dismissed.shortcut.dispatch('click').done;
  dismissed.key('Escape');
  resolve(catalog);
  await pending;
  assert.equal(dismissed.panel.hidden, true);
});

test('draft catalog failures retain the failure note', async () => {
  const p = page({ reply: async () => { throw new Error('unavailable'); } });
  await p.input('please check');
  await p.shortcut.dispatch('click').done;
  assert.equal(p.panel.hidden, false);
  assert.equal(p.buttons().length, 0);
  assert.equal(p.nodes.get('slash-note').textContent, net.slashFailureNote('unavailable'));
});
