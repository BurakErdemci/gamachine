// Codex tabaudit (26 Sep 2026): deleting the branch on screen while a sidebar
// title was being edited left an empty screen; the root tab must open.
import { describe, it, expect, vi } from 'vitest'
import { renderHook, act } from '@testing-library/react'

vi.mock('axios', () => ({ default: { get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() } }))
vi.mock('../renderer/components/ui/ConfirmDialog', () => ({ confirmDialog: vi.fn(async () => true) }))

import axios from 'axios'
import { useChat } from '../renderer/hooks/home/useChat'


const api = 'http://127.0.0.1:8000'
const user = { id: 1, name: 'test', sessionToken: 'test' } as any
const config = { provider_type: 'subscription', model_name: 'claude-opus-5' } as any
const conv = (id: number, parent_id: number | null) => ({ id, parent_id, title: `chat-${id}`, hidden: false, created_at: '', updated_at: '' })

describe('delete active branch while sidebar rename is open', () => {
  it('hands the screen to the root tab', async () => {
    let list = [conv(1, null), conv(2, 1)]
    vi.mocked(axios.get).mockReset().mockImplementation(async (url: string) => {
      if (String(url).endsWith('/conversations/1')) return { data: list.map(c => ({ ...c })) }
      if (String(url).endsWith('/approval-mode')) return { data: { mode: 'step', stored: true } }
      return { data: [] }
    })
    vi.mocked(axios.delete).mockReset().mockImplementation(async () => {
      list = [conv(1, null)]
      return { data: { deleted_ids: [2] } }
    })
    const { result } = renderHook(() => useChat(api, user, config, '/ws', vi.fn(), vi.fn(), (s: string) => s))
    await act(async () => { await result.current.fetchConversations(1) })
    await act(async () => { await result.current.selectConversation(result.current.conversations[1]) })
    expect(result.current.activeConvId).toBe(2)
    act(() => { result.current.setEditingId(1) })
    await act(async () => { await result.current.deleteBranch(2) })
    expect(result.current.conversations.map(c => c.id)).toEqual([1])
    expect(result.current.activeConvId).toBe(1)
  })
})
