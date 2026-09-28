// The phone page against the shapes the PC bridge sends (Backend/app/remote/):
// the real Link over a fake socket with a node:crypto PC, and the page's
// DOM-free helpers in public/net.js.

import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import * as N from './nodeimpl.mjs';
import * as C from '../public/crypto.js';
import {
  pair, Link, ReplyParts, mergeParts, PHONE_FRAME_MAX, ChatView, cardActions, answerFailure, turnEndLine, eventLine,
  isCommand, sendFailureNote, COMMANDS_NOTE,
  messageText, mergeChat, stopLine, ASK_ON_PC, cardsMissing,
} from '../public/net.js';
import { readFileSync } from 'node:fs';

class FakeWS {
  static last = null;
  constructor(url, protocols) {
    this.url = url;
    this.protocols = protocols;
    this.sent = [];
    this.closed = false;
    FakeWS.last = this;
    queueMicrotask(() => this.onopen?.());
  }
  send(text) { this.sent.push(text); }
  close() {
    if (this.closed) return;
    this.closed = true;
    this.onclose?.({ code: 1000 });
  }
  deliver(obj) { this.onmessage({ data: JSON.stringify(obj) }); }
}

const tick = () => new Promise((r) => setTimeout(r, 0));
async function until(fn, ms = 2000) {
  const end = Date.now() + ms;
  while (!fn()) {
    if (Date.now() > end) throw new Error('condition not met');
    await new Promise((r) => setTimeout(r, 5));
  }
}

// Every Link keeps a ping interval running; stop them so node can exit.
const links = [];
afterEach(() => {
  for (const l of links.splice(0)) l.stop();
});

// A paired phone whose Link has finished the handshake with a node PC.
async function connected({ timeouts } = {}) {
  const pc = N.keyPair();
  const phone = await C.generateKeyPair();
  const kStatic = N.ecdh(pc.d, Buffer.from(phone.publicRaw));
  const deviceId = N.enc(crypto.getRandomValues(new Uint8Array(16)));
  const pushes = [];
  const statuses = [];
  const link = new Link({
    origin: 'wss://relay.test',
    device: { pairId: 'p', pcPub: new Uint8Array(pc.pub), deviceId, token: 't', privateKey: phone.privateKey },
    onStatus: (s) => statuses.push(s),
    onPush: (m) => pushes.push(m),
    WS: FakeWS,
    timeouts,
  });
  links.push(link);
  await link.start();
  const ws = FakeWS.last;
  await until(() => ws.sent.length === 1);
  const { ack, keys } = N.pcHandleHello(JSON.parse(ws.sent[0]), {
    kStatic, knownDeviceId: deviceId, nowSeconds: Math.floor(Date.now() / 1000),
  });
  ws.deliver(ack);
  await until(() => link.ready);
  let counter = 0;
  const pcSend = (obj) => ws.deliver(N.seal(keys.pcToPhone, N.PC_TO_PHONE, ++counter, JSON.stringify(obj)));
  // The request the phone sent last, decrypted as the PC sees it.
  const lastRequest = () => {
    const frame = JSON.parse(ws.sent[ws.sent.length - 1]);
    return frame.d ? JSON.parse(N.open(keys.phoneToPc, N.PHONE_TO_PC, frame)) : {};
  };
  return { link, ws, pcSend, lastRequest, pushes, statuses };
}

// What Backend/app/remote/session.py split_reply produces for an open_chat reply.
function splitOpenChat(id, parts) {
  return parts.map((piece, i) => ({
    id, ok: true, part: i + 1, parts: parts.length,
    result: { chat_id: '7', gap: false, epoch: 'e1', last_seq: 12, ...piece },
  }));
}

// ---------------------------------------------------------------- split replies

