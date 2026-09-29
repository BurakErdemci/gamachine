/**
 * An approval mode a phone changed shows on the desktop by itself
 * (docs/remote-control.md). The backend applies the switch and publishes
 * `approval_mode_changed` on `/wake-stream-all`; the renderer takes the new mode
 * the way it takes its own switch (same code: `adoptGenerationMode`) and says
 * which phone did it.
 *
 * Harness: the real `useChat` with hand-fed streams, as in
 * `remote-card-closed.test.tsx`.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, renderHook, act } from '@testing-library/react'

const ipcInvoke = vi.hoisted(() => {
  const invoke = vi.fn()
  ;(globalThis as any).window.ipc = { invoke }
  return invoke
})

vi.mock('axios', () => {
  const post = vi.fn(); const get = vi.fn(); const del = vi.fn(); const put = vi.fn()
  return { default: { post, get, delete: del, put }, post, get }
})

import axios from 'axios'
import { useChat } from '../renderer/hooks/home/useChat'
import { parseModeChanged } from '../renderer/lib/remoteControl'
import { cevir } from '../renderer/lib/i18n'

const mockedAxios = axios as unknown as { post: ReturnType<typeof vi.fn>; get: ReturnType<typeof vi.fn> }

const API = 'http://127.0.0.1:8000'
const USER = { id: 1, name: 'b', sessionToken: 'tok' } as any
const CONFIG = { provider_type: 'subscription', model_name: 'claude-opus-5' } as any

const enc = (ev: object) => new TextEncoder().encode(`data: ${JSON.stringify(ev)}\n\n`)

const makeStream = () => {
  const queue: Array<{ done: boolean; value?: Uint8Array }> = []
  let waiter: ((v: any) => void) | null = null
  const deliver = (item: { done: boolean; value?: Uint8Array }) => {
    if (waiter) { const w = waiter; waiter = null; w(item) } else queue.push(item)
  }
  return {
    push: (ev: object) => deliver({ done: false, value: enc(ev) }),
    pushRaw: (payload: string) => deliver({ done: false, value: new TextEncoder().encode(`data: ${payload}\n\n`) }),
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

const installFetch = () => {
  turns = []
  wakes = []
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
    return Promise.resolve({ ok: true, status: 200, json: async () => ({ status: 'ok' }) })
  }))
}

const showToast = vi.fn()
const hook = () => renderHook(() => useChat(API, USER, CONFIG, '/ws', showToast, vi.fn(), (n: string) => n))
type Api = ReturnType<typeof hook>['result']

const flush = async () => { await act(async () => { for (let i = 0; i < 20; i++) await Promise.resolve() }) }
const open = async (result: Api, id: number) => {
  await act(async () => { await result.current.selectConversation({ id } as any) })
}
const startTurn = async (result: Api, conv?: number) => {
  act(() => {
    void result.current.sendMessage('go', '', 'tr', 'step', 'medium', vi.fn(), vi.fn(),
      undefined, false, undefined, 'user', conv)
  })
  await flush()
  return turns[turns.length - 1]
}
const changed = (mode: string, extra: Record<string, unknown> = {}) => ({
  type: 'approval_mode_changed', mode, previous: 'step', approved_pending: 0, by: 'phone:iPhone', at: 1, ...extra,
})
const pushWake = async (frame: object) => { wakes.forEach(w => w.push(frame)); await flush() }
const toastTexts = () => showToast.mock.calls.map(c => c[0])

beforeEach(() => {
  installFetch()
  showToast.mockReset()
  ipcInvoke.mockReset()
  mockedAxios.post.mockReset().mockResolvedValue({ data: {} })
  mockedAxios.get.mockReset().mockImplementation(async (url: string) => {
    if (String(url).endsWith('/approval-mode')) return { data: { mode: 'step', stored: true } }
    if (String(url).includes('/context-usage')) return { data: { percent: 1, should_compact: false, message_count: 0 } }
    return { data: [] }
  })
})

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('approval_mode_changed · the mode on screen', () => {
  it('a phone switch shows the new mode and names the phone', async () => {
    const { result } = hook()
    await flush()
    expect(result.current.generationMode).toBe('step')

    await pushWake(changed('balanced'))

    expect(result.current.generationMode).toBe('balanced')
    expect(toastTexts()).toEqual([cevir('mode.changedByPhone', { cihaz: 'iPhone', mod: cevir('mode.balanced') })])
    expect(showToast.mock.calls[0][1]).toBe('info')

    await pushWake(changed('step', { previous: 'balanced' }))
    expect(result.current.generationMode).toBe('step')
  })

  it('says how many waiting approvals the switch to auto approved', async () => {
    const { result } = hook()
    await flush()
    await pushWake(changed('auto', { approved_pending: 3 }))
    expect(result.current.generationMode).toBe('auto')
    expect(toastTexts()).toEqual([cevir('mode.changedByPhoneApproved',
      { cihaz: 'iPhone', mod: cevir('mode.auto'), sayi: 3 })])
  })

  it('a switch to the mode already in force is adopted without a note', async () => {
    const { result } = hook()
    await flush()
    await pushWake(changed('step', { previous: 'step' }))
    expect(result.current.generationMode).toBe('step')
    expect(showToast).not.toHaveBeenCalled()
  })

  it('every window that gets the frame shows it', async () => {
    const a = hook()
    const b = hook()
    await flush()
    await pushWake(changed('auto'))
    expect([a.result.current.generationMode, b.result.current.generationMode]).toEqual(['auto', 'auto'])
  })
})

describe('approval_mode_changed · cards, as after the desktop\'s own switch', () => {
  const twoChatsWithCards = async (result: Api) => {
    await open(result, 1)
    const first = await startTurn(result)
    first.push({ type: 'command_approval_needed', command: 'git push', gate_id: 'g1' })
    first.push({ type: 'command_approval_needed', command: 'git commit', gate_id: 'g2' })
    const second = await startTurn(result, 2)
    second.push({ type: 'command_approval_needed', command: 'ls', gate_id: 'g3' })
    await flush()
    expect(result.current.pendingCommand?.gateId).toBe('g1')
    expect(result.current.attention[2].approvals).toEqual(['cmd:g3'])
  }

  it('auto clears the in-chat cards of every chat', async () => {
    const { result } = hook()
    await twoChatsWithCards(result)

    await pushWake(changed('auto', { approved_pending: 3 }))

    expect(result.current.pendingCommand).toBeNull()
    expect(result.current.attention[1]?.approvals ?? []).toEqual([])
    expect(result.current.attention[2]?.approvals ?? []).toEqual([])
  })

  it('balanced and step keep them: the backend approved none of the in-chat ones', async () => {
    const { result } = hook()
    await twoChatsWithCards(result)

    await pushWake(changed('balanced'))
    await pushWake(changed('step', { previous: 'balanced' }))

    expect(result.current.pendingCommand?.gateId).toBe('g1')
    expect(result.current.attention[2].approvals).toEqual(['cmd:g3'])
  })

  it('the desktop\'s own switch to auto ends in the same state', async () => {
    const own = hook()
    await twoChatsWithCards(own.result)
    ipcInvoke.mockResolvedValue({ mode: 'auto' })
    await act(async () => { await own.result.current.setGenerationMode('auto') })
    expect(ipcInvoke).toHaveBeenCalledWith('approval-mode-set', 'auto', 'chat')
    expect(own.result.current.generationMode).toBe('auto')
    expect(own.result.current.pendingCommand).toBeNull()
    expect(own.result.current.attention[2]?.approvals ?? []).toEqual([])
  })

  it('a refused own switch changes nothing', async () => {
    const { result } = hook()
    await flush()
    ipcInvoke.mockResolvedValue({ refused: { code: 'agy_step_refused', pids: '7' } })
    await act(async () => { await result.current.setGenerationMode('step') })
    expect(result.current.generationMode).toBe('step')
    expect(toastTexts()).toEqual([cevir('mode.agyStepRefused', { pids: '7' })])
  })
})

describe('approval_mode_changed · frames that are not one', () => {
  it('malformed or foreign frames are ignored and the stream keeps working', async () => {
    const { result } = hook()
    await flush()
    const bad = [
      { type: 'approval_mode_changed' },
      changed('plan'),
      changed('AUTO'),
      changed(7 as any),
      changed('auto', { by: undefined }),
      changed('auto', { by: 'desktop' }),
      changed('auto', { by: 7 }),
    ]
    for (const frame of bad) await pushWake(frame)
    wakes.forEach(w => w.pushRaw('{"type":"approval_mode_changed","mode":'))
    await flush()
    expect(result.current.generationMode).toBe('step')
    expect(showToast).not.toHaveBeenCalled()

    await pushWake(changed('auto'))
    expect(result.current.generationMode).toBe('auto')
  })

  it('parses the fields it uses and clamps the count', () => {
    expect(parseModeChanged(changed('auto', { approved_pending: 2 })))
      .toEqual({ mode: 'auto', previous: 'step', approvedPending: 2, by: 'phone:iPhone' })
    expect(parseModeChanged(changed('step', { approved_pending: -4, previous: 5 })))
      .toEqual({ mode: 'step', previous: '', approvedPending: 0, by: 'phone:iPhone' })
    expect(parseModeChanged(changed('step', { approved_pending: 'many' }))?.approvedPending).toBe(0)
    for (const bad of [null, undefined, 'x', 7, [], { type: 'card_closed' }]) {
      expect(parseModeChanged(bad as any)).toBeNull()
    }
  })
})
