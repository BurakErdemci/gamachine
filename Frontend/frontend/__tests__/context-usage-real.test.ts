import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook } from '@testing-library/react'
import axios from 'axios'

vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }))

import { useChat } from '../renderer/hooks/home/useChat'

const reading = {
  percent: 56, should_compact: false, message_count: 2, estimated: false,
  real: { used: '112.4k', total: '200k', model: 'claude-test' },
  last_turn: { input_tokens: 100, output_tokens: 10, cost_usd: null },
}
const hook = () => renderHook(() => useChat(
  'http://x', { id: 1, name: 'b', sessionToken: 'tok' } as any,
  { provider_type: 'subscription' } as any, null, vi.fn(), vi.fn(), (name: string) => name,
))

beforeEach(() => {
  vi.mocked(axios.get).mockReset().mockImplementation(async (url) => ({
    data: String(url).endsWith('/context-usage') ? reading : [],
  }))
  vi.mocked(axios.post).mockReset().mockResolvedValue({ data: {} })
})

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('real context usage reaches the composer', () => {
  it('keeps real tokens and estimated:false from the GET endpoint', async () => {
    const { result } = hook()
    await act(async () => { await result.current.selectConversation({ id: 7 } as any) })
    expect(result.current.contextUsage).toEqual(reading)
  })

  it.each([reading, {
    percent: 1, should_compact: false, message_count: 1, estimated: true,
  }])('carries SSE readings without keeping a previous real block', async (event) => {
    const { result } = hook()
    await act(async () => { await result.current.selectConversation({ id: 7 } as any) })
    const bytes = new TextEncoder().encode(`data: ${JSON.stringify({ type: 'context_usage', ...event })}\n\n`)
    const read = vi.fn()
      .mockResolvedValueOnce({ done: false, value: bytes })
      .mockResolvedValue({ done: true })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true, body: { getReader: () => ({ read }) },
    }))
    await act(async () => {
      await result.current.sendMessage('hello', '', 'tr', 'auto', 'medium', vi.fn(), vi.fn())
    })
    expect(result.current.contextUsage).toEqual(event)
  })
})
