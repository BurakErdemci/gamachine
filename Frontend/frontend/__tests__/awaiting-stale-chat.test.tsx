// Codex tabaudit (26 Sep 2026): a runtime outlived its chat (deleted from
// another window, dropped by a list refresh) and kept the awaiting badge up.
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'

vi.mock('axios', () => ({ default: { get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() } }))

import axios from 'axios'
import { useChat } from '../renderer/hooks/home/useChat'
import { awaitingElsewhere } from '../renderer/lib/convFamily'


const api = 'http://127.0.0.1:8000'
const user = { id: 1, name: 'test', sessionToken: 'test' } as any
const config = { provider_type: 'subscription', model_name: 'claude-opus-5' } as any
const conv = (id: number) => ({ id, title: `chat-${id}`, parent_id: null, hidden: false, created_at: '', updated_at: '' })
let list: ReturnType<typeof conv>[]

beforeEach(() => {
  list = [conv(1), conv(2)]
  vi.mocked(axios.get).mockReset().mockImplementation(async (url: string) => {
    if (String(url).endsWith('/conversations/1')) return { data: list.map(c => ({ ...c })) }
    if (String(url).endsWith('/approval-mode')) return { data: { mode: 'step', stored: true } }
    return { data: [] }
  })
})

describe('removed chat awaiting signal', () => {
  it('does not count a chat absent from the refreshed list', async () => {
    const { result } = renderHook(() => useChat(api, user, config, '/ws', vi.fn(), vi.fn(), (s: string) => s))
    await act(async () => { await result.current.fetchConversations(1) })
    act(() => { result.current.setBridgeGates({ 2: ['gate-2'] }) })
    expect(awaitingElsewhere(result.current.convStatus, 1, result.current.conversations)).toBe(1)
    list = [conv(1)]
    await act(async () => { await result.current.fetchConversations(1) })
    expect(result.current.conversations.map(c => c.id)).toEqual([1])
    expect(awaitingElsewhere(result.current.convStatus, 1, result.current.conversations)).toBe(0)
  })

  it('does not retain an own-chat question after that chat disappears from the list', async () => {
    const { result } = renderHook(() => useChat(api, user, config, '/ws', vi.fn(), vi.fn(), (s: string) => s))
    await act(async () => { await result.current.fetchConversations(1) })
    await act(async () => { await result.current.selectConversation(result.current.conversations[1]) })
    act(() => { result.current.setPendingQuestion({ gateId: 'question-2', questions: [], messageId: 20 } as any) })
    await act(async () => { await result.current.selectConversation(result.current.conversations[0]) })
    expect(awaitingElsewhere(result.current.convStatus, 1, result.current.conversations)).toBe(1)
    list = [conv(1)]
    await act(async () => { await result.current.fetchConversations(1) })
    expect(result.current.conversations.map(c => c.id)).toEqual([1])
    expect(awaitingElsewhere(result.current.convStatus, 1, result.current.conversations)).toBe(0)
  })
})
