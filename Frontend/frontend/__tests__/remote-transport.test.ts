import { describe, expect, it, vi } from 'vitest'

import {
  INLINE_BODY_MAX,
  RemoteError,
  RemoteTransport,
  UPLOAD_PART_BYTES,
  fromBase64,
  toBase64,
  type RemoteLink,
} from '../renderer/lib/remote/transport'

type Handler = (type: string, params: any) => any

function fakeLink(handler: Handler = () => ({})) {
  const calls: Array<{ type: string; params: any }> = []
  const link: RemoteLink = {
    request: vi.fn(async (type: string, params: any = {}) => {
      calls.push({ type, params })
      try {
        return { id: calls.length, ok: true, result: await handler(type, params) }
      } catch (err: any) {
        if (err?.reply) return err.reply
        throw err
      }
    }),
  }
  return { link, calls }
}

const refuse = (error: string) => Object.assign(new Error(error), { reply: { ok: false, error } })

describe('RemoteTransport requests', () => {
  it('waits for the link to be ready, then resolves the result', async () => {
    const { link, calls } = fakeLink(() => ({ status: 200 }))
    const t = new RemoteTransport(link)
    const pending = t.request('ui_http', { method: 'GET', path: '/x' })
    await Promise.resolve()
    expect(calls).toHaveLength(0)
    t.handleStatus('ready')
    await expect(pending).resolves.toEqual({ status: 200 })
    expect(calls[0]).toEqual({ type: 'ui_http', params: { method: 'GET', path: '/x' } })
  })

  it('surfaces the desktop error code as RemoteError', async () => {
    const { link } = fakeLink(() => {
      throw refuse('remote_unavailable')
    })
    const t = new RemoteTransport(link)
    t.handleStatus('ready')
    const err = await t.request('ui_ipc', { channel: 'x' }).catch((e) => e)
    expect(err).toBeInstanceOf(RemoteError)
    expect(err.code).toBe('remote_unavailable')
  })

  it('maps a dropped link to a disconnected error', async () => {
    const link: RemoteLink = { request: vi.fn().mockRejectedValue(new Error('disconnected')) }
    const t = new RemoteTransport(link)
    t.handleStatus('ready')
    await expect(t.request('ui_http')).rejects.toMatchObject({ code: 'disconnected' })
  })

  it('gives up with not_ready when the link never comes up', async () => {
    const { link, calls } = fakeLink()
    const t = new RemoteTransport(link, 10)
    await expect(t.request('ui_http')).rejects.toMatchObject({ code: 'not_ready' })
    expect(calls).toHaveLength(0)
  })

  it('fails at once after the device was removed', async () => {
    const { link } = fakeLink()
    const t = new RemoteTransport(link)
    t.handleStatus('removed')
    await expect(t.request('ui_http')).rejects.toMatchObject({ code: 'not_ready' })
  })
})

describe('RemoteTransport uploads', () => {
  it('sends a small body inline', async () => {
    const { link, calls } = fakeLink(() => ({ status: 200 }))
    const t = new RemoteTransport(link)
    t.handleStatus('ready')
    await t.http({ method: 'post', path: '/a', body: '{"x":1}', contentType: 'application/json' })
    expect(calls.map((c) => c.type)).toEqual(['ui_http'])
    expect(calls[0].params).toMatchObject({ method: 'POST', path: '/a', body: '{"x":1}', content_type: 'application/json' })
  })

  it('uploads a body over the frame budget in parts and references it once', async () => {
    const { link, calls } = fakeLink((type) => (type === 'ui_upload_part' ? { received: 1 } : { status: 200 }))
    const t = new RemoteTransport(link)
    t.handleStatus('ready')
    const bytes = new Uint8Array(INLINE_BODY_MAX * 3 + 17).map((_, i) => i % 251)
    await t.http({ method: 'POST', path: '/files/write', bytes })

    const parts = calls.filter((c) => c.type === 'ui_upload_part')
    expect(parts.length).toBe(Math.ceil(bytes.length / UPLOAD_PART_BYTES))
    const uploadId = parts[0].params.upload_id
    parts.forEach((p, i) => {
      expect(p.params.upload_id).toBe(uploadId)
      expect(p.params.seq).toBe(i)
      expect(p.params.last).toBe(i === parts.length - 1)
      expect(fromBase64(p.params.data_b64).length).toBeLessThanOrEqual(48 * 1024)
      // Sealing turns the plaintext into base64url: it must stay under the relay's 64 KiB frame.
      const sealed = Math.ceil(((JSON.stringify(p.params).length + 40) * 4) / 3)
      expect(sealed).toBeLessThan(64 * 1024)
    })
    const joined = new Uint8Array(parts.flatMap((p) => Array.from(fromBase64(p.params.data_b64))))
    expect(joined).toEqual(bytes)

    const http = calls[calls.length - 1]
    expect(http.type).toBe('ui_http')
    expect(http.params.upload_id).toBe(uploadId)
    expect(http.params.body).toBeUndefined()
    expect(http.params.body_b64).toBeUndefined()
  })

  it('round-trips base64', () => {
    const bytes = new Uint8Array(70_000).map((_, i) => (i * 7) % 256)
    expect(fromBase64(toBase64(bytes))).toEqual(bytes)
  })
})