test('split reply: parts in order are joined before the request resolves', async () => {
  const { link, pcSend, lastRequest, pushes } = await connected();
  const reply = link.request('open_chat', { chat_id: '7' });
  await until(() => lastRequest().type === 'open_chat');
  const { id } = lastRequest();
  const frames = splitOpenChat(id, [
    { messages: [{ role: 'user', text: 'a' }, { role: 'assistant', text: 'b' }], events: [] },
    { messages: [{ role: 'user', text: 'c' }], events: [{ type: 'event', kind: 'turn_start', seq: 11 }] },
    { messages: [], events: [{ type: 'event', kind: 'text', seq: 12, text: 'x' }] },
  ]);
  let settled = false;
  reply.then(() => (settled = true));
  pcSend(frames[0]);
  pcSend(frames[1]);
  await tick();
  await tick();
  assert.equal(settled, false, 'not resolved on the first part');
  pcSend(frames[2]);
  const r = await reply;
  assert.equal(r.ok, true);
  assert.equal(r.part, undefined);
  assert.equal(r.parts, undefined);
  assert.deepEqual(r.result.messages.map((m) => m.text), ['a', 'b', 'c']);
  assert.deepEqual(r.result.events.map((e) => e.seq), [11, 12]);
  assert.equal(r.result.chat_id, '7');
  assert.equal(r.result.last_seq, 12);
  assert.deepEqual(pushes, [], 'later parts never reach onPush');
});

test('split reply: parts out of order are joined in part order', () => {
  const frames = splitOpenChat(3, [
    { messages: [{ text: '1' }], events: [] },
    { messages: [{ text: '2' }], events: [] },
    { messages: [{ text: '3' }], events: [{ seq: 1 }] },
  ]);
  const set = new ReplyParts();
  assert.deepEqual(set.add(frames[2], 10), {});
  assert.deepEqual(set.add(frames[0], 10), {});
  const { done } = set.add(frames[1], 10);
  assert.deepEqual(done.result.messages.map((m) => m.text), ['1', '2', '3']);
  assert.deepEqual(done.result.events, [{ seq: 1 }]);
  assert.equal(done.id, 3);
});

test('split reply: a single-frame reply resolves unchanged', async () => {
  const { link, pcSend, lastRequest } = await connected();
  const reply = link.request('list_chats');
  await until(() => lastRequest().type === 'list_chats');
  pcSend({ id: lastRequest().id, ok: true, result: { chats: [{ chat_id: '1' }] } });
  assert.deepEqual((await reply).result, { chats: [{ chat_id: '1' }] });
});

test('split reply: bounds on part count and size, and malformed sets', () => {
  const tooMany = new ReplyParts({ maxParts: 4 });
  assert.deepEqual(tooMany.add({ id: 1, ok: true, part: 1, parts: 5, result: {} }), { error: 'too_large' });

  const tooBig = new ReplyParts({ maxChars: 100 });
  assert.deepEqual(tooBig.add({ id: 1, ok: true, part: 1, parts: 2, result: {} }, 60), {});
  assert.deepEqual(tooBig.add({ id: 1, ok: true, part: 2, parts: 2, result: {} }, 60), { error: 'too_large' });

  const bad = new ReplyParts();
  assert.deepEqual(bad.add({ id: 1, ok: true, part: 0, parts: 2, result: {} }), { error: 'bad_reply' });
  assert.deepEqual(bad.add({ id: 1, ok: true, part: 3, parts: 2, result: {} }), { error: 'bad_reply' });
  assert.deepEqual(bad.add({ id: 1, ok: true, part: '1', parts: 2, result: {} }), { error: 'bad_reply' });
  const dup = new ReplyParts();
  dup.add({ id: 1, ok: true, part: 1, parts: 3, result: {} });
  assert.deepEqual(dup.add({ id: 1, ok: true, part: 1, parts: 3, result: {} }), { error: 'bad_reply' });
  assert.deepEqual(dup.add({ id: 1, ok: true, part: 2, parts: 4, result: {} }), { error: 'bad_reply' }, 'parts must not change');
});

