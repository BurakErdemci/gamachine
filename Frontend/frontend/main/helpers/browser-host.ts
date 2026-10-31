import type { Cookie, Input, NativeImage, Rectangle, Session, WebContents, WebContentsView, WebContentsViewConstructorOptions } from 'electron'
import type { HttpClient } from './remote-control'
import type { BrowserCommand } from './browser-server'
import { isIP } from 'net'
import { isAllowedNavigation, isPrivateAddress, resolvesToPrivate, type LookupAll } from './browser-url-policy'
import { historyUrl, type BrowserHistory } from './browser-history'

// persist: keeps one login for every chat's tab and across app restarts.
export const BROWSER_PARTITION = 'persist:gamachine-agent-browser'
export const BROWSER_LIVE_CHANNEL = 'browser-live'
export const BROWSER_FULL_TOGGLE_CHANNEL = 'browser-full-toggle'
/** Main to renderer: notes the user saved in annotate mode ({ conversationId, tabId, url, title, notes }). */
export const BROWSER_NOTES_CHANNEL = 'browser-notes'
// Electron reserves 0 (page) and 999 (its own isolated world); any other id up to 2^29 is free.
export const BROWSER_WORLD_ID = 1777
export const NAVIGATION_TIMEOUT_MS = 30_000
export const MAX_TABS = 8
// A chat never holds more tabs than the app does, so the per-chat cap is the global one.
export const MAX_CHAT_TABS = MAX_TABS
const MAX_CLOSED_TABS = 10
/** site-remove repeats while a cookie of the site is still there after a pass; then it gives up as partial. */
const MAX_SITE_REMOVE_PASSES = 3
// How long the key events a `press` is about to send back to the host stay expected.
const SYNTHETIC_INPUT_MS = 1_000
const SCRIPT_TIMEOUT_MS = 10_000
const HOST_CACHE_MS = 60_000
// A page may open a tab only within this long after its own click or key, and only so often.
const GESTURE_MS = 1_000
const PAGE_TAB_WINDOW_MS = 10_000
const MAX_PAGE_TABS = 3
const MAX_FAVICON_BYTES = 32 * 1024
const FAVICON_TIMEOUT_MS = 5_000
const MAX_FAVICON_REDIRECTS = 3
const SCREENSHOT_MAX_WIDTH = 1280
const MAX_TEXT_CHARS = 100_000
const MAX_FILL_CHARS = 10_000
// While annotate mode is on and shown with no comment open, the overlay's boxes are read from the page this often.
const FRAME_REFRESH_MS = 250
// After a wheel the overlay forwards, the page has moved: its boxes are read again this soon.
const WHEEL_REFRESH_MS = 80
const FRAME_MAX_ELEMENTS = 2000
// A frame whose page moved under its picture (scrolled, resized, navigated) is read again, this many times in all.
const FRAME_TRIES = 3
// A press that moves this far before it is let go frames an area instead of picking an element.
const NOTE_DRAG_PX = 6
const NOTE_AREA_CHARS = 500
const NOTE_COMMENT_CHARS = 1000
const NOTE_MAX_NOTES = 50
const NOTE_SAVE_FAILED = 'Not kaydedilemedi, tekrar dene'
const NOTE_BOX_WIDTH = 320
const NOTE_BOX_MIN_WIDTH = 160
const NOTE_BOX_MIN_HEIGHT = 56
const NOTE_BOX_MAX_HEIGHT = 192
const NOTE_BOX_GAP = 10
const NOTE_BOX_MARGIN = 8
const MAX_CAPTURE_SIDE = 1600
const CAPTURE_JPEG_QUALITY = 80
const CAPTURE_TIMEOUT_MS = 4_000
const MAX_CLEAR_IDS = 200
const KEYS: Record<string, string> = {
  Enter: 'Enter', Tab: 'Tab', Escape: 'Escape', ArrowUp: 'Up', ArrowDown: 'Down', ArrowLeft: 'Left',
  ArrowRight: 'Right', PageUp: 'PageUp', PageDown: 'PageDown',
}

export interface BrowserHostWindow {
  isDestroyed(): boolean
  contentView: { addChildView(view: WebContentsView): void; removeChildView(view: WebContentsView): void }
  webContents: { send(channel: string, payload: unknown): void }
}

// Second-level labels under a two-letter country code (co.uk, com.tr): refused as a site on their own.
const SECOND_LEVEL = new Set(['co', 'com', 'org', 'net', 'gov', 'edu', 'ac'])
const SITE_RE = /^[a-z0-9-]+(\.[a-z0-9-]+)*$/
const STORAGES = ['localstorage', 'indexdb', 'serviceworkers', 'cachestorage', 'websql', 'filesystem']

/** A cookie's own domain as a host: leading dot dropped, lower case. */
export function siteOf(domain: string): string {
  return domain.replace(/^\.+/, '').toLowerCase()
}

/**
 * The listed site a cookie host belongs to: its highest ancestor (or itself) that is some cookie's own domain.
 * Never a guessed registrable domain, so alice.github.io and bob.github.io stay two sites.
 */
function rowOf(host: string, domains: Set<string>): string {
  if (isIP(host) || host.includes(':')) return host
  const labels = host.split('.')
  for (let i = labels.length - 1; i > 0; i--) {
    const parent = labels.slice(i).join('.')
    if (domains.has(parent)) return parent
  }
  return host
}

function siteRows(cookies: Cookie[]): Map<string, Cookie[]> {
  const domains = new Set(cookies.filter((c) => c.domain).map((c) => siteOf(c.domain!)))
  const rows = new Map<string, Cookie[]>()
  for (const cookie of cookies) {
    if (!cookie.domain) continue
    const row = rowOf(siteOf(cookie.domain), domains)
    rows.set(row, [...(rows.get(row) ?? []), cookie])
  }
  return rows
}

/** A bare public suffix (com, co.uk) or the like: removing it would wipe every site under it. */
function isPublicSuffix(site: string): boolean {
  const labels = site.split('.')
  if (labels.length === 1) return site !== 'localhost'
  return labels.length === 2 && SECOND_LEVEL.has(labels[0]) && labels[1].length === 2
}

export interface BrowserHostDeps {
  window: () => BrowserHostWindow | null
  createView: (options: WebContentsViewConstructorOptions) => WebContentsView
  fromPartition: (partition: string) => Session
  http: HttpClient
  baseUrl: () => string
  appToken: string
  uiSecret: string
  lookup?: LookupAll
  /** Visited-page store behind the omnibox and the Suggested grid; absent in tests that do not need it. */
  history?: Pick<BrowserHistory, 'visit' | 'update' | 'list' | 'clear' | 'flush'> & Partial<Pick<BrowserHistory, 'remove' | 'clearSince'>>
  /** Decides which modifier the tab shortcuts use (Cmd on darwin, Ctrl elsewhere); defaults to process.platform. */
  platform?: string
  /** Called on every halt so the command server can drop what is still queued: one chat, or every chat with null. */
  onHalt?: (conversationId: number | null) => void
  now?: () => number
  /** The favicon request; defaults to the hardened session's own network stack. */
  fetch?: (url: string, init: { credentials: 'omit'; redirect: 'manual'; signal: AbortSignal }) => Promise<Response>
  setTimeout?: typeof setTimeout
  clearTimeout?: typeof clearTimeout
  log?: (...args: unknown[]) => void
}

export interface BrowserTabInfo {
  id: number
  url: string
  title: string
  loading: boolean
  favicon: string
  /** The one tab the agent's tools reach in this chat. */
  agent: boolean
}

export interface BrowserLivePayload {
  active: boolean
  conversationId: number
  /** The selected tab's page. */
  url: string
  title: string
  loading: boolean
  /** The user has taken the chat's agent tab over; agent commands are refused until release. */
  userControl: boolean
  tabs: BrowserTabInfo[]
  activeTabId: number | null
  /** Set only while the selected tab is in annotate mode (left out otherwise, so older readers see the same payload). */
  annotating?: true
  /** Set on the event that reports a blocked private address. */
  error?: string
}

type Reply = { ok: boolean; error?: string; url: string; title: string; [key: string]: unknown }
type LoadResult = { ok: true } | { ok: false; error: string }

// Runs inside the isolated world, so it must be plain browser JavaScript. The world's
// globals survive between calls on one document and vanish with it, which is what
// makes refs from an earlier page come back as stale_snapshot.
export const PAGE_LIB = String.raw`(() => {
  if (globalThis.__gmBrowser) return;
  const SELECTOR = 'a[href], button, input:not([type=hidden]), select, textarea, summary, [role=button], [role=link], [role=checkbox], [role=radio], [role=tab], [role=menuitem], [role=option], [role=combobox], [role=textbox], [role=searchbox], [contenteditable=""], [contenteditable="true"]';
  const MAX_ELEMENTS = 300;
  let current = null;
  const clean = (s, n) => String(s || '').replace(/\s+/g, ' ').trim().slice(0, n);
  const shown = (el) => {
    if (!el.getClientRects().length) return false;
    const st = getComputedStyle(el);
    return st.visibility !== 'hidden' && st.display !== 'none';
  };
  const tag = (el) => el.tagName.toLowerCase();
  const inputType = (el) => String(el.type || 'text').toLowerCase();
  const BUTTON_TYPES = ['button', 'submit', 'reset', 'image'];
  const NON_TEXT_TYPES = ['checkbox', 'radio', 'file', 'range', 'color', 'hidden'];
  const roleOf = (el) => {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit;
    const t = tag(el);
    if (t === 'a') return 'link';
    if (t === 'button' || t === 'summary') return 'button';
    if (t === 'select') return 'combobox';
    if (t === 'textarea') return 'textbox';
    if (t === 'input') {
      const ty = inputType(el);
      if (BUTTON_TYPES.includes(ty)) return 'button';
      if (ty === 'checkbox' || ty === 'radio') return ty;
      return ty === 'search' ? 'searchbox' : 'textbox';
    }
    return el.isContentEditable ? 'textbox' : t;
  };
  const kindOf = (el) => {
    const t = tag(el);
    if (t === 'a') return 'link';
    if (t === 'select') return 'select';
    if (t === 'textarea' || el.isContentEditable) return 'input';
    if (t === 'input') return BUTTON_TYPES.includes(inputType(el)) ? 'button' : 'input';
    return 'button';
  };
  // A referenced label that is a field, or sits inside one (an editor), is a person's own text: it is skipped.
  const refName = (n, read) => (n && !isField(n) ? read(n) : '');
  // Text inside anything typed into is never part of a name, wherever the name is read from; a select's or combobox's own text still names it.
  const nameText = (n) => textOf(n, 120, EDITABLE + ', script, style, noscript, template');
  const nameOf = (el) => {
    const by = el.getAttribute('aria-labelledby');
    const byText = by ? by.split(/\s+/).map((id) => refName(document.getElementById(id), nameText)).join(' ') : '';
    const labels = el.labels && el.labels.length ? Array.from(el.labels).map((l) => refName(l, nameText)).join(' ') : '';
    const img = el.querySelector ? el.querySelector('img[alt]') : null;
    const buttonValue = tag(el) === 'input' && BUTTON_TYPES.includes(inputType(el)) ? el.value : '';
    const typed = el.isContentEditable || !!(el.closest && el.closest(EDITABLE));
    return clean(el.getAttribute('aria-label') || byText || labels || buttonValue || (typed ? '' : nameText(el)) ||
      el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('alt') || (img ? img.alt : ''), 120);
  };
  const resolve = (id, ref) => {
    if (!current || current.id !== id) return { error: 'stale_snapshot' };
    const weak = current.refs.get(ref);
    const el = weak && weak.deref();
    if (!el || !el.isConnected) return { error: 'element_gone' };
    return { el };
  };
  const fire = (el, type) => el.dispatchEvent(new Event(type, { bubbles: true }));
  const formOf = (el) => el.form || (el.closest ? el.closest('form') : null);
  // A named control (name="method", "action", "elements", "querySelector") shadows the form's own properties on the instance, so form state is read through the prototype.
  const formGet = (form, prop) => Object.getOwnPropertyDescriptor(HTMLFormElement.prototype, prop).get.call(form);
  const submitControl = (el) => (tag(el) === 'button' && el.type === 'submit') || (tag(el) === 'input' && (el.type === 'submit' || el.type === 'image'));
  // Every control the form owns, in tree order: form="id" controls outside it count, and a form in an open shadow root owns controls the document query cannot see.
  const ownedControls = (el, form) => {
    const root = el.getRootNode();
    const scopes = root !== document && root && root.querySelectorAll ? [root, document] : [document];
    return scopes.flatMap((scope) => Array.from(scope.querySelectorAll('button, input, select, textarea')).filter((f) => f.form === form));
  };
  // A stable id for each element this page describes, so an Enter approved on one element cannot be spent on an equal-looking other one.
  // The ids carry a random token made when this page script starts, so a new document at the same URL never repeats an old id.
  const documentToken = Array.from(crypto.getRandomValues(new Uint32Array(3)), (n) => n.toString(36)).join('');
  const elementIds = new WeakMap();
  let lastElementId = 0;
  const elementId = (el) => {
    if (!elementIds.has(el)) elementIds.set(el, 'd' + documentToken + '-el' + (++lastElementId));
    return elementIds.get(el);
  };
  // ---- annotation frames (docs/agent-browser.md "Annotate mode") ----
  // The page side only answers reads: frame() and mark() keep no pick, draw nothing and write nothing to the page. The host
  // keeps every pick, draft and note, and its overlay view draws the outline and the pins over the tab.
  const NOTE_NAME_CHARS = 200;
  const NOTE_PATH_CHARS = 200;
  const NOTE_TEXT_CHARS = 200;
  const FRAME_MAX_ELEMENTS = ${FRAME_MAX_ELEMENTS};
  // Text a person typed or chose never leaves the page: every place that reads text skips fields and editors (any contenteditable value except "false", in any case; a role attribute is a token list, so one field token anywhere in it counts).
  const EDITABLE = 'input, textarea, [contenteditable]:not([contenteditable="false" i]), [role~="textbox" i], [role~="searchbox" i], [role~="spinbutton" i], [aria-multiline="true" i]';
  const FIELD_SELECTOR = EDITABLE + ', select, option, [role~="combobox" i]';
  const SKIPPED_TEXT = FIELD_SELECTOR + ', script, style, noscript, template';
  const isField = (el) => !!el.isContentEditable || !!(el.closest && el.closest(FIELD_SELECTOR));
  const isSkipped = (el) => !!el.isContentEditable || !!el.closest(SKIPPED_TEXT);
  const rectOf = (r) => ({ x: Math.round(r.left), y: Math.round(r.top), width: Math.round(r.width), height: Math.round(r.height) });
  const textOf = (root, max, skip = SKIPPED_TEXT) => {
    let out = '';
    let visited = 0;
    const walk = (node, depth) => {
      if (out.length >= max || visited++ > 3000 || depth > 80) return;
      if (node.nodeType === 3) { out += node.nodeValue; return; }
      if (node.nodeType !== 1 || node.isContentEditable || node.matches(skip)) return;
      const st = getComputedStyle(node);
      if (st.display === 'none' || st.visibility === 'hidden') return;
      // Inline text runs on as in innerText ("E-mail", not "E -mail"); a block or a line break separates.
      const gap = node.tagName === 'BR' || !st.display.startsWith('inline') ? ' ' : '';
      out += gap;
      for (const child of node.childNodes) walk(child, depth + 1);
      out += gap;
    };
    walk(root, 0);
    return clean(out, max);
  };
  const noteName = (el) => {
    const refText = (n) => refName(n, (m) => textOf(m, NOTE_NAME_CHARS));
    const by = el.getAttribute('aria-labelledby');
    const byText = by ? by.split(/\s+/).map((id) => refText(document.getElementById(id))).join(' ') : '';
    const labels = el.labels && el.labels.length ? Array.from(el.labels).map(refText).join(' ') : '';
    const field = isField(el);
    const buttonValue = tag(el) === 'input' && BUTTON_TYPES.includes(inputType(el)) ? el.value : '';
    const img = !field && el.querySelector ? el.querySelector('img[alt]') : null;
    return clean(el.getAttribute('aria-label') || byText || labels || buttonValue || (field ? '' : textOf(el, NOTE_NAME_CHARS)) ||
      el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('alt') || (img ? img.alt : ''), NOTE_NAME_CHARS);
  };
  const cssPath = (el) => {
    const parts = [];
    for (let n = el; n && n.nodeType === 1 && n !== document.documentElement && parts.length < 6; n = n.parentElement) {
      let part = tag(n);
      if (n.id && /^[A-Za-z][\w-]{0,40}$/.test(n.id)) { parts.unshift(part + '#' + n.id); break; }
      const classes = typeof n.className === 'string' ? n.className.trim().split(/\s+/).filter((c) => /^[A-Za-z_][\w-]{0,30}$/.test(c)).slice(0, 2) : [];
      if (classes.length) part += '.' + classes.join('.');
      const same = n.parentElement ? Array.from(n.parentElement.children).filter((s) => s.tagName === n.tagName) : [];
      if (same.length > 1) part += ':nth-of-type(' + (same.indexOf(n) + 1) + ')';
      parts.unshift(part);
    }
    return clean(parts.join(' > '), NOTE_PATH_CHARS);
  };
  // What a note about this element says: the same description a pick had, plus its own visible text (fields and editors give none).
  const frameElement = (el, box) => {
    const data = { box, id: elementId(el), tag: tag(el), role: roleOf(el), name: noteName(el), path: cssPath(el), text: isSkipped(el) ? '' : textOf(el, NOTE_TEXT_CHARS) };
    const link = el.closest ? el.closest('a[href]') : null;
    if (link && link.href) data.href = String(link.href).slice(0, 300);
    return data;
  };
  // Where the page is: a frame is only as good as the picture taken with it, so the host takes frame(), then the picture,
  // then mark(), and keeps the three only when mark still names the same document, scroll and viewport.
  const here = () => ({ docId: documentToken, scroll: [Math.round(scrollX), Math.round(scrollY)], viewport: { width: innerWidth, height: innerHeight } });
  globalThis.__gmBrowser = {
    snapshot(a) {
      const refs = new Map();
      const elements = [];
      for (const el of document.querySelectorAll(SELECTOR)) {
        if (elements.length >= MAX_ELEMENTS) break;
        if (!shown(el)) continue;
        const ref = 'e' + (elements.length + 1);
        refs.set(ref, new WeakRef(el));
        const item = { ref, role: roleOf(el), name: nameOf(el), kind: kindOf(el) };
        if (tag(el) === 'a' && el.href) item.href = el.href;
        if (tag(el) === 'select') item.value = clean(el.selectedOptions[0] ? el.selectedOptions[0].text : '', 200);
        else if ((tag(el) === 'input' && item.kind === 'input' && inputType(el) !== 'password') || tag(el) === 'textarea') item.value = String(el.value || '').slice(0, 200);
        elements.push(item);
      }
      current = { id: a.snapshotId, refs };
      const body = document.body ? document.body.innerText : '';
      const text = String(body || '').replace(/[ \t]+/g, ' ').replace(/\n\s*\n\s*\n+/g, '\n\n').trim();
      return { text: text.slice(0, a.maxChars), truncated: text.length > a.maxChars, elements };
    },
    click(a) {
      const r = resolve(a.snapshotId, a.ref);
      if (r.error) return r;
      r.el.scrollIntoView({ block: 'center', inline: 'center' });
      if (r.el.focus) r.el.focus({ preventScroll: true });
      r.el.click();
      return {};
    },
    fill(a) {
      const r = resolve(a.snapshotId, a.ref);
      if (r.error) return r;
      const el = r.el;
      const t = tag(el);
      const textual = t === 'textarea' || (t === 'input' && !BUTTON_TYPES.concat(['checkbox', 'radio', 'file', 'range', 'color']).includes(inputType(el)));
      if (!textual && !el.isContentEditable) return { error: 'not_fillable' };
      if (el.disabled || el.readOnly) return { error: 'not_editable' };
      el.scrollIntoView({ block: 'center' });
      el.focus();
      if (textual) el.value = a.value; else el.textContent = a.value;
      fire(el, 'input');
      fire(el, 'change');
      return {};
    },
    select(a) {
      const r = resolve(a.snapshotId, a.ref);
      if (r.error) return r;
      const el = r.el;
      if (tag(el) !== 'select') return { error: 'not_select' };
      if (el.disabled) return { error: 'not_editable' };
      const wanted = String(a.option).trim().toLowerCase();
      const options = Array.from(el.options);
      const option = options.find((o) => o.value === a.option) || options.find((o) => clean(o.text, 500).toLowerCase() === wanted);
      if (!option) return { error: 'option_not_found' };
      el.value = option.value;
      fire(el, 'input');
      fire(el, 'change');
      return {};
    },
    scroll(a) {
      const step = a.amount > 0 ? a.amount : Math.round(innerHeight * 0.8);
      window.scrollBy({ top: a.direction === 'up' ? -step : step, behavior: 'instant' });
      const root = document.scrollingElement || document.documentElement;
      return { scroll_y: Math.round(scrollY), at_bottom: Math.ceil(scrollY + innerHeight) >= root.scrollHeight };
    },
    describe(a) {
      let el;
      if (a.focused) {
        el = document.activeElement;
        while (el && el.shadowRoot && el.shadowRoot.activeElement) el = el.shadowRoot.activeElement;
        if (el && (tag(el) === 'iframe' || tag(el) === 'frame')) return { error: 'focus_in_frame' };
        if (!el || el === document.documentElement) el = document.body || document.documentElement;
      } else {
        const r = resolve(a.snapshotId, a.ref);
        if (r.error) return r;
        el = r.el;
      }
      const t = tag(el);
      const element = { role: roleOf(el), name: nameOf(el), tag: t, element_id: elementId(el) };
      if (t === 'input') element.type = inputType(el);
      if (t === 'button') element.type = el.type;
      if (t === 'a') {
        if (el.href) element.href = el.href;
        element.download = el.hasAttribute('download');
      }
      element.is_password = t === 'input' && inputType(el) === 'password';
      const form = formOf(el);
      if (form) {
        const submitter = submitControl(el);
        // Enter in a text field submits when the form has a submit button or the field is its only text field.
        const typing = t === 'input' && !BUTTON_TYPES.concat(NON_TEXT_TYPES).includes(element.type);
        const textFields = Array.from(formGet(form, 'elements')).filter((f) => tag(f) === 'input' && !BUTTON_TYPES.concat(NON_TEXT_TYPES).includes(inputType(f)));
        // Enter presses the form's default button, so its formmethod/formaction decide where the form goes (read as attributes: jsdom lacks the formMethod/formAction properties).
        // form.elements omits <input type=image>, so the form's controls are collected from the tree (form="id" controls included).
        const owned = ownedControls(el, form);
        const defaultButton = owned.find(submitControl) || null;
        element.submits_form = submitter || (typing && (!!defaultButton || textFields.length === 1));
        const via = submitter ? el : typing ? defaultButton : null;
        if (typing && defaultButton) element.form_submit_name = nameOf(defaultButton);
        const rawMethod = via && via.hasAttribute('formmethod') ? via.getAttribute('formmethod') : formGet(form, 'method');
        // A non-string method is unknown: report post so a card opens.
        const method = typeof rawMethod === 'string' ? rawMethod.toLowerCase() : 'post';
        element.form_method = ['post', 'dialog'].includes(method) ? method : 'get';
        element.form_action = (via && via.hasAttribute('formaction') ? new URL(via.getAttribute('formaction'), document.baseURI).href : formGet(form, 'action')) || location.href;
        element.form_has_password = owned.some((f) => tag(f) === 'input' && inputType(f) === 'password');
      } else {
        element.submits_form = false;
        element.form_has_password = false;
      }
      return { element };
    },
    // Every element that is shown and inside the viewport, in document order, with its box in viewport CSS px.
    frame() {
      const elements = [];
      for (const el of document.body ? [document.body, ...document.body.querySelectorAll('*')] : []) {
        if (elements.length >= FRAME_MAX_ELEMENTS) break;
        const box = rectOf(el.getBoundingClientRect());
        if (box.width <= 0 || box.height <= 0 || box.x + box.width <= 0 || box.y + box.height <= 0 || box.x >= innerWidth || box.y >= innerHeight || !shown(el)) continue;
        elements.push(frameElement(el, box));
      }
      return Object.assign(here(), { elements });
    },
    mark() {
      return here();
    },
  };
})();`

