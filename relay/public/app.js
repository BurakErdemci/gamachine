import { t, setLang, getLang, applyStatic } from './i18n.js';

// Phone UI. Everything shown comes from the PC and is written with
// textContent only: chat text is untrusted input on this page.

import * as C from './crypto.js';
import * as store from './store.js';
import {
  pair, Link, wsOrigin, ChatView, cardActions, answerFailure, eventLine, messageText, mergeChat, stopLine, SEND_TEXT_MAX,
  sendFailureNote, sentNote, cardsMissing, slashItems, filterSlash, withCommand, slashFailureNote,
  AUTO_MODE_WARNING, modeInfo, modeChangedNote, modeFailureNote,
  desktopEffort, effortLabel, effortOutcomeNote, effortSetNote, effortFailureNote, EFFORT_UNKNOWN_NOTE, configFailureNote,
  modelGroups, parseModelValue, modelChangedNote, modelFailureNote, modelListFailureNote, ULTRACODE_OPTION,
} from './net.js';

const $ = (id) => document.getElementById(id);
const SCREENS = ['loading', 'install', 'welcome', 'pairing', 'main', 'chat'];
const CARD_PUSH_GRACE_MS = 1500;
const textRenderers = new Map();
function setText(node, render) {
  textRenderers.set(node, render);
  node.textContent = render();
}

function applyDesktopUI(ui) {
  if (!ui) return;
  if (['arena', 'sade', 'pafta', 'atolye'].includes(ui.theme)) document.documentElement.dataset.theme = ui.theme;
  if (!['en', 'tr'].includes(ui.lang) || ui.lang === getLang()) return;
  setLang(ui.lang);
  applyStatic();
  for (const [node, render] of textRenderers) {
    if (node.isConnected) node.textContent = render();
    else textRenderers.delete(node);
  }
  renderChats();
  renderChatHeader();
  renderCards();
  renderChatSettings();
  if (!$('slash-panel').hidden) renderSlash();
  if (view.shown) loadChat();
}
const REMOVED_TEXT = () => t('pair.removed');

let device = null;
let link = null;
let chats = [];
let cards = [];
const view = new ChatView();
let wantedChatId = null;
let liveText = null;
let pendingFragment = null;

function show(name) {
  for (const s of SCREENS) $('screen-' + s).hidden = s !== name;
  window.scrollTo(0, 0);
  if (name === 'main') refreshConfig();
}

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === 'class') node.className = v;
    else if (k === 'onclick') node.addEventListener('click', v);
    else node[k] = v;
  }
  for (const c of children) if (c !== null && c !== undefined) node.append(c);
  return node;
}

const isIos = () => /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.userAgent.includes('Macintosh') && navigator.maxTouchPoints > 1);
const isStandalone = () => window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;

function deviceName() {
  const ua = navigator.userAgent;
  if (/iPhone/.test(ua)) return 'iPhone';
  if (/iPad/.test(ua) || (ua.includes('Macintosh') && navigator.maxTouchPoints > 1)) return 'iPad';
  if (/Android/.test(ua)) return 'Android telefon';
  return 'Tarayıcı';
}

function clock(ms) {
  if (!ms) return '';
  const d = new Date(ms);
  const sameDay = d.toDateString() === new Date().toDateString();
  const time = d.toLocaleTimeString((getLang() === 'tr' ? 'tr-TR' : 'en-GB'), { hour: '2-digit', minute: '2-digit' });
  return sameDay ? time : d.toLocaleDateString((getLang() === 'tr' ? 'tr-TR' : 'en-GB'), { day: 'numeric', month: 'short' }) + ' ' + time;
}

const STATUS_WORDS = () => ({ running: t('status.running'), idle: t('status.idle'), awaiting_card: t('status.awaitingApproval') });

// ---------------------------------------------------------------- pairing

const PAIR_ERRORS = {
  get no_room() { return t('pair.noWaitingPc'); },
  get pc_offline() { return t('pair.pcOffline'); },
  get refused() { return t('pair.connectionRefused'); },
  get rejected() { return t('pair.rejected'); },
  get timeout() { return t('pair.timeout'); },
  get bad_reply() { return t('pair.unverifiedReply'); },
  get expired() { return t('pair.expired'); },
  get rate_limited() { return t('pair.rateLimited'); },
};

