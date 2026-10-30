import type { NativeImage, Rectangle, Session, WebContents, WebContentsView, WebContentsViewConstructorOptions } from 'electron'
import type { HttpClient } from './remote-control'
import type { BrowserCommand } from './browser-server'
import { isIP } from 'net'
import { isAllowedNavigation, isPrivateAddress, resolvesToPrivate, type LookupAll } from './browser-url-policy'
import type { BrowserHistory } from './browser-history'

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
// How often the page is asked for a new pick while annotate mode is on and visible.
const PICK_POLL_MS = 150
const MAX_NOTES_PER_POLL = 50
const NOTE_COMMENT_CHARS = 1000
const NOTE_BOX_WIDTH = 320
const NOTE_BOX_MIN_HEIGHT = 56
const NOTE_BOX_MAX_HEIGHT = 168
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
  history?: Pick<BrowserHistory, 'visit' | 'update' | 'list' | 'clear' | 'flush'>
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
  const nameOf = (el) => {
    const by = el.getAttribute('aria-labelledby');
    const byText = by ? by.split(/\s+/).map((id) => { const n = document.getElementById(id); return n ? n.innerText : ''; }).join(' ') : '';
    const labels = el.labels && el.labels.length ? Array.from(el.labels).map((l) => l.innerText).join(' ') : '';
    const img = el.querySelector ? el.querySelector('img[alt]') : null;
    const buttonValue = tag(el) === 'input' && BUTTON_TYPES.includes(inputType(el)) ? el.value : '';
    return clean(el.getAttribute('aria-label') || byText || labels || buttonValue || el.innerText ||
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
  // ---- annotation overlay (docs/agent-browser.md "Annotate mode") ----
  // The page side only picks and draws. The comment is typed into an app-owned view (BrowserHost.openBox), so no text input exists here and the page never sees the comment.
  const NOTE_MAX_NOTES = 50;
  const NOTE_NAME_CHARS = 200;
  const NOTE_PATH_CHARS = 200;
  const NOTE_AREA_CHARS = 500;
  const DRAG_PX = 6;
  // Text a person typed or chose never leaves the page: every place that reads text skips fields and editors (any contenteditable value except "false", in any case).
  const FIELD_SELECTOR = 'input, textarea, select, option, [contenteditable]:not([contenteditable="false" i]), [role="textbox" i], [role="searchbox" i], [role="combobox" i], [aria-multiline="true" i]';
  const SKIPPED_TEXT = FIELD_SELECTOR + ', script, style, noscript, template';
  const isField = (el) => !!el.isContentEditable || !!(el.closest && el.closest(FIELD_SELECTOR));
  const isSkipped = (el) => !!el.isContentEditable || !!el.closest(SKIPPED_TEXT);
  const OVERLAY_CSS = [
    '*{box-sizing:border-box;font:13px/1.4 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}',
    '.hl,.frame{position:fixed;display:none;pointer-events:none;border:2px solid #2f6fed;border-radius:4px;background:rgba(47,111,237,.10)}',
    '.frame{border-style:dashed}',
    '.mark{position:fixed;pointer-events:none;border:2px solid #2f6fed;border-radius:4px;background:rgba(47,111,237,.06)}',
    '.pin{position:fixed;width:20px;height:20px;margin:-10px 0 0 -10px;border-radius:50%;background:#2f6fed;color:#fff;font-size:11px;font-weight:600;display:flex;align-items:center;justify-content:center;box-shadow:0 0 0 2px #fff,0 2px 6px rgba(15,23,42,.35);pointer-events:none}',
    '.bar{position:fixed;top:10px;left:50%;transform:translateX(-50%);max-width:calc(100vw - 24px);padding:5px 12px;border-radius:999px;background:rgba(21,27,38,.88);color:#f4f6fa;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;box-shadow:0 2px 10px rgba(15,23,42,.3);pointer-events:none}',
  ].join('');
  const rectOf = (r) => ({ x: Math.round(r.left), y: Math.round(r.top), width: Math.round(r.width), height: Math.round(r.height) });
  const placeAt = (node, r) => {
    node.style.left = r.x + 'px';
    node.style.top = r.y + 'px';
    node.style.width = r.width + 'px';
    node.style.height = r.height + 'px';
  };
  const textOf = (root, max) => {
    let out = '';
    let visited = 0;
    const walk = (node, depth) => {
      if (out.length >= max || visited++ > 3000 || depth > 80) return;
      if (node.nodeType === 3) { out += node.nodeValue + ' '; return; }
      if (node.nodeType !== 1 || node.isContentEditable || node.matches(SKIPPED_TEXT)) return;
      const st = getComputedStyle(node);
      if (st.display === 'none' || st.visibility === 'hidden') return;
      for (const child of node.childNodes) walk(child, depth + 1);
    };
    walk(root, 0);
    return clean(out, max);
  };
  const noteName = (el) => {
    const by = el.getAttribute('aria-labelledby');
    const byText = by ? by.split(/\s+/).map((id) => { const n = document.getElementById(id); return n ? textOf(n, NOTE_NAME_CHARS) : ''; }).join(' ') : '';
    const labels = el.labels && el.labels.length ? Array.from(el.labels).map((l) => textOf(l, NOTE_NAME_CHARS)).join(' ') : '';
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
  const describeElement = (el) => {
    const data = { tag: tag(el), role: roleOf(el), name: noteName(el), path: cssPath(el), rect: rectOf(el.getBoundingClientRect()) };
    const link = el.closest ? el.closest('a[href]') : null;
    if (link && link.href) data.href = String(link.href).slice(0, 300);
    return data;
  };
  // Own text of the elements the frame overlaps, in tree order; fields and editors are skipped like everywhere else.
  const areaText = (rect) => {
    let out = '';
    let visited = 0;
    for (const el of document.body ? document.body.querySelectorAll('*') : []) {
      if (out.length >= NOTE_AREA_CHARS || visited++ > 5000) break;
      if (isSkipped(el)) continue;
      let own = '';
      for (const child of el.childNodes) if (child.nodeType === 3) own += child.nodeValue + ' ';
      if (!own.trim()) continue;
      const r = el.getBoundingClientRect();
      if (r.right <= rect.x || r.left >= rect.x + rect.width || r.bottom <= rect.y || r.top >= rect.y + rect.height || !shown(el)) continue;
      out += own;
    }
    return clean(out, NOTE_AREA_CHARS);
  };
  // saved: committed notes (frame and pin drawn); pending: the one pick waiting for its comment (kept by the host).
  const saved = [];
  let pending = null;
  let pickSeq = 0;
  // The overlay (anno) outlives the mode while pins or a pending pick are drawn; modeOn is the mode itself.
  let anno = null;
  let modeOn = false;
  const clamp = (n, lo, hi) => Math.max(lo, Math.min(n, hi));
  const anchorRect = (note) => {
    const a = note.anchor;
    const el = a.el && a.el.deref();
    if (el && el.isConnected) return rectOf(el.getBoundingClientRect());
    return { x: a.x - scrollX, y: a.y - scrollY, width: a.width, height: a.height };
  };
  const drawPins = () => {
    if (!anno) return;
    anno.pins.replaceChildren();
    saved.forEach((note, i) => {
      const r = anchorRect(note);
      if (r.x + r.width < 0 || r.y + r.height < 0 || r.x > innerWidth || r.y > innerHeight) return;
      const mark = document.createElement('div');
      mark.className = 'mark';
      placeAt(mark, r);
      anno.pins.appendChild(mark);
      const pin = document.createElement('div');
      pin.className = 'pin';
      pin.textContent = String(i + 1);
      pin.style.left = clamp(r.x, 10, innerWidth - 10) + 'px';
      pin.style.top = clamp(r.y, 10, innerHeight - 10) + 'px';
      anno.pins.appendChild(pin);
    });
  };
  // The pending pick is outlined while the mode is on, so the user sees what the comment is about.
  const drawPick = () => {
    if (!anno || anno.drag) return;
    anno.hl.style.display = 'none';
    anno.frame.style.display = 'none';
    if (!pending || !modeOn) return;
    const node = pending.kind === 'area' ? anno.frame : anno.hl;
    placeAt(node, anchorRect(pending));
    node.style.display = 'block';
  };
  const dropPending = () => {
    pending = null;
    drawPick();
  };
  const setPending = (kind, el, rect) => {
    const id = 'n' + documentToken.slice(0, 5) + '.' + (++pickSeq);
    const base = { id, kind };
    const data = Object.assign(base, kind === 'area' ? { rect, text: areaText(rect) } : describeElement(el));
    const anchor = { x: rect.x + scrollX, y: rect.y + scrollY, width: rect.width, height: rect.height };
    if (el) anchor.el = new WeakRef(el);
    pending = { id, kind, data, anchor, reported: false };
    drawPick();
  };
  const buildOverlay = () => {
    const host = document.createElement('div');
    host.setAttribute('data-gm-annotate', '');
    host.style.cssText = 'all:initial;position:fixed;inset:0;z-index:2147483647;pointer-events:none;';
    const root = host.attachShadow({ mode: 'closed' });
    try {
      const sheet = new CSSStyleSheet();
      sheet.replaceSync(OVERLAY_CSS);
      root.adoptedStyleSheets = [sheet];
    } catch (e) {
      // A constructed sheet is the way past a strict page CSP; a plain style element is the fallback.
      const style = document.createElement('style');
      style.textContent = OVERLAY_CSS;
      root.appendChild(style);
    }
    const make = (cls, text) => {
      const n = document.createElement('div');
      n.className = cls;
      if (text) n.textContent = text;
      root.appendChild(n);
      return n;
    };
    const hl = make('hl');
    const frame = make('frame');
    const pins = make('pins');
    const bar = make('bar', 'İşaretleme · bir öğeye tıkla ya da alan çiz · Esc çıkış');
    return { host, hl, frame, pins, bar };
  };
  const startAnnotate = () => {
    if (!anno) {
      anno = Object.assign(buildOverlay(), { drag: null, cursor: '', listeners: [], frameId: 0 });
      (document.body || document.documentElement).appendChild(anno.host);
    }
    modeOn = true;
    anno.cursor = document.documentElement.style.getPropertyValue('cursor');
    anno.bar.style.display = 'block';
    const on = (type, fn, target) => {
      (target || window).addEventListener(type, fn, true);
      anno.listeners.push([target || window, type, fn]);
    };
    // Whatever the overlay itself shows reaches the page as an event from the host element.
    const foreign = (e) => e.isTrusted && e.target !== anno.host;
    const stop = (e) => { e.preventDefault(); e.stopImmediatePropagation(); };
    const targetAt = (x, y) => {
      const el = document.elementFromPoint(x, y);
      if (!el || el === anno.host) return null;
      return el === document.documentElement ? document.body : el;
    };
    const setCursor = (on) => {
      if (on) document.documentElement.style.setProperty('cursor', 'crosshair', 'important');
      else if (anno.cursor) document.documentElement.style.setProperty('cursor', anno.cursor);
      else document.documentElement.style.removeProperty('cursor');
    };
    for (const type of ['mousedown', 'mouseup', 'click', 'dblclick', 'auxclick']) {
      on(type, (e) => { if (foreign(e) && (e.button === 0 || type === 'auxclick')) stop(e); });
    }
    on('pointerdown', (e) => {
      if (!foreign(e)) return;
      e.stopImmediatePropagation();
      if (e.button !== 0) return;
      dropPending();
      anno.hl.style.display = 'none';
      anno.drag = { x: e.clientX, y: e.clientY, area: false, rect: null };
    });
    on('pointermove', (e) => {
      if (!foreign(e)) return;
      const d = anno.drag;
      if (!d && pending) return;
      if (d && e.buttons === 0) { anno.drag = null; anno.frame.style.display = 'none'; return; }
      if (d) {
        if (!d.area && Math.hypot(e.clientX - d.x, e.clientY - d.y) >= DRAG_PX) d.area = true;
        if (d.area) {
          const x = clamp(Math.min(d.x, e.clientX), 0, innerWidth);
          const y = clamp(Math.min(d.y, e.clientY), 0, innerHeight);
          d.rect = { x, y, width: clamp(Math.max(d.x, e.clientX), 0, innerWidth) - x, height: clamp(Math.max(d.y, e.clientY), 0, innerHeight) - y };
          placeAt(anno.frame, d.rect);
          anno.frame.style.display = 'block';
        }
        return;
      }
      const el = targetAt(e.clientX, e.clientY);
      if (!el) { anno.hl.style.display = 'none'; return; }
      placeAt(anno.hl, rectOf(el.getBoundingClientRect()));
      anno.hl.style.display = 'block';
    });
    on('pointerup', (e) => {
      if (!foreign(e)) return;
      e.stopImmediatePropagation();
      const d = anno.drag;
      if (!d) return;
      anno.drag = null;
      if (d.area && d.rect) { setPending('area', null, d.rect); return; }
      const el = targetAt(e.clientX, e.clientY);
      if (el) setPending('element', el, rectOf(el.getBoundingClientRect()));
    });
    on('keydown', (e) => {
      if (!e.isTrusted || e.target === anno.host || e.key !== 'Escape') return;
      stop(e);
      if (anno.drag) { anno.drag = null; anno.frame.style.display = 'none'; return; }
      if (pending) { dropPending(); return; }
      stopAnnotate();
    });
    const redraw = () => {
      if (anno.frameId) return;
      anno.frameId = requestAnimationFrame(() => { anno.frameId = 0; drawPins(); drawPick(); });
    };
    on('scroll', redraw);
    on('resize', redraw);
    setCursor(true);
    drawPins();
    drawPick();
  };
  const dropOverlayIfIdle = () => {
    if (anno && !modeOn && saved.length === 0 && !pending) { anno.host.remove(); anno = null; }
  };
  // Ends the mode; the overlay stays while pins or a pending pick exist, and goes with the last one.
  const stopAnnotate = () => {
    if (!anno) return;
    modeOn = false;
    for (const [target, type, fn] of anno.listeners) target.removeEventListener(type, fn, true);
    anno.listeners = [];
    anno.drag = null;
    anno.bar.style.display = 'none';
    anno.hl.style.display = 'none';
    anno.frame.style.display = 'none';
    if (anno.frameId) cancelAnimationFrame(anno.frameId);
    anno.frameId = 0;
    if (anno.cursor) document.documentElement.style.setProperty('cursor', anno.cursor);
    else document.documentElement.style.removeProperty('cursor');
    dropOverlayIfIdle();
  };
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
    annotateOn() {
      if (modeOn) return { active: true };
      if (!document.body) return { error: 'no_page' };
      startAnnotate();
      return { active: true };
    },
    annotateOff() {
      stopAnnotate();
      return { active: false };
    },
    // The host polls this while the mode is on. "pick" is a new target not reported yet; "picking" and "rect" describe the pending one (null once it is gone); "active" is false once the page left the mode itself (Esc).
    takePick() {
      const fresh = pending && !pending.reported ? pending : null;
      if (fresh) fresh.reported = true;
      return {
        active: modeOn,
        scroll: [Math.round(scrollX), Math.round(scrollY)],
        viewport: { width: innerWidth, height: innerHeight },
        picking: pending ? pending.id : null,
        rect: pending ? anchorRect(pending) : null,
        pick: fresh ? fresh.data : null,
      };
    },
    // The host saved the comment: the pending pick becomes a pinned, framed note.
    commitPin(a) {
      if (!pending || pending.id !== (a && a.pickId)) return { error: 'no_pick' };
      if (saved.length >= NOTE_MAX_NOTES) return { error: 'too_many_notes' };
      saved.push({ data: pending.data, taken: false, anchor: pending.anchor, n: a.n });
      pending = null;
      drawPins();
      drawPick();
      return { ok: true };
    },
    dropPick(a) {
      if (pending && (!a || !a.pickId || a.pickId === pending.id)) dropPending();
      dropOverlayIfIdle();
      return { ok: true };
    },
    // Pinned notes not handed over yet (they carry no comment: the host holds it); the pins stay until clearNotes.
    takeNotes() {
      const fresh = saved.filter((note) => !note.taken);
      for (const note of fresh) note.taken = true;
      return { notes: fresh.map((note) => note.data), active: modeOn };
    },
    clearNotes(a) {
      const ids = Array.isArray(a && a.ids) ? a.ids : null;
      let cleared = 0;
      for (let i = saved.length - 1; i >= 0; i--) {
        if (ids && !ids.includes(saved[i].data.id)) continue;
        saved.splice(i, 1);
        cleared++;
      }
      drawPins();
      dropOverlayIfIdle();
      return { cleared };
    },
  };
})();`

/**
 * The comment box: a self-contained page the host shows in its own view above the tab, so the typed text never
 * enters the page being annotated. It has no preload; it talks to the host through document.title (JSON with a
 * counter, so two equal messages still fire page-title-updated).
 */
export const NOTE_BOX_HTML = String.raw`<!doctype html><html><head><meta charset="utf-8"><style>
*{box-sizing:border-box}
html,body{margin:0;background:transparent;overflow:hidden;font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
body{padding:6px}
#b{display:flex;align-items:flex-end;gap:8px;padding:6px 6px 6px 16px;border-radius:24px;background:#1e2126;color:#f2f3f5;border:1px solid rgba(255,255,255,.14);box-shadow:0 8px 24px rgba(0,0,0,.4)}
#t{flex:1;min-width:0;height:24px;max-height:112px;margin:5px 0;padding:0;border:0;outline:0;resize:none;background:transparent;color:inherit;font:inherit;caret-color:#f2f3f5}
#t::placeholder{color:#9aa1ad}
#s{flex:none;width:32px;height:32px;border:0;border-radius:50%;background:#f2f3f5;color:#1e2126;cursor:pointer;display:flex;align-items:center;justify-content:center;padding:0}
#s:disabled{opacity:.35;cursor:default}
</style></head><body><div id="b"><textarea id="t" rows="1" maxlength="1000" placeholder="Yorum ekle…" aria-label="Yorum ekle"></textarea><button id="s" type="button" aria-label="Gönder" disabled><svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 13V3M3.5 7.5 8 3l4.5 4.5"/></svg></button></div><script>
const ta=document.getElementById('t'),send=document.getElementById('s'),box=document.getElementById('b');
let seq=0;
const post=(o)=>{o.q=++seq;document.title=JSON.stringify(o)};
const fit=()=>{ta.style.height='auto';ta.style.height=Math.min(ta.scrollHeight,112)+'px';send.disabled=!ta.value.trim()};
const draft=()=>{fit();post({t:'draft',text:ta.value,h:box.offsetHeight+12})};
const submit=()=>{if(ta.value.trim())post({t:'submit',text:ta.value})};
ta.addEventListener('input',draft);
ta.addEventListener('keydown',(e)=>{
  if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();submit()}
  else if(e.key==='Escape'){e.preventDefault();post({t:'cancel'})}
});
send.addEventListener('click',submit);
window.__set=(v)=>{ta.value=v;draft();ta.focus()};
ta.focus();
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
  return { x: Math.round(x), y: Math.round(y), width: size.width, height: size.height }
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

