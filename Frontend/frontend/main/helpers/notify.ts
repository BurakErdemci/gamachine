/**
 * Desktop notifications for chats that need the user while they look elsewhere
 * (the `notify` invoke channel).
 *
 * The renderer decides WHEN to notify; this module only shows what it was
 * handed, after checking the shape. The payload is treated as untrusted: its
 * strings reach the OS only as a notification title and body, and the one
 * value that drives behaviour on click, the conversation id, is a positive
 * integer or nothing.
 */

export const NOTIFY_TITLE_MAX = 80
export const NOTIFY_BODY_MAX = 240

export interface NotifyRequest {
  title: string
  body: string
  conversationId?: number
}

const ALLOWED_KEYS = new Set(['title', 'body', 'conversationId'])

// C0/C1 controls and bidi overrides/isolates. A toast is plain text; none of
// these carry meaning there, and the bidi ones can reorder what is read.
const UNSAFE_CHARS = /[\u0000-\u001F\u007F-\u009F\u061C\u202A-\u202E\u2066-\u2069\u200E\u200F]/g
// Zero width space, word joiner, zero width no-break space: they draw nothing,
// so two different strings look identical. Deleted rather than spaced, so the
// toast reads as the chat title does on screen (`stripBidi` removes the same
// ones). U+200C/U+200D stay: Persian/Indic spelling and emoji sequences need them.
const ZERO_WIDTH = /[\u200B\u2060\uFEFF]/g

const clean = (s: string, max: number): string => {
  const flat = s.replace(ZERO_WIDTH, '').replace(UNSAFE_CHARS, ' ').replace(/\s+/g, ' ').trim()
  // Code points, not UTF-16 units, so a cut never splits a surrogate pair.
  const chars = Array.from(flat)
  return chars.length > max ? `${chars.slice(0, max - 1).join('')}…` : flat
}

/** The validated request, or `null` for anything that is not exactly one. */
export function parseNotifyPayload(payload: unknown): NotifyRequest | null {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return null
  const proto = Object.getPrototypeOf(payload)
  if (proto !== Object.prototype && proto !== null) return null
  const p = payload as Record<string, unknown>
  if (Object.keys(p).some((k) => !ALLOWED_KEYS.has(k))) return null
  if (typeof p.title !== 'string' || typeof p.body !== 'string') return null
  const id = p.conversationId
  if (id !== undefined && !(typeof id === 'number' && Number.isSafeInteger(id) && id > 0)) return null
  const title = clean(p.title, NOTIFY_TITLE_MAX)
  const body = clean(p.body, NOTIFY_BODY_MAX)
  if (!title) return null
  return id === undefined ? { title, body } : { title, body, conversationId: id as number }
}

/** The part of Electron's `Notification` this module uses. */
export interface NotificationLike {
  on(event: 'click' | 'failed', listener: (...args: any[]) => void): unknown
  show(): void
}

/** The part of Electron's `BrowserWindow` a click touches. */
export interface NotifyWindow {
  isDestroyed(): boolean
  isMinimized(): boolean
  restore(): void
  show(): void
  focus(): void
  webContents: { send(channel: string, ...args: unknown[]): void }
}

export interface NotifierDeps {
  isSupported: () => boolean
  create: (options: { title: string; body: string }) => NotificationLike
}

/**
 * Upper bound on notifications held for their click handler. Each one is a
 * few closures; the cap only exists so the set cannot grow without limit when
 * the user never clicks.
 */
export const NOTIFY_LIVE_MAX = 32

export function createNotifier(deps: NotifierDeps) {
  // Electron pitfall: a Notification with no JS reference left can be garbage
  // collected while its toast is still on screen, and the click then reaches
  // no handler. References are kept until the click (or a failure), not until
  // 'close': on Windows 'close' also fires when the toast times out into the
  // Action Center, where it can still be clicked (Electron 34 docs, `close`).
  const live = new Set<NotificationLike>()

  const show = (payload: unknown, win: NotifyWindow | null): { shown: boolean } => {
    const req = parseNotifyPayload(payload)
    if (!req) throw new Error('Invalid notification payload.')
    if (!deps.isSupported()) return { shown: false }

    const n = deps.create({ title: req.title, body: req.body })
    const release = () => { live.delete(n) }
    n.on('failed', release)
    n.on('click', () => {
      release()
      if (!win || win.isDestroyed()) return
      if (win.isMinimized()) win.restore()
      win.show()
      win.focus()
      if (req.conversationId !== undefined) win.webContents.send('open-conversation', req.conversationId)
    })
    live.add(n)
    if (live.size > NOTIFY_LIVE_MAX) {
      const oldest = live.values().next().value
      if (oldest) live.delete(oldest)
    }
    n.show()
    return { shown: true }
  }

  return { show, liveCount: () => live.size }
}
