/**
 * Queued messages in the chat flow (4.1.0 design C1): the composer tray is gone; a message that
 * waits for the running turn is a dimmed bubble with its place in line and one action, "Remove
 * from queue". The backend's queue (GET/DELETE /conversations/{id}/queue) and this window's own
 * queue are one ordered list; 409 "claimed" says it already started.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import React from 'react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { render, screen, cleanup, fireEvent, renderHook, act } from '@testing-library/react'

import { QueuedMessages, ordinal } from '../renderer/components/home/QueuedMessages'
import { useTurnQueue, parseServerQueue, type QueuedView } from '../renderer/hooks/home/useTurnQueue'
import type { QueuedMessage } from '../renderer/hooks/home/useChat'
import { aktifDilAyarla } from '../renderer/lib/i18n'

const local = (id: number, draft: string, over: Partial<QueuedMessage> = {}) =>
  ({ id, text: draft, draft, code: '', lang: 'en', genMode: 'auto', thinkingLevel: 'auto', setPendingGenFiles: () => {}, setPendingDelete: () => {}, ultracode: false, ...over }) as unknown as QueuedMessage

const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve() })

beforeEach(() => aktifDilAyarla('en'))
afterEach(() => { cleanup(); vi.useRealTimers(); aktifDilAyarla('tr') })

describe('the queue in the flow', () => {
  it('draws each waiting message as a dimmed bubble with its place in line', () => {
    const items: QueuedView[] = [
      { key: 'l1', source: 'local', id: 1, text: 'Also make the arena floor darker.', place: 1 },
      { key: 'l2', source: 'local', id: 2, text: 'And put a checkpoint before the boss door.', place: 2 },
    ]
    render(<QueuedMessages items={items} onRemove={() => {}} />)
    const bubbles = screen.getAllByTestId('queued-message')
    expect(bubbles.map(b => b.className)).toEqual(['msg msg-user is-queued', 'msg msg-user is-queued'])
    const tags = screen.getAllByTestId('queued-tag').map(t => t.textContent)
    expect(tags[0]).toBe('Queued · 1st — starts when the current turn finishesRemove from queue')
    expect(tags[1]).toBe('Queued · 2nd — starts when the current turn finishesRemove from queue')
  })

  it('offers no edit and no "send now": withdraw only', () => {
    render(<QueuedMessages items={[{ key: 'l1', source: 'local', id: 1, text: 'x', place: 1 }]} onRemove={() => {}} />)
    expect(screen.getAllByRole('button').map(b => b.textContent)).toEqual(['Remove from queue'])
  })

  it('a message already handed to the running turn has no ordinal and is not withdrawable until its turn ends', () => {
    const { rerender } = render(<QueuedMessages items={[{ key: 'l1', source: 'local', id: 1, text: 'x', sent: true }]} onRemove={() => {}} />)
    expect(screen.getByTestId('queued-tag').textContent).toBe('sent, read at the next step')
    expect(screen.queryByTestId('queued-remove')).toBeNull()
    rerender(<QueuedMessages items={[{ key: 'l1', source: 'local', id: 1, text: 'x', sent: true, turnEnded: true }]} onRemove={() => {}} />)
    expect(screen.getByTestId('queued-remove')).toBeTruthy()
  })

  it('a paused queue says so and offers "Send next"', () => {
    const resume = vi.fn()
    render(<QueuedMessages items={[{ key: 'l1', source: 'local', id: 1, text: 'x', place: 1 }]} onRemove={() => {}} paused onResume={resume} />)
    fireEvent.click(screen.getByTestId('queued-resume'))
    expect(resume).toHaveBeenCalledTimes(1)
  })

  it('writes English ordinals and the Turkish "1."', () => {
    expect([1, 2, 3, 4, 11, 12, 13, 21, 22].map(n => ordinal(n, 'en'))).toEqual(['1st', '2nd', '3rd', '4th', '11th', '12th', '13th', '21st', '22nd'])
    expect(ordinal(2, 'tr')).toBe('2.')
  })

  it('the composer no longer gets the tray: home passes it no queue', () => {
    const home = readFileSync(resolve(__dirname, '../renderer/pages/home.tsx'), 'utf8')
    expect(home).not.toMatch(/queue=\{\{/)
    expect(home).not.toContain('sendQueuedNow')
    expect(home).toContain('queued: turnQueue.items')
  })
})

describe('useTurnQueue', () => {
  const API = 'http://x'
  const mk = (server: any[] = []) => ({
    get: vi.fn(async () => ({ data: { conversation_id: 12, queue: server } })),
    delete: vi.fn(async () => ({ data: { status: 'withdrawn' } })),
  })

  it('reads the backend queue only while a turn runs, server items first, then this window\'s', async () => {
    const http = mk([{ id: 882, text: 'second', created_at: null, order: 2 }, { id: 881, text: 'first', created_at: null, order: 1 }])
    const { result, rerender } = renderHook(({ active }) => useTurnQueue({
      API, conversationId: 12, active, local: [local(5, 'mine')], removeLocal: () => {}, http,
    }), { initialProps: { active: false } })
    await flush()
    expect(http.get).not.toHaveBeenCalled()
    expect(result.current.items.map(i => i.text)).toEqual(['mine'])
    rerender({ active: true })
    await flush()
    expect(http.get).toHaveBeenCalledWith(`${API}/conversations/12/queue`, undefined)
    expect(result.current.items.map(i => [i.text, i.place])).toEqual([['first', 1], ['second', 2], ['mine', 3]])
  })

  it('Remove calls DELETE for a server item and drops it', async () => {
    const http = mk([{ id: 881, text: 'first', order: 1 }])
    const { result } = renderHook(() => useTurnQueue({ API, conversationId: 12, active: true, local: [], removeLocal: () => {}, http, token: 'tok' }))
    await flush()
    await act(async () => { await result.current.remove(result.current.items[0]) })
    expect(http.delete).toHaveBeenCalledWith(`${API}/conversations/12/queue/881`, { headers: { 'X-Session-Token': 'tok' } })
    expect(result.current.items).toEqual([])
  })

  it('409 "claimed": the message stays and says it already started; no second try is offered', async () => {
    const http = mk([{ id: 881, text: 'first', order: 1 }])
    http.delete.mockRejectedValueOnce({ response: { status: 409, data: { detail: { code: 'claimed' } } } })
    const { result } = renderHook(() => useTurnQueue({ API, conversationId: 12, active: true, local: [], removeLocal: () => {}, http }))
    await flush()
    await act(async () => { await result.current.remove(result.current.items[0]) })
    expect(result.current.items[0].note).toBe('started')
    render(<QueuedMessages items={result.current.items} onRemove={() => {}} />)
    expect(screen.getByRole('status').textContent).toBe('Already started, it can no longer be removed')
    expect(screen.queryByTestId('queued-remove')).toBeNull()
  })

  it('a window-local message is withdrawn in the window, with no request', async () => {
    const http = mk()
    const removeLocal = vi.fn()
    const { result } = renderHook(() => useTurnQueue({ API, conversationId: 12, active: false, local: [local(7, 'later')], removeLocal, http }))
    await act(async () => { await result.current.remove(result.current.items[0]) })
    expect(removeLocal).toHaveBeenCalledWith(7)
    expect(http.delete).not.toHaveBeenCalled()
  })

  it('a queue read that lands after the turn ended does not bring a claimed message back (audit F1)', async () => {
    let land!: (v: any) => void
    const http = { get: vi.fn(() => new Promise(res => { land = res })), delete: vi.fn() }
    const { result, rerender } = renderHook(({ active }) => useTurnQueue({
      API, conversationId: 12, active, local: [], removeLocal: () => {}, http,
    }), { initialProps: { active: true } })
    expect(http.get).toHaveBeenCalledTimes(1)
    rerender({ active: false })
    await act(async () => { land({ data: { queue: [{ id: 7, text: 'already claimed', order: 1 }] } }); await Promise.resolve() })
    expect(result.current.items).toEqual([])
  })

  it('an older read landing after a newer one does not replace it (audit F1)', async () => {
    const lands: ((v: any) => void)[] = []
    const http = { get: vi.fn(() => new Promise(res => { lands.push(res) })), delete: vi.fn() }
    const { result } = renderHook(() => useTurnQueue({ API, conversationId: 12, active: true, local: [], removeLocal: () => {}, http }))
    let second!: Promise<void>
    act(() => { second = result.current.refresh() })
    await act(async () => { lands[1]({ data: { queue: [] } }); await second })
    await act(async () => { lands[0]({ data: { queue: [{ id: 7, text: 'old', order: 1 }] } }); await Promise.resolve() })
    expect(result.current.items).toEqual([])
  })

  it('accepts the bare list too and drops malformed rows', () => {
    expect(parseServerQueue([{ id: 1, text: 'a' }, { id: 2 }, null]).map(r => r.id)).toEqual([1])
  })
})
