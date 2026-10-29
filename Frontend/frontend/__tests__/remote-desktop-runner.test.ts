// The desktop renderer's side of the web remote's IPC
// (renderer/lib/remote/desktopRunner.ts): allow-listed calls run through
// window.ipc and the answer goes back to the backend; events are forwarded
// until the backend says no browser wants them.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const ipc = vi.hoisted(() => {
  const listeners = new Map<string, (...args: unknown[]) => void>()
  const stub = {
    invoke: vi.fn(),
    on: vi.fn((channel: string, cb: (...args: unknown[]) => void) => {
      listeners.set(channel, cb)
      return () => { listeners.delete(channel) }
    }),
    listeners,
  }
  ;(globalThis as any).window.ipc = stub
  return stub
})

vi.mock('axios', () => {
  const post = vi.fn()
  const get = vi.fn()
  return { default: { post, get }, post, get }
})

import axios from 'axios'
import {
  handleRemoteIpcFrame, parseRemoteIpc, remoteEventAllowed, remoteIpcAllowed, resetRemoteSubscriptionsForTests,
  runRemoteIpc,
} from '../renderer/lib/remote/desktopRunner'

const post = (axios as unknown as { post: ReturnType<typeof vi.fn> }).post
const get = (axios as unknown as { get: ReturnType<typeof vi.fn> }).get
const API = 'http://127.0.0.1:8000'
const RID = '0123456789abcdef'
const flush = () => new Promise(r => setTimeout(r, 0))

beforeEach(() => {
  ipc.invoke.mockReset()
  ipc.on.mockClear()
  post.mockReset()
  post.mockResolvedValue({ data: { accepted: true } })
  get.mockReset()
})

afterEach(() => resetRemoteSubscriptionsForTests())

describe('allow-list mirror', () => {
  it('lets work-parity channels through and keeps desktop-only ones out', () => {
    for (const ch of ['read-file', 'write-file', 'terminal-spawn', 'read-directory', 'move-entry']) {
      expect(remoteIpcAllowed(ch, [])).toBe(true)
    }
    for (const ch of ['app-token-get', 'profile-reset', 'authorize-workspace', 'open-folder-dialog',
      'remote-control', 'approval-mode-set', 'register-dropped-folder']) {
      expect(remoteIpcAllowed(ch, [])).toBe(false)
    }
    expect(remoteIpcAllowed('browser-live', [{ op: 'halt' }])).toBe(true)
    expect(remoteIpcAllowed('browser-live', [{ op: 'enable' }])).toBe(false)
    expect(remoteIpcAllowed('computer-use-live', [{ op: 'open-panel' }])).toBe(false)
    expect(remoteEventAllowed('terminal-data-t1')).toBe(true)
    expect(remoteEventAllowed('menu-open-terminal')).toBe(false)
  })

  it('parses only well-formed frames', () => {
    expect(parseRemoteIpc({ type: 'remote_ipc', request_id: RID, channel: 'read-file', payload: ['a'] }))
      .toEqual({ requestId: RID, channel: 'read-file', args: ['a'] })
    expect(parseRemoteIpc({ type: 'remote_ipc', request_id: '../x', channel: 'read-file', payload: [] })).toBeNull()
    expect(parseRemoteIpc({ type: 'remote_ipc', request_id: RID, channel: 'read-file', payload: 'a' })).toBeNull()
  })
})