test('split reply: an oversized set rejects the request and its remaining parts are dropped', async () => {
  const { link, pcSend, lastRequest, pushes } = await connected();
  const reply = link.request('open_chat', { chat_id: '7' });
  await until(() => lastRequest().type === 'open_chat');
  const { id } = lastRequest();
  pcSend({ id, ok: true, part: 1, parts: 1000, result: { messages: [] } });
  await assert.rejects(reply, /too_large/);
  pcSend({ id, ok: true, part: 2, parts: 1000, result: { messages: [] } });
  await tick();
  await tick();
  assert.deepEqual(pushes, []);
  assert.equal(link.pending.size, 0);
});

test('split reply: an incomplete set times out once parts stop arriving', async () => {
  const { link, pcSend, lastRequest, pushes } = await connected({ timeouts: { request: 5000, partGap: 60 } });
  const reply = link.request('open_chat', { chat_id: '7' });
  await until(() => lastRequest().type === 'open_chat');
  const { id } = lastRequest();
  const frames = splitOpenChat(id, [{ messages: [] }, { messages: [] }, { messages: [] }]);
  pcSend(frames[0]);
  await new Promise((r) => setTimeout(r, 40));
  pcSend(frames[1]); // re-arms the gap timer
  await new Promise((r) => setTimeout(r, 40));
  assert.equal(link.pending.size, 1, 'each part extends the deadline');
  await assert.rejects(reply, /timeout/);
  pcSend(frames[2]);
  await tick();
  await tick();
  assert.deepEqual(pushes, [], 'a late part is dropped, not shown as a push');
});

test('split reply: an error part wins over the others', () => {
  const merged = mergeParts([
    { id: 1, ok: true, part: 1, parts: 2, result: { messages: [1] } },
    { id: 1, ok: false, part: 2, parts: 2, error: 'internal' },
  ]);
  assert.deepEqual(merged, { id: 1, ok: false, error: 'internal' });
});

test('replies nobody waits for are dropped; pushes still arrive', async () => {
  const { pcSend, pushes } = await connected();
  pcSend({ id: 99, ok: true, result: {} });
  pcSend({ id: null, ok: false, error: 'busy' });
  pcSend({ type: 'card_closed', card_id: 'k' });
  await until(() => pushes.length === 1);
  await tick();
  assert.deepEqual(pushes, [{ type: 'card_closed', card_id: 'k' }]);
});

test('a request whose frame would exceed the relay phone cap is refused before sending', async () => {
  const { link, ws } = await connected();
  const before = ws.sent.length;
  // 3-byte UTF-8 characters: under the 20 000-character bridge limit, over 64 KiB once sealed.
  await assert.rejects(link.request('send_message', { chat_id: '1', text: '日'.repeat(19_000) }), /too_large/);
  assert.equal(ws.sent.length, before, 'nothing reached the socket (the relay would close it)');
  assert.ok(!ws.closed);
  const ok = link.request('send_message', { chat_id: '1', text: 'kısa' });
  await until(() => ws.sent.length === before + 1);
  assert.ok(ws.sent[ws.sent.length - 1].length <= PHONE_FRAME_MAX);
  link.stop();
  await assert.rejects(ok, /disconnected/);
});

// ---------------------------------------------------------------- question cards

// Backend/app/remote/rpc.py answer_card + chats.single_choice_options, as a
// predicate: would the bridge accept this payload for this card?
function bridgeAccepts(card, options, payload) {
  const { decision, choice } = payload;
  if (!['approve', 'reject', 'choice'].includes(decision)) return false;
  if (card.kind === 'question') {
    if (decision === 'reject') return true;
    if (decision === 'choice') return options !== null && typeof choice === 'string' && options.includes(choice);
    return false; // approve -> unsupported_on_phone
  }
  return decision !== 'choice';
}

// chats.phone_card output for a single-select question.
const singleQuestion = {
  card_id: 'q1', chat_id: '4', kind: 'question', tool: null, risk: null, created_at: 1,
  title: 'Soru', detail: 'Hangi sahne?',
  choices: [{ id: 'Arena', label: 'Arena' }, { id: 'Menü', label: 'Menü' }],
};
const multiQuestion = { card_id: 'q2', chat_id: '4', kind: 'question', title: 'Soru', detail: 'A?\nB?' };
const commandCard = { card_id: 'c1', chat_id: '4', kind: 'command', tool: 'git', title: 'git', detail: 'git commit -m x' };

