import http from 'http'
import { createHash, timingSafeEqual } from 'crypto'

export const MAX_BODY_BYTES = 64 * 1024

export interface BrowserCommand {
  op: string
  conversation_id?: number
  args?: Record<string, unknown>
}

export interface CommandServer {
  port: number
  /** Answers every command still waiting in the queue with `halted` instead of running it. */
  cancelPending(): void
  close(): Promise<void>
}

export const HALTED_REPLY = { ok: false, error: 'halted', url: '', title: '' }

// Hashing first gives both sides the same length, so timingSafeEqual never throws
// and the comparison time does not depend on how much of the token matched.
function sameToken(header: string | undefined, expected: string): boolean {
  const digest = (s: string) => createHash('sha256').update(s).digest()
  return timingSafeEqual(digest(header ?? ''), digest(`Bearer ${expected}`))
}

function reply(res: http.ServerResponse, status: number, body: unknown, close = false): void {
  res.writeHead(status, { 'content-type': 'application/json', ...(close ? { connection: 'close' } : {}) })
  res.end(JSON.stringify(body))
}

/**
 * Loopback-only command endpoint for the browser MCP server: POST /cmd, Bearer token,
 * one command at a time. Commands for which `immediate` returns true (halt) skip the
 * queue, because waiting behind a 30 s navigation would defeat them, and they drop
 * whatever was still queued.
 */
export function startCommandServer(options: {
  token: string
  handle: (cmd: BrowserCommand) => Promise<unknown>
  immediate?: (cmd: BrowserCommand) => boolean
}): Promise<CommandServer> {
  const { token, handle, immediate } = options
  let tail: Promise<unknown> = Promise.resolve()
  let epoch = 0
  const enqueue = (cmd: BrowserCommand): Promise<unknown> => {
    if (immediate?.(cmd)) {
      epoch++
      return Promise.resolve().then(() => handle(cmd))
    }
    const arrived = epoch
    const run = tail.then(() => (arrived === epoch ? handle(cmd) : HALTED_REPLY))
    tail = run.catch(() => undefined)
    return run
  }

  const server = http.createServer((req, res) => {
    if (req.method !== 'POST' || (req.url ?? '').split('?')[0] !== '/cmd') {
      req.resume()
      reply(res, 404, { ok: false, error: 'not_found' }, true)
      return
    }
    if (!sameToken(req.headers.authorization, token)) {
      req.resume()
      reply(res, 401, { ok: false, error: 'unauthorized' }, true)
      return
    }
    const declared = Number(req.headers['content-length'])
    if (Number.isFinite(declared) && declared > MAX_BODY_BYTES) {
      req.resume()
      reply(res, 413, { ok: false, error: 'body_too_large' }, true)
      return
    }
    const chunks: Buffer[] = []
    let size = 0
    let refused = false
    req.on('data', (chunk: Buffer) => {
      if (refused) return
      size += chunk.length
      if (size > MAX_BODY_BYTES) {
        refused = true
        chunks.length = 0
        reply(res, 413, { ok: false, error: 'body_too_large' }, true)
        return
      }
      chunks.push(chunk)
    })
    req.on('end', () => {
      if (refused) return
      let cmd: BrowserCommand
      try {
        cmd = JSON.parse(Buffer.concat(chunks).toString('utf8'))
      } catch {
        reply(res, 400, { ok: false, error: 'invalid_json' })
        return
      }
      if (!cmd || typeof cmd !== 'object' || Array.isArray(cmd) || typeof cmd.op !== 'string') {
        reply(res, 400, { ok: false, error: 'invalid_command' })
        return
      }
      enqueue(cmd).then(
        (result) => reply(res, 200, result),
        (err) => reply(res, 200, { ok: false, error: err instanceof Error ? err.message : String(err), url: '', title: '' }),
      )
    })
  })
  server.requestTimeout = 120_000

  return new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      server.off('error', reject)
      const address = server.address()
      const port = typeof address === 'object' && address ? address.port : 0
      resolve({
        port,
        cancelPending: () => { epoch++ },
        close: () => new Promise<void>((done) => {
          server.close(() => done())
          server.closeAllConnections?.()
        }),
      })
    })
  })
}