/**
 * The comment box: a self-contained page the host shows in its own view above the tab, so the typed text never
 * enters the page being annotated. It has no preload; it talks to the host through document.title (JSON with a
 * counter, so two equal messages still fire page-title-updated). Every message carries the id of the pick __set gave
 * it (p), so the host can route it to that pick, or drop it when that pick is no longer open; __error carries the id of
 * the pick it is about and is ignored when the box shows another one.
 */
export const NOTE_BOX_HTML = String.raw`<!doctype html><html><head><meta charset="utf-8"><style>
*{box-sizing:border-box}
html,body{margin:0;background:transparent;overflow:hidden;font:13.5px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
body{padding:6px}
/* Gamachine's own plate, not a chat pill: the mascot's slate and cream with its orange visor as the edge mark, two
   chamfered corners as in the app's cut-corner chips. The border is a 1px outer plate, since clip-path cuts a box-shadow. */
#w{filter:drop-shadow(0 8px 14px rgba(6,8,16,.5))}
#b{padding:1px;background:#3b4359;clip-path:polygon(10px 0,100% 0,100% calc(100% - 10px),calc(100% - 10px) 100%,0 100%,0 10px)}
#i{position:relative;padding:9px 10px 8px 15px;background:#191e2b;color:#F1EEE6;clip-path:polygon(9.6px 0,100% 0,100% calc(100% - 9.6px),calc(100% - 9.6px) 100%,0 100%,0 9.6px)}
#i::before{content:"";position:absolute;left:0;top:12px;width:3px;height:16px;background:#FF6B3D;transition:top .14s,bottom .14s,height .14s}
#i:focus-within::before{top:8px;bottom:8px;height:auto}
#t{display:block;width:100%;height:20px;max-height:112px;margin:0;padding:0;border:0;outline:0;resize:none;background:transparent;color:inherit;font:inherit;caret-color:#FF6B3D}
#t::placeholder{color:#7f879b}
#f{display:flex;align-items:center;gap:8px;margin-top:7px;font:10.5px/1 ui-monospace,"JetBrains Mono",Consolas,monospace;color:#7f879b}
#f .h{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.k{display:inline-block;padding:2px 4px;margin-right:3px;border:1px solid #3b4359;color:#b9bfcd}
#s{flex:none;display:flex;align-items:center;gap:6px;height:24px;padding:0 10px 0 7px;border:0;cursor:pointer;background:#FF6B3D;color:#141826;font:600 11px/1 ui-monospace,"JetBrains Mono",Consolas,monospace;letter-spacing:.03em;clip-path:polygon(6px 0,100% 0,100% calc(100% - 6px),calc(100% - 6px) 100%,0 100%,0 6px)}
#s .k{margin:0;border-color:rgba(20,24,38,.45);color:inherit}
#s:hover:not(:disabled){background:#ff8259}
#s:focus-visible{outline:0;box-shadow:inset 0 0 0 2px #F1EEE6}
#s:disabled{background:#2a3041;color:#6d7488;cursor:default}
#s:disabled .k{border-color:#3b4359}
#e{margin:6px 2px 0;color:#ff9d88;font:11.5px/1.35 ui-monospace,"JetBrains Mono",Consolas,monospace}
@media (prefers-reduced-motion:reduce){#i::before{transition:none}}
</style></head><body><div id="w"><div id="b"><div id="i"><textarea id="t" rows="1" maxlength="1000" placeholder="Yorum ekle…" aria-label="Yorum ekle"></textarea><div id="f"><span class="h"><span class="k">⇧↵</span>satır <span class="k">Esc</span>vazgeç</span><button id="s" type="button" disabled><span class="k" aria-hidden="true">↵</span>Kaydet</button></div></div></div><div id="e" role="alert" hidden></div></div><script>
const ta=document.getElementById('t'),send=document.getElementById('s'),wrap=document.getElementById('w'),err=document.getElementById('e');
let seq=0,pick=null;
const post=(o)=>{o.q=++seq;o.p=pick;document.title=JSON.stringify(o)};
const fit=()=>{ta.style.height='auto';ta.style.height=Math.min(ta.scrollHeight,112)+'px';send.disabled=!ta.value.trim()};
const height=()=>wrap.offsetHeight+12;
const report=(t)=>post({t,text:ta.value,h:height()});
const draft=()=>{err.hidden=true;fit();report('draft')};
const submit=()=>{if(ta.value.trim())post({t:'submit',text:ta.value})};
ta.addEventListener('input',draft);
// Esc never arrives here: the host claims it (escape()) and drops the pick.
ta.addEventListener('keydown',(e)=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();submit()}});
send.addEventListener('click',submit);
// The view's width before it is shown: a box not laid out yet measured its text at 0 px and opened ~130 px tall.
window.__width=(w)=>{wrap.style.width=Math.max(0,w-12)+'px'};
window.__set=(v,p)=>{pick=p;err.hidden=true;ta.value=v;fit();report('size');ta.focus();return height()};
window.__error=(m,p)=>{if(p!==pick)return;err.textContent=m;err.hidden=false;fit();report('size');return height()};
ta.focus();
</script></body></html>`

/**
 * The annotate overlay: a self-contained page in its own transparent view exactly over the annotating tab, above it and
 * below the comment box. The page under it gets no pointer or key event from the user while the mode is on; the overlay
 * outlines the element under the cursor from the boxes of the latest frame (__boxes; no host round trip), and tells the
 * host through document.title (JSON with a counter, like the box) about a click (pick), a drag (area) and a wheel; Esc
 * never reaches it (the host claims it, escape()). __freeze shows a frame's picture over the live page (null goes back to the live look), __pins draws the
 * numbered pins. Page-derived strings only ever reach it as data, drawn with textContent.
 */
export const OVERLAY_HTML = String.raw`<!doctype html><html><head><meta charset="utf-8"><style>
*{box-sizing:border-box}
html,body{margin:0;width:100%;height:100%;overflow:hidden;background:transparent;cursor:crosshair;user-select:none;font:13px/1.4 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
#f{position:fixed;left:0;top:0;width:100%;height:100%;display:none;pointer-events:none}
.o{position:fixed;display:none;pointer-events:none;border:2px solid #2f6fed;border-radius:4px;background:rgba(47,111,237,.10)}
#d{border-style:dashed}
.pin{position:fixed;width:20px;height:20px;margin:-10px 0 0 -10px;border-radius:50%;background:#2f6fed;color:#fff;font-size:11px;font-weight:600;display:flex;align-items:center;justify-content:center;box-shadow:0 0 0 2px #fff,0 2px 6px rgba(15,23,42,.35);pointer-events:none}
#bar{position:fixed;top:10px;left:50%;transform:translateX(-50%);max-width:calc(100vw - 24px);padding:5px 12px;border-radius:999px;background:rgba(21,27,38,.88);color:#f4f6fa;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;box-shadow:0 2px 10px rgba(15,23,42,.3);pointer-events:none}
</style></head><body><img id="f" alt=""><div id="pins"></div><div id="h" class="o"></div><div id="s" class="o"></div><div id="d" class="o"></div><div id="bar">İşaretleme · bir öğeye tıkla ya da alan çiz · Esc çıkış</div><script>
const $=(id)=>document.getElementById(id);
const img=$('f'),hover=$('h'),chosen=$('s'),frame=$('d'),pins=$('pins'),bar=$('bar');
let seq=0,boxes=[],quiet=false,down=null,at=null;
const post=(o)=>{o.q=++seq;document.title=JSON.stringify(o)};
const place=(n,r)=>{if(!r){n.style.display='none';return}n.style.left=r.x+'px';n.style.top=r.y+'px';n.style.width=r.width+'px';n.style.height=r.height+'px';n.style.display='block'};
// The host's hitElement rule: among the boxes containing the point the smallest wins, a tie goes to the later one.
const hit=(x,y)=>{let best=null,area=Infinity;for(const b of boxes){if(x<b.x||y<b.y||x>=b.x+b.width||y>=b.y+b.height)continue;const s=b.width*b.height;if(s<=area){best=b;area=s}}return best};
const outline=()=>place(hover,!quiet&&!down&&at?hit(at.x,at.y):null);
const dragRect=(d,e)=>{const x=Math.max(0,Math.min(d.x,e.clientX)),y=Math.max(0,Math.min(d.y,e.clientY));return{x,y,width:Math.min(innerWidth,Math.max(d.x,e.clientX))-x,height:Math.min(innerHeight,Math.max(d.y,e.clientY))-y}};
addEventListener('pointermove',(e)=>{at={x:e.clientX,y:e.clientY};if(down){if(!down.area&&Math.hypot(e.clientX-down.x,e.clientY-down.y)>=${NOTE_DRAG_PX})down.area=true;if(down.area)place(frame,dragRect(down,e))}outline()});
addEventListener('pointerdown',(e)=>{if(e.button!==0)return;down={x:e.clientX,y:e.clientY,area:false};outline()});
addEventListener('pointerup',(e)=>{const d=down;down=null;place(frame,null);if(d&&e.button===0)post(d.area?{t:'area',rect:dragRect(d,e)}:{t:'pick',x:e.clientX,y:e.clientY});outline()});
document.addEventListener('mouseleave',()=>{at=null;outline()});
addEventListener('wheel',(e)=>{e.preventDefault();post({t:'wheel',x:e.clientX,y:e.clientY,dx:e.deltaX,dy:e.deltaY})},{passive:false});
addEventListener('keydown',(e)=>e.preventDefault());
addEventListener('contextmenu',(e)=>e.preventDefault());
window.__boxes=(list)=>{boxes=Array.isArray(list)?list:[];outline()};
// A picture with a rect is a comment being written (its target outlined); one without is a capture: no hover, no bar.
window.__freeze=(src,r)=>{quiet=!!src&&!r;bar.hidden=quiet;place(chosen,src?r:null);outline();if(!src){img.removeAttribute('src');img.style.display='none';return null}img.src=src;img.style.display='block';return img.decode().then(()=>null,()=>null)};
window.__pins=(list)=>{pins.replaceChildren();for(const p of Array.isArray(list)?list:[]){if(!(p.x>=0&&p.y>=0&&p.x<=innerWidth&&p.y<=innerHeight))continue;const n=document.createElement('div');n.className='pin';n.textContent=String(p.n);n.style.left=Math.max(10,Math.min(p.x,innerWidth-10))+'px';n.style.top=Math.max(10,Math.min(p.y,innerHeight-10))+'px';pins.appendChild(n)}};
</script></body></html>`