test('question card: one button per option sending decision "choice" with the option label, plus Reject', () => {
  const { buttons, note } = cardActions(singleQuestion);
  assert.equal(note, null);
  assert.deepEqual(buttons.map((b) => b.payload), [
    { card_id: 'q1', decision: 'choice', choice: 'Arena' },
    { card_id: 'q1', decision: 'choice', choice: 'Menü' },
    { card_id: 'q1', decision: 'reject' },
  ]);
  assert.deepEqual(buttons.map((b) => b.label), ['Arena', 'Menü', 'Reddet']);
  for (const b of buttons) assert.ok(bridgeAccepts(singleQuestion, ['Arena', 'Menü'], b.payload), JSON.stringify(b.payload));
});

test('question card without choices: no answer button, "answer on the PC" note, Reject still works', () => {
  const { buttons, note } = cardActions(multiQuestion);
  assert.equal(note, ASK_ON_PC);
  assert.deepEqual(buttons.map((b) => b.payload), [{ card_id: 'q2', decision: 'reject' }]);
  for (const b of buttons) assert.ok(bridgeAccepts(multiQuestion, null, b.payload));
});

test('approval cards send approve / reject and never "choice"', () => {
  const { buttons, note } = cardActions({ ...commandCard, choices: ['x'] });
  assert.equal(note, null);
  assert.deepEqual(buttons.map((b) => b.payload.decision), ['approve', 'reject']);
  for (const b of buttons) assert.ok(bridgeAccepts(commandCard, null, b.payload));
});

test('answer failures: already_answered names who answered and closes the card', () => {
  assert.deepEqual(answerFailure('already_answered', { by: 'desktop', at: 'x' }), { close: true, note: 'Başka cihaz (bilgisayar) cevapladı.' });
  assert.deepEqual(answerFailure('already_answered', { by: 'phone:iPad' }), { close: true, note: 'Başka cihaz (iPad) cevapladı.' });
  assert.equal(answerFailure('already_answered', { by: 'system' }).close, true);
  assert.doesNotMatch(answerFailure('already_answered', { by: 'system' }).note, /Başka cihaz/);
  assert.equal(answerFailure('not_found').close, true);
  assert.deepEqual(answerFailure('unsupported_on_phone'), { close: false, onlyReject: true, note: ASK_ON_PC });
  assert.deepEqual(answerFailure('busy'), { close: false, note: 'Gönderilemedi: busy' });
});

test('already_answered from the PC reaches the page with by/at intact', async () => {
  const { link, pcSend, lastRequest } = await connected();
  const reply = link.request('answer_card', { card_id: 'c1', decision: 'approve' });
  await until(() => lastRequest().type === 'answer_card');
  pcSend({ id: lastRequest().id, ok: false, error: 'already_answered', by: 'desktop', at: '2026-09-28T10:00:00Z' });
  const r = await reply;
  assert.equal(answerFailure(r.error, r).note, 'Başka cihaz (bilgisayar) cevapladı.');
});

// ---------------------------------------------------------------- cards the log announces

// Real iPhone, 28 Sep 2026: the chat log said "Onay kartı açıldı" (Codex
// command gate) and "Soru kartı açıldı" (AskUserQuestion) but no card with
// buttons was in sight. The shapes below are what chats.phone_card sends for
// those two gates.
const codexCard = {
  card_id: 'gate-codex', chat_id: '5', kind: 'command', tool: 'item/commandExecution/requestApproval',
  risk: 'shell', created_at: '2026-09-28 23:34:10', title: 'item/commandExecution/requestApproval',
  detail: 'powershell -Command Get-ChildItem',
};
const askCard = {
  card_id: 'gate-ask', chat_id: '5', kind: 'question', tool: 'AskUserQuestion', risk: null,
  created_at: '2026-09-28 23:35:40', title: 'Soru', detail: 'Hangi motoru kullanalım?',
  choices: [{ id: 'Unity', label: 'Unity' }, { id: 'Godot', label: 'Godot' }],
};