async function startPairing(parsed) {
  history.replaceState(null, '', '/p');
  show('pairing');
  setText($('pair-status'), () => t('pair.connecting'));
  setText($('pair-error'), () => '');
  $('pair-code-box').hidden = true;
  $('btn-pair-back').hidden = true;
  try {
    const result = await pair({
      origin: wsOrigin(location),
      parsed,
      deviceName: deviceName(),
      onSas: (code) => {
        setText($('pair-code'), () => code);
        $('pair-code-box').hidden = false;
        setText($('pair-status'), () => t('pair.awaitingApproval'));
      },
    });
    if (link) link.stop();
    device = {
      pairId: parsed.pairId,
      pcPub: C.b64u(parsed.pcPub),
      deviceId: result.deviceId,
      token: result.token,
      vapidPub: result.vapidPub,
      privateKey: result.privateKey,
      publicRaw: C.b64u(result.publicRaw),
      pairedAt: Date.now(),
      pushDone: false,
    };
    await store.put('device', device);
    startMain();
  } catch (err) {
    $('pair-code-box').hidden = true;
    setText($('pair-status'), () => t('pair.failed'));
    setText($('pair-error'), () => err.message === 'no_room' ? PAIR_ERRORS[err.message]
      : t('pair.retryError', { error: PAIR_ERRORS[err.message] || t('pair.unknownError', { error: err.message }) }));
    $('btn-pair-back').hidden = false;
  }
}

function showWelcome(text) {
  show('welcome');
  setText($('welcome-error'), () => '');
  if (text) setText($('welcome-text'), typeof text === 'function' ? text : () => text);
  $('btn-show-install').hidden = !(isIos() && !isStandalone());
}

function pasteToFragment(value) {
  const v = value.trim();
  const hashAt = v.indexOf('#');
  if (hashAt < 0) return { error: t('link.missingFragment') };
  if (hashAt > 0) {
    let url;
    try { url = new URL(v); } catch { return { error: t('link.invalid') }; }
    if (url.origin !== location.origin) return { error: t('pair.otherOrigin', { origin: url.origin }) };
  }
  const parsed = C.parsePairFragment(v.slice(hashAt));
  return parsed ? { parsed } : { error: t('link.damaged') };
}

// ---------------------------------------------------------------- main screen

function setStatus(status, info = {}) {
  const statusText = $('status-text');
  // Read only by style.css: the lamp in the status bands follows the link.
  document.body.dataset.link = status;
  const note = $('main-note');
  setText(note, () => '');
  if (status === 'ready') {
    setText(statusText, () => t('status.connected'));
    refreshAll();
    return;
  }
  $('mode-select').disabled = true;
  renderChatSettings();
  if (status === 'connecting') setText(statusText, () => t('status.connecting'));
  else if (status === 'pc_offline') setText(statusText, () => t('status.offline', { lastSeen: info.lastSeen ? t('status.lastSeen', { time: clock(info.lastSeen) }) : '' }));
  else if (status === 'removed' || (status === 'hello_rejected' && info.reason === 'unknown_device')) {
    forgetRemoved();
    return;
  } else if (status === 'hello_rejected') {
    setText(statusText, () => t('status.phoneRejected'));
    setText(note, () => info.reason === 'clock'
      ? t('status.clockIncorrect')
      : t('pair.unregistered'));
  }
  renderChatHeader();
}

async function call(type, params) {
  if (!link || !link.ready) throw new Error('not_ready');
  const reply = await link.request(type, params);
  if (!reply.ok) {
    const err = new Error(reply.error || 'failed');
    err.reply = reply;
    throw err;
  }
  return reply.result || {};
}

// Applied when its own reply arrives: the PC sends replies and card pushes in
// one ordered stream, so a push that lands after this reply is newer. Held
// back until list_chats also answered, it would overwrite such a push.
function refreshCards() {
  return call('pending_cards').then((p) => {
    cards = Array.isArray(p.cards) ? p.cards : [];
    renderCards();
  });
}

function refreshCardsIfMissing(events) {
  if (cardsMissing(cards, events)) refreshCards().catch(() => {});
}

async function refreshAll() {
  refreshConfig();
  try {
    const [c] = await Promise.all([call('list_chats'), refreshCards()]);
    chats = Array.isArray(c.chats) ? c.chats : [];
    renderChats();
    renderCards();
    renderChatHeader();
    if (wantedChatId) {
      const id = wantedChatId;
      wantedChatId = null;
      openChat(id);
    } else if (view.shown) {
      loadChat();
      refreshChatSettings();
    }
  } catch (err) {
    setText($('main-note'), () => t('chats.loadFailed', { error: err.message }));
  }
}

function renderChats() {
  const list = $('chats');
  list.replaceChildren();
  const sorted = [...chats].sort((a, b) => (b.last_activity || 0) - (a.last_activity || 0));
  for (const chat of sorted) {
    const status = STATUS_WORDS()[chat.status] || chat.status || '';
    const who = [chat.provider, chat.model].filter(Boolean).join(' · ');
    const meta = [status, who, clock(chat.last_activity)].filter(Boolean).join(' · ');
    list.append(el('li', {}, el('button', { type: 'button', onclick: () => openChat(chat.chat_id) },
      chat.title || t('chat.untitled'),
      el('span', { class: chat.status === 'awaiting_card' ? 'meta wait' : 'meta', textContent: meta }))));
  }
  $('chats-empty').hidden = chats.length > 0;
}

