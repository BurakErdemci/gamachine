/**
 * A chat model a phone switched shows on the desktop by itself
 * (docs/remote-control.md). The backend stores the chat's model and publishes
 * `chat_model_changed` on `/wake-stream-all`; the renderer re-reads the model
 * of that chat when it is the one on screen. A chat that is not on screen needs
 * nothing: the page reads a chat's model fresh whenever it comes on screen.
 *
 * Harness: the real `useChat` and `useAIConfig` with hand-fed streams, wired as
 * home.tsx wires them.
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
import { useAIConfig } from '../renderer/hooks/home/useAIConfig'
import { parseChatModelChanged } from '../renderer/lib/remoteControl'

const mockedAxios = axios as unknown as { post: ReturnType<typeof vi.fn>; get: ReturnType<typeof vi.fn> }

const API = 'http://127.0.0.1:8000'
const USER = { id: 1, name: 'b', sessionToken: 'tok' } as any

const enc = (ev: object) => new TextEncoder().encode(`data: ${JSON.stringify(ev)}\n\n`)

const makeStream = () => {
  const queue: Array<{ done: boolean; value?: Uint8Array }> = []
  let waiter: ((v: any) => void) | null = null
  const deliver = (item: { done: boolean; value?: Uint8Array }) => {
    if (waiter) { const w = waiter; waiter = null; w(item) } else queue.push(item)
  }
  return {
    push: (ev: object) => deliver({ done: false, value: enc(ev) }),
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

let wakes: Array<ReturnType<typeof makeStream>>
let chatModels: Record<number, { provider_type: string; model_name: string }>

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

// As home.tsx: the chat on screen decides the model shown, and a phone's
// switch of that chat re-reads it.
const hook = () => renderHook(() => {
  const ai = useAIConfig(API, USER, showToast)
  const chat = useChat(API, USER, ai.aiConfig, '/ws', showToast, vi.fn(), (n: string) => n,
    convId => { void ai.showChatModel(USER.id, convId) })
  return { ai, chat }
})
type Api = ReturnType<typeof hook>['result']

const flush = async () => { await act(async () => { for (let i = 0; i < 20; i++) await Promise.resolve() }) }
const open = async (result: Api, id: number) => {
  await act(async () => { await result.current.chat.selectConversation({ id } as any) })
  await act(async () => { await result.current.ai.showChatModel(USER.id, id) })
}
const changed = (conversationId: number, provider = 'openai', model = 'gpt-5.5', extra: Record<string, unknown> = {}) => ({
  type: 'chat_model_changed', conversation_id: conversationId, provider_type: provider, model_name: model,
  by: 'phone:iPhone', at: 1, ...extra,
})
const pushWake = async (frame: object) => { wakes.forEach(w => w.push(frame)); await flush() }
const modelReads = (id: number) =>
  mockedAxios.get.mock.calls.filter(c => String(c[0]).endsWith(`/conversations/${id}/model`)).length

beforeEach(() => {
  installFetch()
  showToast.mockReset()
  ipcInvoke.mockReset()
  chatModels = {
    7: { provider_type: 'subscription', model_name: 'gpt-6-luna' },
    8: { provider_type: 'subscription', model_name: 'claude-opus-5' },
  }
  mockedAxios.post.mockReset().mockResolvedValue({ data: {} })
  mockedAxios.get.mockReset().mockImplementation(async (url: string) => {
    const u = String(url)
    const m = u.match(/\/conversations\/(\d+)\/model$/)
    if (m) return { data: { ...chatModels[Number(m[1])], has_key: false } }
    if (u.endsWith('/approval-mode')) return { data: { mode: 'step', stored: true } }
    if (u.includes('/context-usage')) return { data: { percent: 1, should_compact: false, message_count: 0 } }
    if (u.includes('/mcp/unity/status')) return { data: { status: 'off' } }
    return { data: [] }
  })
})

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('chat_model_changed · the model on screen', () => {
  it('re-reads and shows the new model when the chat is the one on screen', async () => {
    const { result } = hook()
    await flush()
    await open(result, 7)
    expect(result.current.ai.aiConfig.model_name).toBe('gpt-6-luna')
    expect(modelReads(7)).toBe(1)

    chatModels[7] = { provider_type: 'openai', model_name: 'gpt-5.5' }
    await pushWake(changed(7))

    expect(modelReads(7)).toBe(2)
    expect(result.current.ai.aiConfig.provider_type).toBe('openai')
    expect(result.current.ai.aiConfig.model_name).toBe('gpt-5.5')
    // Showing a chat never writes: the phone's switch is not saved back.
    expect(mockedAxios.post).not.toHaveBeenCalled()
  })

  it('leaves the screen alone for a chat that is not on screen, and shows it fresh later', async () => {
    const { result } = hook()
    await flush()
    await open(result, 7)
    chatModels[8] = { provider_type: 'openai', model_name: 'gpt-5.5' }

    await pushWake(changed(8))

    expect(modelReads(8)).toBe(0)
    expect(modelReads(7)).toBe(1)
    expect(result.current.ai.aiConfig.model_name).toBe('gpt-6-luna')

    await open(result, 8)
    expect(modelReads(8)).toBe(1)
    expect(result.current.ai.aiConfig.model_name).toBe('gpt-5.5')
  })

  it('ignores a malformed frame', async () => {
    const { result } = hook()
    await flush()
    await open(result, 7)
    for (const bad of [
      changed(7, 'openai', 'gpt-5.5', { by: 'someone' }),
      changed(7, 'openai', 'gpt-5.5', { by: 4 }),
      changed(7, '', 'gpt-5.5'),
      changed(7, 'openai', 'gpt-5.5', { model_name: 5 }),
      changed(7, 'openai', 'gpt-5.5', { conversation_id: '7' }),
      changed(7, 'openai', 'gpt-5.5', { conversation_id: 0 }),
      { type: 'chat_model_changed' },
    ]) {
      await pushWake(bad)
    }
    expect(modelReads(7)).toBe(1)
  })
})

describe('chat_model_changed · parsing', () => {
  it('reads a good frame', () => {
    expect(parseChatModelChanged(changed(7, 'subscription', ''))).toEqual({
      conversationId: 7, providerType: 'subscription', modelName: '', by: 'phone:iPhone',
    })
  })

  it('refuses everything else', () => {
    for (const bad of [null, undefined, 'chat_model_changed', 7, [], { type: 'approval_mode_changed' },
      changed(7, 'openai', 'gpt-5.5', { conversation_id: -1 }),
      changed(7, 'openai', 'gpt-5.5', { conversation_id: 1.5 }),
      changed(7, 'openai', 'gpt-5.5', { by: 'desktop' })]) {
      expect(parseChatModelChanged(bad), JSON.stringify(bad)).toBeNull()
    }
  })
})
