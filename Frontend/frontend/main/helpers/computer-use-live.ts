import path from 'path'
import type { ChildProcess, SpawnOptions } from 'child_process'
import type { HttpClient } from './remote-control'

/** One computer call the backend let through (Backend/app/computer_use/activity.py). */
export interface LiveAction {
  tool: string | null
  bundle_id: string | null
  app_name: string | null
  pid: number | null
  window_id: number | null
  at: number
  decision: string
}

export interface LiveTarget {
  bundle_id: string | null
  app_name: string | null
  pid: number | null
  window_id: number | null
}

/** What every renderer window receives on the 'computer-use-live' event channel. */
export interface LivePayload {
  active: boolean
  running: boolean
  conversationId: number | null
  startedAt: number | null
  last: LiveAction | null
  target: LiveTarget | null
  /** A PNG data URL, or null on a state-only update (keep showing the previous frame). */
  frame: string | null
  /** The last Stop did not reach the backend; the run is still live and Stop can be retried. */
  haltError: boolean
  at: number
}

/** The slice of ComputerUseHost this module reads; the halt itself is the host's job. */
export interface LiveHost {
  running: boolean
  socketPath: string | null
  driverPath(): string
  invoke(req: unknown): Promise<unknown>
}

export interface LiveWindow {
  isDestroyed(): boolean
  webContents: { send(channel: string, ...args: unknown[]): void }
}

export interface IndicatorWindow extends LiveWindow {
  close(): void
  showInactive(): void
  getPosition(): number[]
  setAlwaysOnTop(flag: boolean, level?: any): void
  setVisibleOnAllWorkspaces?(visible: boolean, options?: Record<string, boolean>): void
  on(event: string, listener: () => void): unknown
  once(event: string, listener: () => void): unknown
}

export interface Rect { x: number; y: number; width: number; height: number }

export interface ComputerLiveDeps {
  host: LiveHost
  http: HttpClient
  baseUrl: () => string
  appToken: string
  platform: string
  userData: string
  spawn: (command: string, args: string[], options: SpawnOptions) => ChildProcess
  /** Every open renderer window (the indicator included). */
  windows: () => LiveWindow[]
  newIndicator: (options: Record<string, unknown>) => IndicatorWindow
  loadIndicator: (win: IndicatorWindow) => Promise<unknown> | void
  preload: string
  workAreas: () => { primary: Rect; all: Rect[] }
  shortcut: { register(accelerator: string, callback: () => void): boolean; unregister(accelerator: string): void }
  fs: { readFileSync(file: string, encoding: 'utf8'): string; writeFileSync(file: string, data: string, options?: unknown): void }
  /** Bring the main window forward on the right-panel computer tab. */
  focusMain: () => void
  now?: () => number
  setTimeout?: typeof setTimeout
  clearTimeout?: typeof clearTimeout
  log?: (...args: unknown[]) => void
}

export const LIVE_CHANNEL = 'computer-use-live'
export const LIVE_OPEN_EVENT = 'computer-use-live-open'
export const HALT_SHORTCUT = 'CommandOrControl+Shift+Escape'
export const POLL_MS = 1000
// ~8 fps: a 480 px frame took a median 66 ms on the real driver (15 Oct 2026),
// so 125 ms leaves the driver idle half the time.
export const FRAME_MS = 125
export const IDLE_MS = 10_000
export const MAX_DIMENSION = 480
const REQUEST_MS = 3000
const WINDOW_LOOKUP_MS = 5000
export const INDICATOR_SIZE = { width: 320, height: 64 }
const INDICATOR_MARGIN = 16
const POSITION_FILE = 'computer-live-indicator.json'

interface Pending { resolve: (value: any) => void; reject: (error: Error) => void; timer: ReturnType<typeof setTimeout> }

interface Proxy {
  child: ChildProcess
  socketPath: string
  nextId: number
  pending: Map<number, Pending>
  buffer: string
  ready: Promise<void>
  dead: boolean
}

const isInt = (value: unknown): value is number => typeof value === 'number' && Number.isInteger(value)
const str = (value: unknown) => (typeof value === 'string' && value ? value : null)
const int = (value: unknown) => (isInt(value) ? value : null)

function parseAction(raw: any): LiveAction | null {
  if (!raw || typeof raw !== 'object') return null
  return {
    tool: str(raw.tool), bundle_id: str(raw.bundle_id), app_name: str(raw.app_name),
    pid: int(raw.pid), window_id: int(raw.window_id), at: isInt(raw.at) ? raw.at : 0,
    decision: str(raw.decision) ?? '',
  }
}

