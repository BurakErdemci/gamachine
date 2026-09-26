// Codex tabverify (26 Sep 2026): a sidebar title editor whose chat was
// removed by a list refresh kept refusing every sidebar click.
import { describe, it, expect, vi } from 'vitest'
import { renderHook, act } from '@testing-library/react'

vi.mock('axios', () => ({ default: { get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() } }))

import axios from 'axios'
import { useChat } from '../renderer/hooks/home/useChat'


const api = 'http://127.0.0.1:8000'
const user = { id: 1, name: 'test', sessionToken: 'test' } as any
const config = { provider_type: 'subscription', model_name: 'claude-opus-5' } as any
const conv = (id: number) => ({ id, title: `chat-${id}`, parent_id: null, hidden: false, created_at: '', updated_at: '' })

describe('sidebar editor whose chat vanished on refresh', () => {
  it('allows selection after the edited row disappears', async () => {
    let list = [conv(1), conv(2)]
    vi.mocked(axios.get).mockReset().mockImplementation(async (url: string) => {
      if (String(url).endsWith('/conversations/1')) return { data: list.map(c => ({ ...c })) }
      if (String(url).endsWith('/approval-mode')) return { data: { mode: 'step', stored: true } }
      return { data: [] }
    })
    const { result } = renderHook(() => useChat(api, user, config, '/ws', vi.fn(), vi.fn(), (s: string) => s))
    await act(async () => { await result.current.fetchConversations(1) })
    await act(async () => { await result.current.selectConversation(result.current.conversations[0]) })
    act(() => { result.current.setEditingId(1) })
    list = [conv(2)]
    await act(async () => { await result.current.fetchConversations(1) })
    expect(result.current.conversations.map(c => c.id)).toEqual([2])
    await act(async () => { await result.current.selectConversation(result.current.conversations[0]) })
    expect(result.current.activeConvId).toBe(2)
    expect(result.current.editingId).toBeNull()
  })
})
