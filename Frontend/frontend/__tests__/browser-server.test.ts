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

const cmd = (op: string, args: Record<string, unknown> = {}, conversationId = 1) => JSON.stringify({ op, conversation_id: conversationId, args })
const tick = () => new Promise((r) => setTimeout(r, 50))

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

  it('each conversation has its own queue: a slow command in one does not hold up another', async () => {
    const log: string[] = []
    let release!: () => void
    const gate = new Promise<void>((r) => { release = r })
    server = await startCommandServer({
      token: TOKEN,
      handle: async (c) => {
        log.push(`start ${c.conversation_id} ${c.op}`)
        if (c.op === 'navigate') await gate
        log.push(`end ${c.conversation_id} ${c.op}`)
        return { ok: true, url: '', title: '' }
      },
    })
    const slow = send(server.port, { body: cmd('navigate', {}, 1) })
    await tick()
    const queuedA = send(server.port, { body: cmd('snapshot', {}, 1) })
    const other = await send(server.port, { body: cmd('snapshot', {}, 2) })
    expect(other.body.ok).toBe(true)
    expect(log).toEqual(['start 1 navigate', 'start 2 snapshot', 'end 2 snapshot'])
    release()
    await Promise.all([slow, queuedA])
    expect(log.slice(3)).toEqual(['end 1 navigate', 'start 1 snapshot', 'end 1 snapshot'])
  })

  it('halting conversation A drops only A\'s queued commands; B\'s queued command still runs', async () => {
    const ran: string[] = []
    const gates: Record<number, () => void> = {}
    server = await startCommandServer({
      token: TOKEN,
      immediate: (c) => c.op === 'halt',
      handle: async (c) => {
        ran.push(`${c.conversation_id} ${c.op}`)
        if (c.op === 'navigate') await new Promise<void>((r) => { gates[c.conversation_id as number] = r })
        if (c.op === 'halt') gates[c.conversation_id as number]?.()
        return { ok: true, url: '', title: '' }
      },
    })
    const navA = send(server.port, { body: cmd('navigate', {}, 1) })
    const navB = send(server.port, { body: cmd('navigate', {}, 2) })
    await tick()
    const queuedA = send(server.port, { body: cmd('click', {}, 1) })
    const queuedB = send(server.port, { body: cmd('click', {}, 2) })
    await tick()
    expect((await send(server.port, { body: cmd('halt', {}, 1) })).body.ok).toBe(true)
    await navA
    expect((await queuedA).body).toEqual({ ok: false, error: 'halted', url: '', title: '' })
    gates[2]()
    await navB
    expect((await queuedB).body.ok).toBe(true)
    expect(ran).toEqual(['1 navigate', '2 navigate', '1 halt', '2 click'])
  })

  it('cancelPending with an id drops only that conversation\'s queue', async () => {
    const gates: (() => void)[] = []
    server = await startCommandServer({ token: TOKEN, handle: async (c) => {
      if (c.op === 'navigate') await new Promise<void>((r) => gates.push(r))
      return { ok: true }
    } })
    const navs = [send(server.port, { body: cmd('navigate', {}, 1) }), send(server.port, { body: cmd('navigate', {}, 2) })]
    await tick()
    const queuedA = send(server.port, { body: cmd('fill', {}, 1) })
    const queuedB = send(server.port, { body: cmd('fill', {}, 2) })
    await tick()
    server.cancelPending(1)
    gates.forEach((g) => g())
    await Promise.all(navs)
    expect((await queuedA).body.error).toBe('halted')
    expect((await queuedB).body.ok).toBe(true)
  })

  it('passes tabs and use_tab with their args, and a Stop drops a queued use_tab before it runs', async () => {
    let release!: () => void
    const gate = new Promise<void>((r) => { release = r })
    const seen: BrowserCommand[] = []
    server = await startCommandServer({ token: TOKEN, immediate: (c) => c.op === 'halt',
      handle: async (c) => { seen.push(c); if (c.op === 'navigate') await gate; return { ok: true } } })
    expect((await send(server.port, { body: cmd('tabs') })).body.ok).toBe(true)
    expect((await send(server.port, { body: cmd('use_tab', { tab_id: 2 }) })).body.ok).toBe(true)
    expect(seen.map((c) => [c.op, c.args])).toEqual([['tabs', {}], ['use_tab', { tab_id: 2 }]])
    const nav = send(server.port, { body: cmd('navigate') })
    await tick()
    const queued = send(server.port, { body: cmd('use_tab', { tab_id: 3 }) })
    await tick()
    await send(server.port, { body: cmd('halt') })
    release()
    await nav
    expect((await queued).body.error).toBe('halted')
    expect(seen.some((c) => c.op === 'use_tab' && (c.args as any).tab_id === 3)).toBe(false)
  })

  it('a refused command is answered at once without waiting in the queue', async () => {
    let release!: () => void
    const gate = new Promise<void>((r) => { release = r })
    const ran: string[] = []
    server = await startCommandServer({
      token: TOKEN,
      refuse: (c) => (c.op === 'click' ? { ok: false, error: 'user_control', url: '', title: '' } : null),
      handle: async (c) => { ran.push(c.op); if (c.op === 'navigate') await gate; return { ok: true } },
    })
    const nav = send(server.port, { body: cmd('navigate') })
    await tick()
    expect((await send(server.port, { body: cmd('click') })).body).toEqual({ ok: false, error: 'user_control', url: '', title: '' })
    release()
    await nav
    expect(ran).toEqual(['navigate'])
  })
})
