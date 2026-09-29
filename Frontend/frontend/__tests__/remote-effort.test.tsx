/**
 * The desktop's effort, shown and set from a phone (docs/remote-control.md).
 * Effort is one page-level state (`thinkingLevel` in pages/home.tsx). The
 * renderer reports it to the backend (`PUT /remote/desktop-effort`), and
 * applies a level a phone asks for (`remote_effort` frames on
 * `/wake-stream-all`) only when the active model offers it.
 *
 * Harness: the real `useChat` (which reads the stream) with the real
 * `useRemoteEffort`, wired around a `useState` level as home.tsx wires them.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, renderHook, act } from '@testing-library/react'
import { useState } from 'react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const ipcInvoke = vi.hoisted(() => {
  const invoke = vi.fn()
  ;(globalThis as any).window.ipc = { invoke }
  return invoke
})

vi.mock('axios', () => {
  const post = vi.fn(); const get = vi.fn(); const del = vi.fn(); const put = vi.fn()
  return { default: { post, get, delete: del, put }, post, get, put }
})

import axios from 'axios'
import { useChat } from '../renderer/hooks/home/useChat'
import {
  EFFORT_REPORT_EVERY_MS, deliverEffortRequest, onEffortRequest, parseEffortRequest, useRemoteEffort,
} from '../renderer/lib/remoteControl'
import { cevir } from '../renderer/lib/i18n'

const mockedAxios = axios as unknown as { post: ReturnType<typeof vi.fn>; get: ReturnType<typeof vi.fn>; put: ReturnType<typeof vi.fn> }

const API = 'http://127.0.0.1:8000'
const USER = { id: 1, name: 'b', sessionToken: 'tok' } as any
const CONFIG = { provider_type: 'subscription', model_name: 'claude-opus-5' } as any
const OPUS = ['auto', 'low', 'medium', 'high', 'xhigh', 'max']

const enc = (ev: object) => new TextEncoder().encode(`data: ${JSON.stringify(ev)}\n\n`)

const makeStream = () => {
  const queue: Array<{ done: boolean; value?: Uint8Array }> = []
  let waiter: ((v: any) => void) | null = null
  const deliver = (item: { done: boolean; value?: Uint8Array }) => {
    if (waiter) { const w = waiter; waiter = null; w(item) } else queue.push(item)
  }
  return {
    push: (ev: object) => deliver({ done: false, value: enc(ev) }),
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

let wakes: Array<ReturnType<typeof makeStream>>
const installFetch = () => {
  wakes = []
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    if (String(url).includes('/wake-stream-all')) {
      const stream = makeStream()
      wakes.push(stream)
      return Promise.resolve(stream.response)
    }
    return Promise.resolve({ ok: true, status: 200, json: async () => ({ status: 'ok' }) })
  }))
}

const showToast = vi.fn()
const flush = async () => { await act(async () => { for (let i = 0; i < 20; i++) await Promise.resolve() }) }
const pushWake = async (frame: object) => { wakes.forEach(w => w.push(frame)); await flush() }
const requested = (level: string, extra: Record<string, unknown> = {}) => ({
  type: 'remote_effort', level, by: 'phone:iPhone', at: 1, ...extra,
})
const reports = () => mockedAxios.put.mock.calls.map(c => ({ url: c[0], body: c[1], headers: c[2]?.headers }))

// As home.tsx: the page state, its caps, the report/apply hook and the stream reader.
// `chooseEffort` is home.tsx's: choosing a level also switches Ultracode off
// (the __tests__ source check below pins that home.tsx wires it to both callers).
const page = (initial: { level: string; levels: string[] | null; ultracode?: boolean } = { level: 'auto', levels: OPUS }) => renderHook(
  (props: { levels: string[] | null }) => {
    const [level, setLevel] = useState(initial.level)
    const [ultracode, setUltracode] = useState(initial.ultracode ?? false)
    const chooseEffort = (next: string) => { setLevel(next); if (ultracode) setUltracode(false) }
    useRemoteEffort({ api: API, token: 'tok', level, levels: props.levels, ultracode, setLevel: chooseEffort, showToast })
    const chat = useChat(API, USER, CONFIG, '/ws', showToast, vi.fn(), (n: string) => n)
    return { level, ultracode, chat }
  },
  { initialProps: { levels: initial.levels } },
)

beforeEach(() => {
  installFetch()
  showToast.mockReset()
  ipcInvoke.mockReset()
  mockedAxios.post.mockReset().mockResolvedValue({ data: {} })
  mockedAxios.put.mockReset().mockResolvedValue({ data: {} })
  mockedAxios.get.mockReset().mockImplementation(async (url: string) => {
    if (String(url).endsWith('/approval-mode')) return { data: { mode: 'step', stored: true } }
    if (String(url).includes('/context-usage')) return { data: { percent: 1, should_compact: false, message_count: 0 } }
    return { data: [] }
  })
})

afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals() })

describe('reporting the desktop effort', () => {
  it('reports the level and the levels on mount, with the app token', async () => {
    page()
    await flush()
    expect(reports()).toEqual([{
      url: `${API}/remote/desktop-effort`, body: { level: 'auto', levels: OPUS, ultracode: false }, headers: { 'X-Session-Token': 'tok' },
    }])
  })

  it('reports again when the level changes and when the levels change', async () => {
    const { result, rerender } = page()
    await flush()
    await pushWake(requested('high'))
    expect(result.current.level).toBe('high')
    expect(reports().map(r => r.body)).toEqual([
      { level: 'auto', levels: OPUS, ultracode: false },
      { level: 'high', levels: OPUS, ultracode: false },
    ])

    // Another model with other levels arrives (level still on the list).
    rerender({ levels: ['auto', 'low', 'high'] })
    await flush()
    expect(reports().at(-1)!.body).toEqual({ level: 'high', levels: ['auto', 'low', 'high'], ultracode: false })
  })

  it('reports nothing before the registry answered or while the level is not on it', async () => {
    const { rerender } = page({ level: 'auto', levels: null })
    await flush()
    expect(reports()).toEqual([])
    rerender({ levels: ['low', 'high'] }) // level 'auto' is not offered: an inconsistent moment
    await flush()
    expect(reports()).toEqual([])
    rerender({ levels: ['auto', 'low'] })
    await flush()
    expect(reports().map(r => r.body)).toEqual([{ level: 'auto', levels: ['auto', 'low'], ultracode: false }])
  })

  it('repeats the report on a timer, so a backend that restarted learns it again', async () => {
    vi.useFakeTimers()
    page()
    await vi.advanceTimersByTimeAsync(0)
    expect(reports()).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(EFFORT_REPORT_EVERY_MS)
    expect(reports()).toHaveLength(2)
    await vi.advanceTimersByTimeAsync(EFFORT_REPORT_EVERY_MS)
    expect(reports()).toHaveLength(3)
  })

  it('survives a backend that refuses or is unreachable', async () => {
    mockedAxios.put.mockRejectedValue(new Error('offline'))
    const { result } = page()
    await flush()
    expect(result.current.level).toBe('auto')
    expect(showToast).not.toHaveBeenCalled()
  })

  it('does not report without a token', async () => {
    renderHook(() => useRemoteEffort({
      api: API, token: undefined, level: 'auto', levels: OPUS, ultracode: false, setLevel: vi.fn(), showToast,
    }))
    await flush()
    expect(reports()).toEqual([])
  })
})

describe('a phone asking for an effort', () => {
  it('applies a level the active model offers and names the phone', async () => {
    const { result } = page()
    await flush()
    await pushWake(requested('xhigh'))
    expect(result.current.level).toBe('xhigh')
    expect(showToast.mock.calls.map(c => c[0])).toEqual([
      cevir('effort.changedByPhone', { cihaz: 'iPhone', seviye: cevir('effort.label.xhigh') }),
    ])
    expect(showToast.mock.calls[0][1]).toBe('info')
    // The change reaches the backend through the normal report, which confirms the real value.
    expect(reports().at(-1)!.body).toEqual({ level: 'xhigh', levels: OPUS, ultracode: false })
  })

  it('ignores a level the active model does not offer', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const { result } = page({ level: 'high', levels: ['auto', 'low', 'high'] })
    await flush()
    const before = reports().length
    await pushWake(requested('max'))
    expect(result.current.level).toBe('high')
    expect(showToast).not.toHaveBeenCalled()
    expect(reports()).toHaveLength(before)
    expect(warn).toHaveBeenCalled()
    warn.mockRestore()
  })

  it('ignores every request while the registry has not answered', async () => {
    const { result } = page({ level: 'auto', levels: null })
    await flush()
    await pushWake(requested('high'))
    expect(result.current.level).toBe('auto')
    expect(showToast).not.toHaveBeenCalled()
  })

  it('says nothing when the level is already the one asked for', async () => {
    const { result } = page({ level: 'medium', levels: OPUS })
    await flush()
    await pushWake(requested('medium'))
    expect(result.current.level).toBe('medium')
    expect(showToast).not.toHaveBeenCalled()
  })

  it('a later request judges against the levels the page has by then', async () => {
    const { result, rerender } = page()
    await flush()
    rerender({ levels: ['auto', 'low'] })
    await flush()
    await pushWake(requested('high'))
    expect(result.current.level).toBe('auto')
    await pushWake(requested('low'))
    expect(result.current.level).toBe('low')
  })

  it('names an unnamed phone', async () => {
    page()
    await flush()
    await pushWake(requested('low', { by: 'phone:' }))
    expect(showToast.mock.calls[0][0]).toBe(
      cevir('effort.changedByPhone', { cihaz: cevir('chat.phoneUnnamed'), seviye: cevir('effort.label.low') }))
  })

  it('ignores a malformed frame', async () => {
    const { result } = page()
    await flush()
    for (const bad of [
      requested('high', { by: 'someone' }),
      requested('high', { by: 4 }),
      requested('turbo'),
      requested('HIGH'),
      requested(''),
      { type: 'remote_effort', by: 'phone:iPhone' },
      { type: 'remote_effort', level: 5, by: 'phone:iPhone' },
    ]) {
      await pushWake(bad)
    }
    expect(result.current.level).toBe('auto')
    expect(showToast).not.toHaveBeenCalled()
  })

  it('stops listening when the page goes away', async () => {
    const { result, unmount } = page()
    await flush()
    unmount()
    const seen = vi.fn()
    const off = onEffortRequest(seen)
    deliverEffortRequest({ level: 'low', by: 'phone:iPhone' })
    expect(seen).toHaveBeenCalledTimes(1)
    expect(result.current.level).toBe('auto')
    off()
  })
})

describe('remote_effort · parsing', () => {
  it('reads a good frame', () => {
    expect(parseEffortRequest(requested('max'))).toEqual({ level: 'max', by: 'phone:iPhone' })
  })

  it('takes every level the registry can return, `none` included', () => {
    for (const level of ['auto', 'off', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']) {
      expect(parseEffortRequest(requested(level))?.level, level).toBe(level)
    }
  })

  it('refuses everything else', () => {
    for (const bad of [null, undefined, 'remote_effort', 7, [], { type: 'chat_model_changed' },
      requested('None'), requested('turbo'), requested('high', { by: 'desktop' }), requested('high', { by: undefined })]) {
      expect(parseEffortRequest(bad), JSON.stringify(bad)).toBeNull()
    }
  })
})

describe('Ultracode and a level from a phone', () => {
  it('a level a phone asks for switches Ultracode off, as a click in the panel does', async () => {
    const { result } = page({ level: 'auto', levels: OPUS, ultracode: true })
    await flush()
    await pushWake(requested('high'))
    expect(result.current.level).toBe('high')
    expect(result.current.ultracode).toBe(false)
  })

  it('the level Ultracode sits over counts as chosen too: asking for it switches Ultracode off', async () => {
    const { result } = page({ level: 'high', levels: OPUS, ultracode: true })
    await flush()
    await pushWake(requested('high'))
    expect(result.current.level).toBe('high')
    expect(result.current.ultracode).toBe(false)
  })

  it('with Ultracode off, asking for the current level still changes nothing', async () => {
    const { result } = page({ level: 'high', levels: OPUS })
    await flush()
    await pushWake(requested('high'))
    expect(showToast).not.toHaveBeenCalled()
    expect(result.current.ultracode).toBe(false)
  })

  it('reports whether Ultracode is on, and again when it goes off', async () => {
    const { result } = page({ level: 'high', levels: OPUS, ultracode: true })
    await flush()
    expect(reports().at(-1)!.body).toEqual({ level: 'high', levels: OPUS, ultracode: true })
    await pushWake(requested('low'))
    expect(result.current.ultracode).toBe(false)
    expect(reports().at(-1)!.body).toEqual({ level: 'low', levels: OPUS, ultracode: false })
  })

  it('home.tsx gives the panel and the phone hook the same function, and the panel has no second copy', () => {
    const home = readFileSync(resolve(__dirname, '../renderer/pages/home.tsx'), 'utf8')
    expect(home).toMatch(/const chooseEffort = useCallback\(\(level: ThinkingLevel\) => \{\s*setThinkingLevel\(level\);\s*if \(isClaudeSub && ultracode\) setUltracode\(false\);/)
    expect(home).toMatch(/setThinkingLevel=\{chooseEffort\}/)
    expect(home).toMatch(/setLevel: chooseEffort/)
    const panel = readFileSync(resolve(__dirname, '../renderer/components/home/ControlPanel.tsx'), 'utf8')
    expect(panel).toMatch(/onClick=\{\(\) => setThinkingLevel\(id as ThinkingLevel\)\}/)
    expect(panel).not.toMatch(/setUltracode\?\.\(false\)/)
  })
})