/**
 * Where the comment box goes, in window coordinates: right of the picked rect (viewport CSS px inside the panel),
 * else left, else below, else above, always inside the panel. Only when nothing fits does it cover the target.
 */
export function placeNoteBox(rect: { x: number; y: number; width: number; height: number }, panel: Rectangle,
  size: { width: number; height: number }): Rectangle {
  const left = panel.x + rect.x
  const top = panel.y + rect.y
  const right = left + rect.width
  const bottom = top + rect.height
  const minX = panel.x + NOTE_BOX_MARGIN
  const minY = panel.y + NOTE_BOX_MARGIN
  const maxX = Math.max(minX, panel.x + panel.width - NOTE_BOX_MARGIN - size.width)
  const maxY = Math.max(minY, panel.y + panel.height - NOTE_BOX_MARGIN - size.height)
  const clampX = (v: number) => Math.max(minX, Math.min(v, maxX))
  const clampY = (v: number) => Math.max(minY, Math.min(v, maxY))
  let x = clampX(left)
  let y = clampY(top)
  if (right + NOTE_BOX_GAP <= maxX) x = right + NOTE_BOX_GAP
  else if (left - NOTE_BOX_GAP - size.width >= minX) x = left - NOTE_BOX_GAP - size.width
  else if (bottom + NOTE_BOX_GAP <= maxY) y = bottom + NOTE_BOX_GAP
  else if (top - NOTE_BOX_GAP - size.height >= minY) y = top - NOTE_BOX_GAP - size.height
  // The side chosen above can lie outside the panel (a target scrolled away): the box itself never does.
  return { x: Math.round(clampX(x)), y: Math.round(clampY(y)), width: size.width, height: size.height }
}

/** The box is as wide as the panel allows, up to its normal width; 0 when the panel is too narrow for a usable box. */
function noteBoxWidth(panel: Rectangle): number {
  const width = Math.min(NOTE_BOX_WIDTH, panel.width - 2 * NOTE_BOX_MARGIN)
  return width >= NOTE_BOX_MIN_WIDTH ? width : 0
}

/** Whether any part of the picked rect (viewport CSS px) is inside the panel: only then does the box have a target to sit next to. */
function targetShown(rect: { x: number; y: number; width: number; height: number }, panel: Rectangle): boolean {
  return rect.x + rect.width > 0 && rect.y + rect.height > 0 && rect.x < panel.width && rect.y < panel.height
}

function str(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0
}

function finiteNonNegative(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
}


function positiveInt(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value > 0
}

function toRect(value: unknown): { x: number; y: number; width: number; height: number } | null {
  if (!value || typeof value !== 'object') return null
  const r = value as Record<string, unknown>
  return ['x', 'y', 'width', 'height'].every((k) => typeof r[k] === 'number' && Number.isFinite(r[k]))
    ? { x: r.x as number, y: r.y as number, width: r.width as number, height: r.height as number } : null
}

function toViewport(value: unknown): { width: number; height: number } {
  const r = value && typeof value === 'object' ? value as Record<string, unknown> : {}
  const side = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) && v > 0 ? Math.round(v) : 0)
  return { width: side(r.width), height: side(r.height) }
}

type Box = { x: number; y: number; width: number; height: number }

/** One element of a page frame: its box (viewport CSS px) and the description a note about it carries (frame() in PAGE_LIB). */
type FrameElement = Record<string, unknown> & { box: Box; id: string; text: string }

/**
 * A read-only picture of the page (readFrame): what frame() returned and, when asked for, the capture taken while the
 * page stayed where frame() found it (same document, scroll and viewport; a JPEG data URL, '' without one), with the
 * address and title the tab had when the frame was accepted: a note made from it names that page, wherever the tab went since.
 */
interface FrameReply {
  docId: string
  scroll: [number, number]
  viewport: { width: number; height: number }
  elements: FrameElement[]
  image: string
  url: string
  title: string
  /** The tab's navGen when the frame was read: once the tab's moved on, the frame is of a page the tab no longer shows. */
  navGen: number
}

/** What the page's frame() gives; readFrame adds the picture, the tab's address and title and its navGen. */
type PageFrame = Omit<FrameReply, 'image' | 'url' | 'title' | 'navGen'>

function toScroll(value: unknown): [number, number] {
  return Array.isArray(value) ? [Number(value[0]) || 0, Number(value[1]) || 0] : [0, 0]
}

/** A frame() reply, or null when it is not one. */
function toFrame(raw: Record<string, unknown>): PageFrame | null {
  if (!str(raw.docId) || !Array.isArray(raw.elements)) return null
  const elements = raw.elements.slice(0, FRAME_MAX_ELEMENTS).flatMap((item): FrameElement[] => {
    if (!item || typeof item !== 'object') return []
    const e = item as Record<string, unknown>
    const box = toRect(e.box)
    return box && str(e.id) ? [{ ...e, box, id: e.id, kind: 'element', text: typeof e.text === 'string' ? e.text : '' }] : []
  })
  return { docId: raw.docId, scroll: toScroll(raw.scroll), viewport: toViewport(raw.viewport), elements }
}

/**
 * Whether a mark() reply says the page is still where a frame was read: same document, scroll and viewport. Changes inside
 * the page (animations, ads, clocks) do not count, or such a page would never let a pick open.
 */
function samePlace(mark: Record<string, unknown>, frame: PageFrame): boolean {
  const scroll = toScroll(mark.scroll)
  const viewport = toViewport(mark.viewport)
  return mark.docId === frame.docId && scroll[0] === frame.scroll[0] && scroll[1] === frame.scroll[1] &&
    viewport.width === frame.viewport.width && viewport.height === frame.viewport.height
}

/** The element under a point: among the boxes containing it the smallest wins, a tie goes to the later one (the overlay's hover uses the same rule). */
export function hitElement<T extends { box: Box }>(elements: readonly T[], x: number, y: number): T | null {
  let best: T | null = null
  let area = Infinity
  for (const element of elements) {
    const b = element.box
    if (x < b.x || y < b.y || x >= b.x + b.width || y >= b.y + b.height) continue
    const size = b.width * b.height
    if (size <= area) {
      best = element
      area = size
    }
  }
  return best
}

/** The text of an area: the texts of the elements whose boxes lie fully inside it, in document order. */
export function areaText(elements: readonly { box: Box; text: string }[], rect: Box): string {
  return elements.filter(({ box: b }) => b.x >= rect.x && b.y >= rect.y && b.x + b.width <= rect.x + rect.width && b.y + b.height <= rect.y + rect.height)
    .map((element) => element.text).filter(Boolean).join(' ').slice(0, NOTE_AREA_CHARS)
}

/** The overlay's look while a pick is open: its frame's picture, its target outlined. */
function lookOf(pick: HostPick): Look {
  return { by: 'pick', frame: pick.frame, rect: pick.rect }
}

/** The box call that shows a pick's last save error (the box drops it unless it shows that pick). */
function errorCall(pick: HostPick): string {
  return `window.__error(${JSON.stringify(pick.error)},${JSON.stringify(pick.id)})`
}

/** What a click (pick) or a drag (area) from the overlay chooses in a frame: the element's description, or the area's. */
function frameTarget(frame: FrameReply, message: Record<string, unknown>): { data: Record<string, unknown>; rect: Box } | null {
  if (message.t === 'area') {
    const rect = toRect(message.rect)
    return rect && rect.width > 0 && rect.height > 0 ? { data: { kind: 'area', rect, text: areaText(frame.elements, rect) }, rect } : null
  }
  if (typeof message.x !== 'number' || typeof message.y !== 'number') return null
  const hit = hitElement(frame.elements, message.x, message.y)
  if (!hit) return null
  const { box, ...data } = hit
  return { data, rect: box }
}

/** A message a self-contained app page (the box, the overlay) put in its title, or null for any other title. */
function titleMessage(title: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(title)
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null
  } catch {
    return null
  }
}

/** scheme://host[:port] of a web address, or '' for anything else (blank pages, unparsable text). */
function originOf(url: string): string {
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? parsed.origin : ''
  } catch {
    return ''
  }
}

function inSite(host: string, site: string): boolean {
  return host === site || host.endsWith(`.${site}`)
}

/** The http(s) origin of `url` when its host is `site` or a subdomain of it, else ''. */
function siteOrigin(url: string, site: string): string {
  const origin = originOf(url)
  return origin && inSite(new URL(origin).hostname, site) ? origin : ''
}

/** The response body, or null as soon as it is larger than `max` bytes. */
async function readCapped(response: Response, max: number): Promise<Buffer | null> {
  const reader = response.body?.getReader()
  if (!reader) {
    const whole = Buffer.from(await response.arrayBuffer())
    return whole.length > max ? null : whole
  }
  const chunks: Buffer[] = []
  let size = 0
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    size += value.length
    if (size > max) {
      void reader.cancel().catch(() => {})
      return null
    }
    chunks.push(Buffer.from(value))
  }
  return Buffer.concat(chunks)
}

/** A key event the host's own `press` is about to send back through one of the input channels. */
interface ExpectedEcho {
  channel: 'before' | 'input'
  type: string
  /** Key names the echo may carry; an echo without a key name matches on type alone. */
  keys: string[]
  until: number
}

export type TabShortcut = { action: 'new' | 'close' | 'reopen' } | { action: 'select'; index: number }

/**
 * Ctrl on Windows and Linux, Cmd on macOS; only T, W, Shift+T and 1-9 are claimed, never copy/paste/undo.
 * The panel's tabShortcutOf (renderer/lib/browserSuggest.ts) applies the same rule to DOM key events. They
 * stay two functions because nextron's main build does not compile files under renderer/ (and the renderer
 * cannot import main/); browser-tabs.test.tsx checks both against one table of keys.
 */
export function tabShortcut(input: { type?: string; key?: string; control?: boolean; meta?: boolean; shift?: boolean; alt?: boolean; isAutoRepeat?: boolean },
  platform: string): TabShortcut | null {
  if (input.type !== 'keyDown' || input.isAutoRepeat || typeof input.key !== 'string' || input.alt) return null
  const mac = platform === 'darwin'
  if (!(mac ? input.meta && !input.control : input.control && !input.meta)) return null
  const key = input.key.toLowerCase()
  if (key === 't') return { action: input.shift ? 'reopen' : 'new' }
  if (input.shift) return null
  if (key === 'w') return { action: 'close' }
  return /^[1-9]$/.test(key) ? { action: 'select', index: Number(key) } : null
}

/** Ctrl+Shift+F (Cmd+Shift+F on macOS): the renderer's full-screen toggle, claimed here because keys in the page never reach it. */
export function isFullChord(input: { type?: string; key?: string; control?: boolean; meta?: boolean; shift?: boolean; alt?: boolean; isAutoRepeat?: boolean },
  platform: string): boolean {
  if (input.type !== 'keyDown' || input.isAutoRepeat || typeof input.key !== 'string' || input.alt || !input.shift) return false
  if (input.key.toLowerCase() !== 'f') return false
  return platform === 'darwin' ? !!input.meta && !input.control : !!input.control && !input.meta
}

/** Ctrl+Shift+E (Cmd+Shift+E on macOS): toggles annotate mode, claimed here because keys in the page never reach the renderer. */
export function isAnnotateChord(input: { type?: string; key?: string; control?: boolean; meta?: boolean; shift?: boolean; alt?: boolean; isAutoRepeat?: boolean },
  platform: string): boolean {
  if (input.type !== 'keyDown' || input.isAutoRepeat || typeof input.key !== 'string' || input.alt || !input.shift) return false
  if (input.key.toLowerCase() !== 'e') return false
  return platform === 'darwin' ? !!input.meta && !input.control : !!input.control && !input.meta
}

/**
 * Esc with no modifier, not a repeat: the one key the host routes by state (BrowserHost.escape) when it is pressed in a
 * tab, its overlay or the box, because a key there never reaches the renderer.
 */
export function isEscape(input: { type?: string; key?: string; control?: boolean; meta?: boolean; shift?: boolean; alt?: boolean; isAutoRepeat?: boolean }): boolean {
  return input.type === 'keyDown' && input.key === 'Escape' && !input.isAutoRepeat && !input.control && !input.meta && !input.shift && !input.alt
}

/** One page: a chat's agent tab, or a tab the user opened. */
interface Tab {
  id: number
  conversationId: number
  agent: boolean
  view: WebContentsView
  favicon: string
  /** The id of the newest snapshot taken in this tab; element commands carrying any other id are stale. */
  snapshotId: string
  loadWaiter: ((result: LoadResult) => void) | null
  popupBlocked: string | null
  userControl: boolean
  /** When the user last pressed a key or mouse button in this page (not the host's own `press`); null if never. */
  lastGesture: number | null
  /** The user pressed a key or mouse button in this page since the chat's last message; the agent may not switch to it. */
  userTouched: boolean
  /** The user is placing notes on this page (annotate mode); it stays on across navigations. */
  annotating: boolean
  /** The overlay view over this tab while the mode is on (or while a capture draws on it). */
  overlay: AppView | null
  /** The target waiting for its comment; only the host holds it, and no later frame or page event changes it. */
  pick: HostPick | null
  /**
   * The notes saved in this tab and not yet sent or removed (the renderer holds them as chips); docId and docRect place
   * their pins, url and title name the page the note was made on.
   */
  notes: { note: Record<string, unknown> & { id: string; n: number }; docId: string; docRect: Box; url: string; title: string }[]
  /** The number of the last note saved in this tab: numbers are never reused in a tab, across navigations too. */
  pinSeq: number
  /** Bumped by every frame read, a cancel and the mode going off: a read that finds another value is dropped (readFrame). */
  frameSeq: number
  /** Bumped when a main-frame navigation starts: a read that finds another value is dropped. */
  navGen: number
  /** Frame reads a click or a capture waits on; the refresh holds off meanwhile, so it does not supersede them. */
  waiting: number
  refreshing: boolean
  refreshTimer: ReturnType<typeof setTimeout> | null
  /** What the overlay shows; set only by setLook(). */
  shows: Look | null
  /** Bumped by every navigation, so a favicon fetched for an earlier page is dropped. */
  faviconSeq: number
  /** The main-frame address being loaded and not yet committed (getURL still shows the old page); null once it commits. */
  target: string | null
  /** Settles when the tab is closed, so a command waiting on something else can stop at once. */
  gone: Promise<void>
  markGone: () => void
  /** Key events the host sent with `press` and has not yet seen come back. */
  expected: ExpectedEcho[]
  /** Agent commands running on this tab. */
  busy: number
  lastUsed: number
}

interface ClosedTab { url: string; title: string; closedAt: number }
/** A closed tab a reopen popped; `forgotten` is set by the first removal or forget that matches it. */
interface Reopen { tab: ClosedTab; conversationId: number; forgotten: boolean }

/** A self-contained app page in its own view over the tabs (the comment box, an overlay) and its first load. */
interface AppView { view: WebContentsView; ready: Promise<void> }

/**
 * What a tab's overlay shows: a frame's boxes (the hover) and the pins of its document's notes at its scroll, on the live
 * page ('live': the latest refresh) or over the frame's picture ('pick': an open comment, its target outlined by rect;
 * 'capture': a capture being taken). null: the live page with nothing outlined or pinned.
 */
interface Look { by: 'live' | 'pick' | 'capture'; frame: FrameReply; rect: Box | null }

interface HostPick {
  /** The host's id for this pick, never reused: the box's messages carry it, and the saved note keeps it. */
  id: string
  /** The frozen frame the pick was made from (its docId, scroll, viewport, picture and elements). */
  frame: FrameReply
  /** The note's description of the target: the frame element's (id, kind, tag, role, name, href, path, text) or the area's (id, kind, rect, text). */
  data: Record<string, unknown>
  rect: Box
  /** What the user has typed into the comment box so far. */
  draft: string
  /** Why the last save failed, shown in the box (it comes back with the draft). */
  error: string
}

/**
 * Hardened WebContentsViews over the main window, all on one persistent session: per chat one agent tab
 * (the only page agent commands reach) plus any tabs the user opened.
 */
