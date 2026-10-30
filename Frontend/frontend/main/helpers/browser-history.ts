import fs from 'fs'
import path from 'path'

export const HISTORY_CAP = 500
const SAVE_DELAY_MS = 2_000
const MAX_TITLE_CHARS = 200
const MAX_URL_CHARS = 1_000
// A favicon is stored as the data: URL main fetched (32 KB of image is about 44k characters).
const MAX_FAVICON_CHARS = 48_000
// A visited URL may carry a login or reset token; such pages are not remembered at all. Any query name that
// contains one of these words counts; the short words only count as a whole word (sid, sig, otp, state, code).
const SENSITIVE_PARAM = /token|secret|passw(or)?d|pwd|auth|session|jwt|bearer|key(?!words?)|signature|ticket|nonce|(^|[^a-z0-9])(sid|sig|otp|code|state)([^a-z0-9]|$)/i

export interface HistoryEntry {
  url: string
  title: string
  favicon: string
  lastVisit: number
  visits: number
}

/** The file boundary, so tests (and Windows rename failures) stay out of the real disk. A write that reports false (or throws) did not land. */
export interface HistoryIo {
  read(file: string): string | null
  write(file: string, data: string): boolean | void | Promise<boolean | void>
}

const RETRYABLE = new Set(['EPERM', 'EBUSY', 'EACCES'])
// Windows refuses a rename while another process (an indexer, an antivirus scan) holds the target.
const RENAME_RETRY_DELAYS_MS = [50, 100, 200, 400]

/**
 * Temp file, fsync, then rename: the existing file is never opened for writing, so a failed write leaves
 * the previous history whole. A refused rename is retried after a growing pause; when it still fails the
 * temp file goes and false comes back, and the caller keeps the data in memory for its next flush.
 */
export const fileIo: HistoryIo = {
  read(file) {
    try { return fs.readFileSync(file, 'utf8') } catch { return null }
  },
  async write(file, data) {
    const temp = `${file}.${process.pid}.tmp`
    try {
      fs.mkdirSync(path.dirname(file), { recursive: true })
      const fd = fs.openSync(temp, 'w')
      try {
        fs.writeFileSync(fd, data, 'utf8')
        fs.fsyncSync(fd)
      } finally {
        fs.closeSync(fd)
      }
      for (let attempt = 0; ; attempt++) {
        try {
          fs.renameSync(temp, file)
          return true
        } catch (error) {
          const code = (error as NodeJS.ErrnoException).code ?? ''
          if (!RETRYABLE.has(code) || attempt >= RENAME_RETRY_DELAYS_MS.length) throw error
          await new Promise((resolve) => setTimeout(resolve, RENAME_RETRY_DELAYS_MS[attempt]))
        }
      }
    } catch {
      try { fs.unlinkSync(temp) } catch { /* never written, or already gone */ }
      return false
    }
  },
}

/** The URL as it is remembered, or null for anything that is not a plain http(s) page. */
export function historyUrl(raw: unknown): string | null {
  if (typeof raw !== 'string' || raw.length > MAX_URL_CHARS) return null
  let parsed: URL
  try { parsed = new URL(raw) } catch { return null }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null
  if (parsed.username || parsed.password) return null
  for (const name of parsed.searchParams.keys()) if (SENSITIVE_PARAM.test(name)) return null
  parsed.hash = ''
  return parsed.toString()
}

function clean(value: unknown, max: number): string {
  return typeof value === 'string' ? value.replace(/\s+/g, ' ').trim().slice(0, max) : ''
}

/** Only the data: image main fetched is kept; a remote icon address (the older format) is dropped, never truncated. */
function cleanFavicon(value: unknown): string {
  return typeof value === 'string' && value.length <= MAX_FAVICON_CHARS && /^data:image\/[a-z0-9.+-]+;base64,[A-Za-z0-9+/=]*$/i.test(value) ? value : ''
}

/** Visited http(s) pages: address, title, icon address, last visit and count. No page content. */
export class BrowserHistory {
  private entries: HistoryEntry[] = []
  private dirty = false
  private inflight: Promise<boolean> | null = null
  private timer: ReturnType<typeof setTimeout> | null = null
  private readonly cap: number
  private readonly now: () => number
  private readonly io: HistoryIo

