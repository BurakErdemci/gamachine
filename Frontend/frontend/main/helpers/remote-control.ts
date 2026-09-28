/**
 * Remote control (phone) from the main process: the `remote-control` invoke
 * channel and the keep-awake blocker (docs/remote-control.md, step 4).
 *
 * Every /remote/* call goes through here, not only the four that need the UI
 * secret (enable, pair/start, pair/approve, PUT relay-url): the secret never
 * reaches page JS, and every answer that carries `keep_awake_active` updates
 * the power blocker at once.
 *
 * Keep-awake trigger: the backend's `keep_awake_active` (remote control on AND
 * the checkbox set) is the only input. It is read from every call's answer
 * and from a status poll every 30 s, which also covers an app start with
 * remote control already on and changes made by another window. Three failed
 * polls in a row (backend gone for ~90 s) release the blocker: without the
 * backend there is no remote control to stay awake for.
 *
 * Answers can arrive out of order (a poll may wait up to 20 s), so each
 * request is numbered. A state-changing action's answer counts only if no
 * newer state change started; any other answer counts only if it started
 * after the last state change settled and is newer than the last one that
 * counted. Disable and forget release the blocker before their request.
 */

export type RemoteAction =
  | 'status' | 'enable' | 'disable' | 'forget'
  | 'pair-start' | 'pair-pending' | 'pair-approve' | 'pair-reject'
  | 'devices' | 'remove-device' | 'remove-all-devices'
  | 'relay-url' | 'set-relay-url' | 'set-keep-awake'

interface Route {
  method: 'get' | 'post' | 'put' | 'delete'
  path: (arg: unknown) => string
  body?: (arg: unknown) => unknown
  uiSecret?: boolean
  /** Throws on an argument the route does not take. */
  check?: (arg: unknown) => void
}

const DEVICE_ID = /^[A-Za-z0-9_-]{1,64}$/
const RELAY_URL_MAX = 2048

const fixed = (p: string) => () => p

export const REMOTE_ROUTES: Record<RemoteAction, Route> = {
  'status': { method: 'get', path: fixed('/remote/status') },
  'enable': { method: 'post', path: fixed('/remote/enable'), uiSecret: true },
  'disable': { method: 'post', path: fixed('/remote/disable') },
  'forget': { method: 'post', path: fixed('/remote/forget') },
  'pair-start': { method: 'post', path: fixed('/remote/pair/start'), uiSecret: true },
  'pair-pending': { method: 'get', path: fixed('/remote/pair/pending') },
  'pair-approve': { method: 'post', path: fixed('/remote/pair/approve'), uiSecret: true },
  'pair-reject': { method: 'post', path: fixed('/remote/pair/reject') },
  'devices': { method: 'get', path: fixed('/remote/devices') },
  'remove-device': {
    method: 'delete',
    path: arg => `/remote/devices/${encodeURIComponent(arg as string)}`,
    check: arg => {
      if (typeof arg !== 'string' || !DEVICE_ID.test(arg)) throw new Error('bad device id')
    },
  },
  'remove-all-devices': { method: 'delete', path: fixed('/remote/devices') },
  'relay-url': { method: 'get', path: fixed('/remote/relay-url') },
  'set-relay-url': {
    method: 'put', path: fixed('/remote/relay-url'), uiSecret: true,
    body: arg => ({ url: arg }),
    check: arg => {
      if (arg !== null && (typeof arg !== 'string' || arg.length > RELAY_URL_MAX)) throw new Error('bad relay url')
    },
  },
  'set-keep-awake': {
    method: 'put', path: fixed('/remote/keep-awake'),
    body: arg => ({ enabled: arg }),
    check: arg => {
      if (typeof arg !== 'boolean') throw new Error('enabled must be a boolean')
    },
  },
}

export type RemoteResult =
  | { ok: true; data: any }
  | { ok: false; status?: number; code: string; message?: string }

export interface HttpClient {
  request(config: {
    method: string; url: string; data?: unknown; timeout: number; headers: Record<string, string>
  }): Promise<{ data: any }>
}

export interface PowerBlocker {
  start(type: 'prevent-app-suspension' | 'prevent-display-sleep'): number
  stop(id: number): void
  isStarted(id: number): boolean
}

