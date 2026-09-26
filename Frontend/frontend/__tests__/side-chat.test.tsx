/**
 * SIDE QUESTION ("Yan soru"): a read-only side chat over the chat on screen.
 *
 * It must leave the main chat alone: its stream never lands in the main
 * chat's runtime (messages, activity, sidebar status), its Stop is its own,
 * and its answer reaches the main chat only as a quoted draft in the message
 * box. `home.tsx` does not render in jsdom (Monaco, IPC), so the harness below
 * wires the real hooks and components the way `home.tsx` does, and the page's
 * own wiring is checked from its source.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { render, screen, cleanup, act, fireEvent } from '@testing-library/react'

vi.mock('axios', () => {
  const post = vi.fn()
  const get = vi.fn()
  const del = vi.fn()
  const put = vi.fn()
  return { default: { post, get, delete: del, put }, post, get }
})
vi.mock('../renderer/components/ui/ConfirmDialog', () => ({ confirmDialog: vi.fn().mockResolvedValue(true) }))

import axios from 'axios'
import { useChat } from '../renderer/hooks/home/useChat'
import { useSideChat, sideQuote } from '../renderer/hooks/home/useSideChat'
import { SideChatPanel, SideQuestionButton } from '../renderer/components/home/SideChatPanel'

const mocked = axios as unknown as Record<'post' | 'get' | 'delete' | 'put', ReturnType<typeof vi.fn>>

const API = 'http://127.0.0.1:8000'
const USER = { id: 1, name: 'b', sessionToken: 'tok' } as any
const CONFIG = { provider_type: 'subscription', model_name: 'claude-opus-5' } as any
const MAIN = 1
const SIDE = 50

const enc = (ev: object) => new TextEncoder().encode(`data: ${JSON.stringify(ev)}\n\n`)
const makeStream = () => {
  let waiter: ((v: any) => void) | null = null
  let failer: ((e: any) => void) | null = null
  const queue: any[] = []
  return {
    push: (ev: object) => { const item = { done: false, value: enc(ev) }; if (waiter) { const w = waiter; waiter = null; w(item) } else queue.push(item) },
    close: () => { const item = { done: true }; if (waiter) { const w = waiter; waiter = null; w(item) } else queue.push(item) },
    fail: (err: Error) => { if (failer) { const f = failer; waiter = null; failer = null; f(err) } },
    response: { ok: true, status: 200, body: { getReader: () => ({
      read: () => queue.length ? Promise.resolve(queue.shift()) : new Promise((res, rej) => { waiter = res; failer = rej }),
    }) } },
  }
}

let mainStream: ReturnType<typeof makeStream> | null
let sideStream: ReturnType<typeof makeStream> | null
let sideBodies: any[]
let fetchMock: ReturnType<typeof vi.fn>
let showToast: ReturnType<typeof vi.fn>

const calls = (pred: (url: string, init: any) => boolean) =>
  fetchMock.mock.calls.filter(([u, init]) => pred(String(u), init || {}))

const flush = async () => { await act(async () => { for (let i = 0; i < 10; i++) await Promise.resolve() }) }

const abortable = (init: any, s: ReturnType<typeof makeStream>) =>
  init?.signal?.addEventListener('abort', () => { const e = new Error('aborted'); (e as any).name = 'AbortError'; s.fail(e) })

beforeEach(() => {
  // jsdom has no layout, so no scrollIntoView (the panel follows new answers).
  Element.prototype.scrollIntoView = vi.fn()
  showToast = vi.fn()
  mainStream = null
  sideStream = null
  sideBodies = []
  fetchMock = vi.fn((url: string, init?: any) => {
    const u = String(url)
    if (u.endsWith('/chat-stream')) {
      mainStream = makeStream()
      abortable(init, mainStream)
      return Promise.resolve(mainStream.response)
    }
    if (u.endsWith(`/conversations/${SIDE}/side-stream`)) {
      sideBodies.push(JSON.parse(init.body))
      sideStream = makeStream()
      abortable(init, sideStream)
      return Promise.resolve(sideStream.response)
    }
    if (u.endsWith(`/conversations/${MAIN}/side`) && init?.method === 'POST') {
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ side_id: SIDE, side_of: MAIN }) })
    }
    if (u.includes('/wake-stream')) return new Promise(() => {})
    return Promise.resolve({ ok: true, status: 200, json: async () => ({}) })
  })
  vi.stubGlobal('fetch', fetchMock)
  mocked.get.mockReset().mockImplementation(async (url: string) => {
    const u = String(url)
    if (u.endsWith('/conversations/1')) {
      return { data: [{ id: MAIN, title: 'ana', created_at: '', updated_at: '', parent_id: null, hidden: false }] }
    }
    if (u.includes('/context-usage')) return { data: { percent: 1, should_compact: false, message_count: 0 } }
    return { data: [] }
  })
  mocked.post.mockReset().mockResolvedValue({ data: {} })
  mocked.put.mockReset()
  mocked.delete.mockReset()
})

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

let chat: ReturnType<typeof useChat>
let side: ReturnType<typeof useSideChat>

/** The same wiring as `home.tsx` (toggleSideChat / askSideQuestion / addSideAnswerToMain). */
const Harness = () => {
  chat = useChat(API, USER, CONFIG, '/ws', showToast as any, vi.fn(), (n: string) => n)
  side = useSideChat(API, USER)
  const lastAnswer = [...chat.messages].reverse().find(m => m.role === 'assistant')
  return (
    <div>
      <SideQuestionButton convId={chat.activeConvId} active={side.isOpen}
        onOpen={id => { if (side.mainId === id) side.close(); else void side.open(id) }} />
      <div data-testid="main-input">{chat.chatInput}</div>
      {side.isOpen && (
        <SideChatPanel
          messages={side.messages}
          loading={side.loading}
          onAsk={q => void side.ask(q, { liveContext: chat.loading ? (lastAnswer?.content || '') : '', lang: 'tr', thinkingLevel: 'auto' })}
          onStop={side.stop}
          onClose={side.close}
          onAddToMain={(q, a) => {
            const quote = sideQuote(q, a)
            chat.setChatInput(prev => (prev.trim() ? `${prev}\n\n${quote}` : quote))
          }}
        />
      )}
    </div>
  )
}

