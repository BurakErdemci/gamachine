import { afterEach, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook } from '@testing-library/react'

/**
 * Phone scope excludes commands: a phone-marked `/usage` stays a plain user
 * message, while the same text typed on the desktop still gets its card.
 */

vi.mock('axios', () => {
  const get = vi.fn(async (url: string) => String(url).includes('/context-usage')
    ? { data: { percent: 1, should_compact: false, message_count: 0 } }
    : { data: [] })
  return { default: { get, post: vi.fn(async () => ({ data: {} })), delete: vi.fn(), put: vi.fn() } }
})

import { useChat } from '../renderer/hooks/home/useChat'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

async function send(remote: { device: string } | undefined) {
  const turns: any[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: any) => {
    if (String(url).endsWith('/chat-stream')) turns.push(JSON.parse(init.body))
    return { ok: true, body: { getReader: () => ({ read: () => new Promise(() => {}) }) } }
  }))
  const { result } = renderHook(() => useChat('http://127.0.0.1:9',
    { id: 1, name: 'test', sessionToken: 'fake-token' } as any,
    { provider_type: 'subscription', model_name: 'claude-opus-5' } as any,
    '/workspace', vi.fn(), vi.fn(), (name: string) => name))
  await act(async () => { await result.current.selectConversation({ id: 7 } as any) })
  act(() => {
    void result.current.sendMessage('/usage', '', 'tr', 'auto', 'medium', vi.fn(), vi.fn(),
      undefined, false, undefined, 'user', 7, undefined, remote)
  })
  await act(async () => { for (let i = 0; i < 20; i++) await Promise.resolve() })
  return { turns, messages: result.current.messages }
}

it('a phone /usage is sent as text without a slash card', async () => {
  const { turns, messages } = await send({ device: 'iPhone' })
  expect(turns).toMatchObject([{ conversation_id: 7, message: '/usage', origin: 'user' }])
  expect(messages.find((m: any) => m.role === 'user')).toMatchObject({ source: 'phone' })
  expect(messages.find((m: any) => m.role === 'assistant')?.slashCommand).toBeUndefined()
})

it('the same text typed on the desktop keeps its usage card', async () => {
  const { messages } = await send(undefined)
  expect(messages.find((m: any) => m.role === 'assistant')?.slashCommand).toBe('usage')
})
