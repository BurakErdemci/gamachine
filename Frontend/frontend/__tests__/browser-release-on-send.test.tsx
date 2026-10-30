/**
 * A user message in a chat hands that chat's browser tab back to the agent: sendMessage asks the
 * main process for a `release` of the target chat before the turn starts. A wake turn does not.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, cleanup } from '@testing-library/react'

vi.mock('axios', () => {
  const post = vi.fn()
  const get = vi.fn()
  return { default: { post, get, delete: vi.fn(), put: vi.fn() }, post, get }
})

import axios from 'axios'
import { useChat } from '../renderer/hooks/home/useChat'

const mockedAxios = axios as unknown as { post: ReturnType<typeof vi.fn>; get: ReturnType<typeof vi.fn> }
const API = 'http://127.0.0.1:8000'

const sseDone = () => ({
  ok: true,
  body: {
    getReader: () => {
      let given = false
      return {
        read: async () => {
          if (given) return { done: true, value: undefined }
          given = true
          return { done: false, value: new TextEncoder().encode(`data: ${JSON.stringify({ type: 'done', stop_reason: 'complete' })}\n\n`) }
        },
      }
    },
  },
})

const hook = () => renderHook(() => useChat(
  API,
  { id: 1, name: 'b', sessionToken: 'tok' } as any,
  { provider_type: 'subscription', model_name: 'claude-opus-5' } as any,
  '/ws',
  vi.fn(),
  vi.fn(),
  (n: string) => n,
))

let ipcInvoke: ReturnType<typeof vi.fn>
const releases = () => ipcInvoke.mock.calls.filter(c => c[0] === 'browser-live' && c[1]?.op === 'release').map(c => c[1])

beforeEach(() => {
  mockedAxios.post.mockReset().mockResolvedValue({ data: { id: 5 } })
  mockedAxios.get.mockReset().mockResolvedValue({ data: [] })
  ipcInvoke = vi.fn().mockResolvedValue({ ok: true })
  vi.stubGlobal('ipc', { invoke: ipcInvoke, on: vi.fn(() => () => {}) })
  vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) =>
    String(url).includes('/wake-stream') ? new Promise(() => {}) : Promise.resolve(sseDone())))
})

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('browser tab release on a user send', () => {
  it('a user send from a fresh chat releases the tab of the chat it created', async () => {
    const { result } = hook()
    await act(async () => {
      await result.current.sendMessage('selam', '', 'tr', 'auto', 'medium', vi.fn(), vi.fn())
    })
    expect(releases()).toEqual([{ op: 'release', conversationId: 5 }])
  })

  it('a wake turn does not release the tab', async () => {
    const { result } = hook()
    await act(async () => {
      await result.current.sendMessage('devam', '', 'tr', 'auto', 'medium', vi.fn(), vi.fn(), undefined, false, undefined, 'wake')
    })
    expect(releases()).toHaveLength(0)
  })

  it('a throwing release does not abort the send', async () => {
    ipcInvoke.mockImplementation(() => { throw new Error('bridge down') })
    const { result } = hook()
    await act(async () => {
      await result.current.sendMessage('selam', '', 'tr', 'auto', 'medium', vi.fn(), vi.fn())
    })
    expect(result.current.messages.some((m: any) => m.role === 'user')).toBe(true)
    expect(mockedAxios.post.mock.calls.length + (fetch as any).mock.calls.length).toBeGreaterThan(0)
  })

  it('a send without window.ipc still works', async () => {
    vi.stubGlobal('ipc', undefined)
    const { result } = hook()
    await act(async () => {
      await result.current.sendMessage('selam', '', 'tr', 'auto', 'medium', vi.fn(), vi.fn())
    })
    expect(result.current.messages.some((m: any) => m.role === 'user')).toBe(true)
  })
})