/** scheme://host[:port] of a web address, or '' for anything else (blank pages, unparsable text). */
function originOf(url: string): string {
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? parsed.origin : ''
  } catch {
    return ''
  }
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
  /** The user is placing notes on this page (annotate mode); ends with any navigation. */
  annotating: boolean
  /** The target waiting for its comment (held here so a closed box can come back); the page keeps its outline until it is saved or dropped. */
  pick: HostPick | null
  /** Notes committed in this document, for the pin numbers. */
  pinSeq: number
  /** Bumped by every navigation, so a favicon fetched for an earlier page is dropped. */
  faviconSeq: number
  /** Settles when the tab is closed, so a command waiting on something else can stop at once. */
  gone: Promise<void>
  markGone: () => void
  /** Key events the host sent with `press` and has not yet seen come back. */
  expected: ExpectedEcho[]
  /** Agent commands running on this tab. */
  busy: number
  lastUsed: number
}

interface ClosedTab { url: string; title: string }

interface HostPick {
  /** The page's description of the target (id, kind, tag, role, name, rect, ...). */
  data: Record<string, unknown> & { id: string }
  /** What the user has typed into the comment box so far. */
  draft: string
  scroll: [number, number]
  viewport: { width: number; height: number }
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
  /** Bumped by every halt (one chat, or all): an agent command that awaited across a bump must not act. */
  private haltAll = 0
  private readonly haltChat = new Map<number, number>()
  private hardened = false
  private visibleId: number | null = null
  /** Chats the agent has acted in. Outlives the tab on evict and clear so a Stop still reaches the backend halt; close (a deleted chat) drops it. */
  private readonly actedIn = new Set<number>()
  /** True while clear-browsing-data is awaited: no tab may be created or navigated meanwhile. */
  private clearing = false
  /** Bumped when a clear starts: an open or navigation that awaited across it must not act. */
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
  private notesTimer: ReturnType<typeof setTimeout> | null = null
  /** The one comment box (an app-owned view above the tabs) and the tab whose pick it shows right now. */
  private noteBox: { view: WebContentsView; ready: Promise<void> } | null = null
  private boxTab: Tab | null = null
  private boxRect = { x: 0, y: 0, width: 0, height: 0 }
  private boxHeight = NOTE_BOX_MIN_HEIGHT
  private boxSeq = 0
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
      if (this.pollTimer) this.clear(this.pollTimer)
      this.pollTimer = null
      if (this.notesTimer) this.clear(this.notesTimer)
      this.notesTimer = null
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
      popupBlocked: null, userControl: false, lastGesture: null, userTouched: false, annotating: false, pick: null, pinSeq: 0, faviconSeq: 0, gone, markGone, expected: [], busy: 0, lastUsed: 0 }
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
      else if ((disposition === 'foreground-tab' || disposition === 'background-tab') && this.pageMayOpenTab(tab)) {
        this.openUserTab(tab.conversationId, url, true).catch(() => { /* refused or no window */ })
      }
      return { action: 'deny' }
    })
    const refuse = (event: { url: string; isMainFrame: boolean; preventDefault(): void }) => {
      if (isAllowedNavigation(event.url)) return
      event.preventDefault()
      // A refused main-frame navigation ends as ERR_ABORTED, which waitForLoad ignores.
      if (event.isMainFrame) this.settleLoad(tab, { ok: false, error: 'url_not_allowed' })
    }
    wc.on('will-navigate', refuse)
    wc.on('will-redirect', refuse)
    wc.on('will-frame-navigate', refuse)
    wc.on('will-attach-webview', (event) => event.preventDefault())
    wc.on('did-finish-load', () => this.settleLoad(tab, { ok: true }))
    wc.on('did-fail-load', (_event, code, description, _url, isMainFrame) => {
      // -3 is ERR_ABORTED: a new navigation replaced this one, and its own load event will follow.
      if (isMainFrame && code !== -3) this.settleLoad(tab, { ok: false, error: `load_failed: ${description || code}` })
    })
    wc.on('render-process-gone', () => this.settleLoad(tab, { ok: false, error: 'page_crashed' }))
    for (const name of ['did-start-loading', 'did-stop-loading', 'page-title-updated', 'did-navigate', 'did-navigate-in-page'] as const) {
      wc.on(name as 'did-start-loading', () => this.emitLive(tab.conversationId))
    }
    // A new document takes the overlay with it; a same-document load (did-finish-load after a reload) ends the mode too.
    wc.on('did-navigate', () => this.forgetPick(tab))
    wc.on('did-finish-load', () => this.forgetPick(tab))
    wc.on('did-navigate', (_event, url) => {
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
      if (isFullChord(input, this.platform)) {
        event.preventDefault()
        const win = this.deps.window()
        if (win && !win.isDestroyed()) {
          try { win.webContents.send(BROWSER_FULL_TOGGLE_CHANNEL, null) } catch { /* window closing */ }
        }
        return
      }
      if (isAnnotateChord(input, this.platform)) {
        event.preventDefault()
        void this.setAnnotate(tab, !tab.annotating)
        return
      }
      const shortcut = tabShortcut(input, this.platform)
      if (shortcut) {
        event.preventDefault()
        void this.runShortcut(tab, shortcut)
        return
      }
      this.userInput(tab, 'before', input)
    })
    // before-input-event sees keys only; input-event also sees the mouse.
    wc.on('input-event', (_event, input) => this.userInput(tab, 'input', input))
  }

  private async runShortcut(tab: Tab, shortcut: TabShortcut): Promise<void> {
    const id = tab.conversationId
    try {
      if (shortcut.action === 'select') {
        const tabs = this.tabsOf(id)
        const target = shortcut.index === 9 ? tabs[tabs.length - 1] : tabs[shortcut.index - 1]
        if (target) this.selectTab(target)
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

  private setUserControl(tab: Tab, on: boolean): void {
    if (tab.userControl === on) return
    tab.userControl = on
    this.emitLive(tab.conversationId)
  }

  private closeTab(tab: Tab): void {
    tab.annotating = false
    this.forgetPick(tab)
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
      stack.push({ url, title: wc.getTitle() })
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
    if (this.boxTab && this.boxTab.id !== shown) this.hideBox()
    this.syncNotesPoll()
  }

  // ---- annotate mode ----

  /**
   * Ends the tab's annotate mode. `removeOverlay` also takes the mode out of a document that stays (a navigation drops it
   * by itself). A pick waiting for its comment is kept, with the draft, for the next time the mode is on.
   */
  private endAnnotate(tab: Tab, removeOverlay = false): void {
    if (!tab.annotating) return
    tab.annotating = false
    if (this.boxTab === tab) this.hideBox()
    if (removeOverlay && this.isLive(tab)) void this.page(tab, 'annotateOff', {}).catch(() => { /* the page is gone */ })
    this.emitLive(tab.conversationId)
  }

  /** A new document has no pick: the target, the draft and the box go. */
  private forgetPick(tab: Tab, removeOverlay = false): void {
    tab.pick = null
    tab.pinSeq = 0
    if (this.boxTab === tab) this.hideBox()
    this.endAnnotate(tab, removeOverlay)
  }

  /** Turns the tab's annotate mode on or off; the error reply when it cannot. */
  private async setAnnotate(tab: Tab, on: boolean): Promise<{ error: string } | null> {
    if (on) {
      const wc = tab.view.webContents
      if (!/^https?:\/\//i.test(wc.getURL()) || wc.isLoading()) return { error: 'no_page' }
      if (!tab.annotating) {
        try {
          const result = await this.page(tab, 'annotateOn', {})
          if (result.error) return { error: String(result.error) }
        } catch {
          return { error: 'annotate_failed' }
        }
        tab.annotating = true
      }
      // The user is working in this page: the agent may not move to it until their next message.
      this.touch(tab)
      tab.userTouched = true
      this.emitLive(tab.conversationId)
      this.syncNotesPoll()
      return null
    }
    this.endAnnotate(tab, true)
    return null
  }

  /** Annotating tabs the user can see right now: only those are polled for picks. */
  private annotatingVisible(): Tab[] {
    return [...this.tabs.values()].filter((tab) => tab.annotating && this.isLive(tab) &&
      tab.conversationId === this.visibleId && this.selected.get(tab.conversationId) === tab.id)
  }

  private syncNotesPoll(): void {
    if (this.notesTimer || this.stopped || this.annotatingVisible().length === 0) return
    this.notesTimer = this.later(() => {
      this.notesTimer = null
      void this.pollPicks()
    }, PICK_POLL_MS)
  }

  private async pollPicks(): Promise<void> {
    try {
      for (const tab of this.annotatingVisible()) {
        try {
          const state = await this.page(tab, 'takePick', {})
          if (!tab.annotating) continue
          // The page left the mode itself (Esc), or it is a new document without the overlay.
          if (state.active === false) this.endAnnotate(tab)
          else this.onPickState(tab, state)
        } catch {
          this.endAnnotate(tab)
        }
      }
    } finally {
      this.syncNotesPoll()
    }
  }

  private onPickState(tab: Tab, state: Record<string, unknown>): void {
    const scroll: [number, number] = Array.isArray(state.scroll) ? [Number(state.scroll[0]) || 0, Number(state.scroll[1]) || 0] : [0, 0]
    const viewport = toViewport(state.viewport)
    const rect = toRect(state.rect)
    const fresh = state.pick
    if (fresh && typeof fresh === 'object' && !Array.isArray(fresh) && typeof (fresh as Record<string, unknown>).id === 'string') {
      const data = fresh as HostPick['data']
      tab.pick = { data, draft: '', scroll, viewport }
      this.hideBox()
      void this.showBox(tab, rect ?? toRect(data.rect))
      return
    }
    const pick = tab.pick
    if (!pick) return
    // The page dropped it (a new click, Esc), or the document changed under it.
    if (state.picking !== pick.data.id) {
      tab.pick = null
      if (this.boxTab === tab) this.hideBox()
      return
    }
    const moved = scroll[0] !== pick.scroll[0] || scroll[1] !== pick.scroll[1]
    pick.scroll = scroll
    pick.viewport = viewport
    // The box is tied to where the target was: it goes while the page moves and comes back (with the draft) once it rests.
    if (this.boxTab === tab) {
      if (moved) this.hideBox()
    } else if (!moved && rect) {
      void this.showBox(tab, rect)
    }
  }

  // ---- comment box ----

  private ensureBox(): { view: WebContentsView; ready: Promise<void> } {
    if (this.noteBox) return this.noteBox
    const view = this.deps.createView({
      webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true, webviewTag: false, spellcheck: false, safeDialogs: true },
    })
    const wc = view.webContents
    try { view.setBackgroundColor('#00000000') } catch { /* older Electron */ }
    wc.setWindowOpenHandler(() => ({ action: 'deny' }))
    wc.on('will-navigate', (event) => event.preventDefault())
    wc.on('page-title-updated', (_event, title) => this.onBoxMessage(title))
    // Keys inside the box never reach the renderer either: the annotate chord is claimed here like in a tab.
    wc.on('before-input-event', (event, input) => {
      if (!isAnnotateChord(input, this.platform)) return
      event.preventDefault()
      const tab = this.boxTab
      if (tab) void this.setAnnotate(tab, !tab.annotating)
    })
    const ready = Promise.resolve(wc.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(NOTE_BOX_HTML)}`)).then(() => undefined, () => undefined)
    this.noteBox = { view, ready }
    return this.noteBox
  }

  private async showBox(tab: Tab, rect: { x: number; y: number; width: number; height: number } | null): Promise<void> {
    const win = this.deps.window()
    if (!win || win.isDestroyed() || !rect || !tab.pick) return
    const token = ++this.boxSeq
    let box: ReturnType<BrowserHost['ensureBox']>
    try { box = this.ensureBox() } catch { return }
    this.boxTab = tab
    this.boxRect = rect
    this.boxHeight = NOTE_BOX_MIN_HEIGHT
    await box.ready
    if (token !== this.boxSeq || this.boxTab !== tab || !tab.pick || this.stopped) return
    try {
      this.placeBox()
      // Re-added each time so the box sits above any tab view added since.
      win.contentView.removeChildView(box.view)
      win.contentView.addChildView(box.view)
      box.view.setVisible(true)
      const wc = box.view.webContents
      await wc.executeJavaScript(`window.__set(${JSON.stringify(tab.pick.draft)})`)
      if (token === this.boxSeq) wc.focus()
    } catch {
      this.log('[browser] note box failed')
    }
  }

  private placeBox(): void {
    if (!this.noteBox) return
    this.noteBox.view.setBounds(placeNoteBox(this.boxRect, this.bounds, { width: NOTE_BOX_WIDTH, height: this.boxHeight }))
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

  /** What the box reported through its title: the draft so far, a submit or a cancel. */
  private onBoxMessage(title: string): void {
    const tab = this.boxTab
    if (!tab || !tab.pick) return
    let message: Record<string, unknown>
    try {
      const parsed = JSON.parse(title)
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return
      message = parsed
    } catch { return }
    const text = typeof message.text === 'string' ? message.text.slice(0, NOTE_COMMENT_CHARS) : ''
    if (message.t === 'draft') {
      tab.pick.draft = text
      if (typeof message.h === 'number' && Number.isFinite(message.h)) {
        this.boxHeight = Math.max(NOTE_BOX_MIN_HEIGHT, Math.min(Math.round(message.h), NOTE_BOX_MAX_HEIGHT))
        this.placeBox()
      }
    } else if (message.t === 'cancel') this.cancelPick(tab)
    else if (message.t === 'submit') void this.savePick(tab, text)
  }

  private cancelPick(tab: Tab): void {
    const pick = tab.pick
    tab.pick = null
    this.hideBox()
    this.focusTab(tab)
    if (pick && this.isLive(tab)) void this.page(tab, 'dropPick', { pickId: pick.data.id }).catch(() => { /* the page is gone */ })
  }

  private focusTab(tab: Tab): void {
    try { tab.view.webContents.focus() } catch { /* destroyed */ }
  }

  /** Saving ends annotate mode here first (the one place that knows), then pins the target and hands the note to the renderer. */
  private async savePick(tab: Tab, text: string): Promise<void> {
    const pick = tab.pick
    const comment = text.trim()
    if (!pick || !comment) return
    tab.pick = null
    tab.annotating = false
    this.hideBox()
    this.focusTab(tab)
    this.emitLive(tab.conversationId)
    try {
      const pinned = await this.page(tab, 'commitPin', { pickId: pick.data.id, n: ++tab.pinSeq })
      if (pinned.error) return
      await this.page(tab, 'annotateOff', {})
    } catch {
      return
    }
    this.emitNotes(tab, [{ ...pick.data, comment }], pick.viewport)
  }

  private emitNotes(tab: Tab, notes: unknown[], viewport?: { width: number; height: number }): void {
    const win = this.deps.window()
    if (!win || win.isDestroyed()) return
    const wc = tab.view.webContents
    try {
      win.webContents.send(BROWSER_NOTES_CHANNEL, { conversationId: tab.conversationId, tabId: tab.id, url: wc.getURL(), title: wc.getTitle(), notes, ...(viewport ? { viewport } : {}) })
    } catch { /* window closing */ }
  }

  private async collectNotes(tab: Tab): Promise<{ notes: unknown[]; active: boolean }> {
    const result = await this.page(tab, 'takeNotes', {})
    return { notes: Array.isArray(result.notes) ? result.notes.slice(0, MAX_NOTES_PER_POLL) : [], active: result.active !== false }
  }

  /**
   * A picture of a tab on screen for a message that carries its notes: frames and pins are in the page, the comment box
   * is a separate view and the hover outline goes with the mode. Empty when the tab is not shown or the capture fails.
   */
  private async captureTab(tab: Tab): Promise<string> {
    if (this.visibleId !== tab.conversationId || this.selected.get(tab.conversationId) !== tab.id || !this.isLive(tab)) return ''
    if (this.boxTab === tab) this.hideBox()
    if (tab.annotating) {
      tab.annotating = false
      this.emitLive(tab.conversationId)
      try { await this.page(tab, 'annotateOff', {}) } catch { /* the page is gone */ }
    }
    let timer: ReturnType<typeof setTimeout> | undefined
    const timeout = new Promise<never>((_resolve, reject) => { timer = this.later(() => reject(new Error('capture_timeout')), CAPTURE_TIMEOUT_MS) })
    try {
      const image = await Promise.race([tab.view.webContents.capturePage(), timeout])
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
   * A new tab of the user's own, selected; with an address it is checked like every navigation. A tab a page
   * opened (`fromPage`) leaves the panel as it is: the chat is already on screen, and nothing may bring another forward.
   */
  private async openUserTab(conversationId: number, url?: unknown, fromPage = false): Promise<Record<string, unknown>> {
    if (this.clearing) return { error: 'clearing' }
    const epoch = this.lifecycleEpoch(conversationId)
    if (url !== undefined && !(await this.navigationAllowed(url))) return { error: 'url_not_allowed' }
    // A clear or the chat's deletion can happen during the lookup above.
    const stale = this.staleReply(conversationId, epoch)
    if (stale) return stale
    let tab: Tab
    try { tab = this.createTab(conversationId, false) } catch (error) { return { error: error instanceof Error ? error.message : 'no_window' } }
    this.selected.set(conversationId, tab.id)
    if (fromPage) this.layout()
    else this.show(conversationId)
    if (str(url)) tab.view.webContents.loadURL(url).catch(() => { /* reported by did-fail-load */ })
    this.emitLive(conversationId)
    return { ...this.livePayload(conversationId) }
  }

  /** The refusal for work that began before a clear started or its chat was closed, or null when it may go on. */
  private staleReply(conversationId: number, epoch: number): { error: string } | null {
    if (this.clearing) return { error: 'clearing' }
    return this.lifecycleEpoch(conversationId) === epoch ? null : { error: 'chat_closed' }
  }

  /** The tab goes back on the stack when it could not be reopened (too many tabs, address refused). */
  private async reopenTab(conversationId: number): Promise<Record<string, unknown>> {
    const last = this.closed.get(conversationId)?.pop()
    if (!last) return { error: 'nothing_to_reopen' }
    const epoch = this.lifecycleEpoch(conversationId)
    const result = await this.openUserTab(conversationId, last.url)
    if (result.error && this.lifecycleEpoch(conversationId) === epoch) {
      this.closed.set(conversationId, [...(this.closed.get(conversationId) ?? []), last].slice(-MAX_CLOSED_TABS))
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
      'select', 'close-tab', 'reopen-tab', 'annotate', 'notes', 'clear-notes', 'capture-notes']
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
        // The panel moved or resized (full screen, a side panel): the box is placed again from the next poll.
        if (before.x !== this.bounds.x || before.y !== this.bounds.y || before.width !== this.bounds.width || before.height !== this.bounds.height) this.hideBox()
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
        this.forgetPick(tab, true)
        tab.view.webContents.loadURL(url as string).catch(() => { /* reported by did-fail-load */ })
        this.emitLive(conversationId)
        return this.livePayload(conversationId)
      }
      case 'back':
      case 'forward':
      case 'reload': {
        const tab = this.chatTab(conversationId, tabId)
        if (!tab) return { error: 'no_tab' }
        this.touch(tab)
        tab.userTouched = true
        const wc = tab.view.webContents
        const history = wc.navigationHistory
        if (op !== 'reload' && (op === 'back' ? !history.canGoBack() : !history.canGoForward())) return { error: 'no_history' }
        this.forgetPick(tab, true)
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
      case 'notes': {
        const tab = this.chatTab(conversationId, tabId)
        if (!tab) return { error: 'no_tab' }
        try {
          const { notes } = await this.collectNotes(tab)
          const wc = tab.view.webContents
          return { notes, tabId: tab.id, url: wc.getURL(), title: wc.getTitle() }
        } catch {
          return { error: 'notes_failed' }
        }
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
        try {
          return await this.page(tab, 'clearNotes', ids === undefined ? {} : { ids })
        } catch {
          return { error: 'notes_failed' }
        }
      }
      case 'history':
        return { entries: this.deps.history?.list() ?? [] }
      case 'close': {
        this.closeGen.set(conversationId, (this.closeGen.get(conversationId) ?? 0) + 1)
        // Like a Stop: the chat's running and queued agent commands end without making a new tab.
        this.stopLocal(conversationId)
        for (const tab of [...this.tabs.values()]) if (tab.conversationId === conversationId) this.closeTab(tab)
        this.pageOpens.delete(conversationId)
        this.closed.delete(conversationId)
        // Only a deleted chat is closed; evict and clear keep the id so a Stop still reaches the backend.
        this.actedIn.delete(conversationId)
        return this.livePayload(conversationId)
      }
      case 'clear-browsing-data': {
        if (this.clearing) return { error: 'clearing' }
        this.clearing = true
        this.clearGen++
        // Every chat's running and queued agent commands end with the clear.
        this.stopLocal(null)
        // Tabs go first so no page writes new cookies between the clear and its close.
        this.destroyView()
        this.closed.clear()
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
          this.clearing = false
        }
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
        return this.loadResult(await this.waitForLoad(tab, () => { wc.loadURL(url as string).catch(() => { /* reported by did-fail-load */ }) }))
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