const setup = async () => {
  render(<Harness />)
  await act(async () => { await chat.fetchConversations(1) })
  await act(async () => { await chat.selectConversation({ id: MAIN, title: 'ana', parent_id: null, hidden: false } as any) })
}

const startMainTurn = async () => {
  act(() => { void chat.sendMessage('ana soru', '', 'tr', 'auto', 'medium', vi.fn(), vi.fn()) })
  await flush()
  mainStream!.push({ type: 'text', content: 'ana cevap yarım' })
  await flush()
  expect(chat.loading).toBe(true)
}

const openAndAsk = async (q = 'bu ne demek?') => {
  fireEvent.click(screen.getByTestId('side-open'))
  await flush()
  expect(screen.getByTestId('side-panel')).toBeTruthy()
  fireEvent.change(screen.getByTestId('side-input'), { target: { value: q } })
  fireEvent.click(screen.getByTestId('side-send'))
  await flush()
}

describe('side question', () => {
  it('opens while the main chat is still streaming, and sends the live answer as context', async () => {
    await setup()
    await startMainTurn()
    const btn = screen.getByTestId('side-open') as HTMLButtonElement
    expect(btn.disabled).toBe(false)
    await openAndAsk()
    expect(calls((u, i) => u.endsWith(`/conversations/${MAIN}/side`) && i.method === 'POST')).toHaveLength(1)
    expect(sideBodies).toHaveLength(1)
    expect(sideBodies[0].message).toBe('bu ne demek?')
    expect(sideBodies[0].live_context).toBe('ana cevap yarım')
    expect(side.loading).toBe(true)
  })

  it("never adds messages, activity or sidebar status to the main chat's runtime", async () => {
    await setup()
    await startMainTurn()
    const mainMessages = chat.messages.map(m => ({ role: m.role, content: m.content }))
    const mainActivity = chat.activity
    await openAndAsk()
    sideStream!.push({ type: 'status', detail: 'yan çalışıyor', conversation_id: SIDE })
    sideStream!.push({ type: 'thinking', text: 'düşünüyor', conversation_id: SIDE })
    sideStream!.push({ type: 'text', content: 'yan ', conversation_id: SIDE })
    sideStream!.push({ type: 'response', content: 'yan cevap', conversation_id: SIDE })
    sideStream!.push({ type: 'done', stop_reason: 'complete', conversation_id: SIDE })
    sideStream!.close()
    await flush()

    expect(chat.messages.map(m => ({ role: m.role, content: m.content }))).toEqual(mainMessages)
    expect(chat.activity).toEqual(mainActivity)
    expect(Object.keys(chat.convStatus)).toEqual([String(MAIN)])
    expect(chat.conversations.map(c => c.id)).toEqual([MAIN])
    expect(screen.getByTestId('side-answer').textContent).toContain('yan cevap')
    expect(chat.loading).toBe(true)
    expect(side.loading).toBe(false)
  })

  it('"Ana sohbete ekle" puts the quoted question and answer into the main message box', async () => {
    await setup()
    await openAndAsk('neden null?')
    sideStream!.push({ type: 'response', content: 'Çünkü\nAwake çalışmadı.', conversation_id: SIDE })
    sideStream!.push({ type: 'done', stop_reason: 'complete', conversation_id: SIDE })
    sideStream!.close()
    await flush()
    fireEvent.click(screen.getByTestId('side-add-to-main'))
    await flush()
    expect(screen.getByTestId('main-input').textContent)
      .toBe('> Yan soru: neden null?\n> Cevap: Çünkü\n> Awake çalışmadı.')
    // Existing text is kept; the quote goes after it.
    act(() => { chat.setChatInput('taslak') })
    fireEvent.click(screen.getByTestId('side-add-to-main'))
    await flush()
    expect(screen.getByTestId('main-input').textContent)
      .toBe('taslak\n\n> Yan soru: neden null?\n> Cevap: Çünkü\n> Awake çalışmadı.')
    // Nothing went to the server for the main chat.
    expect(calls(u => u.endsWith('/chat-stream'))).toHaveLength(0)
  })

  it('closing the panel aborts the side stream and DELETEs the side chat', async () => {
    await setup()
    await openAndAsk()
    fireEvent.click(screen.getByTestId('side-close'))
    await flush()
    expect(screen.queryByTestId('side-panel')).toBeNull()
    expect(calls((u, i) => u.endsWith(`/conversations/${SIDE}/side`) && i.method === 'DELETE')).toHaveLength(1)
    expect(side.isOpen).toBe(false)
    expect(side.messages).toEqual([])
  })

  it("the main chat's Stop leaves the side question running, and the side's Stop leaves the main chat", async () => {
    await setup()
    await startMainTurn()
    await openAndAsk()

    act(() => { chat.stopMessage() })
    await flush()
    expect(calls(u => u.endsWith(`/chat-stop/${MAIN}`))).toHaveLength(1)
    expect(calls(u => u.endsWith(`/chat-stop/${SIDE}`))).toHaveLength(0)
    expect(side.loading).toBe(true)
    sideStream!.push({ type: 'text', content: 'hâlâ akıyor', conversation_id: SIDE })
    await flush()
    expect(screen.getByTestId('side-answer').textContent).toContain('hâlâ akıyor')

    // A new main turn, then the side's own Stop.
    await startMainTurn()
    fireEvent.click(screen.getByTestId('side-stop'))
    await flush()
    expect(calls(u => u.endsWith(`/chat-stop/${SIDE}`))).toHaveLength(1)
    expect(calls(u => u.endsWith(`/chat-stop/${MAIN}`))).toHaveLength(1)
    expect(side.loading).toBe(false)
    expect(chat.loading).toBe(true)
    mainStream!.push({ type: 'text', content: ' devam' })
    await flush()
    expect(chat.messages.at(-1)?.content).toContain('devam')
  })

  it('a frame naming another conversation is not applied to the side answer', async () => {
    await setup()
    await openAndAsk()
    sideStream!.push({ type: 'text', content: 'yabancı', conversation_id: MAIN })
    sideStream!.push({ type: 'text', content: 'kendi', conversation_id: SIDE })
    await flush()
    const text = screen.getByTestId('side-answer').textContent || ''
    expect(text).toContain('kendi')
    expect(text).not.toContain('yabancı')
  })

  it('a refused question (agy) shows the server reason in the panel', async () => {
    await setup()
    fetchMock.mockImplementation((url: string, init?: any) => {
      const u = String(url)
      if (u.endsWith('/side-stream')) {
        return Promise.resolve({ ok: false, status: 409, json: async () => ({ detail: 'Yan soru agy ile kullanılamıyor' }) })
      }
      if (u.endsWith(`/conversations/${MAIN}/side`) && init?.method === 'POST') {
        return Promise.resolve({ ok: true, status: 200, json: async () => ({ side_id: SIDE }) })
      }
      if (u.includes('/wake-stream')) return new Promise(() => {})
      return Promise.resolve({ ok: true, status: 200, json: async () => ({}) })
    })
    await openAndAsk()
    await flush()
    expect(screen.getByTestId('side-answer').textContent).toContain('agy ile kullanılamıyor')
    expect(screen.queryByTestId('side-add-to-main')).toBeNull()
    expect(side.loading).toBe(false)
  })
})