test('the chat view shows its cards under the log, next to the line that announced them', () => {
  // The body scrolls and a loaded chat is scrolled to the bottom, so cards
  // placed above up to 50 messages were off screen while the log below said
  // a card had opened.
  const html = readFileSync(new URL('../public/index.html', import.meta.url), 'utf8');
  const chat = html.slice(html.indexOf('<section id="screen-chat"'));
  const at = (id) => chat.indexOf('id="' + id + '"');
  assert.ok(at('log') > 0 && at('chat-cards') > 0 && at('composer') > 0);
  assert.ok(at('log') < at('chat-cards'), '#chat-cards must come after #log');
  assert.ok(at('chat-cards') < at('composer'), '#chat-cards must stay above the composer');
});

test('both gate kinds pushed to the page give answerable buttons', async () => {
  const { pcSend, pushes } = await connected();
  pcSend({ type: 'card_opened', card: codexCard });
  pcSend({ type: 'card_opened', card: askCard });
  await until(() => pushes.length === 2);
  const [cmd, ask] = pushes.map((p) => cardActions(p.card));
  assert.deepEqual(cmd.buttons.map((b) => b.payload), [
    { card_id: 'gate-codex', decision: 'approve' }, { card_id: 'gate-codex', decision: 'reject' }]);
  assert.deepEqual(ask.buttons.map((b) => b.payload.choice ?? b.payload.decision), ['Unity', 'Godot', 'reject']);
});

test('a card the log announces but the page does not hold is fetched again', () => {
  const opened = (id) => ({ type: 'event', chat_id: '5', kind: 'card_opened', card_id: id, card_kind: 'command' });
  const closed = (id) => ({ type: 'event', chat_id: '5', kind: 'card_closed', card_id: id });
  // The card_opened push was lost (socket replaced, page asleep): only the log has it.
  assert.equal(cardsMissing([], [opened('gate-codex')]), true);
  assert.equal(cardsMissing([codexCard], [opened('gate-codex')]), false);
  // Closed later in the same log: nothing to fetch.
  assert.equal(cardsMissing([], [opened('gate-codex'), closed('gate-codex')]), false);
  assert.equal(cardsMissing([codexCard], [opened('gate-codex'), opened('gate-ask')]), true);
  assert.equal(cardsMissing([], [{ kind: 'tool_call', tool: 'x' }, { kind: 'card_opened' }]), false);
});

// ---------------------------------------------------------------- turn status and log lines

test('turn_end status: done is a normal finish, error is marked, stopped says durduruldu', () => {
  assert.deepEqual(turnEndLine('done'), { text: 'Tur bitti', error: false });
  assert.deepEqual(turnEndLine('error'), { text: 'Tur hatayla bitti', error: true });
  assert.deepEqual(turnEndLine('stopped'), { text: 'Tur durduruldu', error: false });
  assert.equal(eventLine({ type: 'event', kind: 'turn_end', status: 'done', stop_reason: 'complete' }).text, 'Tur bitti');
  assert.equal(eventLine({ kind: 'tool_call', tool: 'Bash', summary: 'ls' }).text, 'Araç: Bash - ls');
  assert.equal(eventLine({ kind: 'card_opened', card_kind: 'question' }).text, 'Soru kartı açıldı');
  assert.match(eventLine({ truncated: true }).text, /gösterilemedi/);
});

test('messages cut by the bridge are labelled, not shown blank', () => {
  assert.equal(messageText({ role: 'user', text: 'selam' }), 'selam');
  assert.match(messageText({ role: 'assistant', text: 'uzun', truncated: true }), /^uzun .*kısaltıldı/);
  assert.match(messageText({ truncated: true }), /uzun/);
});

test('stop reply statuses', () => {
  assert.equal(stopLine('ok'), 'Durdurma isteği gönderildi.');
  assert.match(stopLine('no_session'), /bulunamadı/);
  assert.match(stopLine('error'), /Durdurulamadı/);
});

