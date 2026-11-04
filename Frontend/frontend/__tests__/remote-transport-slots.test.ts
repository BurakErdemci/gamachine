import { describe, expect, it } from 'vitest'

import { RemoteTransport, type RemoteLink, type RemoteReply } from '../renderer/lib/remote/transport'

// The desktop answers a session's 17th concurrent request "busy" (Backend/app/remote/session.py
// MAX_TASKS = 16); the page must keep its own count below that.
function heldLink() {
  const held: Array<{ type: string; resolve: (r: RemoteReply) => void; reject: (e: Error) => void }> = []
  const link: RemoteLink = {
    request: (type) => new Promise((resolve, reject) => held.push({ type, resolve, reject })),
  }
  return { link, held }
}

const flush = async () => {
  for (let i = 0; i < 5; i++) await Promise.resolve()
}

describe('RemoteTransport in-flight limit', () => {
  it('sends at most 12 requests at once and starts a waiting one when a slot frees', async () => {
    const { link, held } = heldLink()
    const t = new RemoteTransport(link)
    t.handleStatus('ready')
    const results = Array.from({ length: 20 }, (_, i) => t.request('ui_http', { path: `/r${i}` }))
    await flush()
    expect(held).toHaveLength(12)

    held[0].resolve({ ok: true, result: 'first' })
    await flush()
    expect(held).toHaveLength(13)
    await expect(results[0]).resolves.toBe('first')

    held[1].reject(new Error('disconnected'))
    await flush()
    expect(held).toHaveLength(14)
    await expect(results[1]).rejects.toMatchObject({ code: 'disconnected' })

    for (let i = 2; i < 14; i++) held[i].resolve({ ok: false, error: 'busy' })
    await flush()
    expect(held).toHaveLength(20)
    for (let i = 14; i < 20; i++) held[i].resolve({ ok: true, result: i })
    await expect(Promise.all(results.slice(14))).resolves.toEqual([14, 15, 16, 17, 18, 19])
    await Promise.allSettled(results)
  })

  it('frees the slot of a request that fails before reaching the link', async () => {
    const { link, held } = heldLink()
    const t = new RemoteTransport(null)
    t.handleStatus('ready')
    const failed = await Promise.allSettled(Array.from({ length: 13 }, () => t.request('ui_http')))
    expect(failed.every((r) => r.status === 'rejected')).toBe(true)
    t.attach(link)
    t.handleStatus('ready')
    void Promise.allSettled(Array.from({ length: 12 }, () => t.request('ui_http')))
    await flush()
    expect(held).toHaveLength(12)
  })

  it('waits for the link again when it dropped while the request waited for a slot', async () => {
    const { link, held } = heldLink()
    const t = new RemoteTransport(link)
    t.handleStatus('ready')
    const results = Array.from({ length: 13 }, (_, i) => t.request('ui_http', { path: `/r${i}` }))
    await flush()
    expect(held).toHaveLength(12)
    t.handleStatus('connecting')
    held[0].reject(new Error('disconnected'))
    await flush()
    expect(held).toHaveLength(12)
    t.handleStatus('ready')
    await flush()
    expect(held).toHaveLength(13)
    held[12].resolve({ ok: true, result: 'late' })
    await expect(results[12]).resolves.toBe('late')
    for (let i = 1; i < 12; i++) held[i].resolve({ ok: true, result: i })
    await Promise.allSettled(results)
  })
})
