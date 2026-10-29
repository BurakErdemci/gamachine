// @vitest-environment node
import http from 'http'
import { afterEach, describe, expect, it } from 'vitest'
import { startCommandServer, type BrowserCommand, type CommandServer } from '../main/helpers/browser-server'

const TOKEN = 'a'.repeat(64)
let server: CommandServer | null = null

afterEach(async () => {
  await server?.close()
  server = null
})

function send(port: number, options: { method?: string; path?: string; auth?: string | null; body?: string }) {
  return new Promise<{ status: number; body: any }>((resolve, reject) => {
    const headers: Record<string, string> = { 'content-type': 'application/json' }
    if (options.auth !== null) headers.authorization = options.auth ?? `Bearer ${TOKEN}`
    const req = http.request({ host: '127.0.0.1', port, path: options.path ?? '/cmd', method: options.method ?? 'POST', headers }, (res) => {
      let data = ''
      res.on('data', (c) => { data += c })
      res.on('end', () => resolve({ status: res.statusCode ?? 0, body: data ? JSON.parse(data) : null }))
    })
    req.on('error', reject)
    req.end(options.body)
  })
}

const cmd = (op: string, args: Record<string, unknown> = {}) => JSON.stringify({ op, conversation_id: 1, args })

describe('browser command server', () => {
  it('runs authorized commands and refuses everything else before the handler', async () => {
    const seen: BrowserCommand[] = []
    server = await startCommandServer({ token: TOKEN, handle: async (c) => { seen.push(c); return { ok: true, url: 'u', title: 't' } } })
    expect((await send(server.port, { auth: null, body: cmd('navigate') })).status).toBe(401)
    expect((await send(server.port, { auth: 'Bearer wrong', body: cmd('navigate') })).status).toBe(401)
    expect((await send(server.port, { auth: TOKEN, body: cmd('navigate') })).status).toBe(401)
    expect((await send(server.port, { path: '/other', body: cmd('navigate') })).status).toBe(404)
    expect((await send(server.port, { method: 'GET' })).status).toBe(404)
    expect((await send(server.port, { body: '{not json' })).status).toBe(400)
    expect((await send(server.port, { body: '[1]' })).status).toBe(400)
    expect((await send(server.port, { body: 'x'.repeat(64 * 1024 + 1) })).status).toBe(413)
    expect(seen).toEqual([])
    const ok = await send(server.port, { body: cmd('navigate', { url: 'https://example.com/' }) })
    expect(ok).toEqual({ status: 200, body: { ok: true, url: 'u', title: 't' } })
    expect(seen).toEqual([{ op: 'navigate', conversation_id: 1, args: { url: 'https://example.com/' } }])
  })

  it('listens on loopback only', async () => {
    server = await startCommandServer({ token: TOKEN, handle: async () => ({}) })
    expect(server.port).toBeGreaterThan(0)
  })

  it('processes one command at a time in arrival order', async () => {
    const log: string[] = []
    let release!: () => void
    const gate = new Promise<void>((r) => { release = r })
    server = await startCommandServer({
      token: TOKEN,
      handle: async (c) => {
        log.push(`start ${c.op}`)
        if (c.op === 'navigate') await gate
        log.push(`end ${c.op}`)
        return { ok: true, url: '', title: '' }
      },
    })
    const first = send(server.port, { body: cmd('navigate') })
    await new Promise((r) => setTimeout(r, 50))
    const second = send(server.port, { body: cmd('snapshot') })
    await new Promise((r) => setTimeout(r, 50))
    expect(log).toEqual(['start navigate'])
    release()
    await Promise.all([first, second])
    expect(log).toEqual(['start navigate', 'end navigate', 'start snapshot', 'end snapshot'])
  })

  it('a handler error still answers 200 with ok:false', async () => {
    server = await startCommandServer({ token: TOKEN, handle: async () => { throw new Error('boom') } })
    expect(await send(server.port, { body: cmd('snapshot') })).toEqual({ status: 200, body: { ok: false, error: 'boom', url: '', title: '' } })
  })

  it('halt skips the queue and drops the commands waiting behind the running one', async () => {
    const ran: string[] = []
    let release!: () => void
    const gate = new Promise<void>((r) => { release = r })
    server = await startCommandServer({
      token: TOKEN,
      immediate: (c) => c.op === 'halt',
      handle: async (c) => {
        ran.push(c.op)
        if (c.op === 'navigate') await gate
        if (c.op === 'halt') release()
        return { ok: true, url: '', title: '' }
      },
    })
    const nav = send(server.port, { body: cmd('navigate') })
    await new Promise((r) => setTimeout(r, 50))
    const queued = send(server.port, { body: cmd('click') })
    await new Promise((r) => setTimeout(r, 50))
    const halt = await send(server.port, { body: cmd('halt') })
    expect(halt.body.ok).toBe(true)
    await nav
    expect((await queued).body).toEqual({ ok: false, error: 'halted', url: '', title: '' })
    expect(ran).toEqual(['navigate', 'halt'])
    expect((await send(server.port, { body: cmd('snapshot') })).body.ok).toBe(true)
  })

  it('cancelPending drops queued commands too', async () => {
    let release!: () => void
    const gate = new Promise<void>((r) => { release = r })
    const ran: string[] = []
    server = await startCommandServer({ token: TOKEN, handle: async (c) => { ran.push(c.op); if (c.op === 'navigate') await gate; return { ok: true } } })
    const nav = send(server.port, { body: cmd('navigate') })
    await new Promise((r) => setTimeout(r, 50))
    const queued = send(server.port, { body: cmd('fill') })
    await new Promise((r) => setTimeout(r, 50))
    server.cancelPending()
    release()
    await nav
    expect((await queued).body.error).toBe('halted')
    expect(ran).toEqual(['navigate'])
  })
})
