// Parallel chats, slice 1 audit (Codex, 26 Sep 2026). The first three tests
// are the audit's probes, kept as written; each failed at 0d161be.
//   shared-card-slot-overwrite: a later chat's file card replaced an earlier
//     chat's unanswered one in useFileSystem's single slot.
//   client-only-state-lost-on-refetch: a turn that finished on screen with an
//     error bubble was refetched away when the chat was reopened.
//   stale-async-selection: a slow new-chat request stole a later selection.
import { describe, it, expect, vi, afterEach } from 'vitest'
import { useState } from 'react'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'

vi.mock('axios', () => {
  const post = vi.fn(); const get = vi.fn(); const del = vi.fn(); const put = vi.fn()
  return { default: { post, get, delete: del, put }, post, get }
})

import axios from 'axios'
import { useChat } from '../renderer/hooks/home/useChat'
import { useFileSystem } from '../renderer/hooks/home/useFileSystem'

const API = 'http://127.0.0.1:8000'
const USER = { id: 1, name: 'b', sessionToken: 'tok' } as any
const TEXT_CONFIG = { provider_type: 'openai', model_name: 'gpt-4' } as any
const SUB_CONFIG = { provider_type: 'subscription', model_name: 'claude-opus-5' } as any
const mockedAxios = axios as unknown as { post: ReturnType<typeof vi.fn>; get: ReturnType<typeof vi.fn> }