function chatTitle(id) {
  return chats.find((c) => c.chat_id === id)?.title || t('chat.title');
}

function cardNode(card) {
  const box = el('div', { class: 'card' });
  const { buttons, note: hint } = cardActions(card);
  const note = el('p', { class: 'note', textContent: hint || '' });
  const title = card.title || t('card.awaitingApproval');
  // The head strip of the approval card (Arena's quest window, Pafta's title block).
  box.append(el('div', { class: 'card-head' }, el('span', { class: 'card-mark' }), el('span', { class: 'card-state', textContent: t('card.awaitingApproval') })));
  box.append(el('b', { textContent: title }));
  if (card.chat_id && view.shown !== card.chat_id) box.append(el('span', { class: 'meta', textContent: chatTitle(card.chat_id) }));
  if (card.detail) box.append(el('pre', { class: 'detail', textContent: String(card.detail) }));
  const row = el('div', { class: 'row' });
  const answer = async (payload) => {
    for (const b of row.querySelectorAll('button')) b.disabled = true;
    setText(note, () => t('send.sending'));
    try {
      await call('answer_card', payload);
      removeCard(card.card_id);
    } catch (err) {
      const f = answerFailure(err.message, err.reply);
      setText(note, () => answerFailure(err.message, err.reply).note);
      if (f.close) {
        setTimeout(() => removeCard(card.card_id), 3000);
        return;
      }
      for (const b of row.querySelectorAll('button')) {
        if (f.onlyReject && b.dataset.decision !== 'reject') b.remove();
        else b.disabled = false;
      }
    }
  };
  for (const btn of buttons) {
    const node = el('button', { type: 'button', textContent: btn.label, onclick: () => answer(btn.payload) });
    if (btn.secondary) node.className = 'secondary';
    node.dataset.decision = btn.payload.decision;
    row.append(node);
  }
  box.append(row, note);
  box.dataset.cardId = card.card_id;
  return box;
}

function renderCards() {
  const all = $('cards');
  all.replaceChildren(...cards.map(cardNode));
  $('cards-box').hidden = cards.length === 0;
  if (view.shown) $('chat-cards').replaceChildren(...cards.filter((c) => c.chat_id === view.shown).map(cardNode));
}

function revealCard(cardId) {
  const node = [...$('chat-cards').children].find((n) => n.dataset.cardId === cardId);
  node?.scrollIntoView({ block: 'nearest' });
}

function removeCard(cardId) {
  cards = cards.filter((c) => c.card_id !== cardId);
  renderCards();
}

// ---------------------------------------------------------------- approval mode

let currentMode = null;

function showMode(mode) {
  currentMode = mode;
  $('mode-select').value = mode;
  setText($('mode-note'), () => modeInfo(mode)?.desc || '');
}

// Read whenever the main screen shows, and again when the link comes back.
async function refreshConfig() {
  if (!link?.ready) return;
  const select = $('mode-select');
  try {
    const cfg = await call('get_config');
    applyDesktopUI(cfg.desktop_ui);
    if (modeInfo(cfg.approval_mode)) {
      showMode(cfg.approval_mode);
      select.disabled = false;
    } else {
      select.disabled = true;
      setText($('mode-note'), () => t('mode.unknownOnPc', { mode: cfg.approval_mode }));
    }
  } catch (err) {
    setText($('mode-note'), () => t('mode.readFailed', { error: err.message }));
  }
}

async function changeMode() {
  const select = $('mode-select');
  const mode = select.value;
  if (mode === currentMode) return;
  if (mode === 'auto' && !confirm(AUTO_MODE_WARNING())) {
    select.value = currentMode;
    return;
  }
  const note = $('mode-note');
  select.disabled = true;
  setText(note, () => t('common.changing'));
  try {
    const r = await call('set_approval_mode', { mode });
    if (modeInfo(r.mode)) {
      showMode(r.mode);
      setText(note, () => modeChangedNote(r) + ' ' + modeInfo(r.mode).desc);
    } else {
      await refreshConfig();
    }
  } catch (err) {
    select.value = currentMode;
    setText(note, () => modeFailureNote(err.message, err.reply, mode));
  } finally {
    select.disabled = !link?.ready || !currentMode;
  }
}

// ---------------------------------------------------------------- model and effort

// The page keeps no settings of its own. The model is the open chat's on the
// PC, the effort is the PC's one level; both are read from the PC and changed
// through set_model / set_effort, so the desktop's own controls move with them.
const CATALOG_TTL_MS = 5 * 60 * 1000;
const RECONCILE_MS = 1500;
// One model pick reaches the page as two frames (chat_model_changed, chat_changed)
// and, on the phone that made it, the reply as well: they share one read.
const SETTINGS_COALESCE_MS = 100;
let catalog = null; // { at, data } | { at, error }
let chatConfig = null; // { chatId, provider_type, model_name } of the open chat
let pcEffort = null; // { level, levels }, or null when the PC does not know
let requestedEffort = null; // asked for, not yet confirmed by the PC
let settingsBusy = false;
let settingsSeq = 0;
let reconcileTimer = null;

