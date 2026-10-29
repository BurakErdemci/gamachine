import axios, { AxiosError, AxiosHeaders, type AxiosAdapter, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'

import { REMOTE_BACKEND_BASE } from './ipcShim'
import { RemoteError, fromBase64, type HttpRequest, type RemoteTransport } from './transport'

type HttpResult = { status: number; content_type?: string; body?: unknown; body_b64?: string }

const encoder = new TextEncoder()
const NULL_BODY_STATUS = new Set([101, 204, 205, 304])

export function isRemoteUrl(url: string | undefined | null): boolean {
  return typeof url === 'string' && url.startsWith(REMOTE_BACKEND_BASE + '/')
}

/** 'remote://backend/a/b?x=1&x=2' -> {path: '/a/b', query: {x: ['1', '2']}} */
export function splitRemoteUrl(url: string): { path: string; query?: Record<string, string | string[]> } {
  const rest = url.slice(REMOTE_BACKEND_BASE.length).split('#')[0]
  const at = rest.indexOf('?')
  const path = at < 0 ? rest : rest.slice(0, at)
  if (at < 0) return { path }
  const query: Record<string, string | string[]> = {}
  new URLSearchParams(rest.slice(at + 1)).forEach((value, key) => {
    const prev = query[key]
    query[key] = prev === undefined ? value : Array.isArray(prev) ? [...prev, value] : [prev, value]
  })
  return Object.keys(query).length ? { path, query } : { path }
}

// The chat turn, side chat and wake endpoints answer with an event stream that the hooks read
// as it arrives; everything else is a plain request/reply.
export function isStreamingPath(path: string): boolean {
  const last = path.split('/').filter(Boolean).pop() ?? ''
  return /(^|-)stream(-|$)/.test(last)
}

/** A 403 from the desktop's allow-list arrives as an RPC error; callers see the HTTP status. */
async function httpOrForbidden(transport: RemoteTransport, req: HttpRequest): Promise<HttpResult> {
  try {
    return await transport.http(req)
  } catch (err) {
    if (err instanceof RemoteError && err.code === 'remote_forbidden') {
      return { status: 403, content_type: 'application/json', body: { detail: 'remote_forbidden' } }
    }
    throw err
  }
}

function resultBytes(r: HttpResult): Uint8Array {
  if (typeof r.body_b64 === 'string') return fromBase64(r.body_b64)
  if (r.body === undefined || r.body === null) return new Uint8Array(0)
  return encoder.encode(typeof r.body === 'string' ? r.body : JSON.stringify(r.body))
}

function exactBuffer(bytes: Uint8Array): ArrayBuffer {
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer
}

async function bodyOf(data: unknown, contentType?: string): Promise<{ body?: string; bytes?: Uint8Array; contentType?: string }> {
  if (data === undefined || data === null) return {}
  if (typeof data === 'string') return { body: data, contentType }
  if (data instanceof URLSearchParams) {
    return { body: data.toString(), contentType: contentType ?? 'application/x-www-form-urlencoded;charset=UTF-8' }
  }
  if (data instanceof ArrayBuffer) return { bytes: new Uint8Array(data), contentType }
  if (ArrayBuffer.isView(data)) return { bytes: new Uint8Array(data.buffer, data.byteOffset, data.byteLength), contentType }
  if (typeof Blob !== 'undefined' && data instanceof Blob) {
    return { bytes: new Uint8Array(await data.arrayBuffer()), contentType: contentType ?? (data.type || undefined) }
  }
  if (typeof FormData !== 'undefined' && data instanceof FormData) {
    // Response encodes the multipart body and picks the boundary that goes with it.
    const encoded = new Response(data)
    return { bytes: new Uint8Array(await encoded.arrayBuffer()), contentType: encoded.headers.get('content-type') ?? undefined }
  }
  return { body: JSON.stringify(data), contentType: contentType ?? 'application/json' }
}

// ---------------------------------------------------------------- axios

/** An adapter for requests to REMOTE_BACKEND_BASE; others go to `fallback`. */
export function createRemoteAxiosAdapter(transport: RemoteTransport, fallback: AxiosAdapter): AxiosAdapter {
  return async (config: InternalAxiosRequestConfig): Promise<AxiosResponse> => {
    const url = axios.getUri(config)
    if (!isRemoteUrl(url)) return fallback(config)
    const contentType = AxiosHeaders.from(config.headers as any).get('content-type')
    const { path, query } = splitRemoteUrl(url)
    let result: HttpResult
    try {
      const body = await bodyOf(config.data, typeof contentType === 'string' && contentType ? contentType : undefined)
      result = await httpOrForbidden(transport, { method: config.method || 'get', path, query, ...body })
    } catch (err) {
      const code = err instanceof RemoteError ? err.code : AxiosError.ERR_NETWORK
      throw new AxiosError(err instanceof Error ? err.message : String(err), code, config)
    }
    const bytes = resultBytes(result)
    const type = config.responseType
    const data =
      type === 'arraybuffer' ? exactBuffer(bytes)
        : type === 'blob' ? new Blob([exactBuffer(bytes)], { type: result.content_type || '' })
          // Text, so axios' own transformResponse parses JSON exactly as on the desktop.
          : typeof result.body === 'string' || result.body_b64 !== undefined ? new TextDecoder().decode(bytes)
            : result.body
    const response: AxiosResponse = {
      data,
      status: result.status,
      statusText: '',
      headers: new AxiosHeaders(result.content_type ? { 'content-type': result.content_type } : {}),
      config,
      request: {},
    }
    const validate = config.validateStatus
    if (!validate || validate(result.status)) return response
    const forbidden = result.status === 403 && (result.body as any)?.detail === 'remote_forbidden'
    throw new AxiosError(
      `Request failed with status code ${result.status}`,
      forbidden ? 'remote_forbidden' : result.status < 500 ? AxiosError.ERR_BAD_REQUEST : AxiosError.ERR_BAD_RESPONSE,
      config,
      response.request,
      response,
    )
  }
}

/** Routes the default axios instance's remote requests through the transport. */
export function installAxiosShim(transport: RemoteTransport): void {
  const fallback = axios.getAdapter(axios.defaults.adapter)
  axios.defaults.adapter = createRemoteAxiosAdapter(transport, fallback)
}

// ---------------------------------------------------------------- fetch

function abortError(): Error {
  return typeof DOMException !== 'undefined'
    ? new DOMException('The operation was aborted.', 'AbortError')
    : Object.assign(new Error('The operation was aborted.'), { name: 'AbortError' })
}

function responseHeaders(contentType?: string): Headers {
  const h = new Headers()
  if (contentType) h.set('content-type', contentType)
  return h
}

function makeResponse(status: number, body: BodyInit | null, contentType?: string): Response {
  return new Response(NULL_BODY_STATUS.has(status) ? null : body, { status, headers: responseHeaders(contentType) })
}

async function streamResponse(transport: RemoteTransport, req: HttpRequest, signal?: AbortSignal | null): Promise<Response> {
  let controller!: ReadableStreamDefaultController<Uint8Array>
  let close: (() => void) | null = null
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c
    },
    cancel() {
      close?.()
    },
  })
  const opened = await transport.openStream(req, {
    onChunk: (bytes) => controller.enqueue(bytes),
    onEnd: (error) => {
      signal?.removeEventListener('abort', onAbort)
      if (error) controller.error(Object.assign(new Error(error), { code: error }))
      else controller.close()
    },
  })
  close = opened.close
  function onAbort() {
    opened.close()
    try {
      controller.error(abortError())
    } catch {}
  }
  if (signal?.aborted) onAbort()
  else signal?.addEventListener('abort', onAbort, { once: true })
  return makeResponse(opened.status, body, opened.contentType)
}