export class BrowserHost {
  private readonly tabs = new Map<number, Tab>()
  private nextTabId = 0
  /** Snapshot ids count across all tabs, so an id never means two pages. */
  private snapshotSeq = 0
  /** The tab each chat shows in the panel. */
  private readonly selected = new Map<number, number>()
  /** Tabs the user closed, newest last, for reopen. */
  private readonly closed = new Map<number, ClosedTab[]>()
  /**
   * Closed tabs a reopen has popped and is still checking. A removal or forget that matches one marks it `forgotten`
   * at that moment: the reopen then neither opens it nor puts it back.
   */
  private readonly reopening = new Set<Reopen>()
  /** Bumped by every halt (one chat, or all): an agent command that awaited across a bump must not act. */
  private haltAll = 0
  private readonly haltChat = new Map<number, number>()
  private hardened = false
  private visibleId: number | null = null
  /** Chats the agent has acted in. Outlives the tab on evict and clear so a Stop still reaches the backend halt; close (a deleted chat) drops it. */
  private readonly actedIn = new Set<number>()
  /** True while clear-browsing-data or site-remove is awaited: no tab may be created or navigated meanwhile. */
  private clearing = false
  /** The site a site-remove holding the lock is removing: a page's own navigation onto it is dropped. */
  private removing: string | null = null
  /**
   * Bumped when a clear-browsing-data starts: an open or navigation that awaited across it must not act. A site-remove
   * leaves it alone: work already running goes on, and what acts during the lock meets `clearing`.
   */
  private clearGen = 0
  /** Bumped when a chat is closed (deleted), for the same reason. */
  private readonly closeGen = new Map<number, number>()
  /** Commands waiting on a lookup listen here; a halt of their chat (or of every chat) wakes them. */
  private readonly haltListeners = new Set<{ conversationId: number; wake: () => void }>()
  /** When each chat's pages last opened a tab, for the rate limit. */
  private readonly pageOpens = new Map<number, number[]>()
  private clock = 0
  private bounds: Rectangle = { x: 0, y: 0, width: 0, height: 0 }
  private readonly hostCache = new Map<string, { blocked: true; at: number }>()
  private runtime: { port: number; token: string } | null = null
  private stopped = false
  private historySaved: Promise<boolean> | null = null
  private pollTimer: ReturnType<typeof setTimeout> | null = null
  /** The one comment box (an app-owned view above the tabs) and the tab whose pick it shows right now. */
  private noteBox: AppView | null = null
  private boxTab: Tab | null = null
  private boxRect = { x: 0, y: 0, width: 0, height: 0 }
  private boxHeight = NOTE_BOX_MIN_HEIGHT
  private boxSeq = 0
  /** What the renderer last said (op 'full'): whether browser full screen is on. */
  private full = false
  /** Pick ids count across all tabs, so an id never means two picks. */
  private pickSeq = 0
  private reports: Promise<unknown> = Promise.resolve()
  private readonly now: () => number
  private readonly later: typeof setTimeout
  private readonly clear: typeof clearTimeout
  private readonly log: (...args: unknown[]) => void
  private readonly platform: string

  constructor(private readonly deps: BrowserHostDeps) {
    this.platform = deps.platform ?? process.platform
    this.now = deps.now ?? Date.now
    this.later = deps.setTimeout ?? setTimeout
    this.clear = deps.clearTimeout ?? clearTimeout
    this.log = deps.log ?? (() => {})
  }

  // ---- backend runtime report ----

  private request(method: string, route: string, data?: unknown, secret = false) {
    const headers: Record<string, string> = { 'X-Session-Token': this.deps.appToken }
    if (secret) headers['X-Gamachine-UI-Secret'] = this.deps.uiSecret
    return this.deps.http.request({ method, url: `${this.deps.baseUrl()}${route}`, data, headers, timeout: 10_000 })
  }

  private runtimeBody() {
    const running = !this.stopped && this.runtime !== null
    return { running, port: running ? this.runtime!.port : null, token: running ? this.runtime!.token : null }
  }

  /** `cleared` makes the backend drop every chat's cards and grants in the same report. */
  private report(cleared = false) {
    const data = this.runtimeBody()
    this.reports = this.reports.then(() => this.request('post', '/browser/runtime', cleared ? { ...data, cleared } : data, true))
      .catch(() => { this.log('[browser] runtime report failed') })
    return this.reports
  }

  setRuntime(runtime: { port: number; token: string } | null): void {
    this.runtime = runtime
  }

  async reconcile(): Promise<void> {
    if (this.stopped) return
    const { data: settings } = await this.request('get', '/browser/settings')
    if (this.stopped) return
    const mine = this.runtimeBody()
    const theirs = settings?.runtime
    if (!theirs || theirs.running !== mine.running || theirs.port !== mine.port || theirs.token !== mine.token) {
      await this.report()
    }
  }

  beginPolling(): void {
    if (this.stopped || this.pollTimer) return
    const poll = async () => {
      // The error object carries both secrets in its request config, so only a fixed line is logged.
      try { await this.reconcile() } catch { this.log('[browser] reconcile failed') }
      if (!this.stopped) this.pollTimer = this.later(() => { void poll() }, 5000)
    }
    this.pollTimer = this.later(() => { void poll() }, 0)
  }

  /** Reports running=false, stops polling and destroys every tab. */
  shutdown(): Promise<void> {
    if (!this.stopped) {
      this.stopped = true
      // A reopen still checking its address must not make a tab after the views are gone.
      this.forgetClosed(() => true)
      if (this.pollTimer) this.clear(this.pollTimer)
      this.pollTimer = null
      this.destroyView()
      void this.report()
    }
    // Every caller waits for the same write; one that failed is tried again by the next caller.
    if (!this.historySaved) {
      this.historySaved = Promise.resolve(this.deps.history?.flush() ?? true).then((ok) => {
        if (!ok) this.historySaved = null
        return ok
      })
    }
    return Promise.all([this.historySaved, this.reports]).then(() => undefined)
  }

  // ---- tabs and session ----

  private session(): Session {
    return this.deps.fromPartition(BROWSER_PARTITION)
  }

  private harden(session: Session): void {
    if (this.hardened) return
    this.hardened = true
    session.setPermissionRequestHandler((_wc, _permission, callback) => callback(false))
    session.setPermissionCheckHandler(() => false)
    session.on('will-download', (event, item) => {
      event.preventDefault()
      try { item.cancel() } catch { /* already gone */ }
    })
    session.webRequest.onBeforeRequest((details, callback) => {
      void this.requestBlocked(details.url, details.resourceType).then(
        (blocked) => callback({ cancel: blocked }),
        () => callback({ cancel: true }),
      )
    })
    // The pre-check resolves the name itself, so a rebinding server can still answer the browser's own
    // lookup with a private address. The connected IP is the ground truth: the first request can reach
    // that address before it is aborted here (residual risk: local services need tokens).
    session.webRequest.onResponseStarted((details) => this.checkConnectedIp(details))
    session.webRequest.onCompleted((details) => this.checkConnectedIp(details))
  }

  private checkConnectedIp(details: { url: string; ip?: string; webContentsId?: number }): void {
    const ip = (details.ip || '').replace(/^\[|\]$/g, '')
    if (!ip || !isPrivateAddress(ip)) return
    let host: string
    try { host = new URL(details.url).hostname } catch { return }
    // A literal host was already judged by the static policy, so the connection is not a rebinding.
    if (isIP(host.replace(/^\[|\]$/g, ''))) return
    this.hostCache.set(host.toLowerCase(), { blocked: true, at: this.now() })
    // A request with no page behind it (a worker) cannot be pinned to one tab, so every tab is stopped.
    const hit = [...this.tabs.values()].filter((tab) => details.webContentsId === undefined ||
      (!tab.view.webContents.isDestroyed() && tab.view.webContents.id === details.webContentsId))
    for (const tab of hit) {
      const wc = tab.view.webContents
      this.settleLoad(tab, { ok: false, error: 'private_address' })
      if (!wc.isDestroyed()) {
        wc.stop()
        wc.loadURL('about:blank').catch(() => { /* nothing left to report */ })
      }
      this.emitLive(tab.conversationId, 'private_address')
    }
  }

  /** Sub-resource gate: frames follow the navigation rule, every http(s)/ws(s) host must not resolve to a private address. */
  async requestBlocked(url: string, resourceType: string): Promise<boolean> {
    let parsed: URL
    try { parsed = new URL(url) } catch { return true }
    const frame = resourceType === 'mainFrame' || resourceType === 'subFrame'
    if (frame && !isAllowedNavigation(url)) return true
    if (['http:', 'https:', 'ws:', 'wss:'].includes(parsed.protocol)) {
      const asHttp = new URL(url)
      asHttp.protocol = parsed.protocol.startsWith('ws') ? 'http:' : parsed.protocol
      if (!isAllowedNavigation(asHttp.toString())) return true
      return this.hostBlocked(parsed.hostname)
    }
    // Inline sub-resources the page made itself (images from data:, blob: workers) carry no host.
    return frame || !['data:', 'blob:'].includes(parsed.protocol)
  }

  private async hostBlocked(hostname: string): Promise<boolean> {
    const key = hostname.toLowerCase()
    const hit = this.hostCache.get(key)
    if (hit && this.now() - hit.at < HOST_CACHE_MS) return true
    // Allowed verdicts are not cached: a name that resolved publicly a moment ago may rebind.
    const blocked = await resolvesToPrivate(key, this.deps.lookup)
    if (blocked) this.hostCache.set(key, { blocked, at: this.now() })
    return blocked
  }

  private isLive(tab: Tab): boolean {
    return !tab.view.webContents.isDestroyed()
  }

  /** The chat's live tabs in opening order. */
  private tabsOf(conversationId: number): Tab[] {
    return [...this.tabs.values()].filter((tab) => tab.conversationId === conversationId && this.isLive(tab)).sort((a, b) => a.id - b.id)
  }

  /** The chat's agent tab: the only tab an agent command can reach. */
  private agentTab(conversationId: number): Tab | null {
    return this.tabsOf(conversationId).find((tab) => tab.agent) ?? null
  }

  private selectedTab(conversationId: number): Tab | null {
    const id = this.selected.get(conversationId)
    const tab = id === undefined ? undefined : this.tabs.get(id)
    return tab && this.isLive(tab) ? tab : null
  }

  /** A tab of this chat by id (the selected one without an id); another chat's id resolves to nothing. */
  private chatTab(conversationId: number, tabId: unknown): Tab | null {
    if (tabId === undefined) return this.selectedTab(conversationId)
    const tab = positiveInt(tabId) ? this.tabs.get(tabId) : undefined
    return tab && tab.conversationId === conversationId && this.isLive(tab) ? tab : null
  }

  private ensureAgentTab(conversationId: number): Tab {
    for (const tab of [...this.tabs.values()]) {
      if (tab.conversationId !== conversationId || !tab.agent) continue
      if (this.isLive(tab)) return this.touch(tab)
      this.closeTab(tab)
    }
    return this.createTab(conversationId, true)
  }

  private createTab(conversationId: number, agent: boolean): Tab {
    // An agent command queued before a shutdown must not bring a view back.
    if (this.stopped) throw new Error('closed')
    const win = this.deps.window()
    if (!win || win.isDestroyed()) throw new Error('no_window')
    if (this.tabsOf(conversationId).length >= MAX_CHAT_TABS) throw new Error('too_many_tabs')
    if (this.tabs.size >= MAX_TABS && !this.evict()) throw new Error('too_many_tabs')
    const session = this.session()
    this.harden(session)
    const view = this.deps.createView({
      webPreferences: {
        session,
        nodeIntegration: false,
        contextIsolation: true,
        sandbox: true,
        webSecurity: true,
        webviewTag: false,
        navigateOnDragDrop: false,
        spellcheck: false,
        safeDialogs: true,
      },
    })
    let markGone = () => {}
    const gone = new Promise<void>((resolve) => { markGone = resolve })
    const tab: Tab = { id: ++this.nextTabId, conversationId, agent, view, favicon: '', snapshotId: '', loadWaiter: null,
      popupBlocked: null, userControl: false, lastGesture: null, userTouched: false, annotating: false, overlay: null, pick: null, notes: [], pinSeq: 0, frameSeq: 0, navGen: 0, waiting: 0, refreshing: false, refreshTimer: null, shows: null, faviconSeq: 0, target: null, gone, markGone, expected: [], busy: 0, lastUsed: 0 }
    this.wire(tab)
    win.contentView.addChildView(view)
    view.setBounds(this.bounds)
    this.tabs.set(tab.id, tab)
    if (!this.selectedTab(conversationId)) this.selected.set(conversationId, tab.id)
    this.layout()
    return this.touch(tab)
  }

  private touch(tab: Tab): Tab {
    tab.lastUsed = ++this.clock
    return tab
  }

  /** At the cap the least recently used hidden tab goes (never one the user controls or one mid-command); false when none may. */
  private evict(): boolean {
    let oldest: Tab | null = null
    for (const tab of this.tabs.values()) {
      if (tab.conversationId !== this.visibleId && !tab.userControl && tab.busy === 0 && (!oldest || tab.lastUsed < oldest.lastUsed)) oldest = tab
    }
    if (oldest) this.closeTab(oldest)
    return oldest !== null
  }