function applySettingsEnabled() {
  const ready = !!link?.ready && !settingsBusy;
  $('model-select').disabled = !ready || !chatConfig || chatConfig.chatId !== view.shown;
  $('effort-select').disabled = !ready || !pcEffort;
}

function renderChatSettings() {
  const model = $('model-select');
  const effort = $('effort-select');
  model.replaceChildren();
  if (chatConfig && chatConfig.chatId === view.shown) {
    const { groups, currentValue } = modelGroups(catalog?.data, chatConfig);
    for (const g of groups) {
      const group = el('optgroup', { label: g.label });
      for (const item of g.items) {
        group.append(el('option', { value: item.value, textContent: item.label, disabled: item.disabled === true }));
      }
      model.append(group);
    }
    model.value = currentValue;
  }
  effort.replaceChildren();
  const note = $('effort-note');
  if (pcEffort) {
    if (pcEffort.ultracode) effort.append(el('option', { value: ULTRACODE_OPTION, textContent: t('effort.ultracode') }));
    for (const level of pcEffort.levels) effort.append(el('option', { value: level, textContent: effortLabel(level) }));
    effort.value = pcEffort.ultracode ? ULTRACODE_OPTION : pcEffort.level;
    if (note.textContent === EFFORT_UNKNOWN_NOTE()) setText(note, () => '');
  } else {
    effort.append(el('option', { value: '', textContent: t('common.unknownLabel') }));
    setText(note, () => EFFORT_UNKNOWN_NOTE());
  }
  $('effort-ultracode').hidden = !pcEffort?.ultracode;
  applySettingsEnabled();
}

// `source` says whether this is what the PC itself told the page ('told') or
// what the page read after its own request ('reconcile'); only a reconcile
// that still disagrees means the desktop did not apply the level.
function takePcEffort(next, source) {
  pcEffort = next;
  if (requestedEffort !== null && next && ((next.level === requestedEffort && !next.ultracode) || source === 'reconcile')) {
    const requested = requestedEffort;
    setText($('effort-note'), () => effortOutcomeNote(requested, next.level, next.ultracode));
    requestedEffort = null;
  }
  renderChatSettings();
}

// Read when a chat opens, when the link comes back, and when the PC says
// something changed. A slower read for a chat just left never lands.
async function refreshChatSettings(source = 'told') {
  const chatId = view.shown;
  if (!chatId || !link?.ready) return;
  const seq = ++settingsSeq;
  const wantCatalog = !catalog || catalog.error || Date.now() - catalog.at > CATALOG_TTL_MS;
  const [cfg, list] = await Promise.allSettled([
    call('get_config', { chat_id: chatId }),
    wantCatalog ? call('list_models') : Promise.resolve(null),
  ]);
  if (seq !== settingsSeq || view.shown !== chatId) return;
  if (cfg.status === 'fulfilled') applyDesktopUI(cfg.value.desktop_ui);
  if (list.status === 'fulfilled') {
    if (list.value) catalog = { at: Date.now(), data: list.value };
  } else {
    catalog = { at: Date.now(), error: list.reason?.message };
    setText($('model-note'), () => modelListFailureNote(list.reason?.message));
  }
  if (cfg.status === 'fulfilled' && typeof cfg.value.provider_type === 'string' && typeof cfg.value.model_name === 'string') {
    chatConfig = { chatId, provider_type: cfg.value.provider_type, model_name: cfg.value.model_name };
    takePcEffort(desktopEffort(cfg.value.desktop_effort), source);
  } else {
    chatConfig = null;
    setText($('model-note'), () => configFailureNote(cfg.status === 'rejected' ? cfg.reason?.message : 'bad_reply'));
    renderChatSettings();
  }
}

let settingsTimer = null;
function scheduleSettingsRefresh() {
  if (settingsTimer) return;
  settingsTimer = setTimeout(() => {
    settingsTimer = null;
    refreshChatSettings();
  }, SETTINGS_COALESCE_MS);
}

async function changeModel() {
  const select = $('model-select');
  const chatId = view.shown;
  const wanted = parseModelValue(select.value);
  if (!wanted || !chatId || chatConfig?.chatId !== chatId) return;
  if (wanted.provider_type === chatConfig.provider_type && wanted.model_name === chatConfig.model_name) return;
  const note = $('model-note');
  settingsBusy = true;
  applySettingsEnabled();
  setText(note, () => t('common.changing'));
  let changed = false;
  try {
    const r = await call('set_model', { chat_id: chatId, ...wanted });
    if (view.shown === chatId) {
      chatConfig = { chatId, provider_type: r.provider_type, model_name: r.model_name };
      setText(note, () => modelChangedNote(r));
    }
    changed = true;
  } catch (err) {
    if (view.shown === chatId) setText(note, () => modelFailureNote(err.message, err.reply));
  } finally {
    settingsBusy = false;
    renderChatSettings();
  }
  // The desktop follows the change: its effort levels belong to the new model.
  if (changed) scheduleSettingsRefresh();
}

