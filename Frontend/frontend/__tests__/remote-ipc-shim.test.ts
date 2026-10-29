import { describe, expect, it, vi } from 'vitest'

import axios from 'axios'

import { isRemoteMode, remoteSession, withRemoteMode } from '../renderer/lib/remote/bootstrap'
import { REMOTE_BACKEND_BASE, createIpcShim } from '../renderer/lib/remote/ipcShim'
import { RemoteTransport, type RemoteLink } from '../renderer/lib/remote/transport'

function setup(handler: (type: string, params: any) => any = () => ({})) {
  const calls: Array<{ type: string; params: any }> = []
  const link: RemoteLink = {
    request: vi.fn(async (type: string, params: any = {}) => {
      calls.push({ type, params })
      const out = await handler(type, params)
      return out && out.ok === false ? out : { ok: true, result: out }
    }),
  }
  const transport = new RemoteTransport(link)
  transport.handleStatus('ready')
  const onUnavailable = vi.fn()
  const ipc = createIpcShim(transport, onUnavailable)
  return { ipc, transport, calls, onUnavailable }
}

describe('remote window.ipc shim', () => {
  it('answers the backend base and token locally', async () => {
    const { ipc, calls } = setup()
    await expect(ipc.invoke('get-backend-base-url')).resolves.toBe(REMOTE_BACKEND_BASE)
    await expect(ipc.invoke('app-token-get')).resolves.toBeNull()
    expect(calls).toHaveLength(0)
  })

  it('sends other channels as ui_ipc and returns the main-process reply', async () => {
    const { ipc, calls } = setup(() => ({ ok: true, bytes: 3 }))
    await expect(ipc.invoke('write-file', '/w/a.cs', 'abc', '/w')).resolves.toEqual({ ok: true, bytes: 3 })
    expect(calls[0]).toEqual({
      type: 'ui_ipc',
      params: { channel: 'write-file', payload: '/w/a.cs', args: ['/w/a.cs', 'abc', '/w'] },
    })
  })

  it('rejects remote_unavailable from the desktop with a coded Error and tells the UI', async () => {
    const { ipc, onUnavailable } = setup(() => ({ ok: false, error: 'remote_unavailable' }))
    const err = await ipc.invoke('app-zoom-set', 1.2).catch((e) => e)
    expect(err).toBeInstanceOf(Error)
    expect(err.code).toBe('remote_unavailable')
    expect(onUnavailable).toHaveBeenCalledWith('app-zoom-set')
  })

  it('refuses desktop-only channels without a round trip', async () => {
    const { ipc, calls, onUnavailable } = setup()
    await expect(ipc.invoke('open-folder-dialog')).rejects.toMatchObject({ code: 'remote_unavailable' })
    expect(calls).toHaveLength(0)
    expect(onUnavailable).toHaveBeenCalledWith('open-folder-dialog')
  })

  it('passes other errors through unchanged', async () => {
    const { ipc, onUnavailable } = setup(() => ({ ok: false, error: 'internal' }))
    await expect(ipc.invoke('read-file', '/x')).rejects.toMatchObject({ code: 'internal' })
    expect(onUnavailable).not.toHaveBeenCalled()
  })

  it('on() subscribes, routes events and returns an unsubscribe', async () => {
    const { ipc, transport, calls } = setup()
    const cb = vi.fn()
    const off = ipc.on('terminal-exit-3', cb)
    expect(typeof off).toBe('function')
    await vi.waitFor(() => expect(calls).toContainEqual({ type: 'ui_ipc_subscribe', params: { channel: 'terminal-exit-3' } }))
    transport.handlePush({ type: 'ui_ipc_event', channel: 'terminal-exit-3', args: [{ exitCode: 0 }] })
    expect(cb).toHaveBeenCalledWith({ exitCode: 0 })
    transport.handlePush({ type: 'ui_ipc_event', channel: 'other', args: [1] })
    off()
    transport.handlePush({ type: 'ui_ipc_event', channel: 'terminal-exit-3', args: [{ exitCode: 1 }] })
    expect(cb).toHaveBeenCalledTimes(1)
  })

  it('answers a dropped folder with an error instead of a path', async () => {
    const { ipc, onUnavailable } = setup()
    await expect(ipc.registerDroppedFolder({} as File)).resolves.toEqual({ error: 'remote_unavailable' })
    expect(onUnavailable).toHaveBeenCalledWith('register-dropped-folder')
  })
})

describe('remote mode detection', () => {
  const loc = (protocol: string, hostname: string) => ({ protocol, hostname })

  it('is remote only without a preload bridge, over https, off localhost', () => {
    expect(isRemoteMode({ location: loc('https:', 'relay.example.dev') })).toBe(true)
    expect(isRemoteMode({ ipc: {}, location: loc('https:', 'relay.example.dev') })).toBe(false)
    expect(isRemoteMode({ location: loc('http:', 'localhost') })).toBe(false)
    expect(isRemoteMode({ location: loc('app:', '-') })).toBe(false)
    expect(isRemoteMode(undefined)).toBe(false)
  })

  it('leaves the desktop app, window.ipc, axios and fetch untouched when window.ipc exists', () => {
    const ipc = { invoke: vi.fn(), on: vi.fn() }
    ;(window as any).ipc = ipc
    const adapter = axios.defaults.adapter
    const fetchBefore = window.fetch
    try {
      const App = () => null
      expect(withRemoteMode(App)).toBe(App)
      expect((window as any).ipc).toBe(ipc)
      expect(axios.defaults.adapter).toBe(adapter)
      expect(window.fetch).toBe(fetchBefore)
      expect(remoteSession()).toBeNull()
    } finally {
      delete (window as any).ipc
    }
  })
})