function parseTarget(raw: any): LiveTarget | null {
  if (!raw || typeof raw !== 'object' || !isInt(raw.pid) || raw.pid <= 0) return null
  return { bundle_id: str(raw.bundle_id), app_name: str(raw.app_name), pid: raw.pid, window_id: int(raw.window_id) }
}

/** Rows of a list_windows answer, from structured content or a JSON text block. */
function windowRows(result: any): any[] {
  const sources: unknown[] = []
  if (result?.structuredContent !== undefined) sources.push(result.structuredContent)
  for (const block of Array.isArray(result?.content) ? result.content : []) {
    if (block?.type === 'text' && typeof block.text === 'string') {
      try { sources.push(JSON.parse(block.text)) } catch { /* plain text, not a listing */ }
    }
  }
  for (const source of sources) {
    const rows = Array.isArray(source) ? source : (source as any)?.windows
    if (Array.isArray(rows)) return rows
  }
  return []
}

/** The window to mirror: on screen first, then frontmost (lowest z_index). */
export function pickWindow(result: unknown, pid: number): number | null {
  const rows = windowRows(result)
    .filter(row => row && isInt(row.window_id) && (row.pid === undefined || row.pid === pid))
  rows.sort((a, b) => (Number(b.is_on_screen !== false) - Number(a.is_on_screen !== false))
    || ((isInt(a.z_index) ? a.z_index : Infinity) - (isInt(b.z_index) ? b.z_index : Infinity)))
  return rows.length ? rows[0].window_id : null
}

function frameOf(result: any): string | null {
  if (!result || result.isError) return null
  for (const block of Array.isArray(result.content) ? result.content : []) {
    if (block?.type === 'image' && typeof block.data === 'string' && block.data) {
      const mime = typeof block.mimeType === 'string' && /^image\/[a-z]+$/.test(block.mimeType) ? block.mimeType : 'image/png'
      return `data:${mime};base64,${block.data}`
    }
  }
  return null
}

/**
 * Live presence while the agent drives the computer: polls the backend's
 * activity, mirrors the driven window through its own read-only driver proxy,
 * shows a small always-on-top indicator, and holds the halt shortcut.
 *
 * The proxy only ever calls `get_window_state` and `list_windows`; every
 * input tool stays behind the backend's approval gate.
 */
export class ComputerLive {
  private pollTimer: ReturnType<typeof setTimeout> | null = null
  private stopped = false
  private live = false
  private conversationId: number | null = null
  private startedAt: number | null = null
  private last: LiveAction | null = null
  private target: LiveTarget | null = null
  private lastActiveSeen = 0
  private haltedAt: number | null = null
  private haltError = false
  private readonly haltInFlight = new Map<number, Promise<unknown>>()
  private panelVisible = false
  private indicator: IndicatorWindow | null = null
  private shortcutOn = false
  private proxy: Proxy | null = null
  private captureToken = 0
  private capturing = false
  private windowCache: { pid: number; windowId: number | null; at: number } | null = null
  private readonly now: () => number
  private readonly later: typeof setTimeout
  private readonly clear: typeof clearTimeout
  private readonly log: (...args: unknown[]) => void

  constructor(private readonly deps: ComputerLiveDeps) {
    this.now = deps.now ?? Date.now
    this.later = deps.setTimeout ?? setTimeout
    this.clear = deps.clearTimeout ?? clearTimeout
    this.log = deps.log ?? (() => {})
  }

  /** True for the indicator window, so the caller can tell it from the main window. */
  isIndicator(win: unknown): boolean {
    return win != null && win === this.indicator
  }

  beginPolling() {
    if (this.stopped || this.pollTimer) return
    this.pollTimer = this.later(() => { void this.poll() }, 0)
  }

  private async poll() {
    try { await this.tick() } catch (error) { this.log('[cua-live] poll failed', error instanceof Error ? error.message : String(error)) }
    if (!this.stopped) this.pollTimer = this.later(() => { void this.poll() }, POLL_MS)
  }

