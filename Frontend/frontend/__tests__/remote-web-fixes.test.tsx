import React from 'react'
import axios from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'

// The page is "remote" when served from the relay; the cards ask at render time.
const page = vi.hoisted(() => ({ remote: false }))
vi.mock('../renderer/lib/remote/transport', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../renderer/lib/remote/transport')>()
  return { ...actual, isRemotePage: () => page.remote }
})

import { BrowserApproval, browserCardInfo } from '../renderer/components/home/BrowserApproval'
import { ComputerUseApproval, computerCardInfo } from '../renderer/components/home/ComputerUseApproval'
import { HOLD_MS } from '../renderer/components/home/ApprovalCard'
import { postComputerDecision } from '../renderer/hooks/home/gateResponse'
import { hasComputerUseIpc } from '../renderer/lib/computerUse'
import { createRemoteAxiosAdapter, createRemoteFetch } from '../renderer/lib/remote/httpShim'
import { RemoteTransport, type RemoteLink } from '../renderer/lib/remote/transport'

const BROWSER = browserCardInfo('browser_click', { risk_reason: 'irreversible', element_name: 'Submit', page_url: 'https://shop.example/' })
const RISKY = computerCardInfo('computer_confirm_risky_step', { app_name: 'Mail', risk_reason: 'sends mail' })
const FIRST_USE = computerCardInfo('computer_click', { app_name: 'Mail', bundle_id: 'com.apple.mail' })

const cards = [
  { name: 'BrowserApproval', approve: 'bp-card-approve', reject: 'bp-card-reject', approved: [true],
    render: (onAnswer: (...a: unknown[]) => void) => render(<BrowserApproval info={BROWSER} onAnswer={onAnswer} />) },
  { name: 'risky ComputerUseApproval', approve: 'cu-card-approve', reject: 'cu-card-reject', approved: [true, false],
    render: (onAnswer: (...a: unknown[]) => void) => render(<ComputerUseApproval info={RISKY} onAnswer={onAnswer} />) },
]

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  page.remote = false
  delete (window as any).ipc
})

describe.each(cards)('$name on the paired browser', (card) => {
  beforeEach(() => {
    page.remote = true
    vi.useFakeTimers()
  })

  it('does not approve on a plain click and says to hold', () => {
    const onAnswer = vi.fn()
    card.render(onAnswer)
    const button = screen.getByTestId(card.approve)
    fireEvent.click(button)
    act(() => { vi.advanceTimersByTime(HOLD_MS * 2) })
    expect(onAnswer).not.toHaveBeenCalled()
    expect(button.textContent).toContain('basılı tut')
  })

  it('approves once the pointer has been held for the full hold', () => {
    const onAnswer = vi.fn()
    card.render(onAnswer)
    const button = screen.getByTestId(card.approve)
    fireEvent.pointerDown(button, { button: 0 })
    expect(button.getAttribute('data-hold')).toBe('holding')
    act(() => { vi.advanceTimersByTime(HOLD_MS - 1) })
    expect(onAnswer).not.toHaveBeenCalled()
    act(() => { vi.advanceTimersByTime(1) })
    expect(onAnswer).toHaveBeenCalledTimes(1)
    expect(onAnswer).toHaveBeenCalledWith(...card.approved)
    fireEvent.pointerUp(button)
    expect(onAnswer).toHaveBeenCalledTimes(1)
  })

  it.each(['pointerUp', 'pointerLeave', 'pointerCancel', 'blur'] as const)('abandons the hold on an early %s', (ev) => {
    const onAnswer = vi.fn()
    card.render(onAnswer)
    const button = screen.getByTestId(card.approve)
    fireEvent.pointerDown(button, { button: 0 })
    act(() => { vi.advanceTimersByTime(HOLD_MS - 100) })
    fireEvent[ev](button)
    expect(button.getAttribute('data-hold')).toBe('idle')
    act(() => { vi.advanceTimersByTime(HOLD_MS * 2) })
    expect(onAnswer).not.toHaveBeenCalled()
  })

  it('ignores a secondary button', () => {
    const onAnswer = vi.fn()
    card.render(onAnswer)
    fireEvent.pointerDown(screen.getByTestId(card.approve), { button: 2 })
    act(() => { vi.advanceTimersByTime(HOLD_MS * 2) })
    expect(onAnswer).not.toHaveBeenCalled()
  })

  it.each(['Enter', ' '])('approves on a held %j key and abandons on an early release', (key) => {
    const onAnswer = vi.fn()
    card.render(onAnswer)
    const button = screen.getByTestId(card.approve)
    fireEvent.keyDown(button, { key })
    act(() => { vi.advanceTimersByTime(HOLD_MS / 2) })
    fireEvent.keyUp(button, { key })
    act(() => { vi.advanceTimersByTime(HOLD_MS * 2) })
    expect(onAnswer).not.toHaveBeenCalled()
    fireEvent.keyDown(button, { key })
    // Auto-repeat while held does not restart the hold.
    act(() => { vi.advanceTimersByTime(HOLD_MS / 2) })
    fireEvent.keyDown(button, { key, repeat: true })
    act(() => { vi.advanceTimersByTime(HOLD_MS / 2) })
    expect(onAnswer).toHaveBeenCalledTimes(1)
    expect(onAnswer).toHaveBeenCalledWith(...card.approved)
  })

  it('rejects on a plain click', () => {
    const onAnswer = vi.fn()
    card.render(onAnswer)
    fireEvent.click(screen.getByTestId(card.reject))
    expect(onAnswer).toHaveBeenCalledWith(false, ...(card.approved.length > 1 ? [false] : []))
  })

  it('drops a hold still running when the card goes away', () => {
    const onAnswer = vi.fn()
    const { unmount } = card.render(onAnswer)
    fireEvent.pointerDown(screen.getByTestId(card.approve), { button: 0 })
    unmount()
    act(() => { vi.advanceTimersByTime(HOLD_MS * 2) })
    expect(onAnswer).not.toHaveBeenCalled()
  })
})

