/**
 * Message-by-message navigation of the chat thread (buttons and Alt+Up/Down).
 *
 * The user's mouse wheel is broken and the scrollbar thumb of a long chat is tiny, so a small
 * drag jumps far. jsdom has no layout: element rects are mocked, what is measured is WHICH
 * message a press picks and WHEN the shortcut stays out of the way.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { cleanup } from '@testing-library/react'

import {
  chatNavTarget, chatNavShortcutDirection, stepChatMessage, NAV_TOP_GAP_PX,
} from '../renderer/lib/chatNav'
import { translations } from '../renderer/lib/i18n'

afterEach(() => { cleanup(); document.body.innerHTML = '' })

const rectAt = (top: number) => ({ top, bottom: top + 50, left: 0, right: 0, width: 0, height: 50, x: 0, y: top, toJSON: () => ({}) }) as DOMRect

/** A thread whose top is at viewport y=100; `tops` are message tops in viewport px (a system event sits among them). */
function makeThread(tops: number[], scrollTop = 0) {
  const thread = document.createElement('div')
  thread.className = 'thread'
  thread.getBoundingClientRect = () => rectAt(100)
  thread.scrollTop = scrollTop
  thread.scrollTo = vi.fn() as any
  tops.forEach((top, i) => {
    const a = document.createElement('article')
    a.className = i % 2 ? 'msg msg-ai' : 'msg msg-user'
    a.setAttribute('data-role', i % 2 ? 'assistant' : 'user')
    a.getBoundingClientRect = () => rectAt(top)
    thread.appendChild(a)
  })
  const sys = document.createElement('article')
  sys.className = 'msg'
  sys.setAttribute('data-role', 'system')
  sys.getBoundingClientRect = () => rectAt(150)
  thread.appendChild(sys)
  document.body.appendChild(thread)
  return thread
}

describe('chatNavTarget: which message a press picks', () => {
  // message tops relative to the thread: -300, -100, 400 (the system event at +50 is never a target)
  const tops = [-200, 0, 500]

  it('prev picks the nearest message above and lands it under the top fade', () => {
    const t = makeThread(tops, 1000)
    expect(chatNavTarget(t, 'prev')).toEqual({ kind: 'message', scrollTop: 1000 - 100 - NAV_TOP_GAP_PX })
  })

  it('next picks the first message below, skipping system events', () => {
    const t = makeThread(tops, 1000)
    expect(chatNavTarget(t, 'next')).toEqual({ kind: 'message', scrollTop: 1000 + 400 - NAV_TOP_GAP_PX })
  })

  it('a message already on the landing line is the current one: prev goes above it, next below it', () => {
    const line = 100 + NAV_TOP_GAP_PX
    const t = makeThread([line - 200, line, line + 300], 500)
    expect(chatNavTarget(t, 'prev')).toEqual({ kind: 'message', scrollTop: 500 - 200 })
    expect(chatNavTarget(t, 'next')).toEqual({ kind: 'message', scrollTop: 500 + 300 })
  })

  it('prev at the first message does nothing; next past the last behaves like jump-to-latest', () => {
    const line = 100 + NAV_TOP_GAP_PX
    expect(chatNavTarget(makeThread([line, 700], 0), 'prev')).toBeNull()
    expect(chatNavTarget(makeThread([-300, line], 800), 'next')).toEqual({ kind: 'bottom' })
  })

  it('stepChatMessage scrolls smoothly, or calls onBottom past the last message', () => {
    const t = makeThread(tops, 1000)
    const onBottom = vi.fn()
    expect(stepChatMessage(t, 'next', onBottom)).toBe(true)
    expect(t.scrollTo).toHaveBeenCalledWith({ top: 1000 + 400 - NAV_TOP_GAP_PX, behavior: 'smooth' })
    expect(onBottom).not.toHaveBeenCalled()

    const end = makeThread([-300], 800)
    expect(stepChatMessage(end, 'next', onBottom)).toBe(true)
    expect(onBottom).toHaveBeenCalledTimes(1)
    expect(end.scrollTo).not.toHaveBeenCalled()

    const top = makeThread([100 + NAV_TOP_GAP_PX], 0)
    expect(stepChatMessage(top, 'prev', onBottom)).toBe(false)
    expect(stepChatMessage(null, 'prev', onBottom)).toBe(false)
  })
})

