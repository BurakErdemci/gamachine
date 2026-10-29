import axios, { AxiosError } from 'axios'
import { describe, expect, it, vi } from 'vitest'

import {
  createRemoteAxiosAdapter,
  createRemoteFetch,
  isStreamingPath,
  splitRemoteUrl,
} from '../renderer/lib/remote/httpShim'
import { INLINE_BODY_MAX, RemoteTransport, toBase64, type RemoteLink } from '../renderer/lib/remote/transport'

const BASE = 'remote://backend'

function setup(handler: (type: string, params: any) => any) {
  const calls: Array<{ type: string; params: any }> = []
  const link: RemoteLink = {
    request: vi.fn(async (type: string, params: any = {}) => {
      calls.push({ type, params })
      const out = await handler(type, params)
      return out && out.ok === false ? out : { ok: true, result: out }
    }),
  }
  const transport = new RemoteTransport(link)
  transport.handleStatus('ready')
  const fallbackAdapter = vi.fn(async () => ({ data: 'local', status: 200, statusText: '', headers: {}, config: {} as any }))
  const http = axios.create({ adapter: createRemoteAxiosAdapter(transport, fallbackAdapter as any) })
  const fallbackFetch = vi.fn(async () => new Response('local'))
  const remoteFetch = createRemoteFetch(transport, fallbackFetch as any)
  return { calls, transport, http, fallbackAdapter, fallbackFetch, remoteFetch }
}

const json = (status: number, body: unknown) => ({ status, content_type: 'application/json', body: JSON.stringify(body) })

describe('remote url helpers', () => {
  it('splits path and query', () => {
    expect(splitRemoteUrl(`${BASE}/conversations/u1`)).toEqual({ path: '/conversations/u1' })
    expect(splitRemoteUrl(`${BASE}/a?x=1&x=2&y=3`)).toEqual({ path: '/a', query: { x: ['1', '2'], y: '3' } })
  })

  it('knows the streaming endpoints', () => {
    expect(isStreamingPath('/chat-stream')).toBe(true)
    expect(isStreamingPath('/conversations/1/side-stream')).toBe(true)
    expect(isStreamingPath('/wake-stream-all')).toBe(true)
    expect(isStreamingPath('/chat-stop/1')).toBe(false)
    expect(isStreamingPath('/streamer')).toBe(false)
  })
})

describe('remote axios adapter', () => {
  it('sends a JSON GET as ui_http and parses the reply', async () => {
    const { http, calls } = setup(() => json(200, { chats: [1, 2] }))
    const res = await http.get(`${BASE}/conversations/u1`, { params: { limit: 5 } })
    expect(res.status).toBe(200)
    expect(res.data).toEqual({ chats: [1, 2] })
    expect(calls).toEqual([{ type: 'ui_http', params: { method: 'GET', path: '/conversations/u1', query: { limit: '5' } } }])
  })

  it('sends a JSON body as text with its content type', async () => {
    const { http, calls } = setup(() => ({ status: 200, content_type: 'application/json', body: { ok: true } }))
    const res = await http.post(`${BASE}/cards/answer`, { approved: true })
    expect(res.data).toEqual({ ok: true })
    expect(calls[0].params).toMatchObject({ method: 'POST', path: '/cards/answer', body: '{"approved":true}' })
    expect(calls[0].params.content_type).toMatch(/^application\/json/)
  })

  it('returns binary bodies as an ArrayBuffer', async () => {
    const bytes = new Uint8Array([0, 255, 7, 128])
    const { http } = setup(() => ({ status: 200, content_type: 'application/octet-stream', body_b64: toBase64(bytes) }))
    const res = await http.get(`${BASE}/files/raw`, { responseType: 'arraybuffer' })
    expect(new Uint8Array(res.data)).toEqual(bytes)
  })

  it('maps the desktop refusal to a 403 axios error', async () => {
    const { http } = setup(() => ({ ok: false, error: 'remote_forbidden' }))
    const err = await http.get(`${BASE}/api-keys`).catch((e) => e)
    expect(err).toBeInstanceOf(AxiosError)
    expect(err.code).toBe('remote_forbidden')
    expect(err.response.status).toBe(403)
    expect(err.response.data).toEqual({ detail: 'remote_forbidden' })
  })

  it('rejects other HTTP errors with the status', async () => {
    const { http } = setup(() => json(404, { detail: 'nope' }))
    const err = await http.get(`${BASE}/x`).catch((e) => e)
    expect(err.response.status).toBe(404)
    expect(err.response.data).toEqual({ detail: 'nope' })
  })

  it('leaves non-remote URLs to the original adapter', async () => {
    const { http, calls, fallbackAdapter } = setup(() => json(200, {}))
    const res = await http.get('http://localhost:8080/health')
    expect(res.data).toBe('local')
    expect(fallbackAdapter).toHaveBeenCalledTimes(1)
    expect(calls).toHaveLength(0)
  })

  it('uploads a large body in parts', async () => {
    const { http, calls } = setup((type) => (type === 'ui_upload_part' ? { received: 1 } : json(200, {})))
    await http.post(`${BASE}/voice`, { pcm_base64: 'a'.repeat(INLINE_BODY_MAX + 10) })
    expect(calls.filter((c) => c.type === 'ui_upload_part').length).toBeGreaterThan(1)
    const last = calls[calls.length - 1]
    expect(last.type).toBe('ui_http')
    expect(typeof last.params.upload_id).toBe('string')
    expect(last.params.body).toBeUndefined()
  })
})