describe.each(cards)('$name on the desktop', (card) => {
  it('approves on a click, as before', () => {
    const onAnswer = vi.fn()
    card.render(onAnswer)
    const button = screen.getByTestId(card.approve)
    expect(button.getAttribute('data-hold')).toBeNull()
    fireEvent.click(button)
    expect(onAnswer).toHaveBeenCalledWith(...card.approved)
  })
})

describe('first-use computer card on the paired browser', () => {
  it('keeps its plain buttons: only the risky step needs the hold', () => {
    page.remote = true
    const onAnswer = vi.fn()
    render(<ComputerUseApproval info={FIRST_USE} onAnswer={onAnswer} />)
    fireEvent.click(screen.getByTestId('cu-card-once'))
    expect(onAnswer).toHaveBeenCalledWith(true, false)
  })
})

describe('isRemotePage', () => {
  it('is true on the relay origin, with or without the remote ipc shim, and false on the desktop', async () => {
    const { isRemotePage } = await vi.importActual<typeof import('../renderer/lib/remote/transport')>(
      '../renderer/lib/remote/transport',
    )
    const at = (href: string, ipc?: unknown) => ({ location: new URL(href), ipc })
    expect(isRemotePage(at('https://relay.test/app/home/'))).toBe(true)
    expect(isRemotePage(at('https://relay.test/app/home/', { invoke: () => null }))).toBe(true)
    expect(isRemotePage(at('app://./home', { invoke: () => null }))).toBe(false)
    expect(isRemotePage(at('http://localhost:8888/home', { invoke: () => null }))).toBe(false)
    expect(isRemotePage(at('https://localhost/home'))).toBe(false)
    expect(isRemotePage(undefined)).toBe(false)
  })
})

describe('computer card answers on the paired browser', () => {
  it('post the answer over HTTP and never use the computer-use channel', async () => {
    page.remote = true
    const invoke = vi.fn(async () => ({ status: 'ok' }))
    ;(window as any).ipc = { invoke }
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ status: 'ok' }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    try {
      expect(hasComputerUseIpc()).toBe(false)
      expect(await postComputerDecision('remote://backend', 'g1', true, true, '')).toBeNull()
    } finally {
      vi.unstubAllGlobals()
    }
    expect(invoke).not.toHaveBeenCalled()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('remote://backend/mcp-approval-respond/g1')
    expect(JSON.parse(String(init.body))).toEqual({ approved: true })
  })

  it('still go through the computer-use channel on the desktop', async () => {
    const invoke = vi.fn(async () => ({ status: 'ok' }))
    ;(window as any).ipc = { invoke }
    expect(hasComputerUseIpc()).toBe(true)
    expect(await postComputerDecision('http://127.0.0.1:8000', 'g1', true, true, 'tok')).toBeNull()
    expect(invoke).toHaveBeenCalledWith('computer-use', { op: 'answer-card', gateId: 'g1', approved: true, remember: true })
  })
})

describe('an upstream failure reaches the page as a failure', () => {
  function setup(reply: (type: string) => any) {
    const link: RemoteLink = { request: vi.fn(async (type: string) => reply(type)) }
    const transport = new RemoteTransport(link)
    transport.handleStatus('ready')
    return {
      transport,
      remoteFetch: createRemoteFetch(transport, vi.fn() as any),
      http: axios.create({ adapter: createRemoteAxiosAdapter(transport, vi.fn() as any) }),
    }
  }
  const failed = { ok: false, error: 'upstream_failed' }

  it('fetch rejects instead of returning a cut-short body', async () => {
    const { remoteFetch } = setup(() => failed)
    await expect(remoteFetch('remote://backend/session-report/1/preview')).rejects.toMatchObject({ code: 'upstream_failed' })
  })

  it('axios rejects with the code', async () => {
    const { http } = setup(() => failed)
    await expect(http.get('remote://backend/session-report/1/preview')).rejects.toMatchObject({ code: 'upstream_failed' })
  })

  it('a stream that ends with the error errors its reader', async () => {
    const { remoteFetch, transport } = setup(() => ({ ok: true, result: { stream_id: 'up1', status: 200 } }))
    const res = await remoteFetch('remote://backend/chat-stream', { method: 'POST', body: '{}' })
    const reader = res.body!.getReader()
    transport.handlePush({ type: 'ui_stream_end', stream_id: 'up1', error: 'upstream_failed' })
    await expect(reader.read()).rejects.toMatchObject({ code: 'upstream_failed' })
  })
})
