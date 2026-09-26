/**
 * PARALLEL CHATS (Phase 3, slice 1).
 *
 * The owner's requirement: switching to another chat does NOT stop the one in
 * the background, a new chat runs independently of it, and a stream never
 * leaks into the wrong chat.
 *
 * Every test here drives the real `useChat` with a hand-fed SSE stream per
 * conversation, so chunks can be delivered to chat A while chat B is on
 * screen. The assertions are on what the hook hands the screen, because that
 * is where the measured bugs were visible: A's answer under B, Stop in B
 * killing A, B refusing to send while A ran, and B's new turn wiping A's card.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, renderHook, act } from '@testing-library/react'

vi.mock('axios', () => {
  const post = vi.fn()
  const get = vi.fn()
  const del = vi.fn()
  const put = vi.fn()
  return { default: { post, get, delete: del, put }, post, get }
})

import axios from 'axios'
import { useChat } from '../renderer/hooks/home/useChat'
import { Sidebar } from '../renderer/components/home/Sidebar'
import { cevir, translations } from '../renderer/lib/i18n'

const mockedAxios = axios as unknown as {
  post: ReturnType<typeof vi.fn>; get: ReturnType<typeof vi.fn>
}

const API = 'http://127.0.0.1:8000'
const USER = { id: 1, name: 'b', sessionToken: 'tok' } as any
// `subscription` keeps the `done` branch away from `parseGeneratedFiles`.
const CONFIG = { provider_type: 'subscription', model_name: 'claude-opus-5' } as any

const enc = (ev: object) => new TextEncoder().encode(`data: ${JSON.stringify(ev)}\n\n`)

/** One SSE stream whose frames the test pushes by hand. */
const makeStream = () => {
  const queue: Array<{ done: boolean; value?: Uint8Array }> = []
  let waiter: ((v: any) => void) | null = null
  let failer: ((e: any) => void) | null = null
  const deliver = (item: { done: boolean; value?: Uint8Array }) => {
    if (waiter) { const w = waiter; waiter = null; failer = null; w(item) } else queue.push(item)
  }
  return {
    signal: null as AbortSignal | null,
    push: (ev: object) => deliver({ done: false, value: enc(ev) }),
    close: () => deliver({ done: true }),
    fail: (err: Error) => { if (failer) { const f = failer; waiter = null; failer = null; f(err) } },
    response: {
      ok: true,
      body: {
        getReader: () => ({
          read: () => queue.length
            ? Promise.resolve(queue.shift())
            : new Promise((res, rej) => { waiter = res; failer = rej }),
        }),
      },
    },
  }
}

type Stream = ReturnType<typeof makeStream>
let streams: Record<number, Stream[]>
let fetchMock: ReturnType<typeof vi.fn>

const installFetch = () => {
  streams = {}
  fetchMock = vi.fn((url: string, init?: any) => {
    const u = String(url)
    if (u.endsWith('/chat-stream')) {
      const convId = JSON.parse(init.body).conversation_id
      const s = makeStream()
      s.signal = init.signal
      ;(streams[convId] ||= []).push(s)
      init.signal?.addEventListener('abort', () => {
        const e = new Error('aborted'); (e as any).name = 'AbortError'; s.fail(e)
      })
      return Promise.resolve(s.response)
    }
    if (u.includes('/wake-stream')) return new Promise(() => {})
    return Promise.resolve({ ok: true, status: 200, json: async () => ({ status: 'ok' }) })
  })
  vi.stubGlobal('fetch', fetchMock)
}

const serverMessages: Record<number, any[]> = {
  1: [],
  2: [{ id: 200, role: 'user', content: 'B old question', smells: [], timestamp: '2026-09-26T00:00:00Z' }],
}

const hook = () => renderHook(() => useChat(API, USER, CONFIG, '/ws', vi.fn(), vi.fn(), (n: string) => n))

/** Lets pending promise callbacks (fetch resolution, reader reads) run. */
const flush = async () => { await act(async () => { for (let i = 0; i < 10; i++) await Promise.resolve() }) }

const send = (result: any, text: string, setPendingDelete: any = vi.fn()) => {
  // Not awaited: the promise only settles when the stream ends.
  let p: Promise<void> | undefined
  act(() => { p = result.current.sendMessage(text, '', 'tr', 'auto', 'medium', vi.fn(), setPendingDelete) })
  return p!
}

const contents = (msgs: any[]) => msgs.map(m => String(m.content || '')).join('\n')