describe('RemoteTransport pushes', () => {
  it('routes stream chunks, including ones that beat the open reply', async () => {
    let t!: RemoteTransport
    const { link } = fakeLink((type) => {
      if (type !== 'ui_stream_open') return {}
      // The desktop's first chunk arrives before the open reply resolves.
      t.handlePush({ type: 'ui_stream_chunk', stream_id: 's1', seq: 0, data_b64: toBase64(new Uint8Array([1])) })
      return { stream_id: 's1', status: 200, content_type: 'text/event-stream' }
    })
    t = new RemoteTransport(link)
    t.handleStatus('ready')
    const got: number[] = []
    const ended = vi.fn()
    const s = await t.openStream({ method: 'POST', path: '/chat-stream' }, { onChunk: (b) => got.push(...b), onEnd: ended })
    expect(s.status).toBe(200)
    t.handlePush({ type: 'ui_stream_chunk', stream_id: 's1', seq: 1, data_b64: toBase64(new Uint8Array([2, 3])) })
    t.handlePush({ type: 'ui_stream_end', stream_id: 's1' })
    t.handlePush({ type: 'ui_stream_chunk', stream_id: 's1', seq: 2, data_b64: toBase64(new Uint8Array([9])) })
    expect(got).toEqual([1, 2, 3])
    expect(ended).toHaveBeenCalledTimes(1)
    expect(ended).toHaveBeenCalledWith(undefined)
  })

  it('closes a stream on the desktop and ignores later chunks', async () => {
    const { link, calls } = fakeLink((type) => (type === 'ui_stream_open' ? { stream_id: 7, status: 200 } : {}))
    const t = new RemoteTransport(link)
    t.handleStatus('ready')
    const onChunk = vi.fn()
    const s = await t.openStream({ method: 'GET', path: '/wake-stream-all' }, { onChunk, onEnd: vi.fn() })
    s.close()
    await vi.waitFor(() => expect(calls.some((c) => c.type === 'ui_stream_close')).toBe(true))
    expect(calls.find((c) => c.type === 'ui_stream_close')!.params).toEqual({ stream_id: '7' })
    t.handlePush({ type: 'ui_stream_chunk', stream_id: '7', seq: 0, data_b64: toBase64(new Uint8Array([1])) })
    expect(onChunk).not.toHaveBeenCalled()
  })

  it('ends open streams with disconnected when the link drops', async () => {
    const { link } = fakeLink((type) => (type === 'ui_stream_open' ? { stream_id: 's', status: 200 } : {}))
    const t = new RemoteTransport(link)
    t.handleStatus('ready')
    const onEnd = vi.fn()
    await t.openStream({ method: 'GET', path: '/wake-stream-all' }, { onChunk: vi.fn(), onEnd })
    t.handleStatus('connecting')
    expect(onEnd).toHaveBeenCalledWith('disconnected')
  })

  it('subscribes ipc channels once and again after a reconnect', async () => {
    const { link, calls } = fakeLink()
    const t = new RemoteTransport(link)
    const a = vi.fn()
    const b = vi.fn()
    t.subscribe('terminal-data-1', a)
    t.subscribe('terminal-data-1', b)
    t.handleStatus('ready')
    await vi.waitFor(() => expect(calls.filter((c) => c.type === 'ui_ipc_subscribe')).toHaveLength(1))
    t.handlePush({ type: 'ui_ipc_event', channel: 'terminal-data-1', args: ['ls\r\n'] })
    expect(a).toHaveBeenCalledWith('ls\r\n')
    expect(b).toHaveBeenCalledWith('ls\r\n')

    t.handleStatus('connecting')
    t.handleStatus('ready')
    await vi.waitFor(() => expect(calls.filter((c) => c.type === 'ui_ipc_subscribe')).toHaveLength(2))
  })

  it('reports status changes to listeners', () => {
    const { link } = fakeLink()
    const t = new RemoteTransport(link)
    const seen: string[] = []
    t.onStatus((s) => seen.push(s))
    t.handleStatus('ready')
    t.handleStatus('pc_offline', { lastSeen: 1 })
    expect(seen).toEqual(['ready', 'pc_offline'])
    expect(t.ready).toBe(false)
  })
})
