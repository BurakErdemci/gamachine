import React from 'react'

import { RemoteGate } from '../../components/remote/RemotePairing'
import { cevir, type TKey } from '../i18n'
import { installFetchShim, installAxiosShim } from './httpShim'
import { createIpcShim } from './ipcShim'
import { RemoteTransport, isRemotePage, type LinkStatus } from './transport'

// Remote mode (docs/remote-control-web.md W2): the renderer served by the relay, without
// Electron. The pairing, keys and encrypted Link are the v1 phone page's own modules, loaded
// from the relay at run time so both pages share one implementation and one IndexedDB record.

/** No preload bridge and served over https from a non-app origin. */
export function isRemoteMode(win: any = typeof window === 'undefined' ? undefined : window): boolean {
  if (!win || win.ipc) return false
  return isRemotePage(win)
}

export type PairStep = 'idle' | 'working' | 'failed'
export type Connection = 'connecting' | 'ready' | 'offline' | 'rejected'

export interface RemoteView {
  /** 'pairing' until the first session is up; after that the app stays mounted. */
  screen: 'loading' | 'pairing' | 'app'
  paired: boolean
  pairStep: PairStep
  sas: string | null
  /** An i18n key plus values: a pairing error or a notice such as removal. */
  message: { key: string; values?: Record<string, string> } | null
  connection: Connection
  lastSeen: number | null
  rejectReason: string | null
}

const PAIR_ERRORS: Record<string, string> = {
  no_room: 'remoteWeb.err.noRoom',
  pc_offline: 'remoteWeb.err.pcOffline',
  refused: 'remoteWeb.err.refused',
  rejected: 'remoteWeb.err.rejected',
  timeout: 'remoteWeb.err.timeout',
  bad_reply: 'remoteWeb.err.badReply',
  expired: 'remoteWeb.err.expired',
  rate_limited: 'remoteWeb.err.rateLimited',
}

const DEVICE_KEY = 'device'

// Kept out of webpack's graph: these files exist only on the relay origin.
const loadRelayModule = (path: string): Promise<any> => import(/* webpackIgnore: true */ path)

export class RemoteSession {
  readonly transport = new RemoteTransport()
  view: RemoteView = {
    screen: 'loading',
    paired: false,
    pairStep: 'idle',
    sas: null,
    message: null,
    connection: 'connecting',
    lastSeen: null,
    rejectReason: null,
  }
  private viewListeners = new Set<(v: RemoteView) => void>()
  private unavailableListeners = new Set<(channel: string) => void>()
  private mods: { net: any; C: any; store: any } | null = null
  private link: any = null
  private started = false

  subscribe(listener: (v: RemoteView) => void): () => void {
    this.viewListeners.add(listener)
    return () => this.viewListeners.delete(listener)
  }

  onUnavailable(listener: (channel: string) => void): () => void {
    this.unavailableListeners.add(listener)
    return () => this.unavailableListeners.delete(listener)
  }

  notifyUnavailable = (channel: string): void => {
    this.unavailableListeners.forEach((l) => l(channel))
  }

  private set(patch: Partial<RemoteView>): void {
    this.view = { ...this.view, ...patch }
    this.viewListeners.forEach((l) => l(this.view))
  }

