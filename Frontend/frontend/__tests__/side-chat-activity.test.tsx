/**
 * SIDE QUESTION activity line. Live test 27 Sep 2026: the panel showed nothing
 * but its placeholder for ~10 s while the model ran read tools, because the
 * hook ignored every event except text. The line now follows the `thinking`
 * and tool events the side stream already carries.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, act, fireEvent } from '@testing-library/react'

import { useSideChat } from '../renderer/hooks/home/useSideChat'
import { SideChatPanel } from '../renderer/components/home/SideChatPanel'
import { cevir } from '../renderer/lib/i18n'

const API = 'http://127.0.0.1:8000'
const USER = { id: 1, name: 'b', sessionToken: 'tok' } as any
const MAIN = 1
const SIDE = 50

const enc = (ev: object) => new TextEncoder().encode(`data: ${JSON.stringify({ conversation_id: SIDE, ...ev })}\n\n`)
const makeStream = () => {
  let waiter: ((v: any) => void) | null = null
  const queue: any[] = []
  const put = (item: any) => { if (waiter) { const w = waiter; waiter = null; w(item) } else queue.push(item) }
  return {
    push: (ev: object) => put({ done: false, value: enc(ev) }),
    close: () => put({ done: true }),
    response: { ok: true, status: 200, body: { getReader: () => ({
      read: () => queue.length ? Promise.resolve(queue.shift()) : new Promise(res => { waiter = res }),
    }) } },
  }
}

let stream: ReturnType<typeof makeStream>
let side: ReturnType<typeof useSideChat>

const flush = async () => { await act(async () => { for (let i = 0; i < 10; i++) await Promise.resolve() }) }

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn()
  vi.stubGlobal('fetch', vi.fn((url: string, init?: any) => {
    const u = String(url)
    if (u.endsWith(`/conversations/${SIDE}/side-stream`)) {
      stream = makeStream()
      return Promise.resolve(stream.response)
    }
    if (u.endsWith(`/conversations/${MAIN}/side`) && init?.method === 'POST') {
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ side_id: SIDE, side_of: MAIN }) })
    }
    return Promise.resolve({ ok: true, status: 200, json: async () => ({}) })
  }))
})

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

const Harness = () => {
  side = useSideChat(API, USER)
  return side.isOpen ? (
    <SideChatPanel messages={side.messages} loading={side.loading}
      onAsk={q => void side.ask(q)} onStop={side.stop} onClose={side.close} onAddToMain={vi.fn()} />
  ) : null
}

const ask = async () => {
  render(<Harness />)
  await act(async () => { await side.open(MAIN) })
  fireEvent.change(screen.getByTestId('side-input'), { target: { value: 'selam nasılsın' } })
  fireEvent.click(screen.getByTestId('side-send'))
  await flush()
}

const activity = () => screen.queryByTestId('side-activity')?.textContent ?? null

describe('side question activity line', () => {
  it('shows thinking, then the tool being run, before any answer text', async () => {
    await ask()
    expect(activity()).toBeNull()
    stream.push({ type: 'thinking', text: 'soruyu tartıyor' })
    await flush()
    expect(activity()).toBe(cevir('side.activityThinking'))
    // The one-shot CLIs report tools as thinking text (cli_base).
    stream.push({ type: 'thinking', text: '🔧 `read` → `Assets/Player.cs`' })
    await flush()
    expect(activity()).toContain(cevir('side.activityReading'))
    expect(activity()).toContain('read → Assets/Player.cs')
    // A tool's output line does not flip the line back.
    stream.push({ type: 'thinking', text: '↩ using UnityEngine;' })
    await flush()
    expect(activity()).toContain(cevir('side.activityReading'))
  })

  it('disappears when text arrives, returns for a tool, and is gone at done', async () => {
    await ask()
    stream.push({ type: 'tool_call', tool: 'read_file', summary: 'Assets/A.cs' })
    await flush()
    expect(activity()).toContain('read_file → Assets/A.cs')
    stream.push({ type: 'text', content: 'Selam, ' })
    await flush()
    expect(activity()).toBeNull()
    expect(screen.getByTestId('side-answer').textContent).toContain('Selam,')
    stream.push({ type: 'tool_call', tool: 'read_file', summary: 'Assets/B.cs' })
    await flush()
    expect(activity()).toContain('Assets/B.cs')
    stream.push({ type: 'text', content: 'iyiyim.' })
    stream.push({ type: 'thinking', text: 'bitiriyor' })
    stream.push({ type: 'response', content: 'Selam, iyiyim.' })
    stream.push({ type: 'done', stop_reason: 'complete' })
    stream.close()
    await flush()
    expect(activity()).toBeNull()
    expect(side.messages.find(m => m.role === 'assistant')?.activity).toBeUndefined()
    expect(side.loading).toBe(false)
  })
})