describe('running a call', () => {
  it('invokes the channel and posts the result', async () => {
    ipc.invoke.mockResolvedValue({ content: 'x' })
    await runRemoteIpc({ requestId: RID, channel: 'read-file', args: ['Assets/a.cs', '/ws'] }, API, 'tok')
    expect(ipc.invoke).toHaveBeenCalledWith('read-file', 'Assets/a.cs', '/ws')
    expect(post).toHaveBeenCalledWith(`${API}/remote/ui-ipc-result/${RID}`, { ok: true, result: { content: 'x' } },
      { headers: { 'X-Session-Token': 'tok' } })
  })

  it('posts the error of a failing call', async () => {
    ipc.invoke.mockRejectedValue(new Error('outside workspace'))
    await runRemoteIpc({ requestId: RID, channel: 'write-file', args: ['/etc/x', 'y'] }, API, 'tok')
    expect(post.mock.calls[0][1]).toEqual({ ok: false, error: 'outside workspace' })
  })

  it('refuses a channel off the list without invoking it', async () => {
    handleRemoteIpcFrame({ type: 'remote_ipc', request_id: RID, channel: 'app-token-get', payload: [] }, API, 'tok')
    await flush()
    expect(ipc.invoke).not.toHaveBeenCalled()
    expect(post.mock.calls[0][1]).toEqual({ ok: false, error: 'remote_unavailable' })
  })

  it('leaves other wake frames alone', () => {
    expect(handleRemoteIpcFrame({ type: 'remote_message' }, API, 'tok')).toBe(false)
    expect(handleRemoteIpcFrame({ type: 'wake' }, API, 'tok')).toBe(false)
  })
})

describe('subscriptions', () => {
  it('forwards events once per channel and stops when no browser wants them', async () => {
    handleRemoteIpcFrame({ type: 'remote_ipc_subscribe', channel: 'terminal-data-t1' }, API, 'tok')
    handleRemoteIpcFrame({ type: 'remote_ipc_subscribe', channel: 'terminal-data-t1' }, API, 'tok')
    expect(ipc.on).toHaveBeenCalledTimes(1)

    post.mockResolvedValueOnce({ data: { subscribed: true } })
    ipc.listeners.get('terminal-data-t1')!('ls\r\n')
    await flush()
    expect(post).toHaveBeenCalledWith(`${API}/remote/ui-ipc-event`, { channel: 'terminal-data-t1', args: ['ls\r\n'] },
      { headers: { 'X-Session-Token': 'tok' } })
    expect(ipc.listeners.has('terminal-data-t1')).toBe(true)

    post.mockResolvedValueOnce({ data: { subscribed: false } })
    ipc.listeners.get('terminal-data-t1')!('x')
    await flush()
    expect(ipc.listeners.has('terminal-data-t1')).toBe(false)

    // A later subscribe listens again.
    handleRemoteIpcFrame({ type: 'remote_ipc_subscribe', channel: 'terminal-data-t1' }, API, 'tok')
    expect(ipc.on).toHaveBeenCalledTimes(2)
  })

  it('listens again to the channels browsers still want after a reload', async () => {
    get.mockResolvedValue({ data: { channels: ['terminal-data-t1', 'browser-live', 'menu-open-terminal', 7] } })
    expect(handleRemoteIpcFrame({ type: 'remote_ipc_resync' }, API, 'tok')).toBe(true)
    await flush()
    expect(get).toHaveBeenCalledWith(`${API}/remote/ui-ipc-subscriptions`, { headers: { 'X-Session-Token': 'tok' } })
    expect(ipc.on.mock.calls.map(c => c[0])).toEqual(['terminal-data-t1', 'browser-live'])

    // A second resync (the wake stream reconnected) keeps one listener per channel.
    handleRemoteIpcFrame({ type: 'remote_ipc_resync' }, API, 'tok')
    await flush()
    expect(ipc.on).toHaveBeenCalledTimes(2)

    post.mockResolvedValueOnce({ data: { subscribed: true } })
    ipc.listeners.get('terminal-data-t1')!('out')
    await flush()
    expect(post).toHaveBeenCalledWith(`${API}/remote/ui-ipc-event`, { channel: 'terminal-data-t1', args: ['out'] },
      { headers: { 'X-Session-Token': 'tok' } })
  })

  it('survives a failing subscription list', async () => {
    get.mockRejectedValue(new Error('down'))
    handleRemoteIpcFrame({ type: 'remote_ipc_resync' }, API, 'tok')
    await flush()
    expect(ipc.on).not.toHaveBeenCalled()
  })

  it('ignores channels off the event list', () => {
    handleRemoteIpcFrame({ type: 'remote_ipc_subscribe', channel: 'menu-open-terminal' }, API, 'tok')
    expect(ipc.on).not.toHaveBeenCalled()
  })
})