/** `fetch` for REMOTE_BACKEND_BASE over the transport; any other URL goes to `fallback`. */
export function createRemoteFetch(transport: RemoteTransport, fallback: typeof fetch): typeof fetch {
  return async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    if (!isRemoteUrl(url)) return fallback(input as any, init)
    const request = typeof input === 'object' && !(input instanceof URL) ? input : null
    const signal = init.signal ?? request?.signal ?? null
    if (signal?.aborted) throw abortError()
    const headers = new Headers(init.headers ?? request?.headers)
    const data = init.body !== undefined ? init.body : request && request.body ? await request.arrayBuffer() : undefined
    const { path, query } = splitRemoteUrl(url)
    const req: HttpRequest = {
      method: init.method ?? request?.method ?? 'GET',
      path,
      query,
      ...(await bodyOf(data, headers.get('content-type') ?? undefined)),
    }
    if (isStreamingPath(path)) {
      try {
        return await streamResponse(transport, req, signal)
      } catch (err) {
        if (err instanceof RemoteError && err.code === 'remote_forbidden') {
          return makeResponse(403, JSON.stringify({ detail: 'remote_forbidden' }), 'application/json')
        }
        throw err
      }
    }
    const pending = httpOrForbidden(transport, req)
    const result = await (signal
      ? Promise.race([
          pending,
          new Promise<never>((_, reject) => signal.addEventListener('abort', () => reject(abortError()), { once: true })),
        ])
      : pending)
    return makeResponse(result.status, exactBuffer(resultBytes(result)), result.content_type)
  }
}

export function installFetchShim(transport: RemoteTransport): void {
  window.fetch = createRemoteFetch(transport, window.fetch.bind(window))
}
