/**
 * Per-chat model (owner request): each chat keeps its own provider/model.
 * Opening a chat puts that chat's model on screen, read fresh from the backend
 * (another chat, window or the phone may have changed the selection since);
 * switching chats writes nothing; a pick lands on the chat on screen and on the
 * default for new chats.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React, { useEffect } from 'react'
import { render, renderHook, screen, cleanup, fireEvent, act } from '@testing-library/react'

vi.mock('axios', () => {
  const post = vi.fn()
  const get = vi.fn()
  const del = vi.fn()
  const put = vi.fn()
  return { default: { post, get, delete: del, put }, post, get }
})

import axios from 'axios'
import { useAIConfig } from '../renderer/hooks/home/useAIConfig'
import { ModelSelector } from '../renderer/components/home/ModelSelector'

const mockedAxios = axios as unknown as { post: ReturnType<typeof vi.fn>; get: ReturnType<typeof vi.fn> }
const API = 'http://127.0.0.1:8000'
const USER = { id: 1, name: 'b', sessionToken: 'tok' } as any

type Deferred = { resolve: (v: any) => void; promise: Promise<any> }
const deferred = (): Deferred => {
  let resolve!: (v: any) => void
  const promise = new Promise(res => { resolve = res })
  return { resolve, promise }
}

// Reads that decide what is on screen wait until a test answers them, so the
// order in which answers land can be chosen; everything else answers at once.
const HELD = ['/model', '/get-ai-config/', '/provider-ready/']
let held: Array<{ url: string; params: any; d: Deferred }>

beforeEach(() => {
  held = []
  mockedAxios.get.mockReset().mockImplementation((url: string, cfg?: any) => {
    const u = String(url)
    if (u.includes('/mcp/unity/status')) return Promise.resolve({ data: { status: 'off' } })
    if (HELD.some(h => u.includes(h))) {
      const d = deferred()
      held.push({ url: u, params: cfg?.params, d })
      return d.promise
    }
    return Promise.resolve({ data: {} })
  })
  mockedAxios.post.mockReset().mockResolvedValue({ data: { status: 'success' } })
})

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

const reads = (part: string) => held.filter(h => h.url.includes(part))

const answer = async (part: string, data: any, nth = 0) => {
  const h = reads(part)[nth]
  expect(h, `no read of ${part} #${nth}`).toBeTruthy()
  await act(async () => { h.d.resolve({ data }); await Promise.resolve() })
}

const model = (provider_type: string, model_name: string) => ({ provider_type, model_name, has_key: false })

// Mirrors home.tsx: the chat on screen decides the model shown.
const withScreen = (convId: number | null) => renderHook(({ convId }) => {
  const ai = useAIConfig(API, USER, vi.fn())
  useEffect(() => { ai.showChatModel(USER.id, convId) }, [convId]) // eslint-disable-line react-hooks/exhaustive-deps
  return ai
}, { initialProps: { convId } })

describe('switching chats', () => {
  it("opening a chat shows that chat's model and writes nothing", async () => {
    const { result } = withScreen(7)
    await answer('/conversations/7/model', model('subscription', 'gpt-6-luna'))
    expect(result.current.aiConfig.provider_type).toBe('subscription')
    expect(result.current.aiConfig.model_name).toBe('gpt-6-luna')
    expect(mockedAxios.post).not.toHaveBeenCalled()
  })

  it('coming back to a chat brings its model back, read fresh', async () => {
    const { result, rerender } = withScreen(7)
    await answer('/conversations/7/model', model('subscription', 'gpt-6-luna'))
    rerender({ convId: 8 })
    await answer('/conversations/8/model', model('subscription', 'claude-opus-5'))
    expect(result.current.aiConfig.model_name).toBe('claude-opus-5')

    // Meanwhile another window (or the phone) moved chat 7 to OpenCode.
    rerender({ convId: 7 })
    expect(reads('/conversations/7/model')).toHaveLength(2)
    await answer('/conversations/7/model', model('subscription', 'opencode:opencode/big-pickle'), 1)
    expect(result.current.aiConfig.model_name).toBe('opencode:opencode/big-pickle')
    expect(mockedAxios.post).not.toHaveBeenCalled()
  })

  it('a slow read for the chat just left cannot put its model on screen', async () => {
    const { result, rerender } = withScreen(7)
    rerender({ convId: 8 })
    await answer('/conversations/8/model', model('subscription', 'claude-opus-5'))
    await answer('/conversations/7/model', model('subscription', 'gpt-6-luna'))
    expect(result.current.aiConfig.model_name).toBe('claude-opus-5')
  })

  it('a pick made while the chat is still loading is not overwritten by the load', async () => {
    const { result } = withScreen(7)
    act(() => { result.current.setAiConfig({ provider_type: 'subscription', model_name: 'kimi-k3', api_key: '' } as any) })
    await answer('/conversations/7/model', model('subscription', 'gpt-6-luna'))
    expect(result.current.aiConfig.model_name).toBe('kimi-k3')
  })

  it('with no chat on screen it shows the default for a new chat', async () => {
    const { result } = withScreen(null)
    expect(reads('/get-ai-config/1')).toHaveLength(1)
    expect(reads('/conversations/')).toHaveLength(0)
    await answer('/get-ai-config/1', model('openai', 'gpt-5.5'))
    expect(result.current.aiConfig.model_name).toBe('gpt-5.5')
  })
})

describe('saving from Settings', () => {
  const pickOllama = (result: any) => act(() => {
    result.current.setAiConfig({ provider_type: 'ollama', model_name: 'qwen2.5-coder:7b', api_key: '' } as any)
  })
  const saved = () => mockedAxios.post.mock.calls.filter(c => String(c[0]).endsWith('/save-ai-config'))

  it('names the chat on screen', async () => {
    const { result } = withScreen(7)
    pickOllama(result)
    await act(async () => { await result.current.saveAIConfig() })
    expect(saved()).toHaveLength(1)
    expect(saved()[0][1]).toMatchObject({ provider_type: 'ollama', model_name: 'qwen2.5-coder:7b', conversation_id: 7 })
  })

  it('with no chat on screen sets only the default', async () => {
    const { result } = withScreen(null)
    pickOllama(result)
    await act(async () => { await result.current.saveAIConfig() })
    expect(saved()).toHaveLength(1)
    expect(saved()[0][1]).not.toHaveProperty('conversation_id')
  })
})

describe('provider gate', () => {
  it('asks about the pair on screen, and a stale answer does not win', async () => {
    const { result, rerender } = withScreen(7)
    await answer('/conversations/7/model', model('openai', 'gpt-5.5'))
    act(() => { void result.current.fetchProviderReady(1) })
    expect(reads('/provider-ready/1')[0].params).toMatchObject({ provider_type: 'openai', model_name: 'gpt-5.5' })

    rerender({ convId: 8 })
    await answer('/conversations/8/model', model('subscription', 'claude-opus-5'))
    act(() => { void result.current.fetchProviderReady(1) })
    expect(reads('/provider-ready/1')[1].params).toMatchObject({ provider_type: 'subscription', model_name: 'claude-opus-5' })

    await answer('/provider-ready/1', { ready: true, kind: 'cli', provider: 'claude', needs: null }, 1)
    await answer('/provider-ready/1', { ready: false, kind: 'api', provider: 'openai', needs: 'apikey' }, 0)
    expect(result.current.providerReady).toMatchObject({ ready: true, provider: 'claude' })
  })
})

describe('model selector pick', () => {
  const props = (conversationId: number | null, post: any, showToast = vi.fn()) => ({
    aiConfig: { provider_type: 'subscription', model_name: 'claude-opus-5', api_key: '' },
    setAiConfig: vi.fn(),
    availableModels: { local: [], cloud: [], subscription: [
      { id: 'claude-opus-5', name: 'Claude Opus 5 (CLI)', provider: 'subscription' },
      { id: 'claude-sonnet-5', name: 'Claude Sonnet 5 (CLI)', provider: 'subscription' },
    ] },
    providersWithKeys: [], effectiveProvider: 'subscription', displayModelName: 'secili',
    isModelDropdownOpen: true, setIsModelDropdownOpen: vi.fn(), modelOrToggles: {},
    setModelOrToggles: vi.fn(), user: USER, fetchAvailableModels: vi.fn(), setShowSettings: vi.fn(),
    API, axios: { get: async () => ({ data: {} }), post }, showToast, conversationId,
  })

  it('sets the model of the chat on screen', async () => {
    const post = vi.fn(async () => ({ data: {} }))
    render(<ModelSelector {...(props(7, post) as any)} />)
    await act(async () => { fireEvent.click(screen.getByText('Claude Sonnet 5 (CLI)')) })
    expect(post).toHaveBeenCalledTimes(1)
    expect((post.mock.calls[0] as any[])[1]).toMatchObject({
      provider_type: 'subscription', model_name: 'claude-sonnet-5', conversation_id: 7, user_id: 1 })
  })

  it('with no chat on screen sets only the default', async () => {
    const post = vi.fn(async () => ({ data: {} }))
    render(<ModelSelector {...(props(null, post) as any)} />)
    await act(async () => { fireEvent.click(screen.getByText('Claude Sonnet 5 (CLI)')) })
    expect((post.mock.calls[0] as any[])[1]).not.toHaveProperty('conversation_id')
  })

  it('a refused save is said, not left as an unhandled rejection', async () => {
    const post = vi.fn(async () => { throw Object.assign(new Error('x'), { response: { status: 404, data: { detail: 'unknown_chat' } } }) })
    const toast = vi.fn()
    render(<ModelSelector {...(props(7, post, toast) as any)} />)
    await act(async () => { fireEvent.click(screen.getByText('Claude Sonnet 5 (CLI)')) })
    expect(toast).toHaveBeenCalledTimes(1)
    expect(toast.mock.calls[0][1]).toBe('error')
  })
})