test('chat_changed: hidden idle chats leave the list like list_chats leaves them out', () => {
  const list = [{ chat_id: '1', status: 'idle' }, { chat_id: '2', status: 'running' }];
  assert.deepEqual(mergeChat(list, { chat_id: '2', status: 'idle', hidden: true }).map((c) => c.chat_id), ['1']);
  assert.deepEqual(mergeChat(list, { chat_id: '2', status: 'running', hidden: true }).map((c) => c.chat_id), ['1', '2']);
  assert.deepEqual(mergeChat(list, { chat_id: '3', status: 'idle', hidden: false }).map((c) => c.chat_id), ['1', '2', '3']);
});

// ---------------------------------------------------------------- late open replies and gaps

test('late open reply after close_chat: ignored, and close_chat is sent again', () => {
  const view = new ChatView();
  view.show('7');
  const t = view.beginLoad();
  assert.equal(view.hide(), '7'); // the page sends close_chat 7 here
  assert.equal(view.endLoad(t), 'orphan', 'the PC may have registered its listener after that close');
});

test('late open reply after moving to another chat: ignored and closed; the new chat renders', () => {
  const view = new ChatView();
  view.show('7');
  const t7 = view.beginLoad();
  assert.equal(view.show('8'), '7', 'the caller closes the previous chat');
  const t8 = view.beginLoad();
  assert.equal(view.endLoad(t7), 'orphan');
  assert.equal(view.endLoad(t8), 'apply');
});

test('close then reopen the same chat: only the newest open reply renders', () => {
  const view = new ChatView();
  view.show('7');
  const first = view.beginLoad();
  view.hide();
  view.show('7');
  const second = view.beginLoad();
  assert.equal(view.endLoad(first), 'stale', 'not rendered, not closed (the chat is shown again)');
  assert.equal(view.endLoad(second), 'apply');
});

test('pushes for a chat not shown ask for close_chat, at most once per interval', () => {
  let now = 0;
  const view = new ChatView({ now: () => now, strayEveryMs: 1000 });
  view.show('1');
  assert.equal(view.stray('1'), false);
  assert.equal(view.stray('2'), true);
  assert.equal(view.stray('2'), false);
  now = 1500;
  assert.equal(view.stray('2'), true);
});

test('gap on the shown chat re-opens it; during a load it re-opens once more afterwards', () => {
  const view = new ChatView();
  view.show('7');
  const t = view.beginLoad();
  assert.equal(view.onGap('7'), 'ignore');
  assert.equal(view.endLoad(t), 'apply');
  assert.equal(view.takeReload(), true);
  assert.equal(view.takeReload(), false);
  assert.equal(view.onGap('7'), 'reload');
  assert.equal(view.onGap('9'), 'close', 'a gap for a chat not shown closes its listener');
});

test('sustained gaps: at most three immediate reloads, then growing delays, one reload at a time', () => {
  let now = 0;
  const view = new ChatView({ now: () => now });
  view.show('7');
  let immediate = 0;
  const delays = [];
  let decision = view.onGap('7');
  for (let i = 0; i < 20; i += 1) {
    if (decision === 'reload') immediate += 1;
    else {
      assert.equal(decision, 'later');
      const s = view.takeSchedule();
      assert.ok(s, 'a delayed reload is planned');
      assert.equal(view.takeSchedule(), null, 'the plan is handed out once');
      assert.equal(view.onGap('7'), 'ignore', 'no second reload while one is planned');
      delays.push(s.delayMs);
      now += s.delayMs;
      assert.equal(view.runScheduled(s), true);
    }
    const t = view.beginLoad();
    assert.equal(view.onGap('7'), 'ignore', 'a gap during a load does not start another load');
    assert.equal(view.endLoad(t), 'apply');
    decision = view.takeReload() ? 'reload' : 'later';
  }
  assert.equal(immediate, 3);
  assert.deepEqual(delays.slice(0, 6), [1000, 2000, 4000, 8000, 16000, 30000]);
  assert.ok(delays.every((d, i) => i === 0 || d >= delays[i - 1]), 'delays never shrink');
  assert.ok(Math.max(...delays) <= 30000);
});

