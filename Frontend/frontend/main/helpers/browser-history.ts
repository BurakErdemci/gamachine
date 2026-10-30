import fs from 'fs'
import path from 'path'

export const HISTORY_CAP = 500
const SAVE_DELAY_MS = 2_000
const MAX_TITLE_CHARS = 200
const MAX_URL_CHARS = 1_000
// A visited URL may carry a login or reset token; such pages are not remembered at all.
const SENSITIVE_PARAM = /^(token|access_token|id_token|refresh_token|code|key|api_key|apikey|secret|password|passwd|pwd|session|sessionid|sid|auth|signature|sig)$/i

export interface HistoryEntry {
  url: string
  title: string
  favicon: string
  lastVisit: number
  visits: number
}

/** The file boundary, so tests (and Windows rename failures) stay out of the real disk. */
export interface HistoryIo {
  read(file: string): string | null
  write(file: string, data: string): void
}

const RETRYABLE = new Set(['EPERM', 'EBUSY', 'EACCES'])

/**
 * Temp file then rename. Windows refuses a rename while another process (an indexer, an antivirus
 * scan) holds the target; a few retries cover that, and a direct write is the last resort.
 */
export const fileIo: HistoryIo = {
  read(file) {
    try { return fs.readFileSync(file, 'utf8') } catch { return null }
  },
  write(file, data) {
    fs.mkdirSync(path.dirname(file), { recursive: true })
    const temp = `${file}.${process.pid}.tmp`
    fs.writeFileSync(temp, data, 'utf8')
    for (let attempt = 0; ; attempt++) {
      try {
        fs.renameSync(temp, file)
        return
      } catch (error) {
        const code = (error as NodeJS.ErrnoException).code ?? ''
        if (!RETRYABLE.has(code) || attempt >= 4) break
      }
    }
    try {
      fs.writeFileSync(file, data, 'utf8')
    } finally {
      try { fs.unlinkSync(temp) } catch { /* already gone */ }
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

/** Visited http(s) pages: address, title, icon address, last visit and count. No page content. */
export class BrowserHistory {
  private entries: HistoryEntry[] = []
  private dirty = false
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
        this.entries.push({ url, title: clean(item.title, MAX_TITLE_CHARS), favicon: clean(item.favicon, MAX_URL_CHARS),
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
      favicon: clean(favicon, MAX_URL_CHARS) || found?.favicon || '',
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
    const favicon = clean(patch.favicon, MAX_URL_CHARS)
    if ((!title || title === entry.title) && (!favicon || favicon === entry.favicon)) return
    if (title) entry.title = title
    if (favicon) entry.favicon = favicon
    this.touched()
  }

  /** Forgets everything and writes the empty file at once. */
  clear(): void {
    this.entries = []
    this.dirty = true
    this.flush()
  }

  /** Writes pending changes now; called on shutdown. */
  flush(): void {
    if (this.timer) clearTimeout(this.timer)
    this.timer = null
    if (!this.dirty) return
    this.dirty = false
    try {
      this.io.write(this.file, JSON.stringify({ version: 1, entries: this.entries }))
    } catch {
      this.dirty = true
    }
  }

  private touched(): void {
    this.dirty = true
    if (this.timer) return
    this.timer = setTimeout(() => this.flush(), SAVE_DELAY_MS)
    this.timer.unref?.()
  }
}
