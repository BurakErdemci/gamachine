/**
 * Desktop notifications for parallel chats (Phase 3, slice 2).
 *
 * Reference behaviour is the Codex desktop app: a chat that needs an approval
 * or whose turn ends raises an OS notification, unless the user is looking at
 * that chat in a focused window; clicking one opens that chat.
 *
 * The harness wires the real `useChat`, `useMCPApproval` and
 * `useChatNotifications` the way `home.tsx` does. `window.ipc.invoke('notify')`
 * is recorded, never delivered: no OS notification is raised by this file.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, cleanup, act } from '@testing-library/react'

vi.mock('axios', () => {
  const post = vi.fn(); const get = vi.fn(); const del = vi.fn(); const put = vi.fn()
  return { default: { post, get, delete: del, put }, post, get }
})

import axios from 'axios'
import { useChat } from '../renderer/hooks/home/useChat'
import { useMCPApproval } from '../renderer/hooks/home/useMCPApproval'
import { useChatNotifications } from '../renderer/hooks/home/useChatNotifications'
import { aktifDilAyarla, cevir } from '../renderer/lib/i18n'

const mockedAxios = axios as unknown as { post: ReturnType<typeof vi.fn>; get: ReturnType<typeof vi.fn> }

const API = 'http://127.0.0.1:8000'
const USER = { id: 1, name: 'b', sessionToken: 'tok' } as any
const CONFIG = { provider_type: 'subscription', model_name: 'claude-opus-5' } as any
const CONVS = [
  { id: 1, title: 'Alpha' },
  { id: 2, title: 'Beta' },
  { id: 3, title: 'safe\u202Eexe.txt' },
] as any[]

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

let streams: Record<number, ReturnType<typeof makeStream>[]>
let pending: Record<string, any>
let focused: boolean
let ipcListeners: Record<string, (...args: unknown[]) => void>
let ipcInvoke: ReturnType<typeof vi.fn>
let api: { chat: ReturnType<typeof useChat>; mcp: ReturnType<typeof useMCPApproval> }

const showToast = vi.fn()
const refreshFileTree = vi.fn()
const suggest = (n: string) => n

const Harness: React.FC = () => {
  const [, setGenFiles] = React.useState<any>(null)
  const [, setDel] = React.useState<any>(null)
  const chat = useChat(API, USER, CONFIG, '/ws', showToast, refreshFileTree, suggest)
  const mcp = useMCPApproval({
    API, enabled: true, workspacePath: '/ws',
    setPendingGenFiles: setGenFiles, setPendingDelete: setDel,
    setPendingCommand: chat.setPendingCommand, setPendingFix: chat.setPendingFix,
    showToast, screenConvId: chat.activeConvId, onOwnersChange: chat.setBridgeGates,
  })
  useChatNotifications({
    conversations: CONVS,
    activeConvId: chat.activeConvId,
    attention: chat.attention,
    trayGates: mcp.unknownGates,
    bridgeSynced: mcp.synced,
    screenCardOpen: false,
    onOpenConversation: chat.selectConversation,
  })
  api = { chat, mcp }
  return null
}

const flush = async () => { await act(async () => { for (let i = 0; i < 10; i++) await Promise.resolve() }) }
const poll = async () => { await act(async () => { await api.mcp.poll() }); await flush() }
const open = async (id: number) => {
  await act(async () => { await api.chat.selectConversation({ id } as any) })
  await flush()
}
const send = async (text: string) => {
  act(() => { void api.chat.sendMessage(text, '', 'tr', 'step', 'medium', vi.fn(), vi.fn()) })
  await flush()
}
/** Mount, and let the first `/mcp-pending` answer land (the reload baseline). */
const mount = async () => { render(<Harness />); await flush(); await poll() }

const notes = () => ipcInvoke.mock.calls.filter(c => c[0] === 'notify').map(c => c[1] as any)
const awaitingBody = (title: string) => cevir('notify.awaiting', { baslik: title })

const unityReq = (owner: number | null) => ({
  tool: 'manage_gameobject', params: { action: 'delete', target: 'Cube' },
  workspace_path: '/ws', conversation_id: owner,
})