beforeEach(() => {
  installFetch()
  mockedAxios.post.mockReset().mockImplementation(async (url: string) => {
    if (String(url).endsWith('/conversations')) return { data: { id: 3 } }
    return { data: {} }
  })
  mockedAxios.get.mockReset().mockImplementation(async (url: string) => {
    const m = String(url).match(/\/conversations\/(\d+)\/messages$/)
    if (m) return { data: serverMessages[Number(m[1])] ?? [] }
    if (String(url).includes('/context-usage')) return { data: { percent: 1, should_compact: false, message_count: 0 } }
    return { data: [] }
  })
})

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('parallel chats · no bleed', () => {
  it("A's chunks never show under B, and switching back to A shows them", async () => {
    const { result } = hook()
    await act(async () => { await result.current.selectConversation({ id: 1 } as any) })
    send(result, 'A question')
    await flush()
    const a = streams[1][0]
    a.push({ type: 'text', content: 'A-chunk-1' })
    await flush()

    // B's history arrives only when the test lets it: the window in which the
    // old code still showed A's list under B's id.
    let releaseB: (v: any) => void = () => {}
    mockedAxios.get.mockImplementationOnce(() => new Promise(r => { releaseB = r }))
    let selecting: Promise<void> | undefined
    act(() => { selecting = result.current.selectConversation({ id: 2 } as any) })
    a.push({ type: 'text', content: 'A-chunk-2' })
    await flush()
    expect(result.current.activeConvId).toBe(2)
    expect(contents(result.current.messages)).not.toContain('A-chunk')
    expect(contents(result.current.messages)).not.toContain('A question')

    await act(async () => { releaseB({ data: serverMessages[2] }); await selecting })
    a.push({ type: 'text', content: 'A-chunk-3' })
    await flush()
    expect(contents(result.current.messages)).toContain('B old question')
    expect(contents(result.current.messages)).not.toContain('A-chunk')

    await act(async () => { await result.current.selectConversation({ id: 1 } as any) })
    const aText = contents(result.current.messages)
    expect(aText).toContain('A question')
    expect(aText).toContain('A-chunk-1A-chunk-2A-chunk-3')
    expect(result.current.loading).toBe(true)
  })

  it('an event naming another conversation is ignored; untagged and matching ones apply', async () => {
    const { result } = hook()
    await act(async () => { await result.current.selectConversation({ id: 1 } as any) })
    send(result, 'A question')
    await flush()
    const a = streams[1][0]
    a.push({ type: 'text', content: 'foreign', conversation_id: 99 })
    a.push({ type: 'command_approval_needed', command: 'x', gate_id: 'g-foreign', conversation_id: 99 })
    a.push({ type: 'text', content: 'tagged-', conversation_id: 1 })
    a.push({ type: 'text', content: 'untagged' })
    await flush()
    const text = contents(result.current.messages)
    expect(text).not.toContain('foreign')
    expect(text).toContain('tagged-untagged')
    expect(result.current.pendingCommand).toBeNull()
  })

  it("A's failure bubble lands in A, not in the chat on screen", async () => {
    const { result } = hook()
    await act(async () => { await result.current.selectConversation({ id: 1 } as any) })
    send(result, 'A question')
    await flush()
    await act(async () => { await result.current.selectConversation({ id: 2 } as any) })
    streams[1][0].fail(new Error('network down'))
    await flush()
    expect(contents(result.current.messages)).not.toContain(cevir('chat.errorOccurred'))
    await act(async () => { await result.current.selectConversation({ id: 1 } as any) })
    expect(contents(result.current.messages)).toContain(cevir('chat.errorOccurred'))
  })
})

describe('parallel chats · stop is per chat', () => {
  it('Stop in B does not abort A and posts /chat-stop for B only', async () => {
    const { result } = hook()
    await act(async () => { await result.current.selectConversation({ id: 1 } as any) })
    send(result, 'A question')
    await flush()
    await act(async () => { await result.current.selectConversation({ id: 2 } as any) })

    act(() => { result.current.stopMessage() })
    await flush()

    expect(streams[1][0].signal!.aborted).toBe(false)
    const stops = fetchMock.mock.calls.map(c => String(c[0])).filter(u => u.includes('/chat-stop/'))
    expect(stops).toEqual([`${API}/chat-stop/2`])

    // A is still running and still receiving.
    streams[1][0].push({ type: 'text', content: 'A-after-stop' })
    await flush()
    await act(async () => { await result.current.selectConversation({ id: 1 } as any) })
    expect(result.current.loading).toBe(true)
    expect(contents(result.current.messages)).toContain('A-after-stop')
  })

  it('with both running, Stop in B aborts B and leaves A alone', async () => {
    const { result } = hook()
    await act(async () => { await result.current.selectConversation({ id: 1 } as any) })
    send(result, 'A question')
    await flush()
    await act(async () => { await result.current.selectConversation({ id: 2 } as any) })
    send(result, 'B question')
    await flush()
    expect(streams[2]?.length).toBe(1)

    act(() => { result.current.stopMessage() })
    await flush()
    expect(streams[2][0].signal!.aborted).toBe(true)
    expect(streams[1][0].signal!.aborted).toBe(false)
    expect(result.current.loading).toBe(false)
  })
})

