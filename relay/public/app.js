// Phone UI. Everything shown comes from the PC and is written with
// textContent only: chat text is untrusted input on this page.

import * as C from './crypto.js';
import * as store from './store.js';
import {
  pair, Link, wsOrigin, ChatView, cardActions, answerFailure, eventLine, messageText, mergeChat, stopLine, SEND_TEXT_MAX,
  sendFailureNote, sentNote, cardsMissing, slashItems, filterSlash, withCommand, slashFailureNote,
  AUTO_MODE_WARNING, modeInfo, modeChangedNote, modeFailureNote,
  desktopEffort, effortLabel, effortOutcomeNote, effortSetNote, effortFailureNote, EFFORT_UNKNOWN_NOTE, configFailureNote,
  modelGroups, parseModelValue, modelChangedNote, modelFailureNote, modelListFailureNote,
} from './net.js';

const $ = (id) => document.getElementById(id);
const SCREENS = ['loading', 'install', 'welcome', 'pairing', 'main', 'chat'];
const CARD_PUSH_GRACE_MS = 1500;

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
  const time = d.toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' });
  return sameDay ? time : d.toLocaleDateString('tr-TR', { day: 'numeric', month: 'short' }) + ' ' + time;
}

const STATUS_WORDS = { running: 'çalışıyor', idle: 'boşta', awaiting_card: 'onay bekliyor' };

// ---------------------------------------------------------------- pairing

const PAIR_ERRORS = {
  no_room: 'Bu eşleştirme kodu için bekleyen bir bilgisayar yok. Bilgisayarda uzaktan kontrolü açıp yeni QR oluştur.',
  pc_offline: 'Bilgisayar şu an bağlı değil. Gamachine açık mı, internet var mı?',
  refused: 'Bağlantı kabul edilmedi. Kısa sürede çok fazla deneme yapılmış olabilir; biraz bekle.',
  rejected: 'Bilgisayar eşleştirmeyi reddetti.',
  timeout: 'Bilgisayardan 5 dakika içinde yanıt gelmedi.',
  bad_reply: 'Bilgisayardan gelen yanıt doğrulanamadı. Eşleştirme yapılmadı.',
  expired: 'QR kodunun süresi dolmuş.',
  rate_limited: 'Çok fazla deneme yapıldı. Biraz bekle.',
};

