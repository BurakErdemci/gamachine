import path from 'path'
import { randomBytes } from 'crypto'
import type fs from 'fs'
import type { ChildProcess, SpawnOptions } from 'child_process'
import type { Socket } from 'net'
import type { HttpClient } from './remote-control'

export interface ComputerUseDeps {
  platform: string
  spawn: (command: string, args: string[], options: SpawnOptions) => ChildProcess
  connect: (socket: string, connected: () => void) => Socket
  fs: Pick<typeof fs, 'existsSync' | 'mkdirSync' | 'chmodSync' | 'mkdtempSync' | 'unlinkSync' | 'rmdirSync'>
  http: HttpClient
  baseUrl: () => string
  appToken: string
  uiSecret: string
  systemPreferences: {
    isTrustedAccessibilityClient(prompt: boolean): boolean
    getMediaAccessStatus(media: 'screen'): string
  }
  shell: { openExternal(url: string): Promise<void> }
  paths: { resourcesPath?: string; repoRoot: string; userData: string; tmpdir: string }
  now?: () => number
  setTimeout?: typeof setTimeout
  clearTimeout?: typeof clearTimeout
  log?: (...args: unknown[]) => void
}

/** Owns one daemon; queued transitions prevent overlapping starts and stops. */
export class ComputerUseHost {
  running = false
  socketPath: string | null = null
  private child: ChildProcess | null = null
  private fallbackDir: string | null = null
  private stopped = false
  private intentionalStop = false
  private crashed = false
  private lastAutoRestart = -Infinity
  private queue: Promise<unknown> = Promise.resolve()
  private reports: Promise<unknown> = Promise.resolve()
  private pollTimer: ReturnType<typeof setTimeout> | null = null
  private cancelProbe: (() => void) | null = null
  private readonly now: () => number
  private readonly later: typeof setTimeout
  private readonly clear: typeof clearTimeout
  private readonly log: (...args: unknown[]) => void

  constructor(private readonly deps: ComputerUseDeps) {
    this.now = deps.now ?? Date.now
    this.later = deps.setTimeout ?? setTimeout
    this.clear = deps.clearTimeout ?? clearTimeout
    this.log = deps.log ?? (() => {})
  }

  driverPath(): string {
    const p = this.deps.paths
    return p.resourcesPath ? path.join(p.resourcesPath, 'cua', 'cua-driver')
      : path.join(p.repoRoot, 'Backend', 'vendor', 'cua', 'cua-driver')
  }

  get available(): boolean {
    return this.deps.platform === 'darwin' && this.deps.fs.existsSync(this.driverPath())
  }

  private enqueue<T>(operation: () => Promise<T>): Promise<T> {
    const result = this.queue.then(operation)
    this.queue = result.catch(() => {})
    return result
  }

  private request(method: string, route: string, data?: unknown, secret = false) {
    const headers: Record<string, string> = { 'X-Session-Token': this.deps.appToken }
    if (secret) headers['X-Gamachine-UI-Secret'] = this.deps.uiSecret
    return this.deps.http.request({ method, url: `${this.deps.baseUrl()}${route}`, data, headers, timeout: 10_000 })
  }

  private report() {
    const data = { running: this.running, driver_path: this.running ? this.driverPath() : null,
      socket_path: this.running ? this.socketPath : null }
    // An exit's stopped report must settle before a replacement's running report.
    this.reports = this.reports.then(() => this.request('post', '/computer-use/runtime', data, true))
      .catch(error => { this.log('[cua] runtime report failed', error) })
    return this.reports
  }

  private cleanSocket() {
    if (this.socketPath) {
      try { this.deps.fs.unlinkSync(this.socketPath) } catch { /* already absent */ }
      this.socketPath = null
    }
    if (this.fallbackDir) {
      try { this.deps.fs.rmdirSync(this.fallbackDir) } catch { /* only remove an empty private directory */ }
      this.fallbackDir = null
    }
  }

