// The phone page's model and effort controls: the DOM-free helpers in
// public/net.js (what app.js renders) and what the markup and app.js wire up.
// The requests themselves are the PC's (Backend/tests/test_remote_effort.py,
// test_remote_model.py).

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import {
  EFFORT_LABELS, EFFORT_UNKNOWN_NOTE, effortLabel, desktopEffort, modelValue, parseModelValue, modelGroups,
  modelFailureNote, modelListFailureNote, modelChangedNote, effortFailureNote, effortSetNote, effortOutcomeNote,
  configFailureNote,
} from '../public/net.js';

// Every level the registry can return (Backend effort_caps.py EFFORT_LEVELS).
const LEVELS = ['auto', 'off', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'];

// ---------------------------------------------------------------- effort

test('the effort labels are the desktop\'s words for every level the registry can return', () => {
  assert.deepEqual(Object.keys(EFFORT_LABELS), LEVELS);
  assert.equal(effortLabel('none'), 'None');
  assert.equal(effortLabel('xhigh'), 'XHigh');
  assert.equal(effortLabel('turbo'), 'turbo');
  assert.equal(effortLabel('constructor'), 'constructor');
  const i18n = new URL('../../Frontend/frontend/renderer/lib/i18n.tsx', import.meta.url);
  if (existsSync(i18n)) {
    const src = readFileSync(i18n, 'utf8');
    for (const [level, label] of Object.entries(EFFORT_LABELS)) {
      assert.ok(src.includes(`'effort.label.${level}': '${label}'`), `desktop i18n has no "${label}" for effort.label.${level}`);
    }
  }
});

test('the desktop effort is shown only when it is whole and consistent', () => {
  assert.deepEqual(desktopEffort({ level: 'high', levels: ['auto', 'low', 'high'] }), { level: 'high', levels: ['auto', 'low', 'high'] });
  // A level the page has no word for is dropped from the list, not shown raw.
  assert.deepEqual(desktopEffort({ level: 'low', levels: ['auto', 'low', 'turbo'] }), { level: 'low', levels: ['auto', 'low'] });
  // OpenAI API models offer `none`.
  assert.deepEqual(desktopEffort({ level: 'none', levels: ['auto', 'none', 'low'] }),
    { level: 'none', levels: ['auto', 'none', 'low'] });
  for (const bad of [null, undefined, 'high', 7, [], {}, { level: 'high' }, { levels: ['high'] },
    { level: 5, levels: ['high'] }, { level: 'high', levels: 'high' }, { level: 'high', levels: [] },
    { level: 'high', levels: ['low'] }, { level: 'turbo', levels: ['turbo'] }, { level: 'constructor', levels: ['constructor'] }]) {
    assert.equal(desktopEffort(bad), null, JSON.stringify(bad));
  }
});

test('set_effort replies read as a sentence; only "accepted" says it went out', () => {
  assert.equal(effortSetNote('accepted', 'high'), 'Bilgisayara iletildi: Yüksek.');
  assert.match(effortSetNote('desktop_not_ready', 'high'), /^Düşünme seviyesi değişmedi\. Bilgisayardaki uygulama hazır değil/);
  assert.match(effortSetNote('weird', 'high'), /Yanıt: weird/);
  assert.match(effortSetNote(undefined, 'high'), /Yanıt: bilinmiyor/);
});

test('what the PC really has is reported after an accepted request', () => {
  assert.equal(effortOutcomeNote('high', 'high'), 'Bilgisayarda değişti: Yüksek.');
  const refused = effortOutcomeNote('max', 'high');
  assert.match(refused, /Max seviyesini uygulamadı/);
  assert.match(refused, /şu an: Yüksek\.$/);
});

test('every failure of set_effort says plainly that nothing changed', () => {
  assert.equal(effortFailureNote('bad_effort'), 'Düşünme seviyesi değişmedi. Bu düşünme seviyesi geçersiz.');
  assert.match(effortFailureNote('not_ready'), /^Düşünme seviyesi değişmedi\. Önce bilgisayara bağlanmalı/);
  assert.match(effortFailureNote('busy'), /meşgul/);
  assert.match(effortFailureNote('timeout'), /yanıt gelmedi/);
  assert.match(effortFailureNote('disconnected'), /Bağlantı koptu/);
  assert.match(effortFailureNote('internal'), /beklenmeyen bir hata/);
  assert.equal(effortFailureNote('boom'), 'Düşünme seviyesi değiştirilemedi: boom');
  // An older desktop app that does not know the request, and a request it cannot read.
  assert.match(effortFailureNote('unknown_type'), /^Düşünme seviyesi değişmedi\. Bilgisayardaki Gamachine bu isteği tanımıyor; .*güncelle\.$/);
  assert.equal(effortFailureNote('bad_request'), 'Düşünme seviyesi değişmedi. İstek anlaşılamadı.');
  assert.match(EFFORT_UNKNOWN_NOTE, /bilinmiyor/);
});

// ---------------------------------------------------------------- model

test('a model option value splits at the first bar, so a model id may hold one', () => {
  assert.equal(modelValue('subscription', 'claude-opus-5'), 'subscription|claude-opus-5');
  assert.deepEqual(parseModelValue('subscription|claude-opus-5'), { provider_type: 'subscription', model_name: 'claude-opus-5' });
  assert.deepEqual(parseModelValue(modelValue('openrouter', 'a|b')), { provider_type: 'openrouter', model_name: 'a|b' });
  assert.deepEqual(parseModelValue('openai|'), { provider_type: 'openai', model_name: '' });
  for (const bad of ['', '|x', 'nobar', null, undefined, 4]) assert.equal(parseModelValue(bad), null, String(bad));
});

const CATALOG = {
  local: [{ id: 'llama3', name: 'Llama3 (Local)', provider: 'ollama' }],
  cloud: [
    { id: 'gpt-5.5', name: 'GPT-5.5', provider: 'openai', available: true },
    { id: 'gpt-old', name: 'GPT Old', provider: 'openai', available: false },
    { id: 'gpt-fallback', name: 'GPT Fallback', provider: 'openai', verified: false },
  ],
  subscription: [
    { id: 'claude-opus-5', name: 'Claude Opus 5 (CLI)', provider: 'subscription' },
    { id: 'claude-opus-5', name: 'Claude Opus 5 again', provider: 'subscription' },
  ],
};

test('the model groups are the picker\'s catalog, without cloud models the account cannot call', () => {
  const { groups, currentValue } = modelGroups(CATALOG, { provider_type: 'subscription', model_name: 'claude-opus-5' });
  assert.deepEqual(groups.map((g) => g.label), ['Abonelik (komut satırı)', 'Bulut (API)', 'Yerel']);
  assert.deepEqual(groups[0].items, [{ value: 'subscription|claude-opus-5', label: 'Claude Opus 5 (CLI)' }]);
  assert.deepEqual(groups[1].items, [{ value: 'openai|gpt-5.5', label: 'GPT-5.5' }]);
  assert.deepEqual(groups[2].items, [{ value: 'ollama|llama3', label: 'Llama3 (Local)' }]);
  assert.equal(currentValue, 'subscription|claude-opus-5');
});

test('the chat\'s own model is always an option, even when the catalog lacks it', () => {
  const typed = modelGroups(CATALOG, { provider_type: 'openai', model_name: 'some-typed-id' });
  assert.equal(typed.groups[0].label, 'Bu sohbetteki');
  assert.deepEqual(typed.groups[0].items, [{ value: 'openai|some-typed-id', label: 'openai · some-typed-id' }]);
  const dflt = modelGroups(CATALOG, { provider_type: 'anthropic', model_name: '' });
  assert.equal(dflt.groups[0].items[0].label, 'anthropic · sağlayıcının varsayılanı');
  // The catalog did not come (list_models failed): the select still shows the chat's model.
  const none = modelGroups(null, { provider_type: 'subscription', model_name: 'claude-opus-5' });
  assert.deepEqual(none.groups.map((g) => g.items.map((i) => i.value)), [['subscription|claude-opus-5']]);
  assert.deepEqual(modelGroups(undefined, null), { groups: [], currentValue: null });
});

test('a malformed catalog entry is skipped, not shown', () => {
  const { groups } = modelGroups({
    subscription: [null, {}, { id: 5, provider: 'subscription' }, { id: 'x' }, { id: 'ok', provider: 'subscription' }],
    cloud: 'nope',
  }, null);
  assert.deepEqual(groups, [{ label: 'Abonelik (komut satırı)', items: [{ value: 'subscription|ok', label: 'ok' }] }]);
});

test('every refusal of set_model says why in plain Turkish and that the model did not change', () => {
  const needs = {
    apikey: /API anahtarı girilmemiş/, install: /kurulu değil/, login: /oturum açılmamış/, service: /Ollama/,
  };
  for (const [key, pattern] of Object.entries(needs)) {
    const note = modelFailureNote('not_ready', { ok: false, error: 'not_ready', needs: key });
    assert.match(note, /^Model değişmedi\. /, key);
    assert.match(note, pattern, key);
  }
  assert.match(modelFailureNote('not_ready', { needs: 'other' }), /Sağlayıcı şu an hazır değil/);
  // The link's own not_ready has no `needs`: it is "not connected".
  assert.equal(modelFailureNote('not_ready'), 'Model değişmedi. Önce bilgisayara bağlanmalı.');
  assert.equal(modelFailureNote('not_ready', undefined), 'Model değişmedi. Önce bilgisayara bağlanmalı.');
  assert.equal(modelFailureNote('unknown_chat'), 'Model değişmedi. Bu sohbet artık yok.');
  assert.equal(modelFailureNote('bad_chat_id'), 'Model değişmedi. Sohbet numarası geçersiz.');
  assert.equal(modelFailureNote('unknown_provider'), 'Model değişmedi. Bilinmeyen sağlayıcı.');
  assert.equal(modelFailureNote('bad_model'), 'Model değişmedi. Model adı geçersiz.');
  assert.match(modelFailureNote('busy'), /meşgul/);
  assert.match(modelFailureNote('timeout'), /yanıt gelmedi/);
  assert.match(modelFailureNote('disconnected'), /Bağlantı koptu/);
  assert.match(modelFailureNote('internal'), /beklenmeyen bir hata/);
  assert.equal(modelFailureNote('boom'), 'Model değiştirilemedi: boom');
  assert.match(modelFailureNote('unknown_type'), /^Model değişmedi\. Bilgisayardaki Gamachine bu isteği tanımıyor; .*güncelle\.$/);
  assert.equal(modelFailureNote('bad_request'), 'Model değişmedi. İstek anlaşılamadı.');
  assert.equal(modelFailureNote('plan_locked'),
    'Model değişmedi. Aboneliğin bu modeli desteklemiyor; Auto modelini kullanabilirsin.');
});

test('the desktop\'s plan lock: a locked model is listed, marked, and not offered as a choice', () => {
  const { groups } = modelGroups({
    subscription: [
      { id: 'copilot-auto', name: 'Copilot Auto', provider: 'subscription' },
      { id: 'copilot-gpt-5.5', name: 'GPT-5.5', provider: 'subscription', disabled: true, disabled_reason: 'plan' },
      { id: 'x', name: 'Not locked', provider: 'subscription', disabled: 'yes' },
    ],
  }, null);
  assert.deepEqual(groups[0].items, [
    { value: 'subscription|copilot-auto', label: 'Copilot Auto' },
    { value: 'subscription|copilot-gpt-5.5', label: 'GPT-5.5 (planında kilitli)', disabled: true },
    { value: 'subscription|x', label: 'Not locked' },
  ]);
});

test('the list and the config notes word an old desktop and a bad request too', () => {
  assert.match(modelListFailureNote('unknown_type'), /^Model listesi alınamadı: Bilgisayardaki Gamachine bu isteği tanımıyor/);
  assert.match(configFailureNote('unknown_type'), /^Bilgisayardaki ayarlar okunamadı: Bilgisayardaki Gamachine bu isteği tanımıyor/);
  assert.equal(modelListFailureNote('bad_request'), 'Model listesi alınamadı: İstek anlaşılamadı.');
});

test('the other notes: model list, chat config, a changed model', () => {
  assert.equal(modelListFailureNote('unavailable'), 'Bilgisayardaki uygulama model listesini veremedi.');
  assert.equal(modelListFailureNote('timeout'), 'Model listesi alınamadı: Bilgisayardan yanıt gelmedi.');
  assert.equal(modelListFailureNote('boom'), 'Model listesi alınamadı: boom');
  assert.equal(configFailureNote('unknown_chat'), 'Bu sohbet artık yok.');
  assert.equal(configFailureNote('busy'), 'Bilgisayardaki ayarlar okunamadı: Bilgisayar şu an meşgul; biraz sonra tekrar dene.');
  assert.equal(configFailureNote('bad_reply'), 'Bilgisayardaki ayarlar okunamadı: bad_reply');
  const changed = modelChangedNote({ provider_type: 'openai', model_name: 'gpt-5.5' });
  assert.match(changed, /^Model değişti: gpt-5\.5\./);
  assert.match(changed, /Çalışan bir tur başladığı modelle biter/);
  assert.match(modelChangedNote({ model_name: '' }), /sağlayıcının varsayılanı/);
});

// ---------------------------------------------------------------- markup and wiring

const html = readFileSync(new URL('../public/index.html', import.meta.url), 'utf8');
const app = readFileSync(new URL('../public/app.js', import.meta.url), 'utf8');

test('the open-chat screen holds the model and effort selects, and says a running turn keeps its model', () => {
  const chat = html.slice(html.indexOf('<section id="screen-chat"'));
  for (const id of ['model-select', 'model-note', 'effort-select', 'effort-note']) {
    assert.ok(chat.includes(`id="${id}"`), `#${id} is not on the chat screen`);
  }
  assert.match(chat, /<select id="model-select"[^>]*disabled/);
  assert.match(chat, /<select id="effort-select"[^>]*disabled/);
  assert.match(chat, /Çalışan bir tur, başladığı modelle biter\./);
});

test('the page changes the PC\'s settings through set_model and set_effort and keeps no effort of its own', () => {
  assert.ok(app.includes("call('set_model'"));
  assert.ok(app.includes("call('set_effort'"));
  assert.ok(app.includes("call('get_config', { chat_id: chatId })"));
  assert.ok(app.includes("call('list_models')"));
  // No per-message effort: the desktop's level applies to a phone message.
  const send = app.slice(app.indexOf("call('send_message'"), app.indexOf("call('send_message'") + 120);
  assert.ok(!/effort/.test(send), send);
  assert.ok(!/localStorage|store\.put\('effort|store\.put\('model/.test(app), 'settings are never kept on the phone');
});

test('the page follows the PC: effort_changed and chat_model_changed refresh what it shows', () => {
  assert.match(app, /msg\.type === 'effort_changed'[\s\S]{0,120}takePcEffort\(desktopEffort\(msg\.desktop_effort\)/);
  assert.match(app, /msg\.type === 'chat_model_changed'[\s\S]{0,120}refreshChatSettings\(\)/);
  // A pick on the desktop with no chat open moves the default; the open chat re-reads its model.
  assert.match(app, /msg\.type === 'default_model_changed'[\s\S]{0,260}refreshAll\(\)/);
  // When the link comes back and when the page becomes visible again.
  assert.match(app, /view\.shown\) \{\s*loadChat\(\);\s*refreshChatSettings\(\);/);
  assert.match(app, /visibilityState === 'visible' && link\) \{\s*link\.wake\(\);\s*refreshChatSettings\(\);/);
});
