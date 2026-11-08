/**
 * After a successful compact the context gauge reads the backend again (audit
 * of 13fc6092, 8 Nov 2026): an in-place compact of a short chat answers with
 * no `summary`, and the gauge used to stay on its pre-compact value.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, renderHook, act } from '@testing-library/react'

vi.mock('@monaco-editor/react', () => ({
  __esModule: true,
  default: () => null,
  DiffEditor: () => null,
  Editor: () => null,
  loader: { config: () => {}, init: () => Promise.resolve({}) },
}))

vi.mock('axios', () => {
  const post = vi.fn()
  const get = vi.fn()
  const del = vi.fn()
  const put = vi.fn()
  return { default: { post, get, delete: del, put }, post, get }
})

import axios from 'axios'
import { useChat } from '../renderer/hooks/home/useChat'

const mocked = axios as unknown as {
  get: ReturnType<typeof vi.fn>; post: ReturnType<typeof vi.fn>
}
const API = 'http://127.0.0.1:8000'
const USER = { id: 1, name: 'b', sessionToken: 'tok' }
const CONV = 7
const USAGE_URL = `${API}/conversations/${CONV}/context-usage`
const AFTER = { message_count: 2, percent: 8, should_compact: false, estimated: false }

const hook = () => renderHook(() => useChat(
  API, USER as any, { provider_type: 'subscription', model_name: 'claude-opus-5' } as any,
  null, vi.fn(), vi.fn(), (n: string) => n,
))

function compactAnswers(data: object) {
  mocked.post.mockReset().mockImplementation((url: string) => (
    String(url).endsWith(`/conversations/${CONV}/compact`)
      ? Promise.resolve({ data })
      : Promise.resolve({ data: {} })
  ))
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn().mockImplementation(() => new Promise(() => {})))
  mocked.get.mockReset().mockImplementation((url: string) => (
    url === USAGE_URL ? Promise.resolve({ data: AFTER }) : Promise.resolve({ data: [] })
  ))
})

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('useChat · compact refreshes the gauge', () => {
  it('reads the gauge after a compact that returns no summary', async () => {
    compactAnswers({ status: 'success', message: 'kısa sohbet' })
    const { result } = hook()
    await act(async () => { await result.current.compactConversation(CONV) })
    expect(mocked.get).toHaveBeenCalledWith(USAGE_URL)
  })

  it('reads the gauge after a compact that returns a summary', async () => {
    compactAnswers({ status: 'success', summary: 'özet' })
    const { result } = hook()
    await act(async () => { await result.current.compactConversation(CONV) })
    expect(mocked.get).toHaveBeenCalledWith(USAGE_URL)
  })

  it('puts the fresh reading on the gauge of the chat on screen', async () => {
    compactAnswers({ status: 'success' })
    const { result } = hook()
    act(() => { result.current.setActiveConvId(CONV) })
    await act(async () => { await result.current.compactConversation(CONV) })
    expect(result.current.contextUsage).toEqual(AFTER)
  })

  it('still refreshes the gauge when reloading the messages fails', async () => {
    compactAnswers({ status: 'success', summary: 'özet' })
    mocked.get.mockImplementation((url: string) => (
      url === USAGE_URL ? Promise.resolve({ data: AFTER }) : Promise.reject(new Error('down'))
    ))
    const { result } = hook()
    act(() => { result.current.setActiveConvId(CONV) })
    await act(async () => { await result.current.compactConversation(CONV) })
    expect(result.current.contextUsage).toEqual(AFTER)
  })

  it('leaves the gauge alone when the compact fails', async () => {
    compactAnswers({ status: 'error' })
    const { result } = hook()
    await act(async () => { await result.current.compactConversation(CONV) })
    expect(mocked.get).not.toHaveBeenCalledWith(USAGE_URL)
  })
})