  private preparePaths() {
    const root = path.join(this.deps.paths.userData, 'cua')
    const home = path.join(root, 'home')
    for (const dir of [root, home]) {
      this.deps.fs.mkdirSync(dir, { recursive: true, mode: 0o700 })
      this.deps.fs.chmodSync(dir, 0o700)
    }
    const name = `d-${randomBytes(4).toString('hex')}.sock`
    this.socketPath = path.join(root, name)
    if (Buffer.byteLength(this.socketPath) >= 100) {
      this.fallbackDir = this.deps.fs.mkdtempSync(path.join(this.deps.paths.tmpdir, 'cua-'))
      this.deps.fs.chmodSync(this.fallbackDir, 0o700)
      this.socketPath = path.join(this.fallbackDir, name)
      if (Buffer.byteLength(this.socketPath) >= 100) {
        this.cleanSocket()
        throw new Error('cua socket path is too long')
      }
    }
    return home
  }

  private probe(socketPath: string, timeout: number): Promise<boolean> {
    return new Promise(resolve => {
      let socket: Socket | undefined
      let done = false
      const finish = (ok: boolean) => {
        if (done) return
        done = true
        this.clear(timer)
        socket?.destroy()
        this.cancelProbe = null
        resolve(ok)
      }
      const timer = this.later(() => finish(false), timeout)
      this.cancelProbe = () => finish(false)
      try {
        socket = this.deps.connect(socketPath, () => finish(true))
        socket.once('error', () => finish(false))
        if (done) socket.destroy()
      } catch { finish(false) }
    })
  }

  start(): Promise<void> { return this.enqueue(() => this.startChild()) }

  private async startChild() {
    if (this.stopped || this.child || !this.available) return
    const home = this.preparePaths()
    const env: NodeJS.ProcessEnv = {}
    for (const key of ['PATH', 'HOME', 'TMPDIR']) {
      if (process.env[key] !== undefined) env[key] = process.env[key]
    }
    Object.assign(env, {
      CUA_DRIVER_EMBEDDED: '1', DO_NOT_TRACK: '1', CUA_DRIVER_RS_TELEMETRY_ENABLED: '0',
      CUA_DRIVER_RS_UPDATE_CHECK: '0', CUA_DRIVER_DISABLE_RUN_SCRIPT: '1',
      CUA_DRIVER_HOST_BUNDLE_ID: 'com.gamachine.app',
      CUA_DRIVER_RS_HOME: home, CUA_DRIVER_TELEMETRY_HOME: home,
    })
    this.intentionalStop = false
    try {
      const child = this.deps.spawn(this.driverPath(), ['serve', '--embedded', '--socket', this.socketPath!,
        '--permission-mode', 'standard', '--host-bundle-id', 'com.gamachine.app', '--no-permissions-gate'],
      { env, stdio: ['ignore', 'pipe', 'pipe'] })
      this.child = child
      const exited = () => {
        if (this.child !== child) return
        this.child = null
        this.running = false
        this.cancelProbe?.()
        if (!this.intentionalStop) {
          this.crashed = true
          this.cleanSocket()
          void this.report()
        }
      }
      child.once('exit', exited)
      child.once('error', error => { this.log('[cua] child error', error); exited() })
      child.stdout?.on('data', chunk => this.log(`[cua] ${chunk.toString().trim()}`))
      child.stderr?.on('data', chunk => this.log(`[cua] ${chunk.toString().trim()}`))
      const deadline = this.now() + 10_000
      while (this.child === child && !this.stopped && this.now() < deadline) {
        if (await this.probe(this.socketPath!, Math.min(100, deadline - this.now()))) {
          if (this.child !== child || this.stopped) break
          this.running = true
          this.crashed = false
          await this.report()
          return
        }
        if (this.child !== child || this.stopped) break
        await new Promise<void>(resolve => this.later(resolve, 100))
      }
      throw new Error('cua socket did not become ready within 10 seconds')
    } catch (error) {
      this.crashed = true
      await this.stopChild()
      throw error
    }
  }

  stop(): Promise<void> { return this.enqueue(() => this.stopChild()) }