test('a quiet period resets the gap streak; a new load or chat cancels a planned reload', () => {
  let now = 0;
  const view = new ChatView({ now: () => now, gapQuietMs: 60_000 });
  view.show('7');
  for (let i = 0; i < 3; i += 1) assert.equal(view.onGap('7'), 'reload');
  assert.equal(view.onGap('7'), 'later');
  const planned = view.takeSchedule();
  const t = view.beginLoad(); // e.g. refreshAll after a reconnect
  assert.equal(view.runScheduled(planned), false, 'the newer load supersedes the planned one');
  assert.equal(view.endLoad(t), 'apply');
  assert.equal(view.takeReload(), false);
  now += 61_000;
  assert.equal(view.onGap('7'), 'reload', 'after a quiet minute gaps reload at once again');
  view.show('7');
  for (let i = 0; i < 3; i += 1) assert.equal(view.onGap('7'), 'reload');
  assert.equal(view.onGap('7'), 'later');
  const s = view.takeSchedule();
  view.show('8');
  assert.equal(view.runScheduled(s), false, 'a chat switch cancels it');
  assert.equal(view.onGap('8'), 'reload', 'and starts a fresh streak');
});

test('gap push from the PC reaches the page and a fresh open_chat recovers the view', async () => {
  const { link, pcSend, lastRequest, pushes } = await connected();
  const view = new ChatView();
  view.show('7');
  const loads = [];
  const load = async () => {
    const t = view.beginLoad();
    const r = await link.request('open_chat', { chat_id: t.chatId });
    loads.push([view.endLoad(t), r.result.events.length]);
  };
  const first = load();
  await until(() => lastRequest().type === 'open_chat');
  pcSend({ id: lastRequest().id, ok: true, result: { chat_id: '7', messages: [], events: [], gap: false, epoch: 'e', last_seq: 3 } });
  await first;
  const firstId = lastRequest().id;
  pcSend({ type: 'gap', chat_id: '7', epoch: 'e', last_seq: 40 });
  await until(() => pushes.length === 1);
  assert.equal(view.onGap(pushes[0].chat_id), 'reload');
  const second = load();
  await until(() => lastRequest().id !== undefined && lastRequest().id !== firstId);
  const req = lastRequest();
  assert.deepEqual([req.type, req.chat_id, req.since_seq], ['open_chat', '7', undefined]);
  pcSend({ id: req.id, ok: true, result: { chat_id: '7', messages: [], events: [{ kind: 'turn_start', seq: 38 }], gap: false, epoch: 'e', last_seq: 40 } });
  await second;
  assert.deepEqual(loads, [['apply', 0], ['apply', 1]]);
});

// ---------------------------------------------------------------- commands

test('slash commands are refused on the phone before sending, by the backend rule', () => {
  for (const text of ['/usage', '  /compact', '\n\t/model x', '\u200b/mode plan', '\ufeff/skill', '\u00a0/x']) {
    assert.equal(isCommand(text), true, JSON.stringify(text));
  }
  for (const text of ['a /usage', 'yol: /tmp/x', '\\/usage', '\uff0fusage', '', 'merhaba']) {
    assert.equal(isCommand(text), false, JSON.stringify(text));
  }
  assert.equal(COMMANDS_NOTE, 'Komutlar telefondan çalıştırılamaz.');
  assert.equal(sendFailureNote('commands_not_allowed'), COMMANDS_NOTE);
  assert.match(sendFailureNote('too_large'), /uzun/);
  assert.equal(sendFailureNote('unknown_chat'), 'Gönderilemedi: unknown_chat');
});

// ---------------------------------------------------------------- close races

// The relay forwards the PC's reply to the pairing socket and closes it with
// pair_done (worker/room.js CLOSE.pairDone) in the same step, so the page sees
// the close while it is still decrypting pair_ok. Measured on an iPhone: the
// PC had the device, the phone said "Bilgisayar eşleştirmeyi reddetti".
const PAIR_DONE = 4002;