  /** One poll step; public so tests can drive it without the timer. */
  async tick() {
    if (this.stopped) return
    if (!this.deps.host.running) {
      this.goIdle()
      return
    }
    let data: any = null
    try {
      data = (await this.deps.http.request({
        method: 'get', url: `${this.deps.baseUrl()}/computer-use/activity`,
        headers: { 'X-Session-Token': this.deps.appToken }, timeout: REQUEST_MS,
      })).data
    } catch {
      // A missed poll is no news; the idle timer below still runs.
    }
    if (this.stopped) return
    if (data) this.apply(data)
    if (this.live && this.now() - this.lastActiveSeen > IDLE_MS) this.goIdle()
    else if (this.live) this.emit(null)
  }

  private apply(data: any) {
    const last = parseAction(data.last)
    const conversationId = isInt(data.conversation_id) && data.conversation_id > 0 ? data.conversation_id : null
    if (data.active !== true || conversationId === null || !last) return
    // After a halt, only an action newer than the halt (the user's next turn) counts.
    if (this.haltedAt !== null && last.at <= this.haltedAt) return
    this.lastActiveSeen = this.now()
    // A capture in flight belongs to the old conversation/target; its reply must never wear the new labels.
    let identityChanged = false
    if (conversationId !== this.conversationId) {
      identityChanged = true
      this.conversationId = conversationId
      // A failed Stop belonged to the previous conversation.
      this.haltError = false
      this.startedAt = isInt(data.started_at) ? data.started_at : this.now()
    }
    this.last = last
    const target = parseTarget(data.target)
    if (target?.pid !== this.target?.pid || target?.window_id !== this.target?.window_id) {
      this.windowCache = null
      identityChanged = true
    }
    if (identityChanged) {
      this.captureToken++
      this.capturing = false
    }
    this.target = target
    if (!this.live) {
      this.live = true
      this.registerShortcut()
      this.openIndicator()
    }
    this.updateCapture()
  }

  private goIdle() {
    const wasLive = this.live
    this.live = false
    this.conversationId = null
    this.startedAt = null
    this.last = null
    this.target = null
    this.windowCache = null
    this.haltError = false
    this.unregisterShortcut()
    this.closeIndicator()
    this.stopCapture()
    if (wasLive) this.emit(null)
  }

  private payload(frame: string | null): LivePayload {
    return {
      active: this.live, running: this.deps.host.running, conversationId: this.conversationId,
      startedAt: this.startedAt, last: this.last, target: this.target, frame, haltError: this.haltError, at: this.now(),
    }
  }

  private emit(frame: string | null) {
    const payload = this.payload(frame)
    for (const win of this.deps.windows()) {
      // Frames are ~100 KB each; the indicator never draws them.
      if (win.isDestroyed() || (frame !== null && win === this.indicator)) continue
      try { win.webContents.send(LIVE_CHANNEL, payload) } catch { /* window closing */ }
    }
  }

  status() {
    const { frame: _frame, ...rest } = this.payload(null)
    return { ...rest, panelVisible: this.panelVisible }
  }

  async invoke(req: unknown): Promise<unknown> {
    if (!req || typeof req !== 'object' || Array.isArray(req)) return { error: 'Invalid computer-use-live request.' }
    const { op, visible, conversationId } = req as Record<string, unknown>
    switch (op) {
      case 'status':
        return this.status()
      case 'panel-visible':
        if (typeof visible !== 'boolean') break
        this.panelVisible = visible
        this.updateCapture()
        return { panelVisible: visible }
      case 'halt':
        return this.halt()
      case 'halted':
        // The panel already called the host's halt itself; only forget the run here.
        if (!isInt(conversationId)) break
        if (conversationId === this.conversationId) {
          this.haltedAt = this.now()
          this.goIdle()
        }
        return { halted: true }
      case 'open-panel':
        this.deps.focusMain()
        return { opened: true }
      default: break
    }
    return { error: 'Unknown operation or malformed computer-use-live arguments.' }
  }

  /** Stop: the host's halt contract removes the computer tools for the rest of the turn. */
  halt(): Promise<unknown> {
    const conversationId = this.conversationId
    if (conversationId === null) return Promise.resolve({ error: 'Computer use is not active.' })
    // Presses for the same conversation while its halt is in flight share one answer.
    const existing = this.haltInFlight.get(conversationId)
    if (existing) return existing
    const run = this.runHalt(conversationId).finally(() => {
      if (this.haltInFlight.get(conversationId) === run) this.haltInFlight.delete(conversationId)
    })
    this.haltInFlight.set(conversationId, run)
    return run
  }