  private async stopChild() {
    const child = this.child
    this.intentionalStop = true
    this.running = false
    if (child) {
      await new Promise<void>(resolve => {
        const finish = () => { this.clear(timer); child.removeListener('exit', finish); resolve() }
        const timer = this.later(() => {
          if (this.child === child) child.kill('SIGKILL')
          finish()
        }, 2000)
        child.once('exit', finish)
        child.kill('SIGTERM')
      })
      if (this.child === child) this.child = null
    }
    this.cleanSocket()
    await this.report()
  }

  reconcile(): Promise<void> {
    return this.enqueue(async () => {
      if (this.stopped) return
      const { data: settings } = await this.request('get', '/computer-use/settings')
      if (this.stopped) return
      if (this.available && settings.enabled === true && settings.mode === 'auto') {
        if (this.child) return
        if (this.crashed) {
          if (this.now() - this.lastAutoRestart < 60_000) return
          this.lastAutoRestart = this.now()
        }
        await this.startChild()
      } else await this.stopChild()
    })
  }

  beginPolling() {
    if (this.stopped || this.pollTimer) return
    const poll = async () => {
      try { await this.reconcile() } catch (error) { this.log('[cua] reconcile failed', error) }
      if (!this.stopped) this.pollTimer = this.later(() => { void poll() }, 5000)
    }
    // Reserve the timer before the first request, making repeated calls idempotent.
    this.pollTimer = this.later(() => { void poll() }, 0)
  }

  shutdown(force = false): Promise<void> {
    this.stopped = true
    if (this.pollTimer) this.clear(this.pollTimer)
    this.pollTimer = null
    this.cancelProbe?.()
    // The process exit hook cannot wait for timers or backend requests.
    if (force) {
      this.intentionalStop = true
      this.child?.kill('SIGKILL')
      this.child = null
      this.running = false
      this.cleanSocket()
      return Promise.resolve()
    }
    return this.stop()
  }

  async invoke(req: unknown): Promise<unknown> {
    if (!req || typeof req !== 'object' || Array.isArray(req)) return { error: 'Invalid computer-use request.' }
    const { op, bundleId, gateId, approved, remember } = req as Record<string, unknown>
    try {
      switch (op) {
        case 'status': {
          const { data: settings } = await this.request('get', '/computer-use/settings')
          return { available: this.available, running: this.running, settings, permissions: {
            accessibility: this.deps.platform === 'darwin' && this.deps.systemPreferences.isTrustedAccessibilityClient(false),
            screen: this.deps.platform === 'darwin' ? this.deps.systemPreferences.getMediaAccessStatus('screen') : 'not-determined',
          } }
        }
        case 'enable':
        case 'disable': {
          const { data } = await this.request('post', '/computer-use/settings', { enabled: op === 'enable' }, true)
          await this.reconcile()
          return data
        }
        case 'request-accessibility':
          return { accessibility: this.deps.platform === 'darwin' && this.deps.systemPreferences.isTrustedAccessibilityClient(true) }
        case 'open-screen-settings':
          if (this.deps.platform === 'darwin') {
            await this.deps.shell.openExternal('x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture')
          }
          return { opened: this.deps.platform === 'darwin' }
        case 'restart':
          await this.stop()
          await this.reconcile()
          return { running: this.running }
        case 'remove-grant':
          if (typeof bundleId !== 'string' || !/^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$/.test(bundleId) || bundleId.length > 255) break
          return (await this.request('delete', `/computer-use/grants/${encodeURIComponent(bundleId)}`, undefined, true)).data
        case 'answer-card':
          if (typeof gateId !== 'string' || !/^[A-Za-z0-9_-]{1,200}$/.test(gateId)
            || typeof approved !== 'boolean' || typeof remember !== 'boolean') break
          return (await this.request('post', `/mcp-approval-respond/${encodeURIComponent(gateId)}`, { approved, remember }, true)).data
        default: break
      }
      return { error: 'Unknown operation or malformed computer-use arguments.' }
    } catch {
      // Do not serialize HTTP errors: their request config carries both secrets.
      return { error: 'Computer-use request failed.' }
    }
  }
}
