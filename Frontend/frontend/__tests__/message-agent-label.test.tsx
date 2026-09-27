/**
 * Each assistant message is labelled with the agent that wrote it (Burak,
 * 27 Sep 2026): the header used to show the chat's CURRENT model on every
 * answer, so a chat moved from OpenCode to Codex showed "gpt-6-luna" on the
 * OpenCode answers too.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, renderHook, act } from '@testing-library/react'

vi.mock('axios', () => {
  const post = vi.fn()
  const get = vi.fn()
  const del = vi.fn()
  const put = vi.fn()
  return { default: { post, get, delete: del, put }, post, get }
})

import axios from 'axios'
import { ChatPanel } from '../renderer/components/home/ChatPanel'
import { messageAgent } from '../renderer/components/home/messageAgent'
import { useChat } from '../renderer/hooks/home/useChat'

const mockedAxios = axios as unknown as { post: ReturnType<typeof vi.fn>; get: ReturnType<typeof vi.fn> }
const API = 'http://127.0.0.1:8000'
const USER = { id: 1, name: 'b', sessionToken: 'tok' } as any

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('messageAgent', () => {
  it.each([
    ['codex', 'gpt-6-luna', 'Codex', 'openai', 'gpt-6-luna'],
    ['opencode', 'opencode:opencode/big-pickle', 'OpenCode', 'opencode', 'big-pickle'],
    ['claude', 'claude-sonnet-5', 'Claude Code', 'claude', 'claude-sonnet-5'],
    ['agy', 'gemini-3.8-flash', 'Antigravity', 'gemini', 'gemini-3.8-flash'],
    ['copilot', 'copilot-gpt-5.5', 'GitHub Copilot', 'copilot', 'gpt-5.5'],
    ['cursor', 'cursor-auto', 'Cursor', 'cursor', 'auto'],
    ['kimi', 'kimi-k3', 'Kimi Code', 'moonshot', 'kimi-k3'],
    ['api-openai', 'gpt-5.5', 'OpenAI', 'openai', 'gpt-5.5'],
    ['api-ollama', 'qwen2.5-coder:7b', 'Ollama', 'ollama', 'qwen2.5-coder:7b'],
  ])('%s / %s -> %s', (provider, model, name, brand, short) => {
    expect(messageAgent(provider, model)).toEqual({ name, brand, model: short })
  })

  it('a message with no recorded agent has no label at all', () => {
    expect(messageAgent(null, null)).toBeNull()
    expect(messageAgent(undefined, 'gpt-6-luna')).toBeNull()
  })
})

const panel = (messages: any[], extra: Record<string, any> = {}) => render(<ChatPanel {...({
  messages, activeConvId: 7, user: USER, loading: false, clearHistory: vi.fn(), lang: 'tr',
  thinkingLevel: 'auto', workspacePath: '/ws', handleExportToUnity: vi.fn(),
  pendingGenFiles: null, setPendingGenFiles: vi.fn(), pendingFix: null, setPendingFix: vi.fn(),
  openedFilePath: null, setCode: vi.fn(), refreshFileTree: vi.fn(), analyzeProject: vi.fn(),
  openFile: vi.fn(), sendMessage: vi.fn(), messagesEndRef: React.createRef<HTMLDivElement>(),
  ipc: { invoke: vi.fn() }, showToast: vi.fn(), diffFile: null, setDiffFile: vi.fn(),
  pendingDelete: null, setPendingDelete: vi.fn(), pendingCommand: null, setPendingCommand: vi.fn(),
  onApproveCommand: vi.fn(), pendingQuestion: null, setPendingQuestion: vi.fn(),
  onAnswerQuestion: vi.fn(), deleteFile: vi.fn(), setIsTerminalOpen: vi.fn(), apiBase: API,
  mcpGate: null, mcpWorkspaceMismatch: false, mcpOpenWorkspacePath: null, onMcpResolved: vi.fn(),
  activity: null,
  ...extra,
} as any)} />)

const labels = () => screen.queryAllByTestId('message-agent').map(n => n.textContent?.trim())

describe('ChatPanel message header', () => {
  it('a chat answered by two agents shows each answer with its own agent and model', () => {
    // The current selection is Codex; the old caller props are passed on
    // purpose to prove they no longer leak into an answer's label.
    panel([
      { id: 1, role: 'user', content: 'soru', smells: [], timestamp: 't' },
      { id: 2, role: 'assistant', content: 'opencode cevabı', smells: [], timestamp: 't',
        provider: 'opencode', model: 'opencode:opencode/big-pickle' },
      { id: 3, role: 'user', content: 'soru 2', smells: [], timestamp: 't' },
      { id: 4, role: 'assistant', content: 'codex cevabı', smells: [], timestamp: 't',
        provider: 'codex', model: 'gpt-6-luna' },
    ], { effectiveProvider: 'subscription', modelName: 'gpt-6-luna' })
    expect(labels()).toEqual(['OpenCode · big-pickle', 'Codex · gpt-6-luna'])
  })

  it('a legacy answer without a recorded agent shows no model name, not the current one', () => {
    panel([
      { id: 1, role: 'assistant', content: 'eski cevap', smells: [], timestamp: 't' },
    ], { effectiveProvider: 'subscription', modelName: 'gpt-6-luna' })
    expect(labels()).toEqual(['AI'])
    expect(document.body.textContent).not.toContain('gpt-6-luna')
    expect(document.body.textContent).not.toContain('Codex')
  })
})

// ── Streaming: the live answer takes the turn's agent from `turn_meta` ──────

const enc = (ev: object) => new TextEncoder().encode(`data: ${JSON.stringify(ev)}\n\n`)

const makeStream = () => {
  const queue: Array<{ done: boolean; value?: Uint8Array }> = []
  let waiter: ((v: any) => void) | null = null
  const deliver = (item: { done: boolean; value?: Uint8Array }) => {
    if (waiter) { const w = waiter; waiter = null; w(item) } else queue.push(item)
  }
  return {
    push: (ev: object) => deliver({ done: false, value: enc(ev) }),
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

const flush = async () => { await act(async () => { for (let i = 0; i < 10; i++) await Promise.resolve() }) }

describe('streaming answer label', () => {
  let stream: ReturnType<typeof makeStream>

  beforeEach(() => {
    stream = makeStream()
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
  })

  it("uses the turn's agent and model, not the selection the hook was given", async () => {
    // The hook's config says Claude; the backend reports the turn ran on OpenCode.
    const config = { provider_type: 'subscription', model_name: 'claude-opus-5' } as any
    const { result } = renderHook(() => useChat(API, USER, config, '/ws', vi.fn(), vi.fn(), (n: string) => n))
    await act(async () => { await result.current.selectConversation({ id: 1 } as any) })
    act(() => { result.current.sendMessage('soru', '', 'tr', 'auto', 'medium', vi.fn(), vi.fn()) })
    await flush()
    stream.push({ type: 'turn_meta', provider: 'opencode', model: 'opencode:opencode/big-pickle' })
    stream.push({ type: 'text', content: 'akan cevap' })
    await flush()
    const live = result.current.messages.find((m: any) => m.role === 'assistant')!
    expect([live.provider, live.model]).toEqual(['opencode', 'opencode:opencode/big-pickle'])
    expect(live.content).toBe('akan cevap')

    panel(result.current.messages, { loading: true })
    expect(labels()).toEqual(['OpenCode · big-pickle'])
  })
})