  constructor(private readonly file: string, options: { cap?: number; now?: () => number; io?: HistoryIo } = {}) {
    this.cap = options.cap ?? HISTORY_CAP
    this.now = options.now ?? Date.now
    this.io = options.io ?? fileIo
    this.load()
  }

  private load(): void {
    const text = this.io.read(this.file)
    if (!text) return
    try {
      const data = JSON.parse(text)
      const list: unknown[] = Array.isArray(data?.entries) ? data.entries : []
      const seen = new Set<string>()
      for (const raw of list) {
        const item = raw as Partial<HistoryEntry>
        const url = historyUrl(item?.url)
        if (!url || seen.has(url) || !Number.isFinite(item.lastVisit) || !Number.isFinite(item.visits)) continue
        seen.add(url)
        this.entries.push({ url, title: clean(item.title, MAX_TITLE_CHARS), favicon: cleanFavicon(item.favicon),
          lastVisit: item.lastVisit as number, visits: Math.max(1, Math.floor(item.visits as number)) })
      }
      this.entries.sort((a, b) => b.lastVisit - a.lastVisit)
      this.entries.length = Math.min(this.entries.length, this.cap)
    } catch {
      this.entries = []
    }
  }

  /** Newest first. */
  list(limit = this.cap): HistoryEntry[] {
    return this.entries.slice(0, Math.max(0, limit)).map((entry) => ({ ...entry }))
  }

  /** A page was opened: counts a visit and moves it to the front. */
  visit(rawUrl: unknown, title?: unknown, favicon?: unknown): void {
    const url = historyUrl(rawUrl)
    if (!url) return
    const at = this.entries.findIndex((entry) => entry.url === url)
    const found = at >= 0 ? this.entries.splice(at, 1)[0] : null
    this.entries.unshift({
      url,
      title: clean(title, MAX_TITLE_CHARS) || found?.title || '',
      favicon: cleanFavicon(favicon) || found?.favicon || '',
      lastVisit: this.now(),
      visits: (found?.visits ?? 0) + 1,
    })
    if (this.entries.length > this.cap) this.entries.length = this.cap
    this.touched()
  }

  /** The title or icon of an already remembered page arrived later. */
  update(rawUrl: unknown, patch: { title?: unknown; favicon?: unknown }): void {
    const url = historyUrl(rawUrl)
    const entry = url ? this.entries.find((item) => item.url === url) : null
    if (!entry) return
    const title = clean(patch.title, MAX_TITLE_CHARS)
    const favicon = cleanFavicon(patch.favicon)
    if ((!title || title === entry.title) && (!favicon || favicon === entry.favicon)) return
    if (title) entry.title = title
    if (favicon) entry.favicon = favicon
    this.touched()
  }

  /** Forgets everything and writes the empty file at once; false when the old file could not be replaced. */
  clear(): Promise<boolean> {
    this.entries = []
    this.dirty = true
    return this.flush()
  }

  /** Writes pending changes now (also on shutdown); false when the write failed and the data waits for the next flush. */
  flush(): Promise<boolean> {
    if (this.timer) clearTimeout(this.timer)
    this.timer = null
    // One write at a time: a slow retry must not be overtaken by a newer write and then overwrite it.
    if (this.inflight) return this.inflight.then(() => this.flush())
    if (!this.dirty) return Promise.resolve(true)
    this.dirty = false
    let outcome: boolean | void | Promise<boolean | void>
    try { outcome = this.io.write(this.file, JSON.stringify({ version: 1, entries: this.entries })) } catch { outcome = false }
    const settle = (result: boolean | void) => {
      const ok = result !== false
      if (!ok) this.dirty = true
      return ok
    }
    if (!(outcome instanceof Promise)) return Promise.resolve(settle(outcome))
    const attempt = outcome.then(settle, () => settle(false)).then((ok) => {
      this.inflight = null
      return ok
    })
    this.inflight = attempt
    return attempt
  }

  private touched(): void {
    this.dirty = true
    if (this.timer) return
    this.timer = setTimeout(() => { void this.flush() }, SAVE_DELAY_MS)
    this.timer.unref?.()
  }
}