  private wire(tab: Tab): void {
    const wc = tab.view.webContents
    wc.setWindowOpenHandler(({ url, disposition }) => {
      if (tab.agent) tab.popupBlocked = url
      // A page-open the lock refuses does not spend the page's tab budget.
      else if ((disposition === 'foreground-tab' || disposition === 'background-tab') && !this.clearing && this.pageMayOpenTab(tab)) {
        // The slot is spent only by a tab that was created: any refusal gives it back.
        this.openUserTab(tab.conversationId, url, true, disposition === 'background-tab')
          .then((result) => { if (result.error) this.refundPageOpen(tab.conversationId) })
          .catch(() => { this.refundPageOpen(tab.conversationId); /* refused or no window */ })
      }
      return { action: 'deny' }
    })
    const refuse = (event: { url: string; isMainFrame: boolean; preventDefault(): void }) => {
      if (event.isMainFrame && this.isBeingRemoved(event.url)) {
        event.preventDefault()
        // Like a refused address: an agent command waiting on this load must not wait for its timeout.
        this.settleLoad(tab, { ok: false, error: 'clearing' })
        return
      }
      if (isAllowedNavigation(event.url)) return
      event.preventDefault()
      // A refused main-frame navigation ends as ERR_ABORTED, which waitForLoad ignores.
      if (event.isMainFrame) this.settleLoad(tab, { ok: false, error: 'url_not_allowed' })
    }
    wc.on('will-navigate', refuse)
    wc.on('will-redirect', refuse)
    wc.on('will-frame-navigate', refuse)
    wc.on('will-attach-webview', (event) => event.preventDefault())
    // Every main-frame load, the host's own and back/forward/reload included, starts here.
    wc.on('did-start-navigation', (details) => {
      if (!details.isMainFrame) return
      // History navigations the page starts itself (history.back()) skip will-navigate, so they are dropped here.
      const dropped = this.isBeingRemoved(details.url)
      tab.target = dropped ? null : details.url
      if (!dropped) return
      if (!wc.isDestroyed()) wc.stop()
      this.settleLoad(tab, { ok: false, error: 'clearing' })
    })
    wc.on('did-finish-load', () => this.settleLoad(tab, { ok: true }))
    wc.on('did-fail-load', (_event, code, description, _url, isMainFrame) => {
      // -3 is ERR_ABORTED: a new navigation replaced this one, and its own load event will follow.
      if (isMainFrame && code !== -3) {
        tab.target = null
        this.settleLoad(tab, { ok: false, error: `load_failed: ${description || code}` })
      }
    })
    // Loading ended: a navigation that did not commit by now (aborted, refused on redirect) is no longer pending.
    wc.on('did-stop-loading', () => {
      tab.target = null
      this.emitLive(tab.conversationId)
    })
    wc.on('render-process-gone', () => this.settleLoad(tab, { ok: false, error: 'page_crashed' }))
    for (const name of ['did-start-loading', 'page-title-updated', 'did-navigate', 'did-navigate-in-page'] as const) {
      wc.on(name as 'did-start-loading', () => this.emitLive(tab.conversationId))
    }
    // Annotate mode outlives a navigation: frames read before it are dropped, and the loaded page is read at once.
    wc.on('did-start-navigation', (details) => { if (details?.isMainFrame) this.navigated(tab) })
    wc.on('did-finish-load', () => this.scheduleRefresh(tab, 0))
    wc.on('did-navigate', (_event, url) => {
      tab.target = null
      if (this.tabs.get(tab.id) !== tab) return
      tab.favicon = ''
      tab.faviconSeq++
      this.deps.history?.visit(url, wc.getTitle())
    })
    wc.on('page-title-updated', (_event, title) => this.deps.history?.update(wc.getURL(), { title }))
    wc.on('page-favicon-updated', (_event, favicons) => {
      const icon = Array.isArray(favicons) ? favicons.find((href) => typeof href === 'string' && /^https?:\/\//i.test(href)) : undefined
      if (icon) void this.loadFavicon(tab, icon)
    })
    // Keys inside the page never reach the renderer, so the tab shortcuts are claimed here.
    wc.on('before-input-event', (event, input) => {
      if (!this.browserKeys(tab, event, input)) this.userInput(tab, 'before', input)
    })
    // before-input-event sees keys only; input-event also sees the mouse.
    wc.on('input-event', (_event, input) => this.userInput(tab, 'input', input))
  }

  /**
   * Esc, one rule for every view the host sees keys in (tabs, overlays, the box) and for the main window's document
   * (op 'escape', sent by the renderer only when none of its own controls used the key): the shown chat's selected tab,
   * when annotating, drops its open pick (or the click waiting on a frame), else leaves the mode; else full screen ends;
   * else nothing happens. True when it acted.
   */
  private escapeStep(): boolean {
    const tab = this.visibleId === null ? null : this.selectedTab(this.visibleId)
    if (tab?.annotating) {
      if (tab.pick || tab.waiting > 0) this.cancelPick(tab)
      else void this.setAnnotate(tab, false)
      return true
    }
    if (!this.full) return false
    // Off at once, so a second Esc before the renderer's next report passes through.
    this.full = false
    const win = this.deps.window()
    if (win && !win.isDestroyed()) {
      try { win.webContents.send(BROWSER_FULL_TOGGLE_CHANNEL, { on: false }) } catch { /* window closing */ }
    }
    return true
  }

  /**
   * Esc pressed in a native view (a tab, its overlay, the box): keys there never reach the renderer, so the host
   * claims the key when the Esc rule acts and passes it to the page otherwise.
   */
  private escape(event: { preventDefault(): void }, input: Input): boolean {
    if (!isEscape(input) || !this.escapeStep()) return false
    event.preventDefault()
    return true
  }

  /** The browser's own keys (Esc, full screen, annotate, tab shortcuts) pressed in a tab or its overlay; true when one was claimed. */
  private browserKeys(tab: Tab, event: { preventDefault(): void }, input: Input): boolean {
    if (this.escape(event, input)) return true
    if (isFullChord(input, this.platform)) {
      event.preventDefault()
      const win = this.deps.window()
      if (win && !win.isDestroyed()) {
        try { win.webContents.send(BROWSER_FULL_TOGGLE_CHANNEL, null) } catch { /* window closing */ }
      }
      return true
    }
    if (isAnnotateChord(input, this.platform)) {
      event.preventDefault()
      void this.setAnnotate(tab, !tab.annotating)
      return true
    }
    const shortcut = tabShortcut(input, this.platform)
    if (shortcut) {
      event.preventDefault()
      void this.runShortcut(tab, shortcut)
      return true
    }
    return false
  }

  private async runShortcut(tab: Tab, shortcut: TabShortcut): Promise<void> {
    const id = tab.conversationId
    try {
      if (shortcut.action === 'select') {
        const tabs = this.tabsOf(id)
        const target = shortcut.index === 9 ? tabs[tabs.length - 1] : tabs[shortcut.index - 1]
        if (target) {
          target.userTouched = true
          this.selectTab(target)
        }
      } else if (shortcut.action === 'new') await this.openUserTab(id)
      else if (shortcut.action === 'close') this.closeByUser(tab)
      else await this.reopenTab(id)
    } catch {
      this.log('[browser] tab shortcut failed')
    }
  }

  /**
   * The user sent a message in this chat: control goes back to the agent, and the tab selected right now becomes
   * the chat's agent tab (the old one turns into a user tab; a chat without an agent tab yet gets this one).
   * The agent stays where it is while a command still runs on it.
   */
  private release(conversationId: number): void {
    for (const tab of this.tabsOf(conversationId)) tab.userTouched = false
    const agent = this.agentTab(conversationId)
    if (agent) this.setUserControl(agent, false)
    const next = this.selectedTab(conversationId)
    if (!next || next === agent || (agent?.busy ?? 0) > 0) return
    this.moveAgent(agent, next)
    this.emitLive(conversationId)
  }

  /** `next` becomes the chat's single agent tab; the old one turns into a user tab. */
  private moveAgent(old: Tab | null, next: Tab): void {
    if (old) old.agent = false
    next.agent = true
    next.expected = []
  }

  /**
   * A key or mouse event inside a tab. It counts as the user's unless it is one the host sent itself; the
   * user's input lets that page open a tab, and on the chat's agent tab it also takes the tab over.
   */
  private userInput(tab: Tab, channel: ExpectedEcho['channel'], input: { type?: string; key?: string; keyCode?: string }): void {
    const type = input.type ?? ''
    if (type !== 'keyDown' && type !== 'rawKeyDown' && type !== 'mouseDown') return
    const now = this.now()
    tab.expected = tab.expected.filter((echo) => echo.until > now)
    const name = input.key ?? input.keyCode
    const at = tab.expected.findIndex((echo) => echo.channel === channel && echo.type === type &&
      (name === undefined || echo.keys.length === 0 || echo.keys.includes(name)))
    if (at >= 0) {
      tab.expected.splice(at, 1)
      return
    }
    tab.lastGesture = now
    tab.userTouched = true
    if (tab.agent) this.setUserControl(tab, true)
  }

  /** A page opens a tab only just after its own user input, while its chat is on screen, and only a few per window. */
  private pageMayOpenTab(tab: Tab): boolean {
    const now = this.now()
    if (tab.lastGesture === null || now - tab.lastGesture > GESTURE_MS || tab.conversationId !== this.visibleId) return false
    const recent = (this.pageOpens.get(tab.conversationId) ?? []).filter((at) => now - at < PAGE_TAB_WINDOW_MS)
    if (recent.length >= MAX_PAGE_TABS) return false
    this.pageOpens.set(tab.conversationId, [...recent, now])
    return true
  }

  private refundPageOpen(conversationId: number): void {
    const opens = this.pageOpens.get(conversationId)
    if (opens) this.pageOpens.set(conversationId, opens.slice(0, -1))
  }

  private setUserControl(tab: Tab, on: boolean): void {
    if (tab.userControl === on) return
    tab.userControl = on
    this.emitLive(tab.conversationId)
  }

  private closeTab(tab: Tab): void {
    // The mode, the pick and the overlay go without a live event of their own (the close sends one); so do the notes.
    tab.annotating = false
    this.endAnnotate(tab)
    tab.notes = []
    if (this.tabs.get(tab.id) === tab) {
      this.tabs.delete(tab.id)
      if (this.selected.get(tab.conversationId) === tab.id) this.reselect(tab)
    }
    this.settleLoad(tab, { ok: false, error: 'halted' })
    tab.markGone()
    const win = this.deps.window()
    try { if (win && !win.isDestroyed()) win.contentView.removeChildView(tab.view) } catch { /* window closing */ }
    try { if (!tab.view.webContents.isDestroyed()) tab.view.webContents.close() } catch { /* already closed */ }
    this.layout()
    this.emitLive(tab.conversationId)
  }

  /** The closed tab's neighbour on the left, else on the right, takes over the selection. */
  private reselect(gone: Tab): void {
    const rest = [...this.tabs.values()].filter((tab) => tab.conversationId === gone.conversationId).sort((a, b) => a.id - b.id)
    const next = [...rest].reverse().find((tab) => tab.id < gone.id) ?? rest.find((tab) => tab.id > gone.id)
    if (next) this.selected.set(gone.conversationId, next.id)
    else this.selected.delete(gone.conversationId)
  }

  /** A close the user asked for: the page can be reopened. */
  private closeByUser(tab: Tab): void {
    const wc = tab.view.webContents
    const url = wc.isDestroyed() ? '' : wc.getURL()
    if (/^https?:\/\//i.test(url)) {
      const stack = this.closed.get(tab.conversationId) ?? []
      stack.push({ url, title: wc.getTitle(), closedAt: this.now() })
      this.closed.set(tab.conversationId, stack.slice(-MAX_CLOSED_TABS))
    }
    // Work running or queued for the agent's tab ends with it instead of recreating the tab.
    if (tab.agent) this.stopLocal(tab.conversationId)
    this.closeTab(tab)
  }

  /** Closes every tab; the next command opens fresh ones. Called when the main window goes away. */
  destroyView(): void {
    for (const tab of [...this.tabs.values()]) this.closeTab(tab)
    this.selected.clear()
    this.destroyBox()
    // The window's renderer went with it; the next one reports its own state.
    this.full = false
  }

  private selectTab(tab: Tab): void {
    this.selected.set(tab.conversationId, tab.id)
    this.touch(tab)
    this.layout()
    this.emitLive(tab.conversationId)
  }

  private show(conversationId: number | null): void {
    this.visibleId = conversationId
    this.layout()
  }

  /** Only the shown chat's selected tab is visible. */
  private layout(): void {
    const shown = this.visibleId === null ? undefined : this.selected.get(this.visibleId)
    for (const tab of this.tabs.values()) {
      const visible = tab.id === shown
      if (visible) tab.view.setBounds(this.bounds)
      tab.view.setVisible(visible)
    }
    this.syncAnnotate()
  }

  // ---- annotate mode ----

  private isShown(tab: Tab): boolean {
    return this.visibleId === tab.conversationId && this.selected.get(tab.conversationId) === tab.id
  }

  /** Annotating tabs the user can see right now (at most one): only there do the overlay, the box and the refresh run. */
  private annotatingVisible(): Tab[] {
    return [...this.tabs.values()].filter((tab) => tab.annotating && this.isLive(tab) && this.isShown(tab))
  }

  /**
   * After the panel, the selection or the bounds changed: every overlay sits over its tab and is visible only with it;
   * the shown annotating tab gets its open pick's box back, or its refresh; a hidden one keeps its pick, unrefreshed.
   */
  private syncAnnotate(): void {
    for (const tab of this.tabs.values()) {
      if (tab.overlay) {
        tab.overlay.view.setBounds(this.bounds)
        tab.overlay.view.setVisible(this.isShown(tab))
      }
      if (!this.isShown(tab)) this.stopRefresh(tab)
    }
    if (this.boxTab && !this.isShown(this.boxTab)) this.hideBox()
    for (const tab of this.annotatingVisible()) {
      if (tab.pick) {
        if (this.boxTab !== tab) void this.showBox(tab, tab.pick)
      } else this.scheduleRefresh(tab, 0)
    }
  }

  /**
   * Ends the tab's annotate mode: a frame read on its way is dropped, the open pick and its draft go, so do the box and
   * the overlay. The saved notes stay with the tab.
   */
  private endAnnotate(tab: Tab): void {
    tab.frameSeq++
    tab.pick = null
    if (this.boxTab === tab) this.hideBox()
    this.stopRefresh(tab)
    this.destroyOverlay(tab)
    if (!tab.annotating) return
    tab.annotating = false
    this.focusTab(tab)
    this.emitLive(tab.conversationId)
  }

  /** Turns the tab's annotate mode on or off; the error reply when it cannot. */
  private async setAnnotate(tab: Tab, on: boolean): Promise<{ error: string } | null> {
    if (!on) {
      this.endAnnotate(tab)
      return null
    }
    const wc = tab.view.webContents
    if (!/^https?:\/\//i.test(wc.getURL()) || wc.isLoading()) return { error: 'no_page' }
    if (!tab.annotating) {
      if (!this.ensureOverlay(tab)) return { error: 'annotate_failed' }
      tab.annotating = true
      if (this.isShown(tab)) this.focusOverlay(tab)
    }
    // The user is working in this page: the agent may not move to it until their next message.
    this.touch(tab)
    tab.userTouched = true
    this.emitLive(tab.conversationId)
    this.syncAnnotate()
    return null
  }

  /** A main-frame navigation started: frames read before it are void and the overlay outlines nothing until the next one. */
  private navigated(tab: Tab): void {
    tab.navGen++
    // A frozen picture (an open pick's, a capture's) stays as it is: the capture puts back what fits once it is done.
    if (!tab.shows || tab.shows.by === 'live') this.setLook(tab, null)
  }

  // ---- frames ----

  /**
   * Reads a frame of the tab's page: frame(), and with `withImage` a capture of the tab and then mark(), which must agree
   * with frame() (same document, scroll and viewport) or the three are read again, FRAME_TRIES times in all. This is the one
   * rule for page replies: the result is null when another read started since (frameSeq), a navigation started (navGen),
   * the tab went, the page failed or the tries ran out. A cancel and the mode going off bump frameSeq too.
   */
  private async readFrame(tab: Tab, withImage: boolean): Promise<FrameReply | null> {
    const seq = ++tab.frameSeq
    const navGen = tab.navGen
    const current = () => tab.frameSeq === seq && tab.navGen === navGen && this.tabs.get(tab.id) === tab && this.isLive(tab)
    const wc = tab.view.webContents
    const accept = (frame: PageFrame, image: string): FrameReply => ({ ...frame, image, url: wc.getURL(), title: wc.getTitle(), navGen })
    try {
      for (let attempt = 0; attempt < FRAME_TRIES; attempt++) {
        const frame = toFrame(await this.page(tab, 'frame', {}))
        if (!current() || !frame) return null
        if (!withImage) return accept(frame, '')
        const image = await this.captureImage(wc, this.isShown(tab))
        if (!current() || !image) return null
        const mark = await this.page(tab, 'mark', {})
        if (!current()) return null
        if (samePlace(mark, frame)) return accept(frame, image)
      }
    } catch {
      // The page is gone or did not answer.
    }
    return null
  }

  /** While the tab is annotating, shown, with no comment open, no capture on the overlay and no click or capture waiting on a frame. */
  private refreshable(tab: Tab): boolean {
    return tab.annotating && !!tab.overlay && !tab.pick && tab.shows?.by !== 'capture' && tab.waiting === 0 && !this.stopped && this.isLive(tab) &&
      this.isShown(tab)
  }

  /** The tab's next refresh in `ms` (it replaces one already due); nothing when the tab is not refreshable. */
  private scheduleRefresh(tab: Tab, ms = FRAME_REFRESH_MS): void {
    this.stopRefresh(tab)
    if (!this.refreshable(tab)) return
    tab.refreshTimer = this.later(() => {
      tab.refreshTimer = null
      void this.refresh(tab)
    }, ms)
  }

  private stopRefresh(tab: Tab): void {
    if (tab.refreshTimer) this.clear(tab.refreshTimer)
    tab.refreshTimer = null
  }

  /** One frame for the overlay's hover and pins, at most one in flight; the next follows FRAME_REFRESH_MS after it. */
  private async refresh(tab: Tab): Promise<void> {
    if (tab.refreshing || !this.refreshable(tab)) return
    tab.refreshing = true
    let frame: FrameReply | null = null
    try {
      frame = await this.readFrame(tab, false)
    } finally {
      tab.refreshing = false
    }
    if (!this.refreshable(tab)) return
    // A failed frame shows nothing (no hover, no pins) until the next good one.
    this.setLook(tab, frame && { by: 'live', frame, rect: null })
    if (!tab.refreshTimer) this.scheduleRefresh(tab)
  }

  /**
   * The one way the overlay's look changes (refresh, a pick opening or closing, a capture, a navigation): `tab.shows` is
   * set and drawn: its boxes, its pins, and its picture only when that changed. Settles once the overlay drew it all
   * (the picture decoded).
   */
  private setLook(tab: Tab, look: Look | null): Promise<unknown> {
    const frozen = (one: Look | null | undefined) => one && one.by !== 'live' ? one : null
    const was = frozen(tab.shows)
    const now = frozen(look)
    tab.shows = look
    const boxes = look ? look.frame.elements.map(({ box, id, kind }) => ({ ...box, id, kind })) : []
    const drawn = [this.overlayCall(tab, `window.__boxes(${JSON.stringify(boxes)},${JSON.stringify(look ? look.frame.scroll : [0, 0])})`), this.pushPins(tab)]
    if (was?.frame !== now?.frame || was?.rect !== now?.rect) {
      drawn.push(this.overlayCall(tab, `window.__freeze(${JSON.stringify(now ? now.frame.image : null)},${JSON.stringify(now ? now.rect : null)})`))
    }
    return Promise.all(drawn)
  }

  /** The overlay's pins: the tab's notes made in the document it shows, where they are at its scroll (viewport CSS px). */
  private pushPins(tab: Tab): Promise<unknown> {
    const look = tab.shows
    const pins = look ? tab.notes.filter((one) => one.docId === look.frame.docId)
      .map((one) => ({ n: one.note.n, x: one.docRect.x - look.frame.scroll[0], y: one.docRect.y - look.frame.scroll[1] })) : []
    return this.overlayCall(tab, `window.__pins(${JSON.stringify(pins)})`)
  }

  // ---- overlay ----

  /** A self-contained page in a hardened view of its own (no preload, no node, sandboxed, transparent, no navigation). */
  private createAppView(html: string, onMessage: (message: Record<string, unknown>) => void,
    onKey: (event: { preventDefault(): void }, input: Input) => void): AppView {
    const view = this.deps.createView({
      webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true, webviewTag: false, spellcheck: false, safeDialogs: true },
    })
    const wc = view.webContents
    try { view.setBackgroundColor('#00000000') } catch { /* older Electron */ }
    wc.setWindowOpenHandler(() => ({ action: 'deny' }))
    wc.on('will-navigate', (event) => event.preventDefault())
    wc.on('page-title-updated', (_event, title) => {
      const message = titleMessage(title)
      if (message) onMessage(message)
    })
    // Keys inside these views never reach the renderer either: the browser's own keys are claimed here like in a tab.
    wc.on('before-input-event', (event, input) => onKey(event, input))
    // The app's CSP (script-src 'self', csp.ts) also reaches these data: pages and refuses their inline <script>
    // (measured in the packaged app, 31 Oct: the overlay drew its bar but no script ran). The markup loads without
    // it and the script runs through executeJavaScript, which the page's CSP does not govern.
    const at = html.lastIndexOf('<script>')
    const end = html.indexOf('</script>', at)
    const script = at < 0 || end < 0 ? '' : html.slice(at + '<script>'.length, end)
    const markup = at < 0 || end < 0 ? html : html.slice(0, at) + html.slice(end + '</script>'.length)
    const ready = Promise.resolve(wc.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(markup)}`))
      .then(() => (script ? wc.executeJavaScript(script) : undefined))
      .then(() => undefined, () => undefined)
    return { view, ready }
  }

  /** The tab's overlay, made and placed over it (visible only while the tab is shown); null when there is no window. */
  private ensureOverlay(tab: Tab): AppView | null {
    if (tab.overlay) return tab.overlay
    const win = this.deps.window()
    if (!win || win.isDestroyed()) return null
    try {
      // The overlay has the focus while the mode is on: every browser key works there as in the tab.
      const overlay = this.createAppView(OVERLAY_HTML, (message) => this.onOverlayMessage(tab, message), (event, input) => { this.browserKeys(tab, event, input) })
      // Added after the tab's own view, so it sits above it; the box is re-added above both whenever it shows.
      win.contentView.addChildView(overlay.view)
      overlay.view.setBounds(this.bounds)
      overlay.view.setVisible(this.isShown(tab))
      tab.overlay = overlay
      return overlay
    } catch {
      this.log('[browser] annotate overlay failed')
      return null
    }
  }

  private destroyOverlay(tab: Tab): void {
    const overlay = tab.overlay
    if (!overlay) return
    tab.overlay = null
    tab.shows = null
    const win = this.deps.window()
    try { if (win && !win.isDestroyed()) win.contentView.removeChildView(overlay.view) } catch { /* window closing */ }
    try { if (!overlay.view.webContents.isDestroyed()) overlay.view.webContents.close() } catch { /* already closed */ }
  }

  /** Runs one of the overlay's functions once it has loaded; calls keep their order. */
  private overlayCall(tab: Tab, code: string): Promise<unknown> {
    const overlay = tab.overlay
    if (!overlay) return Promise.resolve()
    return overlay.ready.then(() => overlay.view.webContents.executeJavaScript(code)).catch(() => { /* overlay closing */ })
  }

  private focusOverlay(tab: Tab): void {
    if (!tab.overlay) { this.focusTab(tab); return }
    try { tab.overlay.view.webContents.focus() } catch { /* destroyed */ }
  }

  /** What the overlay reported: a click, a drag or a wheel (Esc is the host's own, escape()). */
  private onOverlayMessage(tab: Tab, message: Record<string, unknown>): void {
    if (!tab.annotating || this.tabs.get(tab.id) !== tab) return
    if (message.t === 'pick' || message.t === 'area') void this.pickAt(tab, message)
    else if (message.t === 'wheel') this.forwardWheel(tab, message)
  }

  /** The page scrolls under the overlay as if the wheel had been turned over it; its boxes are read again just after. */
  private forwardWheel(tab: Tab, message: Record<string, unknown>): void {
    const { x, y, dx, dy } = message
    // While a comment is open the picture is frozen: the page under it does not move.
    if (tab.pick || ![x, y, dx, dy].every((v) => typeof v === 'number' && Number.isFinite(v))) return
    try {
      // A DOM wheel's positive delta scrolls down; Chromium's wheel event counts the other way.
      tab.view.webContents.sendInputEvent({ type: 'mouseWheel', x: Math.round(x as number), y: Math.round(y as number), deltaX: -(dx as number), deltaY: -(dy as number), canScroll: true })
    } catch {
      return
    }
    this.scheduleRefresh(tab, WHEEL_REFRESH_MS)
  }

  /**
   * A click or a drag in the overlay. A pick with a draft is the user's: nothing replaces it. A pick with an empty draft
   * is replaced from the same frozen frame (no page call). Otherwise a fresh frame with its picture is read, and the pick
   * opens only from that frame, if it is still current when it arrives.
   */
  private async pickAt(tab: Tab, message: Record<string, unknown>): Promise<void> {
    const open = tab.pick
    if (open && open.draft !== '') return
    let frame: FrameReply | null = open ? open.frame : null
    if (!open) {
      this.stopRefresh(tab)
      tab.waiting++
      try {
        frame = await this.readFrame(tab, true)
      } finally {
        tab.waiting--
      }
    }
    const target = frame && frameTarget(frame, message)
    if (!frame || !target) {
      if (!tab.pick) this.scheduleRefresh(tab, 0)
      return
    }
    const id = `n${++this.pickSeq}`
    const pick: HostPick = { id, frame, data: target.data.kind === 'area' ? { ...target.data, id } : target.data, rect: target.rect, draft: '', error: '' }
    tab.pick = pick
    this.setLook(tab, lookOf(pick))
    void this.showBox(tab, pick)
  }

  /** The open pick and its draft go (Esc, escape()); a frame read for a click on its way is dropped. */
  private cancelPick(tab: Tab): void {
    tab.frameSeq++
    this.closePick(tab)
  }

  /**
   * After a cancel or a save: the pick closes, its box goes if it shows it, and the overlay goes back to the live page,
   * outlined from the pick's frame while that is still of the page the tab shows.
   */
  private closePick(tab: Tab): void {
    const pick = tab.pick
    tab.pick = null
    if (this.boxTab === tab) this.hideBox()
    if (pick) this.setLook(tab, pick.frame.navGen === tab.navGen ? { by: 'live', frame: pick.frame, rect: null } : null)
    if (this.isShown(tab)) this.focusOverlay(tab)
    this.scheduleRefresh(tab, 0)
  }

  // ---- comment box ----

  private ensureBox(): AppView {
    if (!this.noteBox) {
      this.noteBox = this.createAppView(NOTE_BOX_HTML, (message) => this.onBoxMessage(message), (event, input) => {
        if (this.escape(event, input) || !isAnnotateChord(input, this.platform)) return
        event.preventDefault()
        const tab = this.boxTab
        if (tab) void this.setAnnotate(tab, !tab.annotating)
      })
    }
    return this.noteBox
  }

  /**
   * Opens the box next to the pick's target with its draft (and its last error). Only for the shown annotating tab: a
   * pick opened on a hidden tab leaves the box alone, and gets it when the tab is shown (syncAnnotate).
   */
  private async showBox(tab: Tab, pick: HostPick): Promise<void> {
    const win = this.deps.window()
    if (!win || win.isDestroyed() || tab.pick !== pick || !this.annotatingVisible().includes(tab)) return
    // The box of this tab's earlier pick goes. A target outside the panel has nothing to sit next to: the box stays hidden with its draft.
    this.hideBox()
    if (!targetShown(pick.rect, this.bounds) || !noteBoxWidth(this.bounds)) return
    const token = ++this.boxSeq
    let box: AppView
    try { box = this.ensureBox() } catch { return }
    this.boxTab = tab
    this.boxRect = pick.rect
    this.boxHeight = NOTE_BOX_MIN_HEIGHT
    await box.ready
    if (token !== this.boxSeq || this.boxTab !== tab || tab.pick !== pick || this.stopped) return
    try {
      this.placeBox()
      // Re-added each time so the box sits above the overlay and any tab view added since. It stays hidden until it has
      // measured itself at its real width, so it never shows at a wrong height first.
      win.contentView.removeChildView(box.view)
      box.view.setVisible(false)
      win.contentView.addChildView(box.view)
      const wc = box.view.webContents
      await wc.executeJavaScript(`window.__width(${noteBoxWidth(this.bounds)})`)
      let height: unknown = await wc.executeJavaScript(`window.__set(${JSON.stringify(pick.draft)},${JSON.stringify(pick.id)})`)
      if (pick.error) {
        const withError: unknown = await wc.executeJavaScript(errorCall(pick))
        if (typeof withError === 'number') height = withError
      }
      if (token !== this.boxSeq) return
      if (typeof height === 'number' && Number.isFinite(height)) {
        this.setBoxHeight(height)
      }
      box.view.setVisible(true)
      wc.focus()
    } catch {
      this.log('[browser] note box failed')
    }
  }

  private setBoxHeight(height: number): void {
    this.boxHeight = Math.max(NOTE_BOX_MIN_HEIGHT, Math.min(Math.round(height), NOTE_BOX_MAX_HEIGHT))
    this.placeBox()
  }

  private placeBox(): void {
    if (!this.noteBox) return
    const width = noteBoxWidth(this.bounds)
    if (!width) { this.hideBox(); return }
    this.noteBox.view.setBounds(placeNoteBox(this.boxRect, this.bounds, { width, height: this.boxHeight }))
  }

  /** The box goes; the pick and its draft stay on the tab. */
  private hideBox(): void {
    this.boxSeq++
    if (!this.boxTab) return
    this.boxTab = null
    const win = this.deps.window()
    try {
      this.noteBox?.view.setVisible(false)
      if (win && !win.isDestroyed() && this.noteBox) win.contentView.removeChildView(this.noteBox.view)
    } catch { /* window closing */ }
  }

  private destroyBox(): void {
    this.hideBox()
    const box = this.noteBox
    this.noteBox = null
    try { if (box && !box.view.webContents.isDestroyed()) box.view.webContents.close() } catch { /* already closed */ }
  }

  /**
   * What the box reported: the draft so far, its height or a submit (Esc is the host's own, escape()). It goes to the tab whose open pick has
   * the message's pick id (p), shown or not: a draft typed just before the box was hidden still reaches its pick. A
   * message for a pick that is no longer open changes nothing.
   */
  private onBoxMessage(message: Record<string, unknown>): void {
    const tab = [...this.tabs.values()].find((one) => one.pick !== null && one.pick.id === message.p)
    if (!tab || !tab.pick) return
    const text = typeof message.text === 'string' ? message.text.slice(0, NOTE_COMMENT_CHARS) : ''
    if (message.t === 'draft' || message.t === 'size') {
      // Typing is a draft (and clears the last error); a resize reports only the height, of the box showing this pick.
      if (message.t === 'draft') {
        tab.pick.draft = text
        tab.pick.error = ''
      }
      if (this.boxTab === tab && typeof message.h === 'number' && Number.isFinite(message.h)) {
        this.setBoxHeight(message.h)
      }
    } else if (message.t === 'submit') this.savePick(tab, text)
  }

  private focusTab(tab: Tab): void {
    try { tab.view.webContents.focus() } catch { /* destroyed */ }
  }

  /**
   * Saving is all or nothing and never touches the page: the note gets the tab's next number and is stored with the
   * tab, and every note of the tab goes to the renderer. If that cannot be delivered the note is taken back and the box
   * stays open with the draft and the reason. On success the pick closes and the overlay goes back to the live page.
   */
  private savePick(tab: Tab, text: string): void {
    const pick = tab.pick
    const comment = text.trim()
    if (!pick || !comment) return
    const failed = (error: string) => {
      pick.error = error
      if (this.boxTab === tab) void this.noteBox?.view.webContents.executeJavaScript(errorCall(pick)).catch(() => { /* box closing */ })
    }
    if (tab.notes.length >= NOTE_MAX_NOTES) return failed(`En fazla ${NOTE_MAX_NOTES} not`)
    const n = tab.pinSeq + 1
    const { frame } = pick
    const saved = { note: { ...pick.data, id: pick.id, comment, n, rect: pick.rect }, docId: frame.docId,
      docRect: { ...pick.rect, x: pick.rect.x + frame.scroll[0], y: pick.rect.y + frame.scroll[1] }, url: frame.url, title: frame.title }
    tab.notes.push(saved)
    // Only the new note: the renderer adds chips per event, so resending earlier notes would duplicate a note
    // that already left with a queued message.
    if (!this.emitNotes(tab, [saved.note], frame)) {
      tab.notes = tab.notes.filter((one) => one !== saved)
      return failed(NOTE_SAVE_FAILED)
    }
    tab.pinSeq = n
    this.closePick(tab)
  }

  /** Sends notes to the renderer as made on the page `from` names (the frame they were picked from); false when nothing could receive them. */
  private emitNotes(tab: Tab, notes: unknown[], from: { url: string; title: string; viewport: { width: number; height: number } }): boolean {
    const win = this.deps.window()
    if (!win || win.isDestroyed()) return false
    try {
      win.webContents.send(BROWSER_NOTES_CHANNEL, { conversationId: tab.conversationId, tabId: tab.id, url: from.url, title: from.title, notes, viewport: from.viewport })
      return true
    } catch {
      return false
    }
  }

  /**
   * One capture of a view as a JPEG data URL, the longer side at most MAX_CAPTURE_SIDE; '' when it is empty, fails or
   * hangs. A view that is not on screen is captured where it is (stayHidden), never brought forward.
   */
  private async captureImage(wc: WebContents, shown: boolean): Promise<string> {
    let timer: ReturnType<typeof setTimeout> | undefined
    const timeout = new Promise<never>((_resolve, reject) => { timer = this.later(() => reject(new Error('capture_timeout')), CAPTURE_TIMEOUT_MS) })
    try {
      const image = await Promise.race([shown ? wc.capturePage() : wc.capturePage(undefined, { stayHidden: true }), timeout])
      if (image.isEmpty()) return ''
      const { width, height } = image.getSize()
      const longer = Math.max(width, height)
      const scaled = longer > MAX_CAPTURE_SIDE
        ? image.resize({ width: Math.max(1, Math.round(width * MAX_CAPTURE_SIDE / longer)), height: Math.max(1, Math.round(height * MAX_CAPTURE_SIDE / longer)), quality: 'good' })
        : image
      const jpeg = scaled.toJPEG(CAPTURE_JPEG_QUALITY)
      return jpeg.length > 0 ? `data:image/jpeg;base64,${jpeg.toString('base64')}` : ''
    } catch {
      return ''
    } finally {
      this.clear(timer)
    }
  }

  /**
   * A picture of a tab with its pins for a message that carries its notes: a fresh frame is shown on the tab's overlay
   * with its pins, and the overlay is captured (the comment box is a separate view, so it is never in it). The overlay
   * then goes back (restoreOverlay). Empty when the tab is gone, any step fails, or the shown look was replaced meanwhile.
   */
  private async captureTab(tab: Tab): Promise<string> {
    if (!this.isLive(tab)) return ''
    const overlay = this.ensureOverlay(tab)
    if (!overlay) return ''
    this.stopRefresh(tab)
    const before = tab.shows
    const navGen = tab.navGen
    tab.waiting++
    let frame: FrameReply | null = null
    try {
      frame = await this.readFrame(tab, true)
    } finally {
      tab.waiting--
    }
    if (!frame || tab.overlay !== overlay) { this.restoreOverlay(tab, before, navGen); return '' }
    // The capture owns the look it sets: once anything else replaced it (a submit, a click, another capture), that
    // replacer owns the overlay, so the capture returns nothing and restores nothing.
    const look: Look = { by: 'capture', frame, rect: null }
    await this.setLook(tab, look)
    if (tab.shows !== look) return ''
    // Visibility can change while the frame was read: it is read right before the capture.
    const image = await this.captureImage(overlay.view.webContents, this.isShown(tab))
    if (tab.shows !== look) return ''
    this.restoreOverlay(tab, before, navGen)
    return image
  }

  /**
   * After a capture the overlay goes with the mode off. Otherwise it shows the open pick's frozen picture, or the live
   * look it had before the capture if the tab did not navigate since (`navGen` is the tab's at the start); after a
   * navigation, nothing until the next refresh draws the new page.
   */
  private restoreOverlay(tab: Tab, before: Look | null, navGen: number): void {
    if (!tab.annotating) { this.destroyOverlay(tab); return }
    this.setLook(tab, tab.pick ? lookOf(tab.pick) : before?.by === 'live' && tab.navGen === navGen ? before : null)
    this.scheduleRefresh(tab, 0)
  }

  /**
   * A new tab of the user's own, selected; with an address it is checked like every navigation. A tab a page
   * opened (`fromPage`) leaves the panel as it is: the chat is already on screen, and nothing may bring another forward.
   */
  private async openUserTab(conversationId: number, url?: unknown, fromPage = false, background = false, reopen?: Reopen): Promise<Record<string, unknown>> {
    const epoch = this.lifecycleEpoch(conversationId)
    const refused = this.staleReply(conversationId, epoch)
    if (refused) return refused
    if (url !== undefined && !(await this.navigationAllowed(url))) return { error: 'url_not_allowed' }
    // A clear, a site-remove, the chat's deletion, a shutdown or a forget of the reopened page can happen during the lookup.
    if (reopen?.forgotten && !this.stopped) return { error: 'nothing_to_reopen' }
    const stale = this.staleReply(conversationId, epoch)
    if (stale) return stale
    let tab: Tab
    try { tab = this.createTab(conversationId, false) } catch (error) { return { error: error instanceof Error ? error.message : 'no_window' } }
    if (!background) this.selected.set(conversationId, tab.id)
    if (fromPage) this.layout()
    else this.show(conversationId)
    if (str(url)) this.load(tab, url)
    this.emitLive(conversationId)
    return { ...this.livePayload(conversationId) }
  }

  /** The host's own navigation of a tab; the address is its target until it commits. */
  private load(tab: Tab, url: string): void {
    tab.target = url
    tab.view.webContents.loadURL(url).catch(() => { /* reported by did-fail-load */ })
  }

  /** The committed address and the one still loading. */
  private tabUrls(tab: Tab): string[] {
    const wc = tab.view.webContents
    return [wc.isDestroyed() ? '' : wc.getURL(), tab.target ?? '']
  }

  /** Whether `url` is on the site a site-remove holding the lock is removing. */
  private isBeingRemoved(url: string): boolean {
    return this.removing !== null && siteOrigin(url, this.removing) !== ''
  }

  /** The refusal for work that began before a clear started or its chat was closed, or null when it may go on. */
  private staleReply(conversationId: number, epoch: number): { error: string } | null {
    if (this.stopped) return { error: 'closed' }
    if (this.clearing) return { error: 'clearing' }
    return this.lifecycleEpoch(conversationId) === epoch ? null : { error: 'chat_closed' }
  }

  /**
   * The clear lock for clear-browsing-data and site-remove, taken before `work`'s first await: while it is held every
   * point that would open or load refuses with 'clearing'. Refused when another holder has it.
   */
  private async locked<T>(work: () => Promise<T>): Promise<T | { error: 'clearing' }> {
    if (this.clearing) return { error: 'clearing' }
    this.clearing = true
    try {
      return await work()
    } finally {
      this.clearing = false
    }
  }

  /** Drops closed tabs from every chat's reopen stack and marks the reopens in flight that it matches. */
  private forgetClosed(drop: (tab: ClosedTab, conversationId: number) => boolean): void {
    for (const [id, stack] of this.closed) this.closed.set(id, stack.filter((tab) => !drop(tab, id)))
    for (const entry of this.reopening) entry.forgotten ||= drop(entry.tab, entry.conversationId)
  }

  /**
   * Logs the agent browser out of `site` and its subdomains; the caller holds the clear lock, so nothing opens or
   * loads meanwhile. The site's tabs close and its closed and reopening pages are forgotten first, their exact
   * origins recorded; then cookies and storage go. A pass repeats while a cookie of the site is still there.
   * `accepted` is set once the removal passed its checks: from then on the login may be gone, whatever the outcome.
   */
  private async removeSite(site: string, accepted: { value: boolean }): Promise<Record<string, unknown>> {
    const ofSite = (list: Cookie[]) => list.filter((cookie) => inSite(siteOf(cookie.domain ?? ''), site))
    const origins = new Set<string>()
    // Pages on other ports or subdomains: the exact origin, port included.
    const claim = (url: string) => {
      const origin = siteOrigin(url, site)
      if (origin) origins.add(origin)
      return origin !== ''
    }
    // Run at lock start and at acceptance: a chat deleted or a history entry dropped during the read takes its record with it.
    const snapshot = () => {
      for (const tab of this.tabs.values()) if (this.isLive(tab)) this.tabUrls(tab).forEach(claim)
      for (const stack of this.closed.values()) for (const tab of stack) claim(tab.url)
      for (const entry of this.reopening) claim(entry.tab.url)
      for (const entry of this.deps.history?.list() ?? []) claim(entry.url)
    }
    snapshot()
    const session = this.session()
    const all = await session.cookies.get({})
    // One removal takes one listed site: a parent of several (github.io over alice. and bob.github.io) is refused.
    if ([...siteRows(all).keys()].filter((row) => inSite(row, site)).length > 1) return { error: 'bad_args' }
    accepted.value = true
    for (const host of [site, `www.${site}`]) for (const scheme of ['https', 'http']) origins.add(`${scheme}://${host}`)
    // Open pages of the site go first: a live page would write the login back after the cookies are gone.
    snapshot()
    const doomed = [...this.tabs.values()].filter((tab) => this.isLive(tab) && this.tabUrls(tab).filter(claim).length > 0)
    for (const tab of doomed) this.closeTab(tab)
    // A reopen of the site still in flight, forgotten here, is neither opened nor put back.
    this.forgetClosed((tab) => claim(tab.url))
    let removed = 0
    let failed = 0
    let cookies = ofSite(all)
    for (let pass = 1; ; pass++) {
      for (const cookie of cookies) {
        const domain = siteOf(cookie.domain ?? '')
        // Storage of a subdomain (app.site) is not covered by the origins of site itself.
        for (const scheme of ['https', 'http']) origins.add(`${scheme}://${domain}`)
        try {
          await session.cookies.remove(`${cookie.secure ? 'https' : 'http'}://${domain}${cookie.path ?? '/'}`, cookie.name)
          removed++
        } catch { failed++ }
      }
      for (const origin of origins) {
        try { await session.clearStorageData({ origin, storages: STORAGES as any }) } catch { failed++ }
      }
      cookies = ofSite(await session.cookies.get({}))
      if (cookies.length === 0 || failed > 0) break
      if (pass === MAX_SITE_REMOVE_PASSES) { failed++; break }
    }
    return failed > 0 ? { ok: false, error: 'site_remove_partial', removed, failed } : { ok: true, removed }
  }

  /**
   * The tab goes back on the stack when it could not be reopened (too many tabs, address refused, a site-remove of
   * another site meanwhile), unless a removal or forget marked it; a marked one is not opened either.
   */
  private async reopenTab(conversationId: number): Promise<Record<string, unknown>> {
    // A refusal by the lock comes from openUserTab, and the popped page goes back below.
    if (this.stopped) return { error: 'closed' }
    const last = this.closed.get(conversationId)?.pop()
    if (!last) return { error: 'nothing_to_reopen' }
    const pending: Reopen = { tab: last, conversationId, forgotten: false }
    this.reopening.add(pending)
    let result: Record<string, unknown> = { error: 'nothing_to_reopen' }
    try {
      result = await this.openUserTab(conversationId, last.url, false, false, pending)
    } finally {
      this.reopening.delete(pending)
      // A clear, the chat's deletion and a shutdown forget every reopen they void, so `forgotten` covers them too.
      if (result.error && !pending.forgotten) {
        this.closed.set(conversationId, [...(this.closed.get(conversationId) ?? []), last].slice(-MAX_CLOSED_TABS))
      }
    }
    return result
  }

  /** Fetches a page's icon through the hardened session and keeps it as a small data: URL; anything else leaves the letter tile. */
  private async loadFavicon(tab: Tab, href: string): Promise<void> {
    const seq = tab.faviconSeq
    const page = tab.view.webContents.getURL()
    const icon = await this.fetchFavicon(href).catch(() => '')
    if (!icon || !this.isLive(tab) || tab.faviconSeq !== seq || this.tabs.get(tab.id) !== tab) return
    tab.favicon = icon
    this.deps.history?.update(page, { favicon: icon })
    this.emitLive(tab.conversationId)
  }

  private async fetchFavicon(href: string): Promise<string> {
    const get = this.deps.fetch ?? ((url, init) => this.session().fetch(url, init))
    const controller = new AbortController()
    const timer = this.later(() => controller.abort(), FAVICON_TIMEOUT_MS)
    try {
      let url = href
      for (let hop = 0; hop <= MAX_FAVICON_REDIRECTS; hop++) {
        const parsed = new URL(url)
        if (parsed.username || parsed.password || !(await this.navigationAllowed(url))) return ''
        const response = await get(url, { credentials: 'omit', redirect: 'manual', signal: controller.signal })
        const location = response.headers.get('location')
        if (response.status >= 300 && response.status < 400 && location) {
          url = new URL(location, url).toString()
          continue
        }
        const type = (response.headers.get('content-type') ?? '').split(';')[0].trim().toLowerCase()
        if (!response.ok || !/^image\/[a-z0-9.+-]+$/.test(type) || Number(response.headers.get('content-length')) > MAX_FAVICON_BYTES) return ''
        const bytes = await readCapped(response, MAX_FAVICON_BYTES)
        return bytes && bytes.length > 0 ? `data:${type};base64,${bytes.toString('base64')}` : ''
      }
      return ''
    } finally {
      this.clear(timer)
    }
  }

  // ---- renderer channel ----

  livePayload(conversationId: number): BrowserLivePayload {
    const tabs = this.tabsOf(conversationId)
    const shown = this.selectedTab(conversationId)
    const wc = shown?.view.webContents
    return {
      active: tabs.length > 0,
      conversationId,
      url: wc ? wc.getURL() : '',
      title: wc ? wc.getTitle() : '',
      loading: wc ? wc.isLoading() : false,
      userControl: this.agentTab(conversationId)?.userControl ?? false,
      tabs: tabs.map((tab) => ({ id: tab.id, url: tab.view.webContents.getURL(), title: tab.view.webContents.getTitle(),
        loading: tab.view.webContents.isLoading(), favicon: tab.favicon, agent: tab.agent })),
      activeTabId: shown ? shown.id : null,
      ...(shown?.annotating ? { annotating: true as const } : {}),
    }
  }

  private emitLive(conversationId: number, error?: string): void {
    const win = this.deps.window()
    if (!win || win.isDestroyed()) return
    const payload = this.livePayload(conversationId)
    try { win.webContents.send(BROWSER_LIVE_CHANNEL, error ? { ...payload, error } : payload) } catch { /* window closing */ }
  }

  /** Same checks as the agent's navigate: allow-list, then the host must not resolve privately. */
  private async navigationAllowed(url: unknown): Promise<boolean> {
    if (!str(url) || !isAllowedNavigation(url)) return false
    return !(await this.hostBlocked(new URL(url).hostname))
  }

  async invoke(req: unknown): Promise<unknown> {
    if (!req || typeof req !== 'object' || Array.isArray(req)) return { error: 'Invalid browser-live request.' }
    const { op, conversationId: id, bounds, visible, url, gateId, approved, tabId, on, ids } = req as Record<string, unknown>
    const tabOps = ['status', 'panel-status', 'set-bounds', 'visible', 'open', 'navigate', 'back', 'forward', 'reload', 'take-over', 'release', 'close',
      'select', 'close-tab', 'reopen-tab', 'annotate', 'clear-notes', 'capture-notes']
    if (typeof op === 'string' && tabOps.includes(op) && !positiveInt(id)) return { error: 'Unknown operation or malformed browser-live arguments.' }
    if (tabId !== undefined && !positiveInt(tabId)) return { error: 'Unknown operation or malformed browser-live arguments.' }
    const conversationId = id as number
    switch (op) {
      // The one op a paired phone may call (renderer/lib/remote/desktopRunner.ts): the agent's page only, never the user's tabs.
      case 'status': {
        const agent = this.agentTab(conversationId)
        const wc = agent?.view.webContents
        return { active: agent !== null, conversationId, url: wc ? wc.getURL() : '', title: wc ? wc.getTitle() : '',
          loading: wc ? wc.isLoading() : false, userControl: agent?.userControl ?? false }
      }
      // The panel's own read: every tab of the chat.
      case 'panel-status':
        return { ...this.livePayload(conversationId), visible: this.visibleId === conversationId, bounds: this.bounds }
      case 'set-bounds': {
        const b = bounds as Record<string, unknown> | null
        if (!b || typeof b !== 'object' || ![b.x, b.y, b.width, b.height].every(finiteNonNegative)) break
        const before = this.bounds
        this.bounds = { x: Math.round(b.x as number), y: Math.round(b.y as number),
          width: Math.round(b.width as number), height: Math.round(b.height as number) }
        for (const tab of this.tabsOf(conversationId)) tab.view.setBounds(this.bounds)
        // The panel moved or resized (full screen, a side panel): the overlays follow, the box is placed again and the boxes read again.
        if (before.x !== this.bounds.x || before.y !== this.bounds.y || before.width !== this.bounds.width || before.height !== this.bounds.height) {
          this.hideBox()
          this.syncAnnotate()
        }
        return { bounds: this.bounds }
      }
      case 'visible':
        if (typeof visible !== 'boolean') break
        if (visible) {
          const tab = this.selectedTab(conversationId)
          if (tab) this.touch(tab)
          this.show(conversationId)
        } else if (this.visibleId === conversationId) {
          this.show(null)
        }
        return { visible }
      // The renderer's full-screen state, for Esc pressed in the native views (escape()).
      case 'full':
        if (typeof on !== 'boolean') break
        this.full = on
        return { full: on }
      // Esc in the main window that none of the renderer's own controls used (renderer/lib/browserFull.ts).
      case 'escape':
        return { claimed: this.escapeStep() }
      case 'halt': {
        if (id !== undefined && !positiveInt(id)) break
        const only = id === undefined ? null : conversationId
        this.stopLocal(only)
        try {
          await this.haltBackend(only)
          return { halted: true }
        } catch {
          this.log('[browser] backend halt failed')
          return { halted: false, error: 'Stopping the agent browser failed in the backend.' }
        }
      }
      case 'open':
        return this.openUserTab(conversationId, url)
      case 'navigate': {
        if (this.clearing) return { error: 'clearing' }
        const epoch = this.lifecycleEpoch(conversationId)
        if (!(await this.navigationAllowed(url))) return { error: 'url_not_allowed' }
        // A clear or the chat's deletion can happen during the lookup above.
        const stale = this.staleReply(conversationId, epoch)
        if (stale) return stale
        const tab = this.chatTab(conversationId, tabId)
        if (!tab) return tabId === undefined ? this.openUserTab(conversationId, url) : { error: 'no_tab' }
        this.touch(tab)
        tab.userTouched = true
        this.load(tab, url as string)
        this.emitLive(conversationId)
        return this.livePayload(conversationId)
      }
      case 'back':
      case 'forward':
      case 'reload': {
        if (this.clearing) return { error: 'clearing' }
        const tab = this.chatTab(conversationId, tabId)
        if (!tab) return { error: 'no_tab' }
        this.touch(tab)
        tab.userTouched = true
        const wc = tab.view.webContents
        const history = wc.navigationHistory
        if (op !== 'reload' && (op === 'back' ? !history.canGoBack() : !history.canGoForward())) return { error: 'no_history' }
        if (op === 'reload') wc.reload()
        else if (op === 'back') history.goBack()
        else history.goForward()
        return this.livePayload(conversationId)
      }
      case 'take-over': {
        const tab = this.agentTab(conversationId)
        if (!tab) return { error: 'no_tab' }
        this.setUserControl(tab, true)
        return this.livePayload(conversationId)
      }
      case 'release':
        this.release(conversationId)
        return this.livePayload(conversationId)
      case 'select':
      case 'close-tab': {
        const tab = tabId === undefined ? null : this.chatTab(conversationId, tabId)
        if (!tab) return { error: 'no_tab' }
        if (op === 'select') {
          tab.userTouched = true
          this.selectTab(tab)
        } else this.closeByUser(tab)
        return this.livePayload(conversationId)
      }
      case 'reopen-tab':
        return this.reopenTab(conversationId)
      case 'annotate': {
        if (typeof on !== 'boolean') break
        const tab = this.chatTab(conversationId, tabId)
        if (!tab) return { error: 'no_tab' }
        const failed = await this.setAnnotate(tab, on)
        if (failed) return failed
        return this.livePayload(conversationId)
      }
      case 'capture-notes': {
        const tab = this.chatTab(conversationId, tabId)
        if (!tab) return { error: 'no_tab' }
        return { image: await this.captureTab(tab) }
      }
      case 'clear-notes': {
        const tab = this.chatTab(conversationId, tabId)
        if (!tab) return { error: 'no_tab' }
        if (ids !== undefined && !(Array.isArray(ids) && ids.length <= MAX_CLEAR_IDS && ids.every((one) => typeof one === 'string' && one.length <= 64))) break
        const before = tab.notes.length
        tab.notes = ids === undefined ? [] : tab.notes.filter((one) => !(ids as string[]).includes(one.note.id))
        this.pushPins(tab)
        return { cleared: before - tab.notes.length }
      }
      case 'history': {
        const { query } = req as { query?: unknown }
        const needle = typeof query === 'string' ? query.slice(0, 200).trim().toLowerCase() : ''
        const entries = this.deps.history?.list() ?? []
        return { entries: (needle ? entries.filter((e) => e.url.toLowerCase().includes(needle) || e.title.toLowerCase().includes(needle)) : entries).slice(0, 500) }
      }
      case 'history-remove': {
        if (!str(url) || !this.deps.history?.remove) return { error: 'bad_args' }
        // Reopening a closed tab would bring the page back into history.
        this.forgetClosed((tab) => tab.url === url || historyUrl(tab.url) === url)
        // An entry already gone (another window, a clear) is not an error; a false from remove() is then a failed write.
        if (!this.deps.history.list().some((entry) => entry.url === url)) return { ok: true, removed: false }
        return (await this.deps.history.remove(url)) ? { ok: true, removed: true } : { error: 'history_remove_failed' }
      }
      case 'history-clear': {
        const { range } = req as { range?: unknown }
        if (range !== 'hour' && range !== 'day' && range !== 'all') return { error: 'bad_args' }
        const history = this.deps.history
        if (!history?.clearSince) return { error: 'bad_args' }
        const now = this.now()
        // 'day' is since the local midnight, not the last 24 hours.
        const since = range === 'hour' ? now - 3_600_000 : range === 'day' ? new Date(now).setHours(0, 0, 0, 0) : -Infinity
        this.forgetClosed((tab) => tab.closedAt >= since)
        return (await (range === 'all' ? history.clear() : history.clearSince(since))) ? { ok: true } : { error: 'history_clear_failed' }
      }
      // Sites only: counts and expiry, never a cookie's name or value.
      case 'sites': {
        try {
          const sites: { site: string; cookies: number; lastExpiry?: number }[] = []
          for (const [site, cookies] of siteRows(await this.session().cookies.get({}))) {
            const entry: { site: string; cookies: number; lastExpiry?: number } = { site, cookies: cookies.length }
            for (const cookie of cookies) {
              if (cookie.expirationDate !== undefined && cookie.expirationDate > (entry.lastExpiry ?? 0)) entry.lastExpiry = cookie.expirationDate
            }
            sites.push(entry)
          }
          return { sites: sites.sort((a, b) => a.site.localeCompare(b.site)) }
        } catch {
          return { error: 'sites_failed' }
        }
      }
      case 'site-remove': {
        const { site } = req as { site?: unknown }
        if (typeof site !== 'string' || site.length > 253 || !SITE_RE.test(site) || !/[a-z0-9]/.test(site) || isPublicSuffix(site)) return { error: 'bad_args' }
        // Work already running goes on (no clearGen bump, no halt); the lock only refuses new work.
        const accepted = { value: false }
        const result = await this.locked(async () => {
          this.removing = site
          try {
            return await this.removeSite(site, accepted)
          } catch {
            this.log('[browser] removing a site failed')
            return { error: 'site_remove_failed' }
          } finally {
            this.removing = null
          }
        })
        // The login may be gone whatever the outcome, so the backend drops the chats' grants like after a full clear.
        if (accepted.value) await this.report(true)
        return result
      }
      case 'close': {
        this.closeGen.set(conversationId, (this.closeGen.get(conversationId) ?? 0) + 1)
        // Like a Stop: the chat's running and queued agent commands end without making a new tab.
        this.stopLocal(conversationId)
        for (const tab of [...this.tabs.values()]) if (tab.conversationId === conversationId) this.closeTab(tab)
        this.pageOpens.delete(conversationId)
        this.forgetClosed((_tab, chat) => chat === conversationId)
        this.closed.delete(conversationId)
        // Only a deleted chat is closed; evict and clear keep the id so a Stop still reaches the backend.
        this.actedIn.delete(conversationId)
        return this.livePayload(conversationId)
      }
      case 'clear-browsing-data': {
        const failed = await this.locked(async () => {
          this.clearGen++
          // Every chat's running and queued agent commands end with the clear.
          this.stopLocal(null)
          // Tabs go first so no page writes new cookies between the clear and its close.
          this.destroyView()
          this.forgetClosed(() => true)
          // A history file that could not be erased is a failed clear: the visits would return after a restart.
          let failed = !((await this.deps.history?.clear()) ?? true)
          try {
            const session = this.session()
            await session.clearStorageData()
            await session.clearCache()
          } catch {
            this.log('[browser] clearing browsing data failed')
            failed = true
          } finally {
            this.destroyView()
          }
          return failed
        })
        if (typeof failed === 'object') return failed
        // Even a failed clear may have wiped logins, so the backend drops every chat's grants either way.
        await this.report(true)
        return failed ? { error: 'Clearing browsing data failed.' } : { cleared: true }
      }
      case 'answer-card':
        if (typeof gateId !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(gateId) || typeof approved !== 'boolean') break
        try {
          return (await this.request('post', `/mcp-approval-respond/${gateId}`, { approved }, true)).data
        } catch {
          // Not serialised: the request config inside the error carries both secrets.
          return { error: 'Approval request failed.' }
        }
      case 'settings':
      case 'enable':
      case 'disable':
        try {
          if (op === 'settings') {
            const { data } = await this.request('get', '/browser/settings')
            return { enabled: data?.enabled === true, running: this.runtimeBody().running }
          }
          const { data } = await this.request('put', '/browser/settings', { enabled: op === 'enable' }, true)
          void this.reconcile().catch(() => { this.log('[browser] reconcile failed') })
          return { enabled: data?.enabled === true }
        } catch {
          // Not serialised: the request config inside the error carries both secrets.
          return { error: 'Browser settings request failed.' }
        }
      default:
        break
    }
    return { error: 'Unknown operation or malformed browser-live arguments.' }
  }

  /**
   * Stops one chat's tab, or every tab without an id (the computer-use Stop is global), wakes a
   * waiting navigation and drops queued commands through onHalt.
   */
  halt(conversationId?: number): void {
    const only = conversationId ?? null
    this.stopLocal(only)
    this.haltBackend(only).catch(() => { this.log('[browser] backend halt failed') })
  }

  /** The backend halt set also covers browser tools, so the agent's next call is refused too. */
  private async haltBackend(only: number | null): Promise<void> {
    const ids = only === null ? [...this.actedIn] : [only]
    await Promise.all(ids.map((id) => this.request('post', `/computer-use/halt/${id}`, undefined, true)))
  }

  /** Halts count what happened to an agent command while it awaited: the sum only ever grows. */
  private haltEpoch(conversationId: number): number {
    return this.haltAll + (this.haltChat.get(conversationId) ?? 0) + this.lifecycleEpoch(conversationId)
  }

  /** Changes when this chat is closed or any browsing data is cleared: pending work for it is void. */
  private lifecycleEpoch(conversationId: number): number {
    return this.clearGen + (this.closeGen.get(conversationId) ?? 0)
  }

  /** Stops the agent's tab(s); tabs the user opened are theirs and keep loading. */
  private stopLocal(only: number | null): void {
    if (only === null) this.haltAll++
    else this.haltChat.set(only, (this.haltChat.get(only) ?? 0) + 1)
    this.deps.onHalt?.(only)
    for (const listener of [...this.haltListeners]) if (only === null || listener.conversationId === only) listener.wake()
    for (const tab of [...this.tabs.values()]) {
      if (!tab.agent || (only !== null && tab.conversationId !== only)) continue
      this.settleLoad(tab, { ok: false, error: 'halted' })
      const wc = tab.view.webContents
      if (!wc.isDestroyed()) wc.stop()
      this.emitLive(tab.conversationId)
    }
  }

  // ---- command handling ----

  private settleLoad(tab: Tab, result: LoadResult): void {
    const waiter = tab.loadWaiter
    tab.loadWaiter = null
    waiter?.(result)
  }

  private waitForLoad(tab: Tab, start: () => void): Promise<LoadResult> {
    this.settleLoad(tab, { ok: false, error: 'superseded' })
    return new Promise<LoadResult>((resolve) => {
      const timer = this.later(() => {
        if (tab.loadWaiter === done) this.settleLoad(tab, { ok: false, error: 'navigation_timeout' })
      }, NAVIGATION_TIMEOUT_MS)
      const done = (result: LoadResult) => {
        this.clear(timer)
        resolve(result)
      }
      tab.loadWaiter = done
      try { start() } catch (error) { this.settleLoad(tab, { ok: false, error: error instanceof Error ? error.message : 'load_failed' }) }
    })
  }

  private async page(tab: Tab, fn: string, args: Record<string, unknown>, userGesture = false): Promise<Record<string, unknown>> {
    const wc = tab.view.webContents
    const code = `${PAGE_LIB}\n;globalThis.__gmBrowser.${fn}(${JSON.stringify(args)})`
    let timer: ReturnType<typeof setTimeout> | undefined
    const timeout = new Promise<never>((_resolve, reject) => {
      timer = this.later(() => reject(new Error('script_timeout')), SCRIPT_TIMEOUT_MS)
    })
    try {
      const result = await Promise.race([wc.executeJavaScriptInIsolatedWorld(BROWSER_WORLD_ID, [{ code }], userGesture), timeout])
      return result && typeof result === 'object' ? result : {}
    } finally {
      this.clear(timer)
    }
  }

  private base(wc: WebContents | undefined): { url: string; title: string } {
    return !wc || wc.isDestroyed() ? { url: '', title: '' } : { url: wc.getURL(), title: wc.getTitle() }
  }

  /** The chat a command belongs to; one without a valid id belongs to none and is refused. */
  private commandConversation(cmd: BrowserCommand): number | null {
    return positiveInt(cmd.conversation_id) ? cmd.conversation_id : null
  }

  /** The refusal for an agent command whose tab the user has taken over, or null when it may run. */
  userControlReply(cmd: BrowserCommand): Reply | null {
    // `tabs` and `use_tab` are judged inside handle: the agent may move away from the tab the user holds.
    if (cmd.op === 'halt' || cmd.op === 'tabs' || cmd.op === 'use_tab') return null
    const id = this.commandConversation(cmd)
    const tab = id === null ? null : this.agentTab(id)
    if (!tab?.userControl) return null
    return { ok: false, error: 'user_control', ...this.base(tab.view.webContents) }
  }

  /** Entry point for the command server; always resolves to a reply object. */
  async handle(cmd: BrowserCommand): Promise<Reply> {
    const id = this.commandConversation(cmd)
    if (id === null) return { ok: false, error: 'no_conversation', url: '', title: '' }
    if (cmd.op !== 'halt' && this.clearing) return { ok: false, error: 'clearing', url: '', title: '' }
    if (cmd.op === 'halt') {
      this.stopLocal(id)
      return { ok: true, ...this.base(this.agentTab(id)?.view.webContents) }
    }
    // Listing and switching stay open while the user holds the agent tab: the agent may move away from it.
    if (cmd.op === 'tabs' || cmd.op === 'use_tab') this.actedIn.add(id)
    if (cmd.op === 'tabs') return this.tabsReply(id)
    if (cmd.op === 'use_tab') return this.useTab(id, cmd.args)
    const refused = this.userControlReply({ ...cmd, conversation_id: id })
    if (refused) return refused
    // Every agent command lands on the chat's agent tab, never on a tab the user opened.
    let tab: Tab
    try {
      tab = this.ensureAgentTab(id)
    } catch (error) {
      return { ok: false, error: error instanceof Error ? error.message : 'no_window', url: '', title: '' }
    }
    this.actedIn.add(id)
    this.emitLive(id)
    const wc = tab.view.webContents
    const epoch = this.haltEpoch(id)
    let reply: Reply
    const halted = () => this.haltEpoch(id) !== epoch
    tab.busy++
    try {
      const result = await this.run(tab, cmd.op, cmd.args && typeof cmd.args === 'object' ? cmd.args : {}, halted)
      // A Stop (or the chat's deletion) that arrived while the command awaited wins over whatever it came back with.
      const extra = halted() ? { error: 'halted' } : result
      reply = { ok: !extra.error, ...extra, ...this.base(wc) } as Reply
      if (!extra.error) delete reply.error
    } catch (error) {
      // After a Stop the failure is the Stop's: the page was cut off, not broken.
      reply = { ok: false, error: halted() ? 'halted' : error instanceof Error ? error.message : String(error), ...this.base(wc) }
    } finally {
      tab.busy--
    }
    if (tab.popupBlocked) {
      reply.popup_blocked = tab.popupBlocked
      tab.popupBlocked = null
    }
    this.emitLive(id)
    return reply
  }

  /**
   * The agent's read of its own chat's tabs; the page in the reply is the agent tab's. Only the agent tab shows its
   * full address: another tab's path and query can carry secrets the agent has no other way to know.
   */
  private tabsReply(conversationId: number): Reply {
    const shown = this.selectedTab(conversationId)
    const tabs = this.tabsOf(conversationId).map((tab) => {
      const url = tab.view.webContents.getURL()
      return { id: tab.id, title: tab.view.webContents.getTitle(), url: tab.agent ? url : originOf(url),
        active: tab === shown, agent: tab.agent, userTouched: tab.userTouched }
    })
    return { ok: true, tabs, ...this.base(this.agentTab(conversationId)?.view.webContents) }
  }

  /**
   * The agent moves its tab to another tab of this chat, the way a user message does in `release`: the chosen
   * tab becomes the agent tab, the old one the user's. A tab the user used since their last message is refused,
   * and what the user sees (the selected tab) does not change.
   */
  private useTab(conversationId: number, args: unknown): Reply {
    const agent = this.agentTab(conversationId)
    const failed = (error: string): Reply => ({ ok: false, error, ...this.base(agent?.view.webContents) })
    const tabId = args && typeof args === 'object' ? (args as Record<string, unknown>).tab_id : undefined
    if (!positiveInt(tabId)) return failed('bad_args')
    const next = this.chatTab(conversationId, tabId)
    if (!next) return failed('no_tab')
    if (next === agent) return { ok: true, ...this.base(next.view.webContents) }
    if (next.userTouched || next.userControl) return failed('user_control')
    if ((agent?.busy ?? 0) > 0) return failed('busy')
    if (agent?.userControl) {
      // The tab the user held stays theirs until their next message.
      agent.userTouched = true
      this.setUserControl(agent, false)
    }
    this.moveAgent(agent, next)
    this.touch(next)
    this.emitLive(conversationId)
    return { ok: true, ...this.base(next.view.webContents) }
  }

  /** `halted` tells whether a Stop arrived since the command began; checked after every await that precedes an action. */
  private async run(tab: Tab, op: string, args: Record<string, unknown>, halted: () => boolean): Promise<Record<string, unknown>> {
    const wc = tab.view.webContents
    const ref = { snapshotId: args.snapshot_id, ref: args.ref }
    const needsRef = () => str(args.snapshot_id) && str(args.ref)
    if (op !== 'snapshot' && str(args.snapshot_id) && args.snapshot_id !== tab.snapshotId) return { error: 'stale_snapshot' }
    switch (op) {
      case 'navigate': {
        const url = args.url
        // A Stop or a closed tab ends the wait at once instead of when the lookup finishes.
        const allowed = await this.untilHalted(tab, this.navigationAllowed(url))
        if (allowed === 'halted') return { error: 'halted' }
        if (!allowed) return { error: 'url_not_allowed' }
        if (halted()) return { error: 'halted' }
        if (this.clearing) return { error: 'clearing' }
        if (!this.isLive(tab)) return { error: 'tab_closed' }
        return this.loadResult(await this.waitForLoad(tab, () => this.load(tab, url as string)))
      }
      case 'back':
      case 'forward': {
        const history = wc.navigationHistory
        if (op === 'back' ? !history.canGoBack() : !history.canGoForward()) return { error: 'no_history' }
        return this.loadResult(await this.waitForLoad(tab, () => (op === 'back' ? history.goBack() : history.goForward())))
      }
      case 'reload':
        return this.loadResult(await this.waitForLoad(tab, () => wc.reload()))
      case 'snapshot': {
        const requested = Number(args.max_chars)
        const maxChars = Number.isFinite(requested) && requested > 0 ? Math.min(Math.floor(requested), MAX_TEXT_CHARS) : 20_000
        const snapshotId = `s${++this.snapshotSeq}`
        tab.snapshotId = snapshotId
        const result = await this.page(tab, 'snapshot', { snapshotId, maxChars })
        return result.error ? result : { ...result, snapshot_id: snapshotId }
      }
      case 'click':
        if (!needsRef()) return { error: 'bad_args' }
        return this.page(tab, 'click', ref, true)
      case 'fill':
        if (!needsRef() || typeof args.value !== 'string') return { error: 'bad_args' }
        if (args.value.length > MAX_FILL_CHARS) return { error: 'value_too_long' }
        return this.page(tab, 'fill', { ...ref, value: args.value }, true)
      case 'select':
        if (!needsRef() || typeof args.option !== 'string') return { error: 'bad_args' }
        return this.page(tab, 'select', { ...ref, option: args.option }, true)
      case 'describe':
        if (args.focused === true) return this.page(tab, 'describe', { focused: true })
        if (!needsRef()) return { error: 'bad_args' }
        return this.page(tab, 'describe', ref)
      case 'scroll': {
        if (args.direction !== 'up' && args.direction !== 'down') return { error: 'bad_args' }
        const amount = finiteNonNegative(args.amount) ? Math.min(args.amount, 100_000) : 0
        return this.page(tab, 'scroll', { direction: args.direction, amount })
      }
      case 'press': {
        const keyCode = typeof args.key === 'string' ? KEYS[args.key] : undefined
        if (!keyCode) return { error: 'key_not_allowed' }
        if (tab.userControl) return { error: 'user_control' }
        // The host's own events come back through both channels; only these are not the user's.
        const until = this.now() + SYNTHETIC_INPUT_MS
        const keys = [args.key as string, keyCode]
        tab.expected = [...tab.expected.filter((echo) => echo.until > this.now()),
          { channel: 'before', type: 'keyDown', keys, until }, { channel: 'input', type: 'rawKeyDown', keys: [], until }]
        wc.focus()
        wc.sendInputEvent({ type: 'keyDown', keyCode })
        if (keyCode === 'Enter') wc.sendInputEvent({ type: 'char', keyCode: '\r' })
        wc.sendInputEvent({ type: 'keyUp', keyCode })
        return {}
      }
      case 'screenshot': {
        let image: NativeImage = await wc.capturePage(undefined, { stayHidden: true })
        if (image.isEmpty()) return { error: 'empty_capture' }
        if (image.getSize().width > SCREENSHOT_MAX_WIDTH) image = image.resize({ width: SCREENSHOT_MAX_WIDTH, quality: 'good' })
        return { image: image.toPNG().toString('base64') }
      }
      default:
        return { error: 'unknown_op' }
    }
  }

  private untilHalted<T>(tab: Tab, work: Promise<T>): Promise<T | 'halted'> {
    let listener: { conversationId: number; wake: () => void } | undefined
    const stopped = new Promise<'halted'>((resolve) => {
      listener = { conversationId: tab.conversationId, wake: () => resolve('halted') }
      this.haltListeners.add(listener)
    })
    return Promise.race([work, stopped, tab.gone.then(() => 'halted' as const)]).finally(() => { this.haltListeners.delete(listener!) })
  }

  private loadResult(result: LoadResult): Record<string, unknown> {
    return 'error' in result ? { error: result.error } : {}
  }
}