const makeStream = () => {
  const queue: any[] = []
  let waiter: ((value: any) => void) | null = null
  const deliver = (item: any) => {
    if (waiter) { const wake = waiter; waiter = null; wake(item) } else queue.push(item)
  }
  return {
    push: (event: object) => deliver({ done: false, value: new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`) }),
    close: () => deliver({ done: true }),
    response: { body: { getReader: () => ({ read: () => queue.length
      ? Promise.resolve(queue.shift()) : new Promise(resolve => { waiter = resolve }) }) } },
  }
}

const serveEmptyChats = () => {
  mockedAxios.get.mockReset().mockImplementation(async (url: string) => {
    if (String(url).endsWith('/messages')) return { data: [] }
    if (String(url).includes('/context-usage')) return { data: { percent: 0, message_count: 0 } }
    return { data: [] }
  })
  mockedAxios.post.mockReset().mockResolvedValue({ data: {} })
}

const installStreams = () => {
  const streams: Record<number, ReturnType<typeof makeStream>> = {}
  vi.stubGlobal('fetch', vi.fn((url: string, init?: any) => {
    if (String(url).endsWith('/chat-stream')) {
      const cid = JSON.parse(init.body).conversation_id
      return Promise.resolve((streams[cid] = makeStream()).response)
    }
    return new Promise(() => {})
  }))
  return streams
}

const flush = async () => { await act(async () => { for (let i = 0; i < 10; i++) await Promise.resolve() }) }

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('file card ownership across chats', () => {
  it('retains the first chat generated-file card after another chat receives one', async () => {
    serveEmptyChats()
    const streams = installStreams()

    const { result } = renderHook(() => {
      const [pendingGenFiles, setPendingGenFiles] = useState<{ files: any[]; messageId: number } | null>(null)
      const chat = useChat(API, USER, TEXT_CONFIG, '/ws', vi.fn(), vi.fn(), (name: string) => name)
      return { chat, pendingGenFiles, setPendingGenFiles }
    })
    const send = (content: string) => {
      act(() => { void result.current.chat.sendMessage(content, '', 'tr', 'step', 'medium', result.current.setPendingGenFiles, vi.fn()) })
    }

    await act(async () => { await result.current.chat.selectConversation({ id: 1 } as any) })
    send('first chat')
    await flush()
    streams[1].push({ type: 'response', content: '```csharp\npublic class First {}\n```', conversation_id: 1 })
    await waitFor(() => expect(result.current.pendingGenFiles?.files[0].name).toBe('First.cs'))

    await act(async () => { await result.current.chat.selectConversation({ id: 2 } as any) })
    send('second chat')
    await flush()
    streams[2].push({ type: 'response', content: '```csharp\npublic class Second {}\n```', conversation_id: 2 })
    await waitFor(() => expect(result.current.pendingGenFiles?.files[0].name).toBe('Second.cs'))

    await act(async () => { await result.current.chat.selectConversation({ id: 1 } as any) })
    expect(result.current.pendingGenFiles?.files[0].name).toBe('First.cs')
  })

  it('an answered generated-file card is not brought back when its chat is reopened', async () => {
    serveEmptyChats()
    const streams = installStreams()
    const { result } = renderHook(() => {
      const [pendingGenFiles, setPendingGenFiles] = useState<{ files: any[]; messageId: number } | null>(null)
      const chat = useChat(API, USER, TEXT_CONFIG, '/ws', vi.fn(), vi.fn(), (name: string) => name)
      return { chat, pendingGenFiles, setPendingGenFiles }
    })

    await act(async () => { await result.current.chat.selectConversation({ id: 1 } as any) })
    act(() => { void result.current.chat.sendMessage('first chat', '', 'tr', 'step', 'medium', result.current.setPendingGenFiles, vi.fn()) })
    await flush()
    streams[1].push({ type: 'response', content: '```csharp\npublic class First {}\n```', conversation_id: 1 })
    await waitFor(() => expect(result.current.pendingGenFiles?.files[0].name).toBe('First.cs'))
    streams[1].close()
    await waitFor(() => expect(result.current.chat.loading).toBe(false))
    act(() => { result.current.setPendingGenFiles(null) })   // the user answered it

    await act(async () => { await result.current.chat.selectConversation({ id: 2 } as any) })
    expect(result.current.chat.convStatus[1]).toBeUndefined()
    await act(async () => { await result.current.chat.selectConversation({ id: 1 } as any) })
    expect(result.current.pendingGenFiles).toBeNull()
  })

  it("a delete card neither hides the next chat's card nor is lost by switching", async () => {
    serveEmptyChats()
    const streams = installStreams()
    const { result } = renderHook(() => {
      const fs = useFileSystem(API, null, vi.fn())
      const chat = useChat(API, USER, SUB_CONFIG, '/ws', vi.fn(), vi.fn(), (name: string) => name)
      return { chat, fs }
    })
    const send = (content: string) => {
      act(() => { void result.current.chat.sendMessage(content, '', 'tr', 'step', 'medium', vi.fn(), result.current.fs.setPendingDelete) })
    }

    await act(async () => { await result.current.chat.selectConversation({ id: 1 } as any) })
    send('first chat')
    await flush()
    streams[1].push({ type: 'pending_delete', path: 'Assets/A.cs', conversation_id: 1 })
    await waitFor(() => expect(result.current.fs.pendingDelete?.path).toBe('Assets/A.cs'))

    await act(async () => { await result.current.chat.selectConversation({ id: 2 } as any) })
    expect(result.current.fs.pendingDelete).toBeNull()
    expect(result.current.chat.convStatus[1]).toBe('awaiting')
    send('second chat')
    await flush()
    streams[2].push({ type: 'pending_delete', path: 'Assets/B.cs', conversation_id: 2 })
    // Used to queue behind A's card, which chat 2 does not draw.
    await waitFor(() => expect(result.current.fs.pendingDelete?.path).toBe('Assets/B.cs'))

    await act(async () => { await result.current.chat.selectConversation({ id: 1 } as any) })
    expect(result.current.fs.pendingDelete?.path).toBe('Assets/A.cs')
    expect(result.current.chat.convStatus[2]).toBe('awaiting')
    act(() => { result.current.fs.setPendingDelete(null) })
    expect(result.current.fs.pendingDelete).toBeNull()

    await act(async () => { await result.current.chat.selectConversation({ id: 2 } as any) })
    expect(result.current.fs.pendingDelete?.path).toBe('Assets/B.cs')
  })
})

describe('client-only error after switching chats', () => {
  it('keeps an error bubble when an errored chat is reopened', async () => {
    serveEmptyChats()
    const streams = installStreams()

    const { result } = renderHook(() => useChat(API, USER, SUB_CONFIG, '/ws', vi.fn(), vi.fn(), (name: string) => name))
    await act(async () => { await result.current.selectConversation({ id: 1 } as any) })
    act(() => { void result.current.sendMessage('question', '', 'tr', 'step', 'medium', vi.fn(), vi.fn()) })
    await waitFor(() => expect(streams[1]).toBeDefined())
    streams[1].push({ type: 'error', message: 'provider failed', conversation_id: 1 })
    streams[1].close()
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.messages.some((m: any) => String(m.content).includes('provider failed'))).toBe(true)

    await act(async () => { await result.current.selectConversation({ id: 2 } as any) })
    await act(async () => { await result.current.selectConversation({ id: 1 } as any) })
    expect(result.current.messages.some((m: any) => String(m.content).includes('provider failed'))).toBe(true)
  })

  it('keeps it through a second round trip and a later clean turn', async () => {
    serveEmptyChats()
    const streams = installStreams()
    const { result } = renderHook(() => useChat(API, USER, SUB_CONFIG, '/ws', vi.fn(), vi.fn(), (name: string) => name))
    const hasError = () => result.current.messages.some((m: any) => String(m.content).includes('provider failed'))
    await act(async () => { await result.current.selectConversation({ id: 1 } as any) })
    act(() => { void result.current.sendMessage('question', '', 'tr', 'step', 'medium', vi.fn(), vi.fn()) })
    await waitFor(() => expect(streams[1]).toBeDefined())
    streams[1].push({ type: 'error', message: 'provider failed' })
    streams[1].close()
    await waitFor(() => expect(result.current.loading).toBe(false))

    for (let i = 0; i < 2; i++) {
      await act(async () => { await result.current.selectConversation({ id: 2 } as any) })
      await act(async () => { await result.current.selectConversation({ id: 1 } as any) })
    }
    expect(hasError()).toBe(true)

    act(() => { void result.current.sendMessage('again', '', 'tr', 'step', 'medium', vi.fn(), vi.fn()) })
    await flush()
    streams[1].push({ type: 'response', content: 'fine now' })
    streams[1].push({ type: 'done', stop_reason: 'complete' })
    streams[1].close()
    await waitFor(() => expect(result.current.loading).toBe(false))
    await act(async () => { await result.current.selectConversation({ id: 2 } as any) })
    await act(async () => { await result.current.selectConversation({ id: 1 } as any) })
    expect(hasError()).toBe(true)
  })
})

describe('new-chat selection race', () => {
  it('keeps the later user-selected chat on screen while another chat runs', async () => {
    let releaseCreate: (value: any) => void = () => {}
    mockedAxios.post.mockReset().mockImplementation((url: string) => {
      if (String(url).endsWith('/conversations')) return new Promise(resolve => { releaseCreate = resolve })
      return Promise.resolve({ data: {} })
    })
    mockedAxios.get.mockReset().mockImplementation(async (url: string) => {
      if (String(url).endsWith('/messages')) return { data: [] }
      if (String(url).includes('/context-usage')) return { data: { percent: 0, message_count: 0 } }
      return { data: [] }
    })
    let aSignal: AbortSignal | undefined
    vi.stubGlobal('fetch', vi.fn((url: string, init?: any) => {
      if (String(url).endsWith('/chat-stream')) {
        aSignal = init.signal
        return Promise.resolve({ body: { getReader: () => ({ read: () => new Promise(() => {}) }) } })
      }
      return new Promise(() => {})
    }))

    const { result } = renderHook(() => useChat(API, USER, SUB_CONFIG, '/ws', vi.fn(), vi.fn(), (name: string) => name))
    await act(async () => { await result.current.selectConversation({ id: 1 } as any) })
    act(() => { void result.current.sendMessage('A', '', 'tr', 'step', 'medium', vi.fn(), vi.fn()) })
    await waitFor(() => expect(aSignal).toBeDefined())

    let creating: Promise<number | null> | undefined
    act(() => { creating = result.current.createNewConversation() })
    await act(async () => { await result.current.selectConversation({ id: 2 } as any) })
    expect(result.current.activeConvId).toBe(2)

    await act(async () => { releaseCreate({ data: { id: 3 } }); await creating })
    expect(aSignal!.aborted).toBe(false)
    expect(result.current.activeConvId).toBe(2)
  })

  it('a first send from the empty screen targets the new chat; the screen follows only if left alone', async () => {
    const releases: Array<(value: any) => void> = []
    mockedAxios.post.mockReset().mockImplementation((url: string) => {
      if (String(url).endsWith('/conversations')) return new Promise(resolve => { releases.push(resolve) })
      return Promise.resolve({ data: {} })
    })
    mockedAxios.get.mockReset().mockImplementation(async (url: string) => {
      if (String(url).endsWith('/messages')) return { data: [] }
      if (String(url).includes('/context-usage')) return { data: { percent: 0, message_count: 0 } }
      return { data: [] }
    })
    const targets: number[] = []
    vi.stubGlobal('fetch', vi.fn((url: string, init?: any) => {
      if (String(url).endsWith('/chat-stream')) {
        targets.push(JSON.parse(init.body).conversation_id)
        return Promise.resolve({ body: { getReader: () => ({ read: () => new Promise(() => {}) }) } })
      }
      return new Promise(() => {})
    }))
    const { result } = renderHook(() => useChat(API, USER, SUB_CONFIG, '/ws', vi.fn(), vi.fn(), (name: string) => name))

    // Left alone: the screen follows the new chat.
    act(() => { void result.current.sendMessage('first', '', 'tr', 'step', 'medium', vi.fn(), vi.fn()) })
    await act(async () => { releases[0]({ data: { id: 5 } }); await Promise.resolve() })
    await waitFor(() => expect(targets).toEqual([5]))
    expect(result.current.activeConvId).toBe(5)

    // Another chat picked meanwhile: the send still goes to the new chat, off screen.
    await act(async () => { await result.current.selectConversation({ id: 9 } as any) })
    act(() => { result.current.setActiveConvId(null) })
    act(() => { void result.current.sendMessage('second', '', 'tr', 'step', 'medium', vi.fn(), vi.fn()) })
    await act(async () => { await result.current.selectConversation({ id: 2 } as any) })
    await act(async () => { releases[1]({ data: { id: 6 } }); await Promise.resolve() })
    await waitFor(() => expect(targets).toEqual([5, 6]))
    expect(result.current.activeConvId).toBe(2)
    expect(result.current.convStatus[6]).toBe('running')
  })
})