  private async runHalt(conversationId: number): Promise<unknown> {
    let result: unknown
    try {
      result = await this.deps.host.invoke({ op: 'halt', conversationId })
    } catch {
      result = { error: 'Computer-use halt failed.' }
    }
    // Only the halt contract's own reply counts; anything else did not stop the run.
    const failed = !result || typeof result !== 'object' || Array.isArray(result)
      || (result as Record<string, unknown>).error !== undefined || (result as Record<string, unknown>).halted !== true
    if (failed) {
      // Nothing was halted: keep the run visible and the shortcut armed so Stop can be retried.
      this.log('[cua-live] halt failed')
      if (this.live && this.conversationId === conversationId) {
        this.haltError = true
        this.emit(null)
      }
      const reply = result as Record<string, unknown> | null
      return reply && typeof reply === 'object' && !Array.isArray(reply) && reply.error !== undefined
        ? result : { error: 'Computer-use halt failed.' }
    }
    // A newer run that started while the halt was in flight is not the one the host stopped.
    if (this.conversationId === conversationId) {
      this.haltedAt = this.now()
      this.goIdle()
    }
    return result
  }

  // --- shortcut ---------------------------------------------------------

  private registerShortcut() {
    if (this.shortcutOn) return
    try {
      this.shortcutOn = this.deps.shortcut.register(HALT_SHORTCUT, () => { void this.halt().catch(() => {}) })
      if (!this.shortcutOn) this.log('[cua-live] halt shortcut is taken by another app')
    } catch (error) {
      this.log('[cua-live] halt shortcut failed', error instanceof Error ? error.message : String(error))
    }
  }

  private unregisterShortcut() {
    if (!this.shortcutOn) return
    this.shortcutOn = false
    try { this.deps.shortcut.unregister(HALT_SHORTCUT) } catch { /* already gone at quit */ }
  }

  // --- indicator --------------------------------------------------------

  private positionFile() { return path.join(this.deps.userData, POSITION_FILE) }

  private indicatorPosition(): { x: number; y: number } {
    const { primary, all } = this.deps.workAreas()
    const { width, height } = INDICATOR_SIZE
    try {
      const saved = JSON.parse(this.deps.fs.readFileSync(this.positionFile(), 'utf8'))
      // A saved spot counts only while it still lies fully on some display.
      if (isInt(saved?.x) && isInt(saved?.y) && all.some(area => saved.x >= area.x && saved.y >= area.y
        && saved.x + width <= area.x + area.width && saved.y + height <= area.y + area.height)) {
        return { x: saved.x, y: saved.y }
      }
    } catch { /* first run or unreadable: fall back to the default spot */ }
    return { x: primary.x + primary.width - width - INDICATOR_MARGIN, y: primary.y + INDICATOR_MARGIN }
  }

  private savePosition(win: IndicatorWindow) {
    try {
      const [x, y] = win.getPosition()
      if (isInt(x) && isInt(y)) this.deps.fs.writeFileSync(this.positionFile(), JSON.stringify({ x, y }), { mode: 0o600 })
    } catch (error) {
      this.log('[cua-live] indicator position not saved', error instanceof Error ? error.message : String(error))
    }
  }

  private openIndicator() {
    if (this.indicator && !this.indicator.isDestroyed()) return
    const { x, y } = this.indicatorPosition()
    let win: IndicatorWindow
    try {
      win = this.deps.newIndicator({
        x, y, ...INDICATOR_SIZE,
        frame: false, transparent: true, hasShadow: false, resizable: false, movable: true,
        minimizable: false, maximizable: false, fullscreenable: false, closable: true,
        alwaysOnTop: true, skipTaskbar: true, show: false, acceptFirstMouse: true,
        title: 'Gamachine',
        // A non-activating panel: clicking Stop or dragging never pulls focus from the user's app.
        ...(this.deps.platform === 'darwin' ? { type: 'panel' } : {}),
        // A window shown without focus counts as hidden to Chromium, which then stops painting it
        // (measured live: the tag stayed grey until clicked).
        webPreferences: { preload: this.deps.preload, backgroundThrottling: false },
      })
    } catch (error) {
      this.log('[cua-live] indicator failed', error instanceof Error ? error.message : String(error))
      return
    }
    this.indicator = win
    try {
      win.setAlwaysOnTop(true, 'floating')
      // skipTransformProcessType keeps the Dock icon; without it macOS hides it.
      win.setVisibleOnAllWorkspaces?.(true, { visibleOnFullScreen: true, skipTransformProcessType: true })
    } catch { /* platform without workspaces */ }
    win.once('ready-to-show', () => { if (!win.isDestroyed() && this.indicator === win) win.showInactive() })
    win.on('moved', () => this.savePosition(win))
    win.on('closed', () => { if (this.indicator === win) this.indicator = null })
    try {
      const loading = this.deps.loadIndicator(win)
      if (loading && typeof (loading as Promise<unknown>).catch === 'function') {
        (loading as Promise<unknown>).catch(error => this.log('[cua-live] indicator page failed', String(error)))
      }
    } catch (error) {
      this.log('[cua-live] indicator page failed', String(error))
    }
  }