async function pairAgainst(answer) {
  const pc = N.keyPair();
  const pairSecret = crypto.getRandomValues(new Uint8Array(16));
  const parsed = C.parsePairFragment(`${N.enc(crypto.getRandomValues(new Uint8Array(16)))}.${N.enc(pc.pub)}.${N.enc(pairSecret)}`);
  const WS = class {
    constructor() { queueMicrotask(() => this.onopen()); }
    send(text) {
      const side = N.pcHandlePairRequest(JSON.parse(text), { pcD: pc.d, pairSecret: Buffer.from(pairSecret) });
      setTimeout(() => answer(this, side.kPair), 0);
    }
    close() {}
  };
  return pair({ origin: 'wss://relay.test', parsed, deviceName: 'iPhone', onSas: () => {}, WS });
}

const frame = (obj) => ({ data: JSON.stringify(obj) });

test('pairing: pair_ok with the pair_done close right behind it resolves with the device', async () => {
  const device = await pairAgainst((ws, kPair) => {
    ws.onmessage(frame(N.pcPairOk(kPair, { device_id: 'dev1', token: 'tok1', vapid_pub: 'vp' })));
    ws.onclose({ code: PAIR_DONE });
  });
  assert.equal(device.deviceId, 'dev1');
  assert.equal(device.token, 'tok1');
  assert.equal(device.vapidPub, 'vp');
  assert.ok(device.privateKey && device.publicRaw);
});

test('pairing: the reply decides, not the pair_done close after it', async () => {
  await assert.rejects(pairAgainst((ws) => {
    ws.onmessage(frame({ type: 'pair_reject', reason: 'sas_mismatch' }));
    ws.onclose({ code: PAIR_DONE });
  }), { message: 'sas_mismatch' });
  await assert.rejects(pairAgainst((ws) => {
    ws.onmessage(frame(N.pcPairOk(Buffer.alloc(32, 7), { device_id: 'dev1', token: 'tok1' })));
    ws.onclose({ code: PAIR_DONE });
  }), { message: 'bad_reply' }, 'a pair_ok that does not open is bad_reply');
  await assert.rejects(pairAgainst((ws) => {
    ws.onmessage({ data: 'not json' });
    ws.onclose({ code: PAIR_DONE });
  }), { message: 'bad_reply' }, 'the PC answered but nothing readable arrived');
  await assert.rejects(pairAgainst((ws) => ws.onclose({ code: 1006 })), { message: 'rejected' });
});

test('hello_ack still being processed when the socket closes does not report ready', async () => {
  const pc = N.keyPair();
  const phone = await C.generateKeyPair();
  const kStatic = N.ecdh(pc.d, Buffer.from(phone.publicRaw));
  const deviceId = N.enc(crypto.getRandomValues(new Uint8Array(16)));
  const statuses = [];
  const link = new Link({
    origin: 'wss://relay.test',
    device: { pairId: 'p', pcPub: new Uint8Array(pc.pub), deviceId, token: 't', privateKey: phone.privateKey },
    onStatus: (s) => statuses.push(s),
    onPush: () => {},
    WS: FakeWS,
  });
  links.push(link);
  await link.start();
  const ws = FakeWS.last;
  await until(() => ws.sent.length === 1);
  const { ack } = N.pcHandleHello(JSON.parse(ws.sent[0]), { kStatic, knownDeviceId: deviceId, nowSeconds: Math.floor(Date.now() / 1000) });
  // The ack verifies, then the session key agreement starts: drop the token
  // right there, while handle() is between its awaits.
  const subtle = crypto.subtle;
  const deriveBits = subtle.deriveBits;
  subtle.deriveBits = function (...args) {
    subtle.deriveBits = deriveBits;
    ws.onclose({ code: 4001 });
    return deriveBits.apply(this, args);
  };
  try {
    ws.deliver(ack);
    await link.inbox;
  } finally {
    subtle.deriveBits = deriveBits;
  }
  assert.ok(statuses.includes('removed'), 'the close landed mid-handshake');
  assert.equal(statuses.at(-1), 'removed');
  assert.equal(link.ready, false);
});