describe('parallel chats · independent turns', () => {
  it('a second chat can send while the first is still loading', async () => {
    const { result } = hook()
    await act(async () => { await result.current.selectConversation({ id: 1 } as any) })
    send(result, 'A question')
    await flush()
    await act(async () => { await result.current.selectConversation({ id: 2 } as any) })
    expect(result.current.loading).toBe(false)
    send(result, 'B question')
    await flush()

    const bodies = fetchMock.mock.calls
      .filter(c => String(c[0]).endsWith('/chat-stream'))
      .map(c => JSON.parse(c[1].body))
    expect(bodies.map(b => b.conversation_id)).toEqual([1, 2])

    streams[2][0].push({ type: 'text', content: 'B-answer' })
    streams[1][0].push({ type: 'text', content: 'A-answer' })
    await flush()
    expect(contents(result.current.messages)).toContain('B-answer')
    expect(contents(result.current.messages)).not.toContain('A-answer')
  })

  it("a new turn in B does not clear A's pending card", async () => {
    const { result } = hook()
    await act(async () => { await result.current.selectConversation({ id: 1 } as any) })
    send(result, 'A question')
    await flush()
    // The stream ends without a terminal event: the card outlives the turn,
    // exactly like the unanswered-question case in question-gate-teardown.
    streams[1][0].push({ type: 'command_approval_needed', command: 'rm -rf build', gate_id: 'gA' })
    streams[1][0].close()
    await flush()
    expect(result.current.pendingCommand?.gateId).toBe('gA')

    await act(async () => { await result.current.selectConversation({ id: 2 } as any) })
    expect(result.current.pendingCommand).toBeNull()
    send(result, 'B question')
    await flush()

    await act(async () => { await result.current.selectConversation({ id: 1 } as any) })
    expect(result.current.pendingCommand?.gateId).toBe('gA')
    // The card is still bound to a message that is on screen.
    const ids = result.current.messages.map((m: any) => m.id)
    expect(ids).toContain(result.current.pendingCommand!.messageId)
  })

  it('creating a new chat while another runs leaves the running chat untouched', async () => {
    const { result } = hook()
    await act(async () => { await result.current.selectConversation({ id: 1 } as any) })
    send(result, 'A question')
    await flush()
    await act(async () => { await result.current.createNewConversation() })
    expect(result.current.activeConvId).toBe(3)
    expect(result.current.messages).toEqual([])
    expect(result.current.loading).toBe(false)

    streams[1][0].push({ type: 'text', content: 'A-still-going' })
    await flush()
    expect(contents(result.current.messages)).not.toContain('A-still-going')
    await act(async () => { await result.current.selectConversation({ id: 1 } as any) })
    expect(contents(result.current.messages)).toContain('A-still-going')
    expect(streams[1][0].signal!.aborted).toBe(false)
  })
})

describe('parallel chats · card ownership', () => {
  it("a background chat's file card waits for that chat instead of filling the shared slot", async () => {
    const { result } = hook()
    const setPendingDelete = vi.fn()
    await act(async () => { await result.current.selectConversation({ id: 1 } as any) })
    send(result, 'A question', setPendingDelete)
    await flush()
    await act(async () => { await result.current.selectConversation({ id: 2 } as any) })
    streams[1][0].push({ type: 'pending_delete', path: 'Assets/Old.cs' })
    await flush()
    expect(setPendingDelete).not.toHaveBeenCalled()
    expect(result.current.convStatus[1]).toBe('awaiting')

    await act(async () => { await result.current.selectConversation({ id: 1 } as any) })
    expect(setPendingDelete).toHaveBeenCalledTimes(1)
    const card = setPendingDelete.mock.calls[0][0]
    expect(card.path).toBe('Assets/Old.cs')
    expect(result.current.messages.map((m: any) => m.id)).toContain(card.messageId)
  })

  it('a card from the global bridge path follows the screen and survives Stop', async () => {
    const { result } = hook()
    await act(async () => { await result.current.selectConversation({ id: 1 } as any) })
    const bridgeCard = { command: 'unity: create', gateId: 'mcp-1', messageId: -1, kind: 'unity' as const }
    act(() => { result.current.setPendingCommand(bridgeCard) })
    await act(async () => { await result.current.selectConversation({ id: 2 } as any) })
    expect(result.current.pendingCommand?.gateId).toBe('mcp-1')
    act(() => { result.current.stopMessage() })
    expect(result.current.pendingCommand?.gateId).toBe('mcp-1')
    // No conversation owns it, so no sidebar row claims it.
    expect(result.current.convStatus).toEqual({})
  })
})