async function startPairing(parsed) {
  history.replaceState(null, '', '/p');
  show('pairing');
  $('pair-status').textContent = 'Bilgisayara bağlanılıyor…';
  $('pair-error').textContent = '';
  $('pair-code-box').hidden = true;
  $('btn-pair-back').hidden = true;
  try {
    const result = await pair({
      origin: wsOrigin(location),
      parsed,
      deviceName: deviceName(),
      onSas: (code) => {
        $('pair-code').textContent = code;
        $('pair-code-box').hidden = false;
        $('pair-status').textContent = 'Bilgisayarın onayı bekleniyor…';
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
    $('pair-status').textContent = 'Eşleştirme olmadı.';
    const advice = err.message === 'no_room' ? '' : ' Bilgisayarda yeni bir QR kodu oluşturup tekrar dene.';
    $('pair-error').textContent = (PAIR_ERRORS[err.message] || 'Hata: ' + err.message) + advice;
    $('btn-pair-back').hidden = false;
  }
}

function showWelcome(text) {
  show('welcome');
  if (text) $('welcome-text').textContent = text;
  $('btn-show-install').hidden = !(isIos() && !isStandalone());
}

function pasteToFragment(value) {
  const v = value.trim();
  const hashAt = v.indexOf('#');
  if (hashAt < 0) return { error: 'Bağlantıda # işaretinden sonraki kısım yok.' };
  if (hashAt > 0) {
    let url;
    try { url = new URL(v); } catch { return { error: 'Bu bir bağlantı gibi görünmüyor.' }; }
    if (url.origin !== location.origin) return { error: 'Bu bağlantı başka bir sunucuya ait (' + url.origin + '). O adresi Safari\'de aç.' };
  }
  const parsed = C.parsePairFragment(v.slice(hashAt));
  return parsed ? { parsed } : { error: 'Bağlantı eksik ya da bozuk.' };
}

// ---------------------------------------------------------------- main screen

function setStatus(status, info = {}) {
  const t = $('status-text');
  const note = $('main-note');
  note.textContent = '';
  if (status === 'ready') {
    t.textContent = 'Bağlı';
    refreshAll();
    return;
  }
  $('mode-select').disabled = true;
  renderChatSettings();
  if (status === 'connecting') t.textContent = 'Bağlanıyor…';
  else if (status === 'pc_offline') t.textContent = 'Bilgisayar çevrimdışı' + (info.lastSeen ? ' (son görülme ' + clock(info.lastSeen) + ')' : '');
  else if (status === 'removed') {
    t.textContent = 'Bu telefon bilgisayardan kaldırıldı';
    note.textContent = 'Yeniden kullanmak için eşleşmeyi silip bilgisayarda yeni bir QR kodu tara.';
  } else if (status === 'hello_rejected') {
    t.textContent = 'Bilgisayar bu telefonu kabul etmedi';
    note.textContent = info.reason === 'clock'
      ? 'Telefonun saati yanlış görünüyor. Ayarlar > Genel > Tarih ve Saat\'ten otomatik saati aç.'
      : 'Telefon bilgisayarda kayıtlı değil. Eşleşmeyi silip yeniden eşleştir.';
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
    $('main-note').textContent = 'Liste alınamadı: ' + err.message;
  }
}

function renderChats() {
  const list = $('chats');
  list.replaceChildren();
  const sorted = [...chats].sort((a, b) => (b.last_activity || 0) - (a.last_activity || 0));
  for (const chat of sorted) {
    const status = STATUS_WORDS[chat.status] || chat.status || '';
    const who = [chat.provider, chat.model].filter(Boolean).join(' · ');
    const meta = [status, who, clock(chat.last_activity)].filter(Boolean).join(' · ');
    list.append(el('li', {}, el('button', { type: 'button', onclick: () => openChat(chat.chat_id) },
      chat.title || 'Adsız sohbet',
      el('span', { class: chat.status === 'awaiting_card' ? 'meta wait' : 'meta', textContent: meta }))));
  }
  $('chats-empty').hidden = chats.length > 0;
}

function chatTitle(id) {
  return chats.find((c) => c.chat_id === id)?.title || 'Sohbet';
}

function cardNode(card) {
  const box = el('div', { class: 'card' });
  const { buttons, note: hint } = cardActions(card);
  const note = el('p', { class: 'note', textContent: hint || '' });
  const title = card.title || 'Onay bekliyor';
  box.append(el('b', { textContent: title }));
  if (card.chat_id && view.shown !== card.chat_id) box.append(el('span', { class: 'meta', textContent: chatTitle(card.chat_id) }));
  if (card.detail) box.append(el('pre', { class: 'detail', textContent: String(card.detail) }));
  const row = el('div', { class: 'row' });
  const answer = async (payload) => {
    for (const b of row.querySelectorAll('button')) b.disabled = true;
    note.textContent = 'Gönderiliyor…';
    try {
      await call('answer_card', payload);
      removeCard(card.card_id);
    } catch (err) {
      const f = answerFailure(err.message, err.reply);
      note.textContent = f.note;
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
  $('mode-note').textContent = modeInfo(mode)?.desc || '';
}

// Read whenever the main screen shows, and again when the link comes back.
async function refreshConfig() {
  if (!link?.ready) return;
  const select = $('mode-select');
  try {
    const cfg = await call('get_config');
    if (modeInfo(cfg.approval_mode)) {
      showMode(cfg.approval_mode);
      select.disabled = false;
    } else {
      select.disabled = true;
      $('mode-note').textContent = 'Bilgisayardaki onay modu tanınmadı: ' + cfg.approval_mode;
    }
  } catch (err) {
    $('mode-note').textContent = 'Onay modu okunamadı: ' + err.message;
  }
}

async function changeMode() {
  const select = $('mode-select');
  const mode = select.value;
  if (mode === currentMode) return;
  if (mode === 'auto' && !confirm(AUTO_MODE_WARNING)) {
    select.value = currentMode;
    return;
  }
  const note = $('mode-note');
  select.disabled = true;
  note.textContent = 'Değiştiriliyor…';
  try {
    const r = await call('set_approval_mode', { mode });
    if (modeInfo(r.mode)) {
      showMode(r.mode);
      note.textContent = modeChangedNote(r) + ' ' + modeInfo(r.mode).desc;
    } else {
      await refreshConfig();
    }
  } catch (err) {
    select.value = currentMode;
    note.textContent = modeFailureNote(err.message, err.reply, mode);
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
    for (const level of pcEffort.levels) effort.append(el('option', { value: level, textContent: effortLabel(level) }));
    effort.value = pcEffort.level;
    if (note.textContent === EFFORT_UNKNOWN_NOTE) note.textContent = '';
  } else {
    effort.append(el('option', { value: '', textContent: 'Bilinmiyor' }));
    note.textContent = EFFORT_UNKNOWN_NOTE;
  }
  applySettingsEnabled();
}

// `source` says whether this is what the PC itself told the page ('told') or
// what the page read after its own request ('reconcile'); only a reconcile
// that still disagrees means the desktop did not apply the level.
function takePcEffort(next, source) {
  pcEffort = next;
  if (requestedEffort !== null && next && (next.level === requestedEffort || source === 'reconcile')) {
    $('effort-note').textContent = effortOutcomeNote(requestedEffort, next.level);
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
  if (list.status === 'fulfilled') {
    if (list.value) catalog = { at: Date.now(), data: list.value };
  } else {
    catalog = { at: Date.now(), error: list.reason?.message };
    $('model-note').textContent = modelListFailureNote(list.reason?.message);
  }
  if (cfg.status === 'fulfilled' && typeof cfg.value.provider_type === 'string' && typeof cfg.value.model_name === 'string') {
    chatConfig = { chatId, provider_type: cfg.value.provider_type, model_name: cfg.value.model_name };
    takePcEffort(desktopEffort(cfg.value.desktop_effort), source);
  } else {
    chatConfig = null;
    $('model-note').textContent = configFailureNote(cfg.status === 'rejected' ? cfg.reason?.message : 'bad_reply');
    renderChatSettings();
  }
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
  note.textContent = 'Değiştiriliyor…';
  let changed = false;
  try {
    const r = await call('set_model', { chat_id: chatId, ...wanted });
    if (view.shown === chatId) {
      chatConfig = { chatId, provider_type: r.provider_type, model_name: r.model_name };
      note.textContent = modelChangedNote(r);
    }
    changed = true;
  } catch (err) {
    if (view.shown === chatId) note.textContent = modelFailureNote(err.message, err.reply);
  } finally {
    settingsBusy = false;
    renderChatSettings();
  }
  // The desktop follows the change: its effort levels belong to the new model.
  if (changed) refreshChatSettings();
}

async function changeEffort() {
  const select = $('effort-select');
  const level = select.value;
  if (!pcEffort || level === pcEffort.level) return;
  const note = $('effort-note');
  settingsBusy = true;
  applySettingsEnabled();
  note.textContent = 'Değiştiriliyor…';
  try {
    const r = await call('set_effort', { level });
    note.textContent = effortSetNote(r.status, level);
    if (r.status === 'accepted') {
      // Shown as asked until the PC says what it really has.
      requestedEffort = level;
      pcEffort = { ...pcEffort, level };
      clearTimeout(reconcileTimer);
      reconcileTimer = setTimeout(() => refreshChatSettings('reconcile'), RECONCILE_MS);
    }
  } catch (err) {
    note.textContent = effortFailureNote(err.message);
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
  $('model-note').textContent = '';
  $('effort-note').textContent = '';
  renderChatSettings();
}

// ---------------------------------------------------------------- chat view

function renderChatHeader() {
  if (!view.shown) return;
  const chat = chats.find((c) => c.chat_id === view.shown);
  $('chat-title').textContent = chat?.title || 'Sohbet';
  const status = chat ? STATUS_WORDS[chat.status] || chat.status || '' : '';
  $('chat-status').textContent = link?.ready ? status : $('status-text').textContent;
  // A turn waiting on a card is still running and can be stopped.
  $('btn-stop').hidden = !(chat && (chat.status === 'running' || chat.status === 'awaiting_card'));
  $('btn-send').disabled = !link?.ready;
  $('btn-slash').disabled = !link?.ready;
}

// ---------------------------------------------------------------- slash commands

const SLASH_TTL_MS = 5 * 60 * 1000;
const slashCache = new Map(); // chat id -> { at, items }

function closeSlash() {
  $('slash-panel').hidden = true;
  $('btn-slash').setAttribute('aria-expanded', 'false');
}

function renderSlash() {
  const list = $('slash-list');
  list.replaceChildren();
  const cached = slashCache.get(view.shown);
  if (!cached) return;
  const { shown, total } = filterSlash(cached.items, $('slash-filter').value);
  for (const item of shown) {
    list.append(el('li', {}, el('button', { type: 'button', onclick: () => pickSlash(item) },
      '/' + item.name + (item.hint ? ' ' + item.hint : ''),
      item.description ? el('span', { class: 'meta', textContent: item.description }) : null)));
  }
  $('slash-note').textContent = !total ? 'Eşleşen komut yok.'
    : total > shown.length ? total + ' sonuçtan ilk ' + shown.length + ' tanesi; aramayı daraltabilirsin.' : '';
}

function pickSlash(item) {
  const box = $('composer-text');
  box.value = withCommand(box.value, item.insert);
  closeSlash();
  box.focus();
  box.setSelectionRange(box.value.length, box.value.length);
}

async function toggleSlash() {
  if (!$('slash-panel').hidden) {
    closeSlash();
    return;
  }
  const chatId = view.shown;
  if (!chatId) return;
  $('slash-panel').hidden = false;
  $('btn-slash').setAttribute('aria-expanded', 'true');
  $('slash-filter').value = '';
  renderSlash();
  const cached = slashCache.get(chatId);
  if (cached && Date.now() - cached.at < SLASH_TTL_MS) return;
  $('slash-note').textContent = 'Komutlar alınıyor…';
  try {
    slashCache.set(chatId, { at: Date.now(), items: slashItems(await call('list_slash_commands', { chat_id: chatId })) });
  } catch (err) {
    if (view.shown === chatId) $('slash-note').textContent = slashFailureNote(err.message);
    return;
  }
  if (view.shown === chatId && !$('slash-panel').hidden) renderSlash();
}

function logMessage(m) {
  const who = m.role === 'user' ? (m.source === 'phone' ? 'Sen (telefon)' : 'Sen') : m.role === 'assistant' ? 'Asistan' : m.role || '';
  $('log').append(el('div', { class: 'msg' }, el('span', { class: 'who', textContent: who }), messageText(m)));
}

function logEvent(ev) {
  const log = $('log');
  if (ev.kind === 'text') {
    if (!liveText) {
      liveText = el('div', { class: 'msg' }, el('span', { class: 'who', textContent: 'Asistan' }));
      log.append(liveText);
    }
    liveText.append(String(ev.text ?? ''));
    return;
  }
  liveText = null;
  const line = eventLine(ev);
  log.append(el('div', { class: line.error ? 'ev error' : 'ev', textContent: line.text }));
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
      $('main-note').textContent = 'Bu sohbet artık yok.';
    } else {
      $('chat-status').textContent = 'Sohbet açılamadı: ' + err.message;
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
  $('composer-note').textContent = '';
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
  if (msg.type === 'event') {
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
      if (msg.chat.chat_id === view.shown && chatConfig && msg.chat.model !== chatConfig.model_name) refreshChatSettings();
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
    if (msg.chat_id === view.shown) refreshChatSettings();
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
    note.textContent = isIos() ? 'Bildirimler için sayfayı ana ekrana ekleyip oradan açman gerekiyor.' : 'Bu tarayıcı bildirim desteklemiyor.';
    return;
  }
  // iOS only grants this from a direct tap, so it must be the first await.
  const permission = await Notification.requestPermission();
  if (permission !== 'granted') {
    note.textContent = 'İzin verilmedi. Ayarlar > Bildirimler > Gamachine\'den açabilirsin.';
    return;
  }
  if (!device.vapidPub) {
    note.textContent = 'Bilgisayar bildirim anahtarını göndermedi; bildirim ayarlanamadı.';
    return;
  }
  try {
    note.textContent = 'Ayarlanıyor…';
    const reg = await navigator.serviceWorker.ready;
    let sub = await reg.pushManager.getSubscription();
    if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: C.fromB64u(device.vapidPub) });
    await call('push_subscribe', { subscription: sub.toJSON() });
    device.pushDone = true;
    await store.put('device', device);
    note.textContent = 'Bildirimler açık.';
    renderNotifyButton();
  } catch (err) {
    note.textContent = err.message === 'not_ready' ? 'Önce bilgisayara bağlanmalı.' : 'Bildirim ayarlanamadı: ' + err.message;
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

async function unpair() {
  if (!confirm('Bu telefondaki eşleşme silinsin mi? Bilgisayardaki cihaz listesinden de kaldırmayı unutma.')) return;
  if (link) link.stop();
  try {
    const reg = await navigator.serviceWorker?.getRegistration();
    const sub = await reg?.pushManager?.getSubscription();
    await sub?.unsubscribe();
  } catch {}
  await store.del('device');
  location.replace('/p');
}

function wire() {
  $('btn-copy-link').addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(location.origin + '/p#' + pendingFragment);
      $('copy-note').textContent = 'Kopyalandı. Şimdi ana ekrandaki simgeden aç ve yapıştır.';
    } catch {
      $('copy-note').textContent = 'Kopyalanamadı.';
    }
  });
  $('btn-pair-here').addEventListener('click', () => startPairing(C.parsePairFragment(pendingFragment)));
  $('btn-paste-pair').addEventListener('click', () => {
    const r = pasteToFragment($('paste-link').value);
    if (r.error) $('welcome-error').textContent = r.error;
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
  $('btn-slash').addEventListener('click', toggleSlash);
  $('slash-filter').addEventListener('input', renderSlash);

  let stopArmed = null;
  $('btn-stop').addEventListener('click', async () => {
    const btn = $('btn-stop');
    if (!stopArmed) {
      btn.textContent = 'Durdurmak için tekrar dokun';
      stopArmed = setTimeout(() => { stopArmed = null; btn.textContent = 'Durdur'; }, 4000);
      return;
    }
    clearTimeout(stopArmed);
    stopArmed = null;
    btn.textContent = 'Durdur';
    try {
      const r = await call('stop', { chat_id: view.shown });
      $('chat-status').textContent = stopLine(r.status);
    } catch (err) {
      $('chat-status').textContent = 'Durdurulamadı: ' + err.message;
    }
  });

  $('composer').addEventListener('submit', async (e) => {
    e.preventDefault();
    const text = $('composer-text').value.trim();
    if (!text || !view.shown) return;
    const note = $('composer-note');
    if (text.length > SEND_TEXT_MAX) {
      note.textContent = 'Mesaj çok uzun (en fazla ' + SEND_TEXT_MAX + ' karakter).';
      return;
    }
    $('btn-send').disabled = true;
    note.textContent = 'Gönderiliyor…';
    try {
      const r = await call('send_message', { chat_id: view.shown, text });
      if (r.status === 'desktop_not_ready') {
        note.textContent = 'Bilgisayardaki uygulama hazır değil; mesaj gönderilmedi.';
      } else {
        $('composer-text').value = '';
        closeSlash();
        note.textContent = sentNote(text, r.status);
      }
    } catch (err) {
      note.textContent = sendFailureNote(err.message);
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
    showWelcome('Bu telefon zaten bir bilgisayarla eşli. Yeni eşleştirme eskisinin yerine geçer.');
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