beforeEach(() => {
  aktifDilAyarla('en')
  streams = {}
  pending = {}
  focused = true
  vi.spyOn(document, 'hasFocus').mockImplementation(() => focused)
  vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => 'visible')
  vi.stubGlobal('fetch', vi.fn((url: string, init?: any) => {
    const u = String(url)
    if (u.endsWith('/chat-stream')) {
      const convId = JSON.parse(init.body).conversation_id
      const s = makeStream()
      ;(streams[convId] ||= []).push(s)
      init.signal?.addEventListener('abort', () => {
        const e = new Error('aborted'); (e as any).name = 'AbortError'; s.fail(e)
      })
      return Promise.resolve(s.response)
    }
    if (u.includes('/wake-stream')) return new Promise(() => {})
    return Promise.resolve({ ok: true, status: 200, json: async () => ({ status: 'ok' }) })
  }))
  mockedAxios.post.mockReset().mockResolvedValue({ data: {} })
  mockedAxios.get.mockReset().mockImplementation(async (url: string) => {
    const u = String(url)
    if (u.endsWith('/mcp-pending')) return { data: { pending } }
    if (/\/conversations\/\d+\/messages$/.test(u)) return { data: [] }
    if (u.includes('/context-usage')) return { data: { percent: 1, should_compact: false, message_count: 0 } }
    return { data: [] }
  })
  ipcListeners = {}
  ipcInvoke = vi.fn(async () => ({ shown: true }))
  ;(window as any).ipc = {
    invoke: ipcInvoke,
    on: vi.fn((channel: string, cb: (...args: unknown[]) => void) => {
      ipcListeners[channel] = cb
      return () => { delete ipcListeners[channel] }
    }),
  }
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  delete (window as any).ipc
})

describe('chat notifications · awaiting', () => {
  it('an approval in an off-screen chat notifies once, however often the state is re-reported', async () => {
    await mount()
    await open(1)
    await send('A question')
    await open(2)
    expect(notes()).toHaveLength(0)

    streams[1][0].push({ type: 'command_approval_needed', command: 'rm x', gate_id: 'g-a1' })
    await flush()
    expect(notes()).toEqual([{ title: cevir('notify.title'), body: awaitingBody('Alpha'), conversationId: 1 }])

    // More stream traffic, polls, and chat switches re-report the same wait.
    streams[1][0].push({ type: 'text', content: 'still working' })
    await flush()
    await poll()
    await poll()
    await open(1)
    await open(2)
    // A second card queued behind the open one is the same wait.
    streams[1][0].push({ type: 'command_approval_needed', command: 'rm y', gate_id: 'g-a2' })
    await flush()
    expect(notes()).toHaveLength(1)
  })

  it('a question in the chat on screen of a focused window is not notified, and stays spent after a blur', async () => {
    await mount()
    await open(1)
    await send('A question')
    streams[1][0].push({ type: 'question_needed', questions: [{ question: 'Which?' }], gate_id: 'q-1' })
    await flush()
    expect(api.chat.pendingQuestion?.gateId).toBe('q-1')
    expect(notes()).toHaveLength(0)

    focused = false
    await poll()
    await open(2)
    await poll()
    expect(notes()).toHaveLength(0)
  })

  it('the chat on screen notifies when the window is not focused', async () => {
    await mount()
    await open(1)
    await send('A question')
    focused = false
    streams[1][0].push({ type: 'command_approval_needed', command: 'rm x', gate_id: 'g-u1' })
    await flush()
    expect(notes()).toEqual([{ title: cevir('notify.title'), body: awaitingBody('Alpha'), conversationId: 1 }])
  })

  it('an owned bridge card notifies once per gate; the ones present at load do not', async () => {
    pending = { 'g-old': unityReq(2) }
    await mount()
    await open(1)
    await poll()
    expect(api.chat.convStatus[2]).toBe('awaiting')
    expect(notes()).toHaveLength(0)

    // The old gate is decided; a new one arrives for the same chat.
    pending = {}
    await poll()
    pending = { 'g-new': unityReq(2) }
    await poll()
    await poll()
    await poll()
    expect(notes()).toEqual([{ title: cevir('notify.title'), body: awaitingBody('Beta'), conversationId: 2 }])
  })

  it('a poll that drops a bridge gate and reports it again does not re-notify', async () => {
    await mount()
    await open(1)
    pending = { 'g-x': unityReq(2) }
    await poll()
    expect(notes()).toHaveLength(1)
    pending = {}
    await poll()
    expect(api.chat.convStatus[2]).toBeUndefined()
    pending = { 'g-x': unityReq(2) }
    await poll()
    expect(api.chat.convStatus[2]).toBe('awaiting')
    expect(notes()).toHaveLength(1)
  })

  it('the chat title is stripped of bidi overrides', async () => {
    await mount()
    pending = { 'g-b': unityReq(3) }
    await poll()
    expect(notes()).toHaveLength(1)
    expect(notes()[0].body).toBe(awaitingBody('safeexe.txt'))
    expect(notes()[0].body).not.toMatch(/\u202E/)
  })
})

