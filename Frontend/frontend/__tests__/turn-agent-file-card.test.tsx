/**
 * The generated-file card is for API loops only (CLI agents write through the
 * approval gate). With a model per chat, a background chat's turn can run on
 * another model than the one on screen, so the turn is judged by the agent it
 * reports in `turn_meta`, not by the screen's selection.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { renderHook, cleanup, act } from '@testing-library/react'

vi.mock('axios', () => {
  const post = vi.fn()
  const get = vi.fn()
  const del = vi.fn()
  const put = vi.fn()
  return { default: { post, get, delete: del, put }, post, get }
})

import axios from 'axios'
import { useChat } from '../renderer/hooks/home/useChat'
// Loaded up front: useChat imports it lazily when a turn ends, and a cold
// import can outlast the flush below, which would make the negative case pass
// for the wrong reason.
import '../renderer/components/home/export-utils'

const mockedAxios = axios as unknown as { post: ReturnType<typeof vi.fn>; get: ReturnType<typeof vi.fn> }
const API = 'http://127.0.0.1:8000'
const USER = { id: 1, name: 'b', sessionToken: 'tok' } as any
const FILE = '**Player.cs**\n```csharp\npublic class Player {}\n```'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

const enc = (ev: object) => new TextEncoder().encode(`data: ${JSON.stringify(ev)}\n\n`)

const makeStream = () => {
  const queue: Array<{ done: boolean; value?: Uint8Array }> = []
  let waiter: ((v: any) => void) | null = null
  const deliver = (item: { done: boolean; value?: Uint8Array }) => {
    if (waiter) { const w = waiter; waiter = null; w(item) } else queue.push(item)
  }
  return {
    push: (ev: object) => deliver({ done: false, value: enc(ev) }),
    end: () => deliver({ done: true }),
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

const flush = async () => { await act(async () => { for (let i = 0; i < 20; i++) await Promise.resolve() }) }

const runTurn = async (screenConfig: any, agent: string) => {
  const stream = makeStream()
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    if (String(url).endsWith('/chat-stream')) return Promise.resolve(stream.response)
    if (String(url).includes('/wake-stream')) return new Promise(() => {})
    return Promise.resolve({ ok: true, status: 200, json: async () => ({ status: 'ok' }) })
  }))
  mockedAxios.post.mockReset().mockResolvedValue({ data: {} })
  mockedAxios.get.mockReset().mockImplementation(async (url: string) => {
    if (String(url).includes('/context-usage')) return { data: { percent: 1, should_compact: false, message_count: 0 } }
    return { data: [] }
  })
  const { result } = renderHook(() => useChat(API, USER, screenConfig, '/ws', vi.fn(), vi.fn(), (n: string) => `Assets/${n}`))
  await act(async () => { await result.current.selectConversation({ id: 1 } as any) })
  const setGen = vi.fn()
  act(() => { result.current.sendMessage('yaz', '', 'tr', 'auto', 'medium', setGen, vi.fn()) })
  await flush()
  stream.push({ type: 'turn_meta', provider: agent, model: 'm' })
  stream.push({ type: 'response', content: FILE })
  stream.push({ type: 'done', stop_reason: 'complete' })
  stream.end()
  await flush()
  return setGen
}

describe('generated-file card of a turn', () => {
  it('an API turn gets the card even when the screen shows a CLI model', async () => {
    const setGen = await runTurn({ provider_type: 'subscription', model_name: 'claude-opus-5' }, 'api-openai')
    await vi.waitFor(() => expect(setGen).toHaveBeenCalled())
    expect(setGen.mock.calls[0][0].files[0]).toMatchObject({ name: 'Player.cs', suggestedPath: 'Assets/Player.cs' })
  })

  it('a CLI turn gets no extra card even when the screen shows an API model', async () => {
    const setGen = await runTurn({ provider_type: 'openai', model_name: 'gpt-5.5' }, 'codex')
    expect(setGen).not.toHaveBeenCalled()
  })
})