export interface RemoteControlDeps {
  baseUrl: () => string
  appToken: string
  uiSecret: string
  http: HttpClient
  blocker: PowerBlocker
  pollMs?: number
  log?: (...args: unknown[]) => void
}

const STATE_CHANGES: ReadonlySet<RemoteAction> = new Set(['enable', 'disable', 'forget', 'set-keep-awake'])

export const POLL_MS = 30_000
const FAILS_BEFORE_RELEASE = 3
// pair/start waits for the relay socket (bridge.connect_wait_s); forget may
// open a temporary one for reset_room.
const TIMEOUT_MS = 20_000

export function createRemoteControl(deps: RemoteControlDeps) {
  let blockId: number | null = null
  let timer: ReturnType<typeof setInterval> | null = null
  let failedPolls = 0
  let seq = 0
  let latestChange = 0
  let changeBarrier = 0
  let changesInFlight = 0
  let lastObserved = 0
  const log = deps.log ?? (() => {})

  const setKeepAwake = (on: boolean) => {
    if (on && blockId === null) {
      blockId = deps.blocker.start('prevent-app-suspension')
      log('[remote] keep-awake on')
    } else if (!on && blockId !== null) {
      if (deps.blocker.isStarted(blockId)) deps.blocker.stop(blockId)
      blockId = null
      log('[remote] keep-awake off')
    }
  }

  const observe = (data: unknown, mySeq: number, isChange: boolean) => {
    if (!data || typeof data !== 'object' || typeof (data as any).keep_awake_active !== 'boolean') return
    const current = isChange
      ? mySeq === latestChange
      : changesInFlight === 0 && mySeq > changeBarrier && mySeq > lastObserved
    if (!current) return
    lastObserved = mySeq
    setKeepAwake((data as any).keep_awake_active)
  }

  const invoke = async (action: unknown, arg?: unknown): Promise<RemoteResult> => {
    if (typeof action !== 'string' || !Object.prototype.hasOwnProperty.call(REMOTE_ROUTES, action)) {
      throw new Error('Unknown remote-control action.')
    }
    const route = REMOTE_ROUTES[action as RemoteAction]
    route.check?.(arg)
    const isChange = STATE_CHANGES.has(action as RemoteAction)
    const headers: Record<string, string> = { 'X-Session-Token': deps.appToken }
    if (route.uiSecret) headers['X-Gamachine-UI-Secret'] = deps.uiSecret
    let base: string
    try {
      base = deps.baseUrl()
    } catch {
      return { ok: false, code: 'backend_not_ready' }
    }
    const mySeq = ++seq
    if (isChange) {
      latestChange = mySeq
      changeBarrier = mySeq
      changesInFlight += 1
      if (action === 'disable' || action === 'forget') setKeepAwake(false)
    }
    try {
      const res = await deps.http.request({
        method: route.method, url: `${base}${route.path(arg)}`,
        data: route.body?.(arg), timeout: TIMEOUT_MS, headers,
      })
      observe(res.data, mySeq, isChange)
      return { ok: true, data: res.data }
    } catch (error) {
      const response = (error as { response?: { status?: number; data?: { detail?: unknown } } })?.response
      const detail = response?.data?.detail
      if (detail && typeof detail === 'object' && typeof (detail as any).code === 'string') {
        const message = typeof (detail as any).message === 'string' ? (detail as any).message : undefined
        return { ok: false, status: response?.status, code: (detail as any).code, message }
      }
      if (response) {
        return { ok: false, status: response.status, code: 'http_error',
          message: typeof detail === 'string' ? detail : undefined }
      }
      return { ok: false, code: 'unreachable' }
    } finally {
      if (isChange) {
        changesInFlight -= 1
        changeBarrier = seq
      }
    }
  }

  const poll = async () => {
    const res = await invoke('status')
    if (res.ok) {
      failedPolls = 0
      return
    }
    failedPolls += 1
    if (failedPolls >= FAILS_BEFORE_RELEASE) setKeepAwake(false)
  }

  return {
    invoke,
    poll,
    start() {
      if (timer) return
      void poll()
      timer = setInterval(() => { void poll() }, deps.pollMs ?? POLL_MS)
    },
    shutdown() {
      if (timer) clearInterval(timer)
      timer = null
      setKeepAwake(false)
    },
    keepAwakeActive: () => blockId !== null,
  }
}
