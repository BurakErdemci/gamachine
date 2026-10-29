// Browser side of the web remote control (docs/remote-control-web.md, "Frame contract").
// Everything the renderer asks of the desktop goes through one RemoteTransport over the v1
// encrypted Link (relay/public/net.js). The Link is passed in, so tests drive a fake one.

/** The part of relay/public/net.js `Link` the transport uses. */
export interface RemoteLink {
  /** Resolves with `{id, ok, result}` or `{id, ok: false, error}`; rejects on transport failure. */
  request(type: string, params?: Record<string, unknown>): Promise<RemoteReply>
}

export interface RemoteReply {
  id?: number
  ok: boolean
  result?: any
  error?: string
  [extra: string]: unknown
}

/** A refused request: `code` is the desktop's error (remote_unavailable, remote_forbidden, ...)
 *  or the transport's (not_ready, disconnected, timeout, too_large). */
export class RemoteError extends Error {
  code: string
  reply?: RemoteReply

  constructor(code: string, reply?: RemoteReply) {
    super(code)
    this.name = 'RemoteError'
    this.code = code
    this.reply = reply
  }
}

export type LinkStatus = 'connecting' | 'ready' | 'pc_offline' | 'removed' | 'hello_rejected'

export interface StreamHandlers {
  onChunk(bytes: Uint8Array): void
  onEnd(error?: string): void
}

// relay/worker/room.js caps a browser frame at 64 KiB of sealed text. Sealing turns the JSON
// plaintext into base64url (x4/3) plus a short envelope, so the plaintext must stay under
// ~48 KiB; a part carries its bytes as base64 inside that JSON, hence 32 KiB per part.
export const INLINE_BODY_MAX = 46 * 1024
export const UPLOAD_PART_BYTES = 32 * 1024
export const UPLOAD_TOTAL_MAX = 32 * 1024 * 1024
const READY_WAIT_MS = 15_000

const encoder = new TextEncoder()

export function toBase64(bytes: Uint8Array): string {
  let bin = ''
  for (let i = 0; i < bytes.length; i += 0x8000) {
    bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  }
  return btoa(bin)
}

export function fromBase64(text: string): Uint8Array {
  const bin = atob(text.replace(/-/g, '+').replace(/_/g, '/'))
  const out = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i)
  return out
}

