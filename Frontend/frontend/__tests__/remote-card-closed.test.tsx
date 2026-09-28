/**
 * A card a phone answered first closes on the desktop by itself
 * (docs/remote-control.md). The backend publishes `card_closed` on
 * `/wake-stream-all` for the answer that won; the renderer drops that card
 * from whichever store holds it (a turn's command/question cards in useChat,
 * bridge and tray cards in useMCPApproval) and says who answered.
 *
 * Harness: the real `useChat` and `useMCPApproval` with hand-fed streams, as
 * in `remote-message.test.tsx`.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, renderHook, act } from '@testing-library/react'

vi.mock('axios', () => {
  const post = vi.fn(); const get = vi.fn(); const del = vi.fn(); const put = vi.fn()
  return { default: { post, get, delete: del, put }, post, get }
})

import axios from 'axios'
import { useChat } from '../renderer/hooks/home/useChat'
import { useMCPApproval } from '../renderer/hooks/home/useMCPApproval'
import { applyCardClosed } from '../renderer/hooks/home/gateResponse'
import { parseCardClosed } from '../renderer/lib/remoteControl'
import { cevir } from '../renderer/lib/i18n'

const mockedAxios = axios as unknown as { post: ReturnType<typeof vi.fn>; get: ReturnType<typeof vi.fn> }

const API = 'http://127.0.0.1:8000'
const USER = { id: 1, name: 'b', sessionToken: 'tok' } as any
const CONFIG = { provider_type: 'subscription', model_name: 'claude-opus-5' } as any

const enc = (ev: object) => new TextEncoder().encode(`data: ${JSON.stringify(ev)}\n\n`)
const encRaw = (payload: string) => new TextEncoder().encode(`data: ${payload}\n\n`)

const makeStream = () => {
  const queue: Array<{ done: boolean; value?: Uint8Array }> = []
  let waiter: ((v: any) => void) | null = null
  const deliver = (item: { done: boolean; value?: Uint8Array }) => {
    if (waiter) { const w = waiter; waiter = null; w(item) } else queue.push(item)
  }
  return {
    push: (ev: object) => deliver({ done: false, value: enc(ev) }),
    pushRaw: (payload: string) => deliver({ done: false, value: encRaw(payload) }),
    close: () => deliver({ done: true }),
    response: {
      ok: true,
      body: {
        getReader: () => ({
          read: () => queue.length ? Promise.resolve(queue.shift()) : new Promise(res => { waiter = res }),
        }),
      },
    },
  }
}

let turns: Array<ReturnType<typeof makeStream>>
let wakes: Array<ReturnType<typeof makeStream>>
let gateReplies: Record<string, object>
let pending: Record<string, any>

const installFetch = () => {
  turns = []
  wakes = []
  gateReplies = {}
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    const u = String(url)
    if (u.endsWith('/chat-stream')) {
      const stream = makeStream()
      turns.push(stream)
      return Promise.resolve(stream.response)
    }
    if (u.includes('/wake-stream-all')) {
      const stream = makeStream()
      wakes.push(stream)
      return Promise.resolve(stream.response)
    }
    const gate = u.match(/\/(?:command-approval|question-answer)\/(.+)$/)
    const body = gate && gateReplies[gate[1]] ? gateReplies[gate[1]] : { status: 'ok' }
    return Promise.resolve({ ok: true, status: 200, json: async () => body })
  }))
}

// Stable across renders, as in mcp-card-routing.test.tsx.
const showToast = vi.fn()
const refreshFileTree = vi.fn()
const suggest = (n: string) => n
const setGenFiles = vi.fn()
const setDel = vi.fn()

const hook = () => renderHook(() => {
  const chat = useChat(API, USER, CONFIG, '/ws', showToast, refreshFileTree, suggest)
  const mcp = useMCPApproval({
    API, enabled: true, workspacePath: '/ws',
    setPendingGenFiles: setGenFiles, setPendingDelete: setDel,
    setPendingCommand: chat.setPendingCommand, setPendingFix: chat.setPendingFix,
    showToast, screenConvId: chat.activeConvId, onOwnersChange: chat.setBridgeGates,
  })
  return { chat, mcp }
})
type Api = ReturnType<typeof hook>['result']

const flush = async () => { await act(async () => { for (let i = 0; i < 20; i++) await Promise.resolve() }) }
const open = async (result: Api, id: number) => {
  await act(async () => { await result.current.chat.selectConversation({ id } as any) })
}
const startTurn = async (result: Api) => {
  act(() => {
    void result.current.chat.sendMessage('go', '', 'tr', 'step', 'medium', vi.fn(), vi.fn(),
      undefined, false, undefined, 'user', undefined)
  })
  await flush()
  return turns[turns.length - 1]
}
const closed = (cardId: unknown, extra: Record<string, unknown> = {}) => ({
  type: 'card_closed', card_id: cardId, conversation_id: 1, by: 'phone:iPhone', decision: 'approve', ...extra,
})
const pushWake = async (frame: object) => { wakes.forEach(w => w.push(frame)); await flush() }
const toastTexts = () => showToast.mock.calls.map(c => c[0])

beforeEach(() => {
  installFetch()
  pending = {}
  showToast.mockReset()
  mockedAxios.post.mockReset().mockResolvedValue({ data: {} })
  mockedAxios.get.mockReset().mockImplementation(async (url: string) => {
    const u = String(url)
    if (u.endsWith('/mcp-pending')) return { data: { pending } }
    if (u.includes('/context-usage')) return { data: { percent: 1, should_compact: false, message_count: 0 } }
    return { data: [] }
  })
})

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('card_closed · a turn\'s cards', () => {
  it('closes the matching command card, shows the next queued one, and names the phone', async () => {
    const { result } = hook()
    await open(result, 1)
    const turn = await startTurn(result)
    turn.push({ type: 'command_approval_needed', command: 'git push', gate_id: 'g1' })
    turn.push({ type: 'command_approval_needed', command: 'rm -rf build', gate_id: 'g2' })
    await flush()
    expect(result.current.chat.pendingCommand?.gateId).toBe('g1')

    await pushWake(closed('g1'))

    expect(result.current.chat.pendingCommand?.gateId).toBe('g2')
    expect(toastTexts()).toEqual([cevir('gate.answered.phoneApproved', { cihaz: 'iPhone' })])
    expect(toastTexts()[0]).toContain('iPhone')
    expect(showToast.mock.calls[0][1]).toBe('info')

    // A queued card closed by the phone leaves the queue without being shown.
    turn.push({ type: 'command_approval_needed', command: 'ls', gate_id: 'g3' })
    await flush()
    await pushWake(closed('g3', { decision: 'reject' }))
    expect(result.current.chat.attention[1].approvals).toEqual(['cmd:g2'])

    // Turn end still clears what is left, and nothing stays "awaiting".
    await pushWake(closed('g2', { decision: 'reject' }))
    expect(result.current.chat.pendingCommand).toBeNull()
    turn.push({ type: 'done', stop_reason: 'complete' })
    turn.close()
    await flush()
    expect(result.current.chat.loading).toBe(false)
    expect(result.current.chat.convStatus[1]).not.toBe('awaiting')
  })

  it('closes a question card with the "answered" note', async () => {
    const { result } = hook()
    await open(result, 1)
    const turn = await startTurn(result)
    turn.push({ type: 'question_needed', gate_id: 'q1', questions: [{ question: 'Engine?', options: [] }] })
    await flush()
    expect(result.current.chat.pendingQuestion?.gateId).toBe('q1')

    await pushWake(closed('q1', { decision: 'answer' }))

    expect(result.current.chat.pendingQuestion).toBeNull()
    expect(toastTexts()).toEqual([cevir('gate.answered.phoneAnswered', { cihaz: 'iPhone' })])
  })

  it('a click that lands after the phone closed the card does not drop the card behind it', async () => {
    const { result } = hook()
    await open(result, 1)
    const turn = await startTurn(result)
    turn.push({ type: 'command_approval_needed', command: 'a', gate_id: 'g1' })
    turn.push({ type: 'command_approval_needed', command: 'b', gate_id: 'g2' })
    await flush()
    await pushWake(closed('g1'))
    expect(result.current.chat.pendingCommand?.gateId).toBe('g2')

    gateReplies.g1 = { status: 'already_answered', by: 'phone:iPhone', decision: 'approve', at: 'x' }
    await act(async () => { await result.current.chat.approveCommand('g1', true) })

    expect(result.current.chat.pendingCommand?.gateId).toBe('g2')
  })

  it('an unknown card id changes nothing and shows nothing', async () => {
    const { result } = hook()
    await open(result, 1)
    const turn = await startTurn(result)
    turn.push({ type: 'command_approval_needed', command: 'a', gate_id: 'g1' })
    await flush()

    await pushWake(closed('someone-elses-card'))

    expect(result.current.chat.pendingCommand?.gateId).toBe('g1')
    expect(showToast).not.toHaveBeenCalled()
  })

  it('malformed frames are ignored and the stream keeps working', async () => {
    const { result } = hook()
    await open(result, 1)
    const turn = await startTurn(result)
    turn.push({ type: 'command_approval_needed', command: 'a', gate_id: 'g1' })
    await flush()

    const bad = [
      { type: 'card_closed' },
      closed(42),
      closed(''),
      closed('x'.repeat(1025)),
      { type: 'card_closed', card_id: 'g1' },
      { type: 'card_closed', card_id: 'g1', by: 7 },
    ]
    for (const frame of bad) await pushWake(frame)
    wakes.forEach(w => w.pushRaw('{"type":"card_closed","card_id":'))
    await flush()
    expect(result.current.chat.pendingCommand?.gateId).toBe('g1')
    expect(showToast).not.toHaveBeenCalled()

    await pushWake(closed('g1'))
    expect(result.current.chat.pendingCommand).toBeNull()
  })
})

describe('card_closed · bridge and tray cards', () => {
  const unityReq = (owner: number | null) => ({
    tool: 'manage_gameobject', params: { action: 'delete', target: 'Cube' },
    workspace_path: '/ws', conversation_id: owner,
  })

  it('closes the bridge card on screen and a tray entry', async () => {
    const { result } = hook()
    await open(result, 1)
    pending = { m1: unityReq(1), t1: unityReq(null) }
    await act(async () => { await result.current.mcp.poll() })
    expect(result.current.mcp.activeGate?.gateId).toBe('m1')
    expect(result.current.chat.pendingCommand?.gateId).toBe('m1')
    expect(result.current.mcp.unknownGates.map(g => g.gateId)).toEqual(['t1'])

    // The answer already took both out of /mcp-pending.
    pending = {}
    await pushWake(closed('m1', { decision: 'reject' }))
    expect(result.current.mcp.activeGate).toBeNull()
    expect(result.current.chat.pendingCommand).toBeNull()
    await pushWake(closed('t1', { conversation_id: null }))
    expect(result.current.mcp.unknownGates).toEqual([])
    expect(toastTexts()).toEqual([
      cevir('gate.answered.phoneRejected', { cihaz: 'iPhone' }),
      cevir('gate.answered.phoneApproved', { cihaz: 'iPhone' }),
    ])
  })
})

describe('card_closed · parsing', () => {
  it('reads only well-formed frames', () => {
    expect(parseCardClosed(closed('g1'))).toEqual({ cardId: 'g1', by: 'phone:iPhone', decision: 'approve' })
    for (const bad of [null, undefined, 'card_closed', 7, [], { type: 'remote_message', card_id: 'g1', by: 'phone:x' },
      closed(null), closed({}), { ...closed('g1'), by: null }]) {
      expect(parseCardClosed(bad as any)).toBeNull()
    }
  })

  it('applyCardClosed never throws, even on frames that are not objects', () => {
    for (const bad of [null, undefined, 1, 'x', Symbol('s'), closed('nobody-holds-this')]) {
      expect(applyCardClosed(bad as any, showToast)).toBe(false)
    }
    expect(showToast).not.toHaveBeenCalled()
  })
})