async function changeEffort() {
  const select = $('effort-select');
  const level = select.value;
  if (!pcEffort || level === ULTRACODE_OPTION || (level === pcEffort.level && !pcEffort.ultracode)) return;
  const note = $('effort-note');
  settingsBusy = true;
  applySettingsEnabled();
  setText(note, () => t('common.changing'));
  try {
    const r = await call('set_effort', { level });
    setText(note, () => effortSetNote(r.status, level));
    if (r.status === 'accepted') {
      // Shown as asked until the PC says what it really has.
      requestedEffort = level;
      pcEffort = { ...pcEffort, level, ultracode: false };
      clearTimeout(reconcileTimer);
      reconcileTimer = setTimeout(() => refreshChatSettings('reconcile'), RECONCILE_MS);
    }
  } catch (err) {
    setText(note, () => effortFailureNote(err.message));
  } finally {
    settingsBusy = false;
    renderChatSettings();
  }
}

function clearChatSettings() {
  settingsSeq++;
  chatConfig = null;
  requestedEffort = null;
  clearTimeout(reconcileTimer);
  clearTimeout(settingsTimer);
  settingsTimer = null;
  setText($('model-note'), () => '');
  setText($('effort-note'), () => '');
  renderChatSettings();
}

// ---------------------------------------------------------------- chat view

function renderChatHeader() {
  if (!view.shown) return;
  const chat = chats.find((c) => c.chat_id === view.shown);
  setText($('chat-title'), () => chat?.title || t('chat.title'));
  const status = chat ? STATUS_WORDS()[chat.status] || chat.status || '' : '';
  setText($('chat-status'), () => link?.ready ? status : $('status-text').textContent);
  // A turn waiting on a card is still running and can be stopped.
  $('btn-stop').hidden = !(chat && (chat.status === 'running' || chat.status === 'awaiting_card'));
  $('btn-send').disabled = !link?.ready;
  $('btn-slash').disabled = !link?.ready;
}

// ---------------------------------------------------------------- slash commands

const SLASH_TTL_MS = 5 * 60 * 1000;
const slashCache = new Map(); // chat id -> { at, items }
const slashPending = new Map();
let slashShown = [];
let slashActive = 0;

function closeSlash() {
  $('slash-panel').hidden = true;
  $('btn-slash').setAttribute('aria-expanded', 'false');
  $('composer-text').setAttribute('aria-expanded', 'false');
  $('composer-text').removeAttribute('aria-activedescendant');
  slashShown = [];
}

function renderSlash() {
  const list = $('slash-list');
  list.replaceChildren();
  slashShown = [];
  $('composer-text').removeAttribute('aria-activedescendant');
  if (slashPending.has(view.shown)) {
    setText($('slash-note'), () => t('slash.loading'));
    return;
  }
  const cached = slashCache.get(view.shown);
  if (!cached) return;
  const { shown, total } = filterSlash(cached.items, $('composer-text').value);
  slashShown = shown;
  slashActive = Math.min(slashActive, Math.max(0, shown.length - 1));
  for (const [index, item] of shown.entries()) {
    const button = el('button', { id: 'slash-option-' + index, type: 'button', tabIndex: -1, onclick: () => pickSlash(item) },
      '/' + item.name + (item.hint ? ' ' + item.hint : ''),
      item.description ? el('span', { class: 'meta', textContent: item.description }) : null);
    button.setAttribute('role', 'option');
    button.setAttribute('aria-selected', String(index === slashActive));
    list.append(el('li', { role: 'presentation' }, button));
  }
  if (shown.length) $('composer-text').setAttribute('aria-activedescendant', 'slash-option-' + slashActive);
  setText($('slash-note'), () => !total ? t('slash.noMatches')
    : total > shown.length ? t('slash.results', { total, shown: shown.length }) : '');
}

function pickSlash(item) {
  const box = $('composer-text');
  box.value = withCommand('', '/' + item.name + ' ');
  closeSlash();
  box.focus();
  box.setSelectionRange(box.value.length, box.value.length);
}