describe('home.tsx wiring', () => {
  const home = readFileSync(resolve(__dirname, '../renderer/pages/home.tsx'), 'utf8')

  it('the side question button is not gated on the branch rules', () => {
    const line = home.split('\n').find(l => l.includes('<SideQuestionButton')) || ''
    expect(line).toContain('convId={chat.activeConvId}')
    expect(line).not.toContain('branchBlocked')
    expect(line).not.toContain('disabled')
  })

  it('the panel closes when the chat on screen changes or is deleted', () => {
    const effect = home.slice(home.indexOf('const side = useSideChat'), home.indexOf('// Onay kartı teslimi'))
    expect(effect).toContain('chat.activeConvId !== side.mainId')
    expect(effect).toContain('chat.conversations.some(c => c.id === side.mainId)')
    expect(effect).toContain('side.close()')
  })

  it('"add to main chat" writes only the message box', () => {
    const fn = home.slice(home.indexOf('const addSideAnswerToMain'), home.indexOf('const langCtxValue'))
    expect(fn).toContain('chat.setChatInput(')
    expect(fn).not.toMatch(/sendMessage|axios|fetch\(/)
  })
})

describe('sideQuote', () => {
  it('quotes every line of both parts', () => {
    expect(sideQuote('a\nb', 'c', { question: 'Q', answer: 'A' })).toBe('> Q: a\n> b\n> A: c')
  })
})