describe('chat notifications · turn end', () => {
  it('a turn that finishes off screen notifies "finished"', async () => {
    await mount()
    await open(1)
    await send('A question')
    await open(2)
    streams[1][0].push({ type: 'text', content: 'answer' })
    streams[1][0].push({ type: 'done' })
    streams[1][0].close()
    await flush()
    expect(notes()).toEqual([{
      title: cevir('notify.title'), body: cevir('notify.finished', { baslik: 'Alpha' }), conversationId: 1,
    }])
    // Another chat's traffic re-renders with the finished turn still recorded.
    await send('B question')
    streams[2][0].push({ type: 'text', content: 'B answer' })
    await flush()
    await poll()
    await open(1)
    expect(notes()).toHaveLength(1)
  })

  it('a turn that fails off screen notifies "stopped with an error"', async () => {
    await mount()
    await open(1)
    await send('A question')
    await open(2)
    streams[1][0].fail(new Error('network down'))
    await flush()
    expect(notes()).toEqual([{
      title: cevir('notify.title'), body: cevir('notify.failed', { baslik: 'Alpha' }), conversationId: 1,
    }])
  })

  it('a turn that finishes on screen in an unfocused window notifies', async () => {
    await mount()
    await open(1)
    await send('A question')
    focused = false
    streams[1][0].push({ type: 'done' })
    streams[1][0].close()
    await flush()
    expect(notes()).toHaveLength(1)
    expect(notes()[0].body).toBe(cevir('notify.finished', { baslik: 'Alpha' }))
  })

  it('a turn the user stops does not notify', async () => {
    await mount()
    await open(1)
    await send('A question')
    focused = false
    act(() => { api.chat.stopMessage() })
    await flush()
    await open(2)
    await poll()
    expect(api.chat.convStatus[1]).toBeUndefined()
    expect(notes()).toHaveLength(0)
  })

  it('a turn that finishes on screen in a focused window does not notify', async () => {
    await mount()
    await open(1)
    await send('A question')
    streams[1][0].push({ type: 'done' })
    streams[1][0].close()
    await flush()
    focused = false
    await poll()
    expect(notes()).toHaveLength(0)
  })
})

describe('chat notifications · tray', () => {
  it('each new unknown-owner card notifies once, with no conversation id', async () => {
    pending = { 't-old': unityReq(null) }
    await mount()
    expect(api.mcp.unknownGates).toHaveLength(1)
    focused = false
    await poll()
    expect(notes()).toHaveLength(0)

    pending = { 't-old': unityReq(null), 't-1': unityReq(null) }
    await poll()
    await poll()
    expect(notes()).toEqual([{ title: cevir('notify.title'), body: cevir('notify.trayAwaiting') }])

    pending = { 't-1': unityReq(null), 't-2': unityReq(null) }
    await poll()
    await poll()
    expect(notes()).toHaveLength(2)

    // The list changes, but what is left in it was announced already.
    pending = { 't-2': unityReq(null) }
    await poll()
    expect(api.mcp.unknownGates.map(g => g.gateId)).toEqual(['t-2'])
    expect(notes()).toHaveLength(2)
  })

  it('a focused window does not notify a tray card', async () => {
    await mount()
    pending = { 't-1': unityReq(null) }
    await poll()
    expect(api.mcp.unknownGates).toHaveLength(1)
    expect(notes()).toHaveLength(0)
  })
})

describe('chat notifications · open-conversation', () => {
  it('opens the named chat the way a sidebar click does', async () => {
    await mount()
    await open(1)
    expect(typeof ipcListeners['open-conversation']).toBe('function')

    await act(async () => { ipcListeners['open-conversation'](2) })
    await flush()
    expect(api.chat.activeConvId).toBe(2)
    expect(mockedAxios.get.mock.calls.some(c => String(c[0]).endsWith('/conversations/2/messages'))).toBe(true)

    // Anything that is not a known chat id changes nothing.
    for (const bad of ['1', 0, -1, 1.5, 99, null]) {
      await act(async () => { ipcListeners['open-conversation'](bad) })
    }
    await flush()
    expect(api.chat.activeConvId).toBe(2)
  })
})