async function updateSlash() {
  const value = $('composer-text').value;
  const chatId = view.shown;
  if (!chatId || !value.startsWith('/') || /\s/.test(value)) {
    closeSlash();
    return;
  }
  $('slash-panel').hidden = false;
  $('btn-slash').setAttribute('aria-expanded', 'true');
  $('composer-text').setAttribute('aria-expanded', 'true');
  slashActive = 0;
  const cached = slashCache.get(chatId);
  if (cached && Date.now() - cached.at < SLASH_TTL_MS) {
    renderSlash();
    return;
  }
  if (!slashPending.has(chatId)) {
    const pending = call('list_slash_commands', { chat_id: chatId })
      .then((catalog) => slashCache.set(chatId, { at: Date.now(), items: slashItems(catalog) }))
      .finally(() => slashPending.delete(chatId));
    slashPending.set(chatId, pending);
  }
  renderSlash();
  try {
    await slashPending.get(chatId);
  } catch (err) {
    if (view.shown === chatId && !$('slash-panel').hidden) setText($('slash-note'), () => slashFailureNote(err.message));
    return;
  }
  if (view.shown === chatId && !$('slash-panel').hidden) renderSlash();
}

function startSlash() {
  const box = $('composer-text');
  if (!box.value) box.value = '/';
  box.focus();
  return updateSlash();
}

function slashKeydown(event) {
  if ($('slash-panel').hidden || event.isComposing) return;
  if (event.key === 'Escape') {
    event.preventDefault();
    closeSlash();
  } else if (slashShown.length && (event.key === 'ArrowUp' || event.key === 'ArrowDown')) {
    event.preventDefault();
    slashActive = (slashActive + (event.key === 'ArrowDown' ? 1 : -1) + slashShown.length) % slashShown.length;
    renderSlash();
    $('slash-list').children[slashActive].firstChild.scrollIntoView({ block: 'nearest' });
  } else if (slashShown.length && (event.key === 'Enter' || event.key === 'Tab')) {
    event.preventDefault();
    pickSlash(slashShown[slashActive]);
  }
}

function logMessage(m) {
  const who = m.role === 'user' ? (m.source === 'phone' ? t('chat.youOnPhone') : t('chat.you')) : m.role === 'assistant' ? t('chat.assistant') : m.role || '';
  $('log').append(el('div', { class: msgClass(m.role) }, el('span', { class: 'who', textContent: who }), messageText(m)));
}

function logEvent(ev) {
  const log = $('log');
  if (ev.kind === 'text') {
    if (!liveText) {
      liveText = el('div', { class: msgClass('assistant') }, el('span', { class: 'who', textContent: t('chat.assistant') }));
      log.append(liveText);
    }
    liveText.append(String(ev.text ?? ''));
    return;
  }
  liveText = null;
  const line = eventLine(ev);
  const node = el('div', { class: eventClass(ev, line), textContent: line.text });
  if (ev.kind === 'card_closed') node.dataset.state = DECIDED.get(ev.decision) || 'closed';
  log.append(node);
}

// Class names for style.css only. The PC's strings never become class names
// directly: only the kinds and decisions listed here are mapped.
function msgClass(role) {
  return role === 'user' ? 'msg msg-user' : role === 'assistant' ? 'msg msg-ai' : 'msg';
}
const EVENT_KINDS = new Set(['tool_call', 'turn_start', 'turn_end', 'card_opened', 'card_closed']);
const DECIDED = new Map([['approve', 'approved'], ['choice', 'approved'], ['reject', 'rejected']]);
function eventClass(ev, line) {
  const kind = EVENT_KINDS.has(ev.kind) ? ' ev-' + ev.kind.replace('_', '-') : '';
  return 'ev' + kind + (line.error ? ' error' : '');
}

function closeOnPc(id) {
  call('close_chat', { chat_id: id }).catch(() => {});
}

async function loadChat() {
  const token = view.beginLoad();
  if (!token) return;
  let r;
  try {
    r = await call('open_chat', { chat_id: token.chatId });
  } catch (err) {
    if (view.endLoad(token) !== 'apply') return;
    if (err.message === 'unknown_chat') {
      closeChat();
      setText($('main-note'), () => t('chat.removed'));
    } else {
      setText($('chat-status'), () => t('chat.openFailed', { error: err.message }));
    }
    return;
  }
  const verdict = view.endLoad(token);
  if (verdict === 'orphan') closeOnPc(token.chatId);
  if (verdict !== 'apply') return;
  $('log').replaceChildren();
  liveText = null;
  for (const m of Array.isArray(r.messages) ? r.messages : []) logMessage(m);
  const events = Array.isArray(r.events) ? r.events : [];
  for (const ev of events) logEvent(ev);
  window.scrollTo(0, document.body.scrollHeight);
  refreshCardsIfMissing(events);
  if (view.takeReload()) loadChat();
  else reloadLater();
}

function reloadLater() {
  const s = view.takeSchedule();
  if (s) setTimeout(() => { if (view.runScheduled(s)) loadChat(); }, s.delayMs);
}

function openChat(id) {
  if (!link?.ready) {
    wantedChatId = id;
    return;
  }
  const prev = view.show(id);
  if (prev) closeOnPc(prev);
  $('log').replaceChildren();
  liveText = null;
  setText($('composer-note'), () => '');
  closeSlash();
  clearChatSettings();
  show('chat');
  renderChatHeader();
  renderCards();
  loadChat();
  refreshChatSettings();
}