  async start(): Promise<void> {
    if (this.started) return
    this.started = true
    try {
      const [net, C, store] = await Promise.all(['/net.js', '/crypto.js', '/store.js'].map(loadRelayModule))
      this.mods = { net, C, store }
    } catch (err) {
      console.error('[remote] relay modules', err)
      this.set({ screen: 'pairing', pairStep: 'failed', message: { key: 'remoteWeb.err.loadFailed' } })
      return
    }
    let device: any = null
    try {
      device = (await this.mods.store.get(DEVICE_KEY)) || null
    } catch {}
    window.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') this.link?.wake()
    })
    window.addEventListener('online', () => this.link?.wake())

    const hash = window.location.hash
    const parsed = hash.length > 1 ? this.mods.C.parsePairFragment(hash) : null
    if (hash.length > 1) this.stripHash()
    if (parsed) {
      await this.pair(parsed)
      return
    }
    if (device) this.connect(device)
    else this.set({ screen: 'pairing' })
  }

  /** The secret in the fragment must not stay in the address bar or history. */
  private stripHash(): void {
    const { pathname, search } = window.location
    window.history.replaceState(window.history.state, '', pathname + search)
  }

  /** Pairs from a pasted link or bare fragment (relay/public/app.js pasteToFragment). */
  async pairFromText(text: string): Promise<void> {
    const v = text.trim()
    const fail = (key: string, values?: Record<string, string>) =>
      this.set({ pairStep: 'failed', message: { key, values } })
    if (!this.mods) return fail('remoteWeb.err.loadFailed')
    const hashAt = v.indexOf('#')
    if (hashAt < 0) return fail('remoteWeb.err.missingFragment')
    if (hashAt > 0) {
      let url: URL
      try {
        url = new URL(v)
      } catch {
        return fail('remoteWeb.err.invalidLink')
      }
      if (url.origin !== window.location.origin) return fail('remoteWeb.err.otherOrigin', { origin: url.origin })
    }
    const parsed = this.mods.C.parsePairFragment(v.slice(hashAt))
    if (!parsed) return fail('remoteWeb.err.damagedLink')
    await this.pair(parsed)
  }

  private async pair(parsed: any): Promise<void> {
    const { net, C, store } = this.mods!
    this.set({ screen: 'pairing', pairStep: 'working', sas: null, message: null })
    try {
      const res = await net.pair({
        origin: net.wsOrigin(window.location),
        parsed,
        deviceName: 'Tarayıcı',
        onSas: (sas: string) => this.set({ sas }),
      })
      this.link?.stop()
      const device = {
        pairId: parsed.pairId,
        pcPub: C.b64u(parsed.pcPub),
        deviceId: res.deviceId,
        token: res.token,
        vapidPub: res.vapidPub,
        privateKey: res.privateKey,
        publicRaw: C.b64u(res.publicRaw),
        pairedAt: Date.now(),
        pushDone: false,
      }
      await store.put(DEVICE_KEY, device)
      this.set({ sas: null, pairStep: 'idle' })
      this.connect(device)
    } catch (err) {
      const code = err instanceof Error ? err.message : String(err)
      this.set({
        sas: null,
        pairStep: 'failed',
        message: {
          key: 'remoteWeb.err.retry',
          values: {
            error: PAIR_ERRORS[code] ? cevir(PAIR_ERRORS[code] as TKey) : cevir('remoteWeb.err.unknown', { error: code }),
          },
        },
      })
    }
  }

  private connect(d: any): void {
    const { net, C } = this.mods!
    this.link?.stop()
    const link = new net.Link({
      origin: net.wsOrigin(window.location),
      device: { pairId: d.pairId, pcPub: C.fromB64u(d.pcPub), deviceId: d.deviceId, token: d.token, privateKey: d.privateKey },
      onStatus: (status: LinkStatus, info?: any) => {
        if (this.link === link) this.onLinkStatus(status, info)
      },
      onPush: (obj: unknown) => {
        if (this.link === link) this.transport.handlePush(obj)
      },
    })
    this.link = link
    this.transport.attach(link)
    this.set({ paired: true, connection: 'connecting', rejectReason: null })
    if (this.view.screen === 'loading') this.set({ screen: 'pairing' })
    link.start().catch((err: unknown) => {
      console.error('[remote] link start', err)
      this.set({ connection: 'rejected', rejectReason: 'unknown' })
    })
  }

  private onLinkStatus(status: LinkStatus, info?: any): void {
    this.transport.handleStatus(status, info)
    if (status === 'removed' || (status === 'hello_rejected' && info?.reason === 'unknown_device')) {
      void this.forget({ key: 'remoteWeb.removed' })
      return
    }
    if (status === 'ready') this.set({ screen: 'app', connection: 'ready', lastSeen: null, rejectReason: null })
    else if (status === 'connecting') this.set({ connection: 'connecting' })
    else if (status === 'pc_offline') this.set({ connection: 'offline', lastSeen: info?.lastSeen ?? null })
    else if (status === 'hello_rejected') this.set({ connection: 'rejected', rejectReason: info?.reason ?? 'unknown' })
  }

  /** Drops the pairing on this browser and returns to the pairing screen. */
  async forget(message: RemoteView['message'] = null): Promise<void> {
    const link = this.link
    this.link = null
    link?.stop()
    this.transport.handleStatus('removed')
    try {
      await this.mods?.store.del(DEVICE_KEY)
    } catch {}
    this.set({ screen: 'pairing', paired: false, pairStep: 'idle', sas: null, message, connection: 'connecting' })
  }
}

let session: RemoteSession | null = null

/** The running remote session, or null on the desktop. */
export function remoteSession(): RemoteSession | null {
  return session
}

/**
 * Wraps the Next app for remote mode; on the desktop (window.ipc present) returns `App` itself.
 * The shims go in while _app's module evaluates, before any page module runs: several hooks
 * read window.ipc once at module load (useAppInitialization.ts).
 */
export function withRemoteMode<P extends object>(App: React.ComponentType<P>): React.ComponentType<P> {
  if (!isRemoteMode()) return App
  if (!session) {
    session = new RemoteSession()
    ;(window as any).ipc = createIpcShim(session.transport, session.notifyUnavailable)
    installAxiosShim(session.transport)
    installFetchShim(session.transport)
  }
  const current = session
  function RemoteApp(props: P) {
    return React.createElement(RemoteGate, { session: current }, React.createElement(App, props))
  }
  return RemoteApp
}
