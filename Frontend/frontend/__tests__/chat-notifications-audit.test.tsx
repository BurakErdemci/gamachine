/**
 * Desktop notification cases found by the Codex audit of 6e4d1a6
 * (.delegate-runs/ARCHIVE/2026-09-26-notifyaudit): an open on-screen file card,
 * a transport error after `done`, seen-id pruning, and the Arabic Letter Mark.
 * Deferred findings of the same audit: zero-width characters, a second queued
 * file card, and the error bubble after `done`.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import React from 'react'
import { render, cleanup, act } from '@testing-library/react'

vi.mock('axios', () => {
  const post = vi.fn(); const get = vi.fn(); const del = vi.fn(); const put = vi.fn()
  return { default: { post, get, delete: del, put }, post, get }
})

import axios from 'axios'
import { useChat, type ChatAttention } from '../renderer/hooks/home/useChat'
import { useChatNotifications } from '../renderer/hooks/home/useChatNotifications'
import { aktifDilAyarla, cevir } from '../renderer/lib/i18n'
import { parseNotifyPayload } from '../main/helpers/notify'
import { stripBidi } from '../renderer/lib/modelText'

const mockedAxios = axios as unknown as { get: ReturnType<typeof vi.fn> }
const API = 'http://127.0.0.1:8000'
const USER = { id: 1, sessionToken: 'tok' } as any
const CONFIG = { provider_type: 'subscription', model_name: 'claude-opus-5' } as any
const CONVS = [{ id: 1, title: 'Alpha' }, { id: 2, title: 'Beta' }] as any[]
const enc = (ev: object) => new TextEncoder().encode(`data: ${JSON.stringify(ev)}\n\n`)

function makeStream() {
  const queue: Array<{ done: boolean; value?: Uint8Array }> = []
  let waiter: ((item: any) => void) | null = null
  let failer: ((error: Error) => void) | null = null
  const deliver = (item: { done: boolean; value?: Uint8Array }) => {
    if (waiter) { const next = waiter; waiter = null; failer = null; next(item) }
    else queue.push(item)
  }
  return {
    push: (ev: object) => deliver({ done: false, value: enc(ev) }),
    close: () => deliver({ done: true }),
    fail: (error: Error) => { if (failer) { const reject = failer; failer = null; waiter = null; reject(error) } },
    response: { ok: true, body: { getReader: () => ({
      read: () => queue.length ? Promise.resolve(queue.shift()) : new Promise((res, rej) => { waiter = res; failer = rej }),
    }) } },
  }
}

let stream: ReturnType<typeof makeStream>
let api: { chat: ReturnType<typeof useChat>; pendingDelete: any; send: () => void }
let invoke: ReturnType<typeof vi.fn>

// The file slot is wired as home.tsx wires it: `screenCardOpen` reads the slot.
const Harness: React.FC = () => {
  const [pendingDelete, setPendingDelete] = React.useState<any>(null)
  const [pendingGen, setPendingGen] = React.useState<any>(null)
  const chat = useChat(API, USER, CONFIG, '/ws', vi.fn(), vi.fn(), (name) => name)
  useChatNotifications({
    conversations: CONVS, activeConvId: chat.activeConvId, attention: chat.attention,
    trayGates: [], bridgeSynced: true, screenCardOpen: !!pendingDelete || !!pendingGen,
    onOpenConversation: chat.selectConversation,
  })
  api = { chat, pendingDelete, send: () => {
    void chat.sendMessage('delete x', '', 'en', 'step', 'medium', setPendingGen, setPendingDelete)
  } }
  return null
}

const flush = async () => { await act(async () => { for (let i = 0; i < 10; i++) await Promise.resolve() }) }
const notes = () => invoke.mock.calls.filter(c => c[0] === 'notify').map(c => c[1] as any)

const setup = async () => {
  aktifDilAyarla('en')
  vi.spyOn(document, 'hasFocus').mockReturnValue(false)
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
  invoke = vi.fn(async () => ({ shown: true }))
  ;(window as any).ipc = { invoke, on: vi.fn(() => () => {}) }
  mockedAxios.get.mockImplementation(async (url: string) => {
    if (String(url).includes('/context-usage')) return { data: { percent: 1 } }
    return { data: [] }
  })
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    if (String(url).endsWith('/chat-stream')) { stream = makeStream(); return Promise.resolve(stream.response) }
    if (String(url).includes('/wake-stream')) return new Promise(() => {})
    return Promise.resolve({ ok: true, status: 200, json: async () => ({}) })
  }))
  render(<Harness />)
  await flush()
  await act(async () => { await api.chat.selectConversation(CONVS[0]) })
  await flush()
  act(() => api.send())
  await flush()
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  delete (window as any).ipc
})

describe('notification audit cases', () => {
  it('does not announce "finished" over an open on-screen file card', async () => {
    await setup()
    stream.push({ type: 'pending_delete', path: '/ws/x' })
    await flush()
    expect(api.pendingDelete).toMatchObject({ path: '/ws/x' })
    stream.push({ type: 'done' })
    stream.close()
    await flush()
    expect(api.pendingDelete).toMatchObject({ path: '/ws/x' })
    expect(notes().map(n => n.body)).toEqual([cevir('notify.awaiting', { baslik: 'Alpha' })])
  })

  it('keeps a turn that sent done as finished when a later read fails', async () => {
    await setup()
    stream.push({ type: 'done' })
    await flush()
    stream.fail(new Error('connection reset after done'))
    await flush()
    expect(api.chat.attention[1].turnEnd?.failed).toBe(false)
    expect(notes().map(n => n.body)).toEqual([cevir('notify.finished', { baslik: 'Alpha' })])
  })

  it('still reports a turn that broke before done as failed', async () => {
    await setup()
    stream.fail(new Error('connection reset'))
    await flush()
    expect(api.chat.attention[1].turnEnd?.failed).toBe(true)
    expect(notes().map(n => n.body)).toEqual([cevir('notify.failed', { baslik: 'Alpha' })])
  })

  it('announces one wait once when a second file card follows an open one', async () => {
    await setup()
    stream.push({ type: 'pending_delete', path: '/ws/x' })
    await flush()
    stream.push({ type: 'pending_delete', path: '/ws/y' })
    await flush()
    expect(notes().map(n => n.body)).toEqual([cevir('notify.awaiting', { baslik: 'Alpha' })])
  })

  it('adds no error bubble when a read fails after done', async () => {
    await setup()
    stream.push({ type: 'text', content: 'all good' })
    stream.push({ type: 'done' })
    await flush()
    stream.fail(new Error('connection reset after done'))
    await flush()
    expect(api.chat.messages.map(m => m.content)).not.toContain(cevir('chat.errorOccurred'))
  })

  it('still refetches a background chat whose read failed after done', async () => {
    await setup()
    stream.push({ type: 'done' })
    await flush()
    await act(async () => { await api.chat.selectConversation(CONVS[1]) })
    await flush()
    mockedAxios.get.mockClear()
    stream.fail(new Error('connection reset after done'))
    await flush()
    const urls = mockedAxios.get.mock.calls.map(c => String(c[0]))
    expect(urls).toContain(`${API}/conversations/1/messages`)
  })
})

describe('seen-id pruning', () => {
  const convs = [{ id: 1, title: 'Alpha' }, { id: 2, title: 'Beta' }] as any[]
  const make = (approvals: string[], awaiting: boolean): Record<number, ChatAttention> => ({
    1: { approvals, bridgeGates: [], awaiting, turnEnd: null },
  })
  const Pruned = ({ attention }: { attention: Record<number, ChatAttention> }) => {
    useChatNotifications({
      conversations: convs, activeConvId: 2, attention, trayGates: [], bridgeSynced: true,
      screenCardOpen: false, onOpenConversation: () => {},
    })
    return null
  }

  it('does not re-announce an id that was briefly absent while thousands of others passed', () => {
    vi.spyOn(document, 'hasFocus').mockReturnValue(true)
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
    invoke = vi.fn(async () => ({ shown: true }))
    ;(window as any).ipc = { invoke, on: vi.fn(() => () => {}) }

    const { rerender } = render(<Pruned attention={make(['cmd:old'], true)} />)
    expect(invoke).toHaveBeenCalledTimes(1)
    rerender(<Pruned attention={make(Array.from({ length: 5000 }, (_, i) => `cmd:new-${i}`), true)} />)
    rerender(<Pruned attention={make([], false)} />)
    rerender(<Pruned attention={make(['cmd:old'], true)} />)
    expect(invoke).toHaveBeenCalledTimes(1)
  })
})

describe('Arabic Letter Mark', () => {
  it('is removed from notification text and from stripBidi', () => {
    const parsed = parseNotifyPayload({ title: 'safe؜name', body: 'file؜.txt' })
    expect(parsed!.title).not.toContain('؜')
    expect(parsed!.body).not.toContain('؜')
    expect(stripBidi('a؜b')).toBe('ab')
  })
})

describe('zero-width characters', () => {
  // U+200B zero width space, U+2060 word joiner, U+FEFF zero width no-break space.
  const hidden = 'sa​fe⁠na﻿me'

  it('are removed from notification text and from stripBidi', () => {
    const parsed = parseNotifyPayload({ title: hidden, body: hidden })
    expect(parsed!.title).toBe('safename')
    expect(parsed!.body).toBe('safename')
    expect(stripBidi(hidden)).toBe('safename')
  })

  it('keeps joiners that legitimate text needs', () => {
    // U+200D builds emoji sequences; U+200C is part of Persian and Indic spelling.
    const family = '\u{1F468}‍\u{1F469}‍\u{1F467}'
    const persian = 'می‌خواهم'
    expect(parseNotifyPayload({ title: family, body: persian })).toEqual({ title: family, body: persian })
    expect(stripBidi(family)).toBe(family)
    expect(stripBidi(persian)).toBe(persian)
  })
})
