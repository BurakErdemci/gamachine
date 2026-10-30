import type { NativeImage, Rectangle, Session, WebContents, WebContentsView, WebContentsViewConstructorOptions } from 'electron'
import type { HttpClient } from './remote-control'
import type { BrowserCommand } from './browser-server'
import { isIP } from 'net'
import { isAllowedNavigation, isPrivateAddress, resolvesToPrivate, type LookupAll } from './browser-url-policy'
import type { BrowserHistory } from './browser-history'

// persist: keeps one login for every chat's tab and across app restarts.
export const BROWSER_PARTITION = 'persist:gamachine-agent-browser'
export const BROWSER_LIVE_CHANNEL = 'browser-live'
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
  /** Set on the event that reports a blocked private address. */
  error?: string
}

type Reply = { ok: boolean; error?: string; url: string; title: string; [key: string]: unknown }
type LoadResult = { ok: true } | { ok: false; error: string }

// Runs inside the isolated world, so it must be plain browser JavaScript. The world's
// globals survive between calls on one document and vanish with it, which is what
// makes refs from an earlier page come back as stale_snapshot.
const PAGE_LIB = String.raw`(() => {
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
  };
})();`

function str(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0
}

function finiteNonNegative(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
}


function positiveInt(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value > 0
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

/** One page: a chat's agent tab, or a tab the user opened. */
interface Tab {
  id: number
  conversationId: number
  agent: boolean
  view: WebContentsView
  favicon: string
  snapshotSeq: number
  loadWaiter: ((result: LoadResult) => void) | null
  popupBlocked: string | null
  userControl: boolean
  /** When the user last pressed a key or mouse button in this page (not the host's own `press`); null if never. */
  lastGesture: number | null
  /** The user pressed a key or mouse button in this page since the chat's last message; the agent may not switch to it. */
  userTouched: boolean
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

/**
 * Hardened WebContentsViews over the main window, all on one persistent session: per chat one agent tab
 * (the only page agent commands reach) plus any tabs the user opened.
 */
export class BrowserHost {
  private readonly tabs = new Map<number, Tab>()
  private nextTabId = 0
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
    const tab: Tab = { id: ++this.nextTabId, conversationId, agent, view, favicon: '', snapshotSeq: 0, loadWaiter: null,
      popupBlocked: null, userControl: false, lastGesture: null, userTouched: false, faviconSeq: 0, gone, markGone, expected: [], busy: 0, lastUsed: 0 }
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
    if (agent) agent.agent = false
    next.agent = true
    next.expected = []
    this.emitLive(conversationId)
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
    const { op, conversationId: id, bounds, visible, url, gateId, approved, tabId } = req as Record<string, unknown>
    const tabOps = ['status', 'panel-status', 'set-bounds', 'visible', 'open', 'navigate', 'back', 'forward', 'reload', 'take-over', 'release', 'close',
      'select', 'close-tab', 'reopen-tab']
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
        this.bounds = { x: Math.round(b.x as number), y: Math.round(b.y as number),
          width: Math.round(b.width as number), height: Math.round(b.height as number) }
        for (const tab of this.tabsOf(conversationId)) tab.view.setBounds(this.bounds)
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
        const wc = tab.view.webContents
        const history = wc.navigationHistory
        if (op === 'reload') wc.reload()
        else if (op === 'back' ? !history.canGoBack() : !history.canGoForward()) return { error: 'no_history' }
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
        if (op === 'select') this.selectTab(tab)
        else this.closeByUser(tab)
        return this.livePayload(conversationId)
      }
      case 'reopen-tab':
        return this.reopenTab(conversationId)
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
    if (cmd.op === 'halt') return null
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
    const tabs = this.tabsOf(conversationId).map((tab) => ({ id: tab.id, title: tab.view.webContents.getTitle(),
      url: tab.agent ? tab.view.webContents.getURL() : originOf(tab.view.webContents.getURL()), active: tab === shown, agent: tab.agent, userTouched: tab.userTouched }))
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
    if (next.userTouched) return failed('user_control')
    if ((agent?.busy ?? 0) > 0) return failed('busy')
    if (agent) {
      agent.agent = false
      this.setUserControl(agent, false)
    }
    next.agent = true
    next.expected = []
    this.touch(next)
    this.emitLive(conversationId)
    return { ok: true, ...this.base(next.view.webContents) }
  }

  /** `halted` tells whether a Stop arrived since the command began; checked after every await that precedes an action. */
  private async run(tab: Tab, op: string, args: Record<string, unknown>, halted: () => boolean): Promise<Record<string, unknown>> {
    const wc = tab.view.webContents
    const ref = { snapshotId: args.snapshot_id, ref: args.ref }
    const needsRef = () => str(args.snapshot_id) && str(args.ref)
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
        const snapshotId = `s${++tab.snapshotSeq}`
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