describe('parallel chats · sidebar status', () => {
  it('running, awaiting approval, and finished-unread are reported per chat', async () => {
    const { result } = hook()
    await act(async () => { await result.current.selectConversation({ id: 1 } as any) })
    send(result, 'A question')
    await flush()
    await act(async () => { await result.current.selectConversation({ id: 2 } as any) })
    expect(result.current.convStatus[1]).toBe('running')
    expect(result.current.convStatus[2]).toBeUndefined()

    streams[1][0].push({ type: 'command_approval_needed', command: 'ls', gate_id: 'g1' })
    await flush()
    expect(result.current.convStatus[1]).toBe('awaiting')

    streams[1][0].push({ type: 'done', stop_reason: 'complete' })
    streams[1][0].close()
    await flush()
    // The card is still open, so approval wins over "unread".
    expect(result.current.convStatus[1]).toBe('awaiting')
  })

  it('a background turn that finishes is marked unread until opened, then synced from the server', async () => {
    const { result } = hook()
    await act(async () => { await result.current.selectConversation({ id: 1 } as any) })
    send(result, 'A question')
    await flush()
    await act(async () => { await result.current.selectConversation({ id: 2 } as any) })
    const before = mockedAxios.get.mock.calls.filter(c => String(c[0]).endsWith('/conversations/1/messages')).length
    streams[1][0].push({ type: 'text', content: 'A-done' })
    streams[1][0].push({ type: 'done', stop_reason: 'complete' })
    streams[1][0].close()
    await flush()
    expect(result.current.convStatus[1]).toBe('unread')
    const after = mockedAxios.get.mock.calls.filter(c => String(c[0]).endsWith('/conversations/1/messages')).length
    expect(after).toBe(before + 1)
    // The screen is still B.
    expect(contents(result.current.messages)).toContain('B old question')

    await act(async () => { await result.current.selectConversation({ id: 1 } as any) })
    expect(result.current.convStatus[1]).toBeUndefined()
  })

  it('the sidebar draws one indicator per status, with translated labels', () => {
    const conversations = [
      { id: 1, title: 'one' }, { id: 2, title: 'two' }, { id: 3, title: 'three' }, { id: 4, title: 'four' },
    ]
    render(<Sidebar
      {...({} as any)}
      isSidebarOpen sidebarTab="chats" setSidebarTab={vi.fn()}
      conversations={conversations} activeConvId={4} selectConversation={vi.fn()}
      createNewConversation={vi.fn()} deleteConversation={vi.fn()}
      editingId={null} setEditingId={vi.fn()} tempTitle="" setTempTitle={vi.fn()} saveRename={vi.fn()}
      user={USER} setShowSettings={vi.fn()} handleLogout={vi.fn()}
      convStatus={{ 1: 'running', 2: 'awaiting', 3: 'unread' }}
    />)
    expect(screen.getByTestId('conv-status-1').getAttribute('title')).toBe(cevir('sidebar.statusRunning'))
    expect(screen.getByTestId('conv-status-2').getAttribute('title')).toBe(cevir('sidebar.statusAwaiting'))
    expect(screen.getByTestId('conv-status-3').getAttribute('title')).toBe(cevir('sidebar.statusUnread'))
    expect(screen.queryByTestId('conv-status-4')).toBeNull()
  })

  it('the status labels exist in both languages', () => {
    for (const key of ['sidebar.statusRunning', 'sidebar.statusAwaiting', 'sidebar.statusUnread']) {
      expect(translations.tr[key]).toBeTruthy()
      expect(translations.en[key]).toBeTruthy()
      expect(translations.tr[key]).not.toBe(translations.en[key])
    }
  })
})
