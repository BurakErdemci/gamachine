/**
 * Terminal agent messages: a `remote_message` frame with source "agent" is sent like a phone
 * message and marked "Terminal · <client>"; a `conversation_created` frame reloads the chat list.
 * Harness as in remote-message.test.tsx.
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
import { useChat } from '../renderer/hooks/home/useChat'
import { ChatPanel } from '../renderer/components/home/ChatPanel'
import { parseRemoteMessage, resetRemoteClaimsForTests } from '../renderer/lib/remoteControl'

const mockedAxios = axios as unknown as { post: ReturnType<typeof vi.fn>; get: ReturnType<typeof vi.fn> }

const API = 'http://127.0.0.1:8000'
const USER = { id: 1, name: 'b', sessionToken: 'tok' } as any
const CONFIG = { provider_type: 'subscription', model_name: 'claude-opus-5' } as any

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
          read: () => queue.length
            ? Promise.resolve(queue.shift())
            : new Promise(res => { waiter = res }),
        }),
      },
    },
  }
}

type Turn = { convId: number; message: string; stream: ReturnType<typeof makeStream> }
let turns: Turn[]
let wakeStreams: Array<(frame: object) => void>

const installFetch = () => {
  turns = []
  wakeStreams = []
  vi.stubGlobal('fetch', vi.fn((url: string, init?: any) => {
    const u = String(url)
    if (u.endsWith('/chat-stream')) {
      const body = JSON.parse(init.body)
      const stream = makeStream()
      turns.push({ convId: body.conversation_id, message: body.message, stream })
      return Promise.resolve(stream.response)
    }
    if (u.includes('/wake-stream-all')) {
      let waiter: ((v: any) => void) | null = null
      const frames: Uint8Array[] = []
      wakeStreams.push((frame: object) => {
        const bytes = enc(frame)
        if (waiter) { const w = waiter; waiter = null; w({ done: false, value: bytes }) } else frames.push(bytes)
      })
      return Promise.resolve({
        ok: true,
        body: {
          getReader: () => ({
            read: () => frames.length
              ? Promise.resolve({ done: false, value: frames.shift() })
              : new Promise(res => { waiter = res }),
          }),
        },
      })
    }
    return Promise.resolve({ ok: true, status: 200, json: async () => ({ status: 'ok' }) })
  }))
}

const hook = () => renderHook(() => useChat(API, USER, CONFIG, '/ws', vi.fn(), vi.fn(), (n: string) => n))
const flush = async () => { await act(async () => { for (let i = 0; i < 20; i++) await Promise.resolve() }) }
const defaults = (result: any) => act(() => {
  result.current.setWakeDefaults({
    lang: 'tr', genMode: 'balanced', thinkingLevel: 'medium',
    setPendingGenFiles: vi.fn(), setPendingDelete: vi.fn(),
  })
})
const open = async (result: any, id: number) => {
  await act(async () => { await result.current.selectConversation({ id } as any) })
}

let seq = 0
const frame = (conversationId: number, text: string, extra: Record<string, unknown> = {}) => ({
  type: 'remote_message', request_id: `areq${++seq}`, conversation_id: conversationId, text,
  source: 'agent', device_id: 'agent:claude-code', device_name: 'claude-code', at: Date.now(), ...extra,
})
const pushAll = (f: object) => wakeStreams.forEach(push => push(f))
const listCalls = () => mockedAxios.get.mock.calls.filter(c => String(c[0]).endsWith('/conversations/1')).length

beforeEach(() => {
  installFetch()
  resetRemoteClaimsForTests()
  try { localStorage.clear() } catch { /* none */ }
  mockedAxios.post.mockReset().mockResolvedValue({ data: {} })
  mockedAxios.get.mockReset().mockImplementation(async (url: string) => {
    if (String(url).includes('/context-usage')) return { data: { percent: 1, should_compact: false, message_count: 0 } }
    return { data: [] }
  })
})

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('agent message', () => {
  it('the parser accepts source agent and keeps rejecting other sources', () => {
    expect(parseRemoteMessage(frame(2, 'hi'))).toMatchObject({ source: 'agent', conversationId: 2, deviceName: 'claude-code' })
    expect(parseRemoteMessage({ ...frame(2, 'hi'), source: 'phone' })).toMatchObject({ source: 'phone' })
    expect(parseRemoteMessage({ ...frame(2, 'hi'), source: 'desktop' })).toBeNull()
    expect(parseRemoteMessage({ ...frame(2, 'hi'), source: undefined })).toBeNull()
  })

  it('goes out as a user turn and its bubble carries source agent and the client name', async () => {
    const { result } = hook()
    defaults(result)
    await open(result, 1)
    await flush()
    pushAll(frame(5, 'from the terminal'))
    await flush()
    expect(turns.map(t => [t.convId, t.message])).toEqual([[5, 'from the terminal']])
    expect(result.current.activeConvId).toBe(1)
    turns[0].stream.push({ type: 'done', stop_reason: 'complete' })
    turns[0].stream.close()
    await flush()
    await open(result, 5)
    const bubble = result.current.messages.find((m: any) => m.role === 'user')
    expect(bubble).toMatchObject({ content: 'from the terminal', source: 'agent', sourceDevice: 'claude-code' })
  })
})