function closeChat() {
  const id = view.hide();
  if (id) closeOnPc(id);
  closeSlash();
  clearChatSettings();
  show('main');
}

function onPush(msg) {
  if (msg.type === 'ui_changed') {
    applyDesktopUI(msg.desktop_ui);
  } else if (msg.type === 'event') {
    const chat = chats.find((c) => c.chat_id === msg.chat_id);
    if (chat && msg.kind === 'turn_start') chat.status = 'running';
    if (chat && msg.kind === 'turn_end') chat.status = 'idle';
    if (msg.chat_id === view.shown) logEvent(msg);
    else if (view.stray(msg.chat_id)) closeOnPc(msg.chat_id);
    // The card's own push is sent right behind this event; only a card still
    // missing after that is fetched.
    if (msg.kind === 'card_opened') setTimeout(() => refreshCardsIfMissing([msg]), CARD_PUSH_GRACE_MS);
    renderChats();
    renderChatHeader();
  } else if (msg.type === 'chat_changed') {
    if (msg.chat?.chat_id) {
      chats = mergeChat(chats, msg.chat);
      renderChats();
      renderChatHeader();
      // The model may have been changed on the desktop while this chat is open.
      if (msg.chat.chat_id === view.shown && chatConfig && msg.chat.model !== chatConfig.model_name) scheduleSettingsRefresh();
    } else {
      refreshAll();
    }
  } else if (msg.type === 'card_opened' && msg.card?.card_id) {
    cards = cards.filter((c) => c.card_id !== msg.card.card_id).concat(msg.card);
    renderCards();
    if (msg.card.chat_id === view.shown) revealCard(msg.card.card_id);
  } else if (msg.type === 'card_closed') {
    removeCard(msg.card_id);
  } else if (msg.type === 'effort_changed') {
    takePcEffort(desktopEffort(msg.desktop_effort), 'told');
  } else if (msg.type === 'chat_model_changed') {
    // A pick in any chat also moves the default a chat with no model of its own
    // follows, so the open chat re-reads whichever chat was picked in.
    scheduleSettingsRefresh();
  } else if (msg.type === 'default_model_changed') {
    // Only the default a new chat opens on changed (a pick on the desktop with
    // no chat open); any chat with no model of its own shows another model now.
    refreshAll();
  } else if (msg.type === 'gap') {
    const what = view.onGap(msg.chat_id);
    if (what === 'reload') loadChat();
    else if (what === 'later') reloadLater();
    else if (what === 'close') closeOnPc(msg.chat_id);
  }
}

// ---------------------------------------------------------------- notifications

function renderNotifyButton() {
  const supported = 'Notification' in window && 'serviceWorker' in navigator;
  $('btn-notify').hidden = !!device?.pushDone && supported && Notification.permission === 'granted';
}

async function enableNotifications() {
  const note = $('notify-note');
  if (!('Notification' in window) || !('PushManager' in window) || !('serviceWorker' in navigator)) {
    setText(note, () => isIos() ? t('notify.homeScreenRequired') : t('notify.unsupported'));
    return;
  }
  // iOS only grants this from a direct tap, so it must be the first await.
  const permission = await Notification.requestPermission();
  if (permission !== 'granted') {
    setText(note, () => t('notify.permissionDenied'));
    return;
  }
  if (!device.vapidPub) {
    setText(note, () => t('notify.missingKey'));
    return;
  }
  try {
    setText(note, () => t('notify.settingUp'));
    const reg = await navigator.serviceWorker.ready;
    let sub = await reg.pushManager.getSubscription();
    if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: C.fromB64u(device.vapidPub) });
    await call('push_subscribe', { subscription: sub.toJSON() });
    device.pushDone = true;
    await store.put('device', device);
    setText(note, () => t('notify.enabled'));
    renderNotifyButton();
  } catch (err) {
    setText(note, () => err.message === 'not_ready' ? t('common.connectFirst') : t('notify.setupFailed', { error: err.message }));
  }
}

// ---------------------------------------------------------------- boot

function startMain() {
  show('main');
  renderNotifyButton();
  const d = device;
  link = new Link({
    origin: wsOrigin(location),
    device: { pairId: d.pairId, pcPub: C.fromB64u(d.pcPub), deviceId: d.deviceId, token: d.token, privateKey: d.privateKey },
    onStatus: setStatus,
    onPush,
  });
  link.start();
}

function handleHash(hash) {
  if (hash.startsWith('#chat=')) {
    const id = decodeURIComponent(hash.slice(6));
    history.replaceState(null, '', '/p');
    if (device) openChat(id);
    return true;
  }
  return false;
}