  private closeIndicator() {
    const win = this.indicator
    this.indicator = null
    if (win && !win.isDestroyed()) {
      try { win.close() } catch { /* already closing */ }
    }
  }

  // --- frames -----------------------------------------------------------

  private wantsFrames() {
    return this.live && this.deps.host.running && !!this.deps.host.socketPath && !!this.target?.pid
      // The indicator draws no frames; only a visible Computer tab needs them.
      && this.panelVisible
  }

  private updateCapture() {
    if (this.wantsFrames()) {
      if (!this.capturing) {
        this.capturing = true
        const token = ++this.captureToken
        void this.captureLoop(token)
      }
    } else {
      this.stopCapture()
    }
  }

  private stopCapture() {
    this.captureToken++
    this.capturing = false
    this.killProxy()
  }

  private sleep(ms: number) {
    return new Promise<void>(resolve => this.later(resolve, ms))
  }

  private async captureLoop(token: number) {
    while (token === this.captureToken && !this.stopped) {
      const started = this.now()
      try {
        const frame = await this.captureOnce(token)
        if (token !== this.captureToken) break
        if (frame) this.emit(frame)
      } catch (error) {
        if (token !== this.captureToken) break
        this.log('[cua-live] frame failed', error instanceof Error ? error.message : String(error))
        this.killProxy()
        await this.sleep(POLL_MS)
        continue
      }
      await this.sleep(Math.max(0, FRAME_MS - (this.now() - started)))
    }
  }

  private async captureOnce(token: number): Promise<string | null> {
    const target = this.target
    if (!target?.pid) return null
    const proxy = await this.ensureProxy()
    let windowId = target.window_id
    if (windowId === null) {
      const cached = this.windowCache
      if (cached && cached.pid === target.pid && this.now() - cached.at < WINDOW_LOOKUP_MS) {
        windowId = cached.windowId
      } else {
        const listing = await this.call(proxy, 'list_windows', { pid: target.pid })
        // A listing issued before a target/conversation change must not seed the new target's cache.
        if (token !== this.captureToken) return null
        windowId = pickWindow(listing, target.pid)
        this.windowCache = { pid: target.pid, windowId, at: this.now() }
      }
      if (windowId === null) return null
    }
    const result = await this.call(proxy, 'get_window_state', {
      pid: target.pid, window_id: windowId, include_accessibility_tree: false, max_dimension: MAX_DIMENSION,
    })
    if (token !== this.captureToken) return null
    const frame = frameOf(result)
    // The window may have closed; look it up again next time.
    if (!frame) this.windowCache = null
    return frame
  }

  // --- driver proxy (MCP over stdio) -------------------------------------

  private proxyEnv(): NodeJS.ProcessEnv {
    // The same minimal environment ComputerUseHost gives the daemon: no app token, no UI secret.
    const home = path.join(this.deps.userData, 'cua', 'home')
    const env = {} as NodeJS.ProcessEnv
    for (const key of ['PATH', 'HOME', 'TMPDIR']) {
      if (process.env[key] !== undefined) env[key] = process.env[key]
    }
    Object.assign(env, {
      CUA_DRIVER_EMBEDDED: '1', DO_NOT_TRACK: '1', CUA_DRIVER_RS_TELEMETRY_ENABLED: '0',
      CUA_DRIVER_RS_UPDATE_CHECK: '0', CUA_DRIVER_DISABLE_RUN_SCRIPT: '1',
      CUA_DRIVER_HOST_BUNDLE_ID: 'com.gamachine.app',
      CUA_DRIVER_RS_HOME: home, CUA_DRIVER_TELEMETRY_HOME: home,
    })
    return env
  }