describe('agent marker · chat bubble', () => {
  const panel = (messages: any[]) => render(<ChatPanel {...({
    messages, activeConvId: 7, user: USER, loading: false, lang: 'tr',
    thinkingLevel: 'auto', workspacePath: '/ws', handleExportToUnity: vi.fn(),
    pendingGenFiles: null, setPendingGenFiles: vi.fn(), pendingFix: null, setPendingFix: vi.fn(),
    openedFilePath: null, setCode: vi.fn(), refreshFileTree: vi.fn(),
    openFile: vi.fn(), sendMessage: vi.fn(), messagesEndRef: React.createRef<HTMLDivElement>(),
    ipc: { invoke: vi.fn() }, showToast: vi.fn(), diffFile: null, setDiffFile: vi.fn(),
    pendingDelete: null, setPendingDelete: vi.fn(), pendingCommand: null, setPendingCommand: vi.fn(),
    onApproveCommand: vi.fn(), pendingQuestion: null, setPendingQuestion: vi.fn(),
    onAnswerQuestion: vi.fn(), deleteFile: vi.fn(), setIsTerminalOpen: vi.fn(), apiBase: API,
    mcpGate: null, mcpWorkspaceMismatch: false, mcpOpenWorkspacePath: null, onMcpResolved: vi.fn(),
    activity: null,
  } as any)} />)

  it('shows "Terminal · name" with bidi controls stripped, and no phone marker', () => {
    panel([{ id: 'u1', role: 'user', content: 'hi', smells: [], timestamp: new Date().toISOString(), source: 'agent', sourceDevice: '‮claude-code‬' }])
    const marker = screen.getByTestId('agent-marker')
    expect(marker.textContent).toContain('Terminal · claude-code')
    expect(marker.textContent).not.toMatch(/[‪-‮]/)
    expect(screen.queryByTestId('phone-marker')).toBeNull()
  })

  it('falls back to the unnamed label, and a typed message has no marker', () => {
    panel([
      { id: 'u1', role: 'user', content: 'a', smells: [], timestamp: new Date().toISOString(), source: 'agent' },
      { id: 'u2', role: 'user', content: 'b', smells: [], timestamp: new Date().toISOString() },
    ])
    expect(screen.getAllByTestId('agent-marker').length).toBe(1)
    expect(screen.getByTestId('agent-marker').textContent).toContain('Terminal · Terminal ajanı')
  })
})

describe('conversation_created', () => {
  it('reloads the chat list once and leaves the active chat alone', async () => {
    const { result } = hook()
    defaults(result)
    await open(result, 1)
    await flush()
    const before = listCalls()
    pushAll({ type: 'conversation_created', conversation_id: 9, at: Date.now() })
    await flush()
    expect(listCalls()).toBe(before + 1)
    expect(result.current.activeConvId).toBe(1)
    pushAll({ type: 'conversation_created', conversation_id: 0, at: Date.now() })
    await flush()
    expect(listCalls()).toBe(before + 1)
  })
})