async function forgetDevice() {
  if (link) link.stop();
  link = null;
  device = null;
  try {
    const reg = await navigator.serviceWorker?.getRegistration();
    const sub = await reg?.pushManager?.getSubscription();
    await sub?.unsubscribe();
  } catch {}
  await store.del('device');
}

async function unpair() {
  if (!confirm(t('unpair.confirm'))) return;
  await forgetDevice();
  location.replace('/p');
}

// The PC removed this phone (or reset remote control): its token can never
// connect again, so keeping it would only leave the page retrying.
async function forgetRemoved() {
  view.hide();
  await forgetDevice();
  showWelcome(REMOVED_TEXT);
}

function wire() {
  $('btn-copy-link').addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(location.origin + '/p#' + pendingFragment);
      setText($('copy-note'), () => t('copy.pairingLinkCopied'));
    } catch {
      setText($('copy-note'), () => t('copy.failed'));
    }
  });
  $('btn-pair-here').addEventListener('click', () => startPairing(C.parsePairFragment(pendingFragment)));
  $('btn-paste-pair').addEventListener('click', () => {
    const r = pasteToFragment($('paste-link').value);
    if (r.error) setText($('welcome-error'), () => r.error);
    else startPairing(r.parsed);
  });
  $('btn-show-install').addEventListener('click', () => {
    $('install-pair').hidden = true;
    show('install');
  });
  $('btn-pair-back').addEventListener('click', () => (device ? startMain() : showWelcome()));
  $('btn-back').addEventListener('click', closeChat);
  $('btn-unpair').addEventListener('click', unpair);
  $('btn-notify').addEventListener('click', enableNotifications);
  $('mode-select').addEventListener('change', changeMode);
  $('model-select').addEventListener('change', changeModel);
  $('effort-select').addEventListener('change', changeEffort);
  $('btn-slash').addEventListener('click', startSlash);
  $('composer-text').addEventListener('input', updateSlash);
  $('composer-text').addEventListener('keydown', slashKeydown);

  let stopArmed = null;
  $('btn-stop').addEventListener('click', async () => {
    const btn = $('btn-stop');
    if (!stopArmed) {
      setText(btn, () => t('stop.tapAgain'));
      stopArmed = setTimeout(() => { stopArmed = null; setText(btn, () => t('stop.label')); }, 4000);
      return;
    }
    clearTimeout(stopArmed);
    stopArmed = null;
    setText(btn, () => t('stop.label'));
    try {
      const r = await call('stop', { chat_id: view.shown });
      setText($('chat-status'), () => stopLine(r.status));
    } catch (err) {
      setText($('chat-status'), () => t('stop.requestFailed', { error: err.message }));
    }
  });

  $('composer').addEventListener('submit', async (e) => {
    e.preventDefault();
    const text = $('composer-text').value.trim();
    if (!text || !view.shown) return;
    const note = $('composer-note');
    if (text.length > SEND_TEXT_MAX) {
      setText(note, () => t('send.tooLong', { max: SEND_TEXT_MAX }));
      return;
    }
    $('btn-send').disabled = true;
    setText(note, () => t('send.sending'));
    try {
      const r = await call('send_message', { chat_id: view.shown, text });
      if (r.status === 'desktop_not_ready') {
        setText(note, () => t('send.desktopNotReady'));
      } else {
        $('composer-text').value = '';
        closeSlash();
        setText(note, () => sentNote(text, r.status));
      }
    } catch (err) {
      setText(note, () => sendFailureNote(err.message));
    } finally {
      $('btn-send').disabled = !link?.ready;
    }
  });

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && link) {
      link.wake();
      refreshChatSettings();
    }
  });
  window.addEventListener('online', () => link?.wake());
  window.addEventListener('hashchange', () => handleHash(location.hash));
  navigator.serviceWorker?.addEventListener('message', (e) => {
    if (e.data?.type === 'navigate' && typeof e.data.url === 'string') handleHash(new URL(e.data.url, location.origin).hash);
  });
}

async function boot() {
  applyStatic();
  wire();
  navigator.serviceWorker?.register('/sw.js', { scope: '/' }).catch(() => {});
  try {
    device = (await store.get('device')) || null;
  } catch {
    device = null;
  }
  const hash = location.hash;
  if (handleHash(hash) || !hash) {
    if (device) startMain();
    else showWelcome();
    return;
  }
  const parsed = C.parsePairFragment(hash);
  if (!parsed) {
    history.replaceState(null, '', '/p');
    return device ? startMain() : showWelcome();
  }
  pendingFragment = hash.slice(1);
  if (device) {
    showWelcome(() => t('pair.replaceExisting'));
    $('paste-link').value = location.href;
    return;
  }
  if (isIos() && !isStandalone()) {
    // The fragment stays in the address bar on purpose: if iOS keeps it when
    // adding to the home screen, the installed app can pair straight away.
    $('install-pair').hidden = false;
    show('install');
    return;
  }
  startPairing(parsed);
}

boot();