function randomId(): string {
  const bytes = new Uint8Array(12)
  crypto.getRandomValues(bytes)
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

/** An HTTP request as the shims hand it over; `body` is text, `bytes` binary (never both). */
export interface HttpRequest {
  method: string
  path: string
  query?: Record<string, string | string[]>
  body?: string
  bytes?: Uint8Array
  contentType?: string
}

export class RemoteTransport {
  private status: LinkStatus = 'connecting'
  private readyWaiters: Array<() => void> = []
  private streams = new Map<string, StreamHandlers>()
  // Chunks that arrive between the desktop opening a stream and the open reply reaching us.
  private early = new Map<string, Array<{ type: string; data_b64?: string; error?: string }>>()
  private ipcListeners = new Map<string, Set<(...args: unknown[]) => void>>()
  private statusListeners = new Set<(status: LinkStatus, info?: unknown) => void>()

  // The shims exist from page load, before pairing gives us a Link; requests wait for one.
  constructor(private link: RemoteLink | null = null, private readyWaitMs = READY_WAIT_MS) {}

  /** A new Link (after pairing again) starts from 'connecting' until it reports ready. */
  attach(link: RemoteLink): void {
    this.link = link
    if (this.status === 'ready') this.handleStatus('connecting')
    else this.status = 'connecting'
  }

  get ready(): boolean {
    return this.status === 'ready'
  }

  /** Wire to Link's onStatus. */
  handleStatus(status: LinkStatus, info?: unknown): void {
    const wasReady = this.status === 'ready'
    this.status = status
    if (status === 'ready') {
      const waiters = this.readyWaiters
      this.readyWaiters = []
      waiters.forEach((w) => w())
      // Subscriptions live as long as the desktop session; a new session needs them again.
      if (!wasReady) {
        for (const channel of this.ipcListeners.keys()) {
          this.request('ui_ipc_subscribe', { channel }).catch(() => {})
        }
      }
    } else if (wasReady) {
      // The desktop cancels a session's streams when it ends, so no chunk will follow.
      for (const [id, s] of this.streams) {
        this.streams.delete(id)
        s.onEnd('disconnected')
      }
      this.early.clear()
    }
    this.statusListeners.forEach((l) => l(status, info))
  }

  onStatus(listener: (status: LinkStatus, info?: unknown) => void): () => void {
    this.statusListeners.add(listener)
    return () => this.statusListeners.delete(listener)
  }

  /** Wire to Link's onPush. */
  handlePush(obj: any): void {
    if (!obj || typeof obj !== 'object') return
    if (obj.type === 'ui_stream_chunk' || obj.type === 'ui_stream_end') {
      const id = String(obj.stream_id)
      const s = this.streams.get(id)
      if (!s) {
        const list = this.early.get(id) ?? []
        list.push(obj)
        this.early.set(id, list)
        return
      }
      this.deliver(id, s, obj)
      return
    }
    if (obj.type === 'ui_ipc_event' && typeof obj.channel === 'string') {
      const args = Array.isArray(obj.args) ? obj.args : []
      this.ipcListeners.get(obj.channel)?.forEach((cb) => {
        try {
          cb(...args)
        } catch (err) {
          console.error('[remote] ipc listener failed', err)
        }
      })
    }
  }

  private deliver(id: string, s: StreamHandlers, obj: any): void {
    if (obj.type === 'ui_stream_chunk') {
      if (typeof obj.data_b64 === 'string') s.onChunk(fromBase64(obj.data_b64))
      return
    }
    this.streams.delete(id)
    s.onEnd(typeof obj.error === 'string' ? obj.error : undefined)
  }

  private waitReady(): Promise<void> {
    if (this.status === 'ready') return Promise.resolve()
    if (this.status === 'removed' || this.status === 'hello_rejected') {
      return Promise.reject(new RemoteError('not_ready'))
    }
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.readyWaiters = this.readyWaiters.filter((w) => w !== done)
        reject(new RemoteError('not_ready'))
      }, this.readyWaitMs)
      const done = () => {
        clearTimeout(timer)
        resolve()
      }
      this.readyWaiters.push(done)
    })
  }

  /** One request; resolves with `result`, rejects with RemoteError carrying the desktop's code. */
  async request(type: string, params: Record<string, unknown> = {}): Promise<any> {
    await this.waitReady()
    if (!this.link) throw new RemoteError('not_ready')
    let reply: RemoteReply
    try {
      reply = await this.link.request(type, params)
    } catch (err) {
      throw new RemoteError(err instanceof Error ? err.message : String(err))
    }
    if (!reply || reply.ok !== true) throw new RemoteError(reply?.error || 'internal', reply)
    return reply.result
  }

  /** Builds ui_http / ui_stream_open params, uploading a body that would not fit one frame. */
  private async httpParams(req: HttpRequest): Promise<Record<string, unknown>> {
    const params: Record<string, unknown> = { method: req.method.toUpperCase(), path: req.path }
    if (req.query && Object.keys(req.query).length) params.query = req.query
    if (req.contentType) params.content_type = req.contentType
    const bytes = req.bytes ?? (req.body !== undefined ? encoder.encode(req.body) : null)
    if (!bytes) return params
    if (bytes.length > INLINE_BODY_MAX) {
      params.upload_id = await this.upload(bytes)
    } else if (req.bytes) {
      params.body_b64 = toBase64(req.bytes)
    } else {
      params.body = req.body
    }
    return params
  }

  /** Sends bytes in ui_upload_part frames; resolves with the upload_id to reference once. */
  async upload(bytes: Uint8Array): Promise<string> {
    if (bytes.length > UPLOAD_TOTAL_MAX) throw new RemoteError('too_large')
    const uploadId = randomId()
    const parts = Math.max(1, Math.ceil(bytes.length / UPLOAD_PART_BYTES))
    for (let seq = 0; seq < parts; seq++) {
      const chunk = bytes.subarray(seq * UPLOAD_PART_BYTES, (seq + 1) * UPLOAD_PART_BYTES)
      await this.request('ui_upload_part', {
        upload_id: uploadId,
        seq,
        data_b64: toBase64(chunk),
        last: seq === parts - 1,
      })
    }
    return uploadId
  }

  /** ui_http: resolves `{status, content_type, body?, body_b64?}`. A reply too large for one
   *  frame is read once more through ui_stream_open, which carries it in chunks; only for a GET,
   *  since the desktop has already run the request once. */
  async http(req: HttpRequest): Promise<{ status: number; content_type?: string; body?: unknown; body_b64?: string }> {
    try {
      return await this.request('ui_http', await this.httpParams(req))
    } catch (err) {
      if (!(err instanceof RemoteError) || err.code !== 'too_large' || req.method.toUpperCase() !== 'GET') throw err
    }
    const parts: Uint8Array[] = []
    let finish!: (error?: string) => void
    const ended = new Promise<string | undefined>((resolve) => (finish = resolve))
    const opened = await this.openStream(req, { onChunk: (b) => parts.push(b), onEnd: (e) => finish(e) })
    const error = await ended
    if (error) throw new RemoteError(error)
    const bytes = new Uint8Array(parts.reduce((n, p) => n + p.length, 0))
    let at = 0
    for (const p of parts) {
      bytes.set(p, at)
      at += p.length
    }
    return { status: opened.status, content_type: opened.contentType, body_b64: toBase64(bytes) }
  }

  /** ui_ipc with the whole argument list. When the frame would go over the inline limit, the
   *  largest string argument (a file being written) goes ahead as an upload and the desktop puts
   *  it back at `upload_arg_index`; the desktop takes one upload per call. */
  async ipc(channel: string, args: unknown[]): Promise<any> {
    const frameBytes = (a: unknown[]) => encoder.encode(JSON.stringify({ channel, payload: a[0] ?? null, args: a })).length
    if (frameBytes(args) <= INLINE_BODY_MAX) return this.request('ui_ipc', { channel, payload: args[0] ?? null, args })
    let index = -1
    args.forEach((a, i) => {
      if (typeof a === 'string' && (index < 0 || a.length > (args[index] as string).length)) index = i
    })
    const rest = [...args]
    if (index >= 0) rest[index] = ''
    if (index < 0 || frameBytes(rest) > INLINE_BODY_MAX) throw new RemoteError('too_large')
    const uploadId = await this.upload(encoder.encode(args[index] as string))
    return this.request('ui_ipc', { channel, payload: rest[0] ?? null, args: rest, upload_id: uploadId, upload_arg_index: index })
  }

  /** ui_stream_open: chunks go to `handlers` until ui_stream_end; `close()` cancels on the desktop. */
  async openStream(
    req: HttpRequest,
    handlers: StreamHandlers,
  ): Promise<{ streamId: string; status: number; contentType?: string; close: () => void }> {
    const result = await this.request('ui_stream_open', await this.httpParams(req))
    const streamId = String(result.stream_id)
    let open = true
    this.streams.set(streamId, {
      onChunk: (b) => open && handlers.onChunk(b),
      onEnd: (e) => {
        if (!open) return
        open = false
        handlers.onEnd(e)
      },
    })
    const queued = this.early.get(streamId)
    this.early.delete(streamId)
    queued?.forEach((obj) => {
      const s = this.streams.get(streamId)
      if (s) this.deliver(streamId, s, obj)
    })
    const close = () => {
      if (!open) return
      open = false
      this.streams.delete(streamId)
      this.request('ui_stream_close', { stream_id: streamId }).catch(() => {})
    }
    return { streamId, status: Number(result.status), contentType: result.content_type, close }
  }

  /** Main -> renderer events of one channel; the first listener subscribes on the desktop. */
  subscribe(channel: string, cb: (...args: unknown[]) => void): () => void {
    let set = this.ipcListeners.get(channel)
    if (!set) {
      set = new Set()
      this.ipcListeners.set(channel, set)
      if (this.status === 'ready') {
        this.request('ui_ipc_subscribe', { channel }).catch((err) => {
          console.warn('[remote] subscribe failed', channel, err?.code ?? err)
        })
      }
    }
    set.add(cb)
    return () => {
      set!.delete(cb)
    }
  }
}
