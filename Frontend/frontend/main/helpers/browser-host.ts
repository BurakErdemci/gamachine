import type { NativeImage, Rectangle, Session, WebContents, WebContentsView, WebContentsViewConstructorOptions } from 'electron'
import type { HttpClient } from './remote-control'
import type { BrowserCommand } from './browser-server'
import { isIP } from 'net'
import { isAllowedNavigation, isPrivateAddress, resolvesToPrivate, type LookupAll } from './browser-url-policy'

export const BROWSER_PARTITION = 'gamachine-agent-browser'
export const BROWSER_LIVE_CHANNEL = 'browser-live'
// Electron reserves 0 (page) and 999 (its own isolated world); any other id up to 2^29 is free.
export const BROWSER_WORLD_ID = 1777
export const NAVIGATION_TIMEOUT_MS = 30_000
const SCRIPT_TIMEOUT_MS = 10_000
const HOST_CACHE_MS = 60_000
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
  /** Called on every halt so the command server can drop what is still queued. */
  onHalt?: () => void
  now?: () => number
  setTimeout?: typeof setTimeout
  clearTimeout?: typeof clearTimeout
  log?: (...args: unknown[]) => void
}

export interface BrowserLivePayload {
  active: boolean
  conversationId: number | null
  url: string
  title: string
  loading: boolean
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
  const inputType = (el) => String(el.getAttribute('type') || 'text').toLowerCase();
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
      const r = resolve(a.snapshotId, a.ref);
      if (r.error) return r;
      const el = r.el;
      const t = tag(el);
      const element = { role: roleOf(el), name: nameOf(el), tag: t };
      if (t === 'input') element.type = inputType(el);
      if (t === 'button') element.type = String(el.getAttribute('type') || 'submit').toLowerCase();
      if (t === 'a') {
        if (el.href) element.href = el.href;
        element.download = el.hasAttribute('download');
      }
      element.is_password = t === 'input' && inputType(el) === 'password';
      const form = formOf(el);
      if (form) {
        const submitter = (t === 'button' && element.type === 'submit') || (t === 'input' && (element.type === 'submit' || element.type === 'image'));
        // Enter in a text field submits when the form has a submit button or the field is its only text field.
        const typing = t === 'input' && !BUTTON_TYPES.concat(NON_TEXT_TYPES).includes(element.type);
        const textFields = Array.from(form.elements).filter((f) => tag(f) === 'input' && !BUTTON_TYPES.concat(NON_TEXT_TYPES).includes(inputType(f)));
        const hasSubmit = !!form.querySelector('button:not([type=button]):not([type=reset]), input[type=submit], input[type=image]');
        element.submits_form = submitter || (typing && (hasSubmit || textFields.length === 1));
        const method = String((submitter && el.getAttribute('formmethod')) || form.getAttribute('method') || 'get').toLowerCase();
        element.form_method = ['post', 'dialog'].includes(method) ? method : 'get';
        element.form_action = (submitter && el.getAttribute('formaction') ? el.formAction : form.action) || location.href;
        element.form_has_password = !!form.querySelector('input[type=password]');
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

/** One agent-driven page in a hardened WebContentsView over the main window, created on the first command. */
export class BrowserHost {
  private view: WebContentsView | null = null
  private hardened = false
  private active = false
  private conversationId: number | null = null
  private bounds: Rectangle = { x: 0, y: 0, width: 0, height: 0 }
  private visible = false
  private popupBlocked: string | null = null
  private snapshotSeq = 0
  private loadWaiter: ((result: LoadResult) => void) | null = null
  private readonly hostCache = new Map<string, { blocked: true; at: number }>()
  private runtime: { port: number; token: string } | null = null
  private stopped = false
  private pollTimer: ReturnType<typeof setTimeout> | null = null
  private reports: Promise<unknown> = Promise.resolve()
  private readonly now: () => number
  private readonly later: typeof setTimeout
  private readonly clear: typeof clearTimeout
  private readonly log: (...args: unknown[]) => void

  constructor(private readonly deps: BrowserHostDeps) {
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

  private report() {
    const data = this.runtimeBody()
    this.reports = this.reports.then(() => this.request('post', '/browser/runtime', data, true))
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

  /** Reports running=false, stops polling and destroys the view. */
  shutdown(): Promise<void> {
    if (this.stopped) return this.reports.then(() => undefined)
    this.stopped = true
    if (this.pollTimer) this.clear(this.pollTimer)
    this.pollTimer = null
    this.destroyView()
    return this.report().then(() => undefined)
  }

  // ---- view and session ----

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

  private checkConnectedIp(details: { url: string; ip?: string }): void {
    const ip = (details.ip || '').replace(/^\[|\]$/g, '')
    if (!ip || !isPrivateAddress(ip)) return
    let host: string
    try { host = new URL(details.url).hostname } catch { return }
    // A literal host was already judged by the static policy, so the connection is not a rebinding.
    if (isIP(host.replace(/^\[|\]$/g, ''))) return
    this.hostCache.set(host.toLowerCase(), { blocked: true, at: this.now() })
    const wc = this.view && !this.view.webContents.isDestroyed() ? this.view.webContents : null
    this.settleLoad({ ok: false, error: 'private_address' })
    if (wc) {
      wc.stop()
      wc.loadURL('about:blank').catch(() => { /* nothing left to report */ })
    }
    this.emitLive('private_address')
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

  private ensureView(): WebContentsView {
    if (this.view && !this.view.webContents.isDestroyed()) return this.view
    if (this.view) this.destroyView()
    const win = this.deps.window()
    if (!win || win.isDestroyed()) throw new Error('no_window')
    const session = this.deps.fromPartition(BROWSER_PARTITION)
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
    this.wire(view.webContents)
    win.contentView.addChildView(view)
    view.setBounds(this.bounds)
    view.setVisible(this.visible)
    this.view = view
    return view
  }

  private wire(wc: WebContents): void {
    wc.setWindowOpenHandler(({ url }) => {
      this.popupBlocked = url
      return { action: 'deny' }
    })
    const refuse = (event: { url: string; isMainFrame: boolean; preventDefault(): void }) => {
      if (isAllowedNavigation(event.url)) return
      event.preventDefault()
      // A refused main-frame navigation ends as ERR_ABORTED, which waitForLoad ignores.
      if (event.isMainFrame) this.settleLoad({ ok: false, error: 'url_not_allowed' })
    }
    wc.on('will-navigate', refuse)
    wc.on('will-redirect', refuse)
    wc.on('will-frame-navigate', refuse)
    wc.on('will-attach-webview', (event) => event.preventDefault())
    wc.on('did-finish-load', () => this.settleLoad({ ok: true }))
    wc.on('did-fail-load', (_event, code, description, _url, isMainFrame) => {
      // -3 is ERR_ABORTED: a new navigation replaced this one, and its own load event will follow.
      if (isMainFrame && code !== -3) this.settleLoad({ ok: false, error: `load_failed: ${description || code}` })
    })
    wc.on('render-process-gone', () => this.settleLoad({ ok: false, error: 'page_crashed' }))
    for (const name of ['did-start-loading', 'did-stop-loading', 'page-title-updated', 'did-navigate', 'did-navigate-in-page'] as const) {
      wc.on(name as 'did-start-loading', () => this.emitLive())
    }
  }

  /** Closes the page; the next command opens a fresh one. Called when the main window goes away. */
  destroyView(): void {
    const view = this.view
    this.view = null
    this.active = false
    this.settleLoad({ ok: false, error: 'halted' })
    if (!view) return
    const win = this.deps.window()
    try { if (win && !win.isDestroyed()) win.contentView.removeChildView(view) } catch { /* window closing */ }
    try { if (!view.webContents.isDestroyed()) view.webContents.close() } catch { /* already closed */ }
    this.emitLive()
  }

  // ---- renderer channel ----

  livePayload(): BrowserLivePayload {
    const wc = this.view && !this.view.webContents.isDestroyed() ? this.view.webContents : null
    return {
      active: this.active,
      conversationId: this.conversationId,
      url: wc ? wc.getURL() : '',
      title: wc ? wc.getTitle() : '',
      loading: wc ? wc.isLoading() : false,
    }
  }

  private emitLive(error?: string): void {
    const win = this.deps.window()
    if (!win || win.isDestroyed()) return
    try { win.webContents.send(BROWSER_LIVE_CHANNEL, error ? { ...this.livePayload(), error } : this.livePayload()) } catch { /* window closing */ }
  }

  async invoke(req: unknown): Promise<unknown> {
    if (!req || typeof req !== 'object' || Array.isArray(req)) return { error: 'Invalid browser-live request.' }
    const { op, bounds, visible, gateId, approved } = req as Record<string, unknown>
    switch (op) {
      case 'status':
        return { ...this.livePayload(), visible: this.visible, bounds: this.bounds }
      case 'set-bounds': {
        const b = bounds as Record<string, unknown> | null
        if (!b || typeof b !== 'object' || ![b.x, b.y, b.width, b.height].every(finiteNonNegative)) break
        this.bounds = { x: Math.round(b.x as number), y: Math.round(b.y as number),
          width: Math.round(b.width as number), height: Math.round(b.height as number) }
        this.view?.setBounds(this.bounds)
        return { bounds: this.bounds }
      }
      case 'visible':
        if (typeof visible !== 'boolean') break
        this.visible = visible
        this.view?.setVisible(visible)
        return { visible }
      case 'halt': {
        const conversationId = this.active ? this.conversationId : null
        this.halt()
        // The backend halt set also covers browser tools, so the agent's next call is refused too.
        if (conversationId !== null) {
          try { await this.request('post', `/computer-use/halt/${conversationId}`, undefined, true) } catch { this.log('[browser] backend halt failed') }
        }
        return { halted: true }
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

  /** Stops loading and wakes a waiting navigation; queued commands are dropped through onHalt. */
  halt(): void {
    this.deps.onHalt?.()
    this.settleLoad({ ok: false, error: 'halted' })
    const wc = this.view?.webContents
    if (wc && !wc.isDestroyed()) wc.stop()
    this.emitLive()
  }

  // ---- command handling ----

  private settleLoad(result: LoadResult): void {
    const waiter = this.loadWaiter
    this.loadWaiter = null
    waiter?.(result)
  }

  private waitForLoad(start: () => void): Promise<LoadResult> {
    this.settleLoad({ ok: false, error: 'superseded' })
    return new Promise<LoadResult>((resolve) => {
      const timer = this.later(() => {
        if (this.loadWaiter === done) this.settleLoad({ ok: false, error: 'navigation_timeout' })
      }, NAVIGATION_TIMEOUT_MS)
      const done = (result: LoadResult) => {
        this.clear(timer)
        resolve(result)
      }
      this.loadWaiter = done
      try { start() } catch (error) { this.settleLoad({ ok: false, error: error instanceof Error ? error.message : 'load_failed' }) }
    })
  }

  private async page(fn: string, args: Record<string, unknown>, userGesture = false): Promise<Record<string, unknown>> {
    const wc = this.view!.webContents
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

  private base(wc: WebContents): { url: string; title: string } {
    return wc.isDestroyed() ? { url: '', title: '' } : { url: wc.getURL(), title: wc.getTitle() }
  }

  /** Entry point for the command server; always resolves to a reply object. */
  async handle(cmd: BrowserCommand): Promise<Reply> {
    if (Number.isInteger(cmd.conversation_id)) this.conversationId = cmd.conversation_id as number
    if (cmd.op === 'halt') {
      this.halt()
      const wc = this.view?.webContents
      return { ok: true, ...(wc ? this.base(wc) : { url: '', title: '' }) }
    }
    let view: WebContentsView
    try {
      view = this.ensureView()
    } catch (error) {
      return { ok: false, error: error instanceof Error ? error.message : 'no_window', url: '', title: '' }
    }
    this.active = true
    this.emitLive()
    const wc = view.webContents
    let reply: Reply
    try {
      const extra = await this.run(wc, cmd.op, cmd.args && typeof cmd.args === 'object' ? cmd.args : {})
      reply = { ok: !extra.error, ...extra, ...this.base(wc) } as Reply
      if (!extra.error) delete reply.error
    } catch (error) {
      reply = { ok: false, error: error instanceof Error ? error.message : String(error), ...this.base(wc) }
    }
    if (this.popupBlocked) {
      reply.popup_blocked = this.popupBlocked
      this.popupBlocked = null
    }
    this.emitLive()
    return reply
  }

  private async run(wc: WebContents, op: string, args: Record<string, unknown>): Promise<Record<string, unknown>> {
    const ref = { snapshotId: args.snapshot_id, ref: args.ref }
    const needsRef = () => str(args.snapshot_id) && str(args.ref)
    switch (op) {
      case 'navigate': {
        const url = args.url
        if (!str(url) || !isAllowedNavigation(url)) return { error: 'url_not_allowed' }
        if (await this.hostBlocked(new URL(url).hostname)) return { error: 'url_not_allowed' }
        return this.loadResult(await this.waitForLoad(() => { wc.loadURL(url).catch(() => { /* reported by did-fail-load */ }) }))
      }
      case 'back':
      case 'forward': {
        const history = wc.navigationHistory
        if (op === 'back' ? !history.canGoBack() : !history.canGoForward()) return { error: 'no_history' }
        return this.loadResult(await this.waitForLoad(() => (op === 'back' ? history.goBack() : history.goForward())))
      }
      case 'reload':
        return this.loadResult(await this.waitForLoad(() => wc.reload()))
      case 'snapshot': {
        const requested = Number(args.max_chars)
        const maxChars = Number.isFinite(requested) && requested > 0 ? Math.min(Math.floor(requested), MAX_TEXT_CHARS) : 20_000
        const snapshotId = `s${++this.snapshotSeq}`
        const result = await this.page('snapshot', { snapshotId, maxChars })
        return result.error ? result : { ...result, snapshot_id: snapshotId }
      }
      case 'click':
        if (!needsRef()) return { error: 'bad_args' }
        return this.page('click', ref, true)
      case 'fill':
        if (!needsRef() || typeof args.value !== 'string') return { error: 'bad_args' }
        if (args.value.length > MAX_FILL_CHARS) return { error: 'value_too_long' }
        return this.page('fill', { ...ref, value: args.value }, true)
      case 'select':
        if (!needsRef() || typeof args.option !== 'string') return { error: 'bad_args' }
        return this.page('select', { ...ref, option: args.option }, true)
      case 'describe':
        if (!needsRef()) return { error: 'bad_args' }
        return this.page('describe', ref)
      case 'scroll': {
        if (args.direction !== 'up' && args.direction !== 'down') return { error: 'bad_args' }
        const amount = finiteNonNegative(args.amount) ? Math.min(args.amount, 100_000) : 0
        return this.page('scroll', { direction: args.direction, amount })
      }
      case 'press': {
        const keyCode = typeof args.key === 'string' ? KEYS[args.key] : undefined
        if (!keyCode) return { error: 'key_not_allowed' }
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

  private loadResult(result: LoadResult): Record<string, unknown> {
    return 'error' in result ? { error: result.error } : {}
  }
}