describe('Alt+ArrowUp / Alt+ArrowDown', () => {
  const press = (target: Element, init: KeyboardEventInit = {}) => {
    const e = new KeyboardEvent('keydown', { key: 'ArrowUp', altKey: true, bubbles: true, cancelable: true, ...init })
    Object.defineProperty(e, 'target', { value: target })
    return chatNavShortcutDirection(e)
  }
  const add = (html: string) => {
    const host = document.createElement('div')
    host.innerHTML = html
    document.body.appendChild(host)
    return host
  }

  it('maps Alt+Up to prev and Alt+Down to next; other combinations are ignored', () => {
    expect(press(document.body)).toBe('prev')
    expect(press(document.body, { key: 'ArrowDown' })).toBe('next')
    expect(press(document.body, { altKey: false })).toBeNull()
    expect(press(document.body, { ctrlKey: true })).toBeNull()
    expect(press(document.body, { shiftKey: true })).toBeNull()
    expect(press(document.body, { key: 'ArrowLeft' })).toBeNull()
  })

  it('works from the composer textarea, but not from other text fields, Monaco or the terminal', () => {
    const h = add(`<div class="composer"><textarea id="c"></textarea></div><input id="i"><textarea id="o"></textarea>
      <div class="monaco-editor"><textarea id="m"></textarea></div><div class="xterm"><textarea id="x"></textarea></div>`)
    const q = (id: string) => h.querySelector('#' + id)!
    expect(press(q('c'))).toBe('prev')
    expect(press(q('i'))).toBeNull()
    expect(press(q('o'))).toBeNull()
    expect(press(q('m'))).toBeNull()
    expect(press(q('x'))).toBeNull()
  })

  it('stays out while a dialog is open', () => {
    add('<div role="dialog"></div>')
    expect(press(document.body)).toBeNull()
  })

  it('stays out while a composer menu (mention or slash palette) is open', () => {
    const h = add('<div class="composer"><textarea id="c"></textarea></div><div class="composer-pop" data-testid="mention-menu"></div>')
    expect(press(h.querySelector('#c')!)).toBeNull()
  })
})

describe('the buttons', () => {
  // No test renders the whole Home page (it needs the backend, auth and Monaco), so the wiring is
  // read from source; the labels are the real translation tables.
  const fe = resolve(__dirname, '..')
  const home = readFileSync(resolve(fe, 'renderer/pages/home.tsx'), 'utf8')
  const css = readFileSync(resolve(fe, 'renderer/styles/gm/thread.css'), 'utf8')

  it('are labelled in Turkish and English', () => {
    expect(translations.tr['chat.prevMessage']).toBe('Önceki mesaj')
    expect(translations.tr['chat.nextMessage']).toBe('Sonraki mesaj')
    expect(translations.en['chat.prevMessage']).toBe('Previous message')
    expect(translations.en['chat.nextMessage']).toBe('Next message')
    expect(home).toContain("aria-label={t('chat.prevMessage')}")
    expect(home).toContain("aria-label={t('chat.nextMessage')}")
  })

  it('are not rendered in the phone layout (the phone shell calls threadWrap without them)', () => {
    expect(home).toMatch(/threadWrap = \(panel: React\.ReactNode, showNav = true\)/)
    expect(home).toMatch(/thread: threadWrap\(<ChatPanel [^\n]*\/>, false\)/)
  })
})

describe('the chat scrollbar', () => {
  const css = readFileSync(resolve(__dirname, '../renderer/styles/gm/thread.css'), 'utf8')

  it('is wider than the 4px global bar and its thumb keeps a minimum height', () => {
    expect(css).toMatch(/\.thread\.custom-scrollbar::-webkit-scrollbar\s*\{\s*width:\s*(\d+)px/)
    const w = Number(/\.thread\.custom-scrollbar::-webkit-scrollbar\s*\{\s*width:\s*(\d+)px/.exec(css)![1])
    expect(w).toBeGreaterThanOrEqual(8)
    const m = /\.thread\.custom-scrollbar::-webkit-scrollbar-thumb\s*\{[^}]*min-height:\s*(\d+)px/.exec(css)
    expect(Number(m?.[1])).toBeGreaterThanOrEqual(40)
  })

  it('resets the standard scrollbar-width/-color that would switch the webkit bar off', () => {
    expect(css).toMatch(/\.thread\.custom-scrollbar\s*\{[^}]*scrollbar-width:\s*auto;[^}]*scrollbar-color:\s*auto/)
    expect(/^\.thread \{[^}]*scrollbar-width/m.test(css)).toBe(false)
  })
})