describe('remote fetch', () => {
  it('serves JSON over ui_http', async () => {
    const { remoteFetch, calls } = setup(() => json(200, { id: 'c1' }))
    const res = await remoteFetch(`${BASE}/conversations/1/side`, { method: 'POST' })
    expect(res.ok).toBe(true)
    await expect(res.json()).resolves.toEqual({ id: 'c1' })
    expect(calls[0]).toEqual({ type: 'ui_http', params: { method: 'POST', path: '/conversations/1/side' } })
  })

  it('serves binary over ui_http', async () => {
    const bytes = new Uint8Array([1, 2, 3, 250])
    const { remoteFetch } = setup(() => ({ status: 200, content_type: 'model/gltf-binary', body_b64: toBase64(bytes) }))
    const res = await remoteFetch(`${BASE}/files/model`)
    expect(new Uint8Array(await res.arrayBuffer())).toEqual(bytes)
    expect(res.headers.get('content-type')).toBe('model/gltf-binary')
  })

  it('turns the desktop refusal into a 403 response', async () => {
    const { remoteFetch } = setup(() => ({ ok: false, error: 'remote_forbidden' }))
    const res = await remoteFetch(`${BASE}/remote/devices`)
    expect(res.status).toBe(403)
    await expect(res.json()).resolves.toEqual({ detail: 'remote_forbidden' })
  })

  it('leaves other URLs to the browser fetch', async () => {
    const { remoteFetch, fallbackFetch, calls } = setup(() => json(200, {}))
    const res = await remoteFetch('/models/mannequin.glb')
    await expect(res.text()).resolves.toBe('local')
    expect(fallbackFetch).toHaveBeenCalledTimes(1)
    expect(calls).toHaveLength(0)
  })

  it('streams chunks into a ReadableStream that ends with ui_stream_end', async () => {
    const { remoteFetch, transport, calls } = setup((type) =>
      type === 'ui_stream_open' ? { stream_id: 'st', status: 200, content_type: 'text/event-stream' } : {},
    )
    const res = await remoteFetch(`${BASE}/chat-stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Session-Token': 'x' },
      body: JSON.stringify({ message: 'hi' }),
    })
    expect(calls[0]).toEqual({
      type: 'ui_stream_open',
      params: { method: 'POST', path: '/chat-stream', content_type: 'application/json', body: '{"message":"hi"}' },
    })
    const reader = res.body!.getReader()
    const enc = new TextEncoder()
    transport.handlePush({ type: 'ui_stream_chunk', stream_id: 'st', seq: 0, data_b64: toBase64(enc.encode('data: {"a":1}\n\n')) })
    transport.handlePush({ type: 'ui_stream_end', stream_id: 'st' })
    const first = await reader.read()
    expect(new TextDecoder().decode(first.value)).toBe('data: {"a":1}\n\n')
    expect((await reader.read()).done).toBe(true)
  })

  it('cancels the desktop stream on abort', async () => {
    const { remoteFetch, transport, calls } = setup((type) =>
      type === 'ui_stream_open' ? { stream_id: 'st2', status: 200, content_type: 'text/event-stream' } : {},
    )
    const ac = new AbortController()
    const res = await remoteFetch(`${BASE}/wake-stream-all`, { signal: ac.signal })
    const reader = res.body!.getReader()
    ac.abort()
    await expect(reader.read()).rejects.toMatchObject({ name: 'AbortError' })
    await vi.waitFor(() => expect(calls).toContainEqual({ type: 'ui_stream_close', params: { stream_id: 'st2' } }))
    // A late chunk must not reach the errored stream.
    expect(() =>
      transport.handlePush({ type: 'ui_stream_chunk', stream_id: 'st2', seq: 0, data_b64: toBase64(new Uint8Array([1])) }),
    ).not.toThrow()
  })

  it('cancels the desktop stream when the reader cancels', async () => {
    const { remoteFetch, calls } = setup((type) => (type === 'ui_stream_open' ? { stream_id: 'st3', status: 200 } : {}))
    const res = await remoteFetch(`${BASE}/chat-stream`, { method: 'POST', body: '{}' })
    await res.body!.cancel()
    await vi.waitFor(() => expect(calls).toContainEqual({ type: 'ui_stream_close', params: { stream_id: 'st3' } }))
  })

  it('errors the stream when the desktop ends it with an error', async () => {
    const { remoteFetch, transport } = setup((type) => (type === 'ui_stream_open' ? { stream_id: 'st4', status: 200 } : {}))
    const res = await remoteFetch(`${BASE}/chat-stream`, { method: 'POST', body: '{}' })
    const reader = res.body!.getReader()
    transport.handlePush({ type: 'ui_stream_end', stream_id: 'st4', error: 'cancelled' })
    await expect(reader.read()).rejects.toMatchObject({ code: 'cancelled' })
  })

  it('rejects an already aborted request without sending it', async () => {
    const { remoteFetch, calls } = setup(() => json(200, {}))
    const ac = new AbortController()
    ac.abort()
    await expect(remoteFetch(`${BASE}/scene-editor/x`, { signal: ac.signal })).rejects.toMatchObject({ name: 'AbortError' })
    expect(calls).toHaveLength(0)
  })
})