  private async ensureProxy(): Promise<Proxy> {
    const socketPath = this.deps.host.socketPath
    if (!socketPath) throw new Error('driver socket is not ready')
    if (this.proxy && (this.proxy.dead || this.proxy.socketPath !== socketPath)) this.killProxy()
    if (!this.proxy) this.proxy = this.spawnProxy(socketPath)
    const proxy = this.proxy
    await proxy.ready
    return proxy
  }

  private spawnProxy(socketPath: string): Proxy {
    const child = this.deps.spawn(this.deps.host.driverPath(), ['mcp', '--embedded', '--socket', socketPath],
      { env: this.proxyEnv(), stdio: ['pipe', 'pipe', 'pipe'] })
    const proxy: Proxy = { child, socketPath, nextId: 1, pending: new Map(), buffer: '', ready: Promise.resolve(), dead: false }
    const fail = (why: string) => {
      if (proxy.dead) return
      proxy.dead = true
      for (const entry of proxy.pending.values()) { this.clear(entry.timer); entry.reject(new Error(why)) }
      proxy.pending.clear()
      if (this.proxy === proxy) this.proxy = null
    }
    child.once('exit', () => fail('driver proxy exited'))
    child.stdin?.on('error', () => fail('driver proxy input closed'))
    child.once('error', error => { this.log('[cua-live] proxy error', error.message); fail('driver proxy failed') })
    child.stderr?.on('data', chunk => this.log(`[cua-live] ${chunk.toString().trim().slice(0, 500)}`))
    child.stdout?.on('data', chunk => {
      proxy.buffer += chunk.toString()
      let newline: number
      while ((newline = proxy.buffer.indexOf('\n')) >= 0) {
        const line = proxy.buffer.slice(0, newline).trim()
        proxy.buffer = proxy.buffer.slice(newline + 1)
        if (!line) continue
        let message: any
        try { message = JSON.parse(line) } catch { continue }
        const entry = isInt(message?.id) ? proxy.pending.get(message.id) : undefined
        if (!entry) continue
        proxy.pending.delete(message.id)
        this.clear(entry.timer)
        if (message.error) entry.reject(new Error(String(message.error?.message ?? 'driver error')))
        else entry.resolve(message.result)
      }
    })
    proxy.ready = this.rpc(proxy, 'initialize', {
      protocolVersion: '2024-11-05', capabilities: {}, clientInfo: { name: 'gamachine-live', version: '1' },
    }).then(() => { this.write(proxy, { jsonrpc: '2.0', method: 'notifications/initialized' }) })
    // A failed handshake surfaces on the awaiting call; this keeps it from going unhandled.
    proxy.ready.catch(() => {})
    return proxy
  }

  private write(proxy: Proxy, message: unknown) {
    proxy.child.stdin?.write(JSON.stringify(message) + '\n')
  }

  private rpc(proxy: Proxy, method: string, params: unknown): Promise<any> {
    if (proxy.dead) return Promise.reject(new Error('driver proxy is gone'))
    const id = proxy.nextId++
    return new Promise((resolve, reject) => {
      const timer = this.later(() => {
        proxy.pending.delete(id)
        reject(new Error(`${method} timed out`))
      }, REQUEST_MS)
      proxy.pending.set(id, { resolve, reject, timer })
      try { this.write(proxy, { jsonrpc: '2.0', id, method, params }) } catch (error) {
        proxy.pending.delete(id)
        this.clear(timer)
        reject(error instanceof Error ? error : new Error(String(error)))
      }
    })
  }

  /** The only driver tools this module may call: both read-only. */
  private call(proxy: Proxy, name: 'get_window_state' | 'list_windows', args: Record<string, unknown>) {
    return this.rpc(proxy, 'tools/call', { name, arguments: args })
  }

  private killProxy(force = false) {
    const proxy = this.proxy
    this.proxy = null
    if (!proxy) return
    for (const entry of proxy.pending.values()) { this.clear(entry.timer); entry.reject(new Error('driver proxy stopped')) }
    proxy.pending.clear()
    proxy.dead = true
    try { proxy.child.kill(force ? 'SIGKILL' : 'SIGTERM') } catch { /* already exited */ }
  }

  shutdown(force = false) {
    this.stopped = true
    if (this.pollTimer) this.clear(this.pollTimer)
    this.pollTimer = null
    this.captureToken++
    this.capturing = false
    this.live = false
    this.unregisterShortcut()
    this.closeIndicator()
    this.killProxy(force)
  }
}
