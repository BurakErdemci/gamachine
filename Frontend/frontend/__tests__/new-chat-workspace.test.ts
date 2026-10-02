import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook } from '@testing-library/react'
import { useLayoutEffect } from 'react'

const mocks = vi.hoisted(() => {
  const invoke = vi.fn()
  ;(globalThis as any).window.ipc = { invoke, on: vi.fn(() => () => {}) }
  return { invoke }
})
vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() } }))

import axios from 'axios'
import { useChat } from '../renderer/hooks/home/useChat'
import * as workspacePaths from '../renderer/lib/backendWorkspacePath'
import type { AIConfig } from '../renderer/components/home/types'

const hostPath = ['host', 'project'].join('/')
const backendPath = ['backend', 'project'].join('/')
const user = { id: 1, name: 'User', sessionToken: 'token' }
const config = { provider_type: 'api', model_name: 'test' } as AIConfig
const mount = (workspace: string | null = hostPath) => renderHook(({ workspace }) =>
  useChat('http://backend', user, config, workspace, vi.fn(), vi.fn(), name => name),
{ initialProps: { workspace } })

const deferredMapping = () => {
  let resolve!: (value: string) => void
  const promise = new Promise<string>(release => { resolve = release })
  return { promise, resolve }
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, body: null })))
  mocks.invoke.mockReset().mockResolvedValue(backendPath)
  vi.mocked(axios.get).mockReset().mockImplementation(async url => ({
    data: String(url).endsWith('/approval-mode') ? { mode: 'auto', stored: true } : [],
  }))
  vi.mocked(axios.post).mockReset().mockResolvedValue({ data: { id: 42 } })
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('new chat workspace stamp', () => {
  it('omits the stamp when the mapping helper rejects', async () => {
    vi.spyOn(workspacePaths, 'backendWorkspacePath').mockRejectedValueOnce(new Error('mapping helper failed'))
    const { result } = mount()
    await act(async () => {})
    await act(async () => { expect(await result.current.createNewConversation('Rejected')).toBe(42) })
    expect(axios.post).toHaveBeenCalledExactlyOnceWith('http://backend/conversations',
      { user_id: 1, title: 'Rejected' })
  })

  it('omits the old stamp when a layout effect creates a chat before passive effects run', async () => {
    const mapping = deferredMapping()
    mocks.invoke.mockImplementation((channel, path) => {
      if (channel !== 'backend-workspace-path') return Promise.resolve(null)
      return path === 'old-project' ? Promise.resolve('old-backend') : mapping.promise
    })
    let created: Promise<number | null> | undefined
    const { rerender } = renderHook(({ workspace }) => {
      const chat = useChat('http://backend', user, config, workspace, vi.fn(), vi.fn(), name => name)
      useLayoutEffect(() => {
        if (workspace === 'new-project') created = chat.createNewConversation('Layout')
      }, [workspace, chat.createNewConversation])
      return chat
    }, { initialProps: { workspace: 'old-project' } })
    await act(async () => {})
    rerender({ workspace: 'new-project' })
    await act(async () => { expect(await created).toBe(42) })
    expect(axios.post).toHaveBeenCalledExactlyOnceWith('http://backend/conversations',
      { user_id: 1, title: 'Layout' })
    await act(async () => { mapping.resolve('new-backend') })
  })

  it('sends the backend-mapped workspace and uses the current workspace after rerender', async () => {
    mocks.invoke.mockImplementation(async (channel, path) =>
      channel === 'backend-workspace-path' ? `mapped-${path}` : null)
    const { result, rerender } = mount('old-project')
    await act(async () => {})
    await act(async () => { expect(await result.current.createNewConversation('Old')).toBe(42) })
    expect(axios.post).toHaveBeenLastCalledWith('http://backend/conversations',
      { user_id: 1, title: 'Old', workspace: 'mapped-old-project' })
    rerender({ workspace: hostPath })
    await act(async () => {})
    await act(async () => { expect(await result.current.createNewConversation('Title')).toBe(42) })
    expect(mocks.invoke).toHaveBeenCalledWith('backend-workspace-path', hostPath)
    expect(axios.post).toHaveBeenCalledTimes(2)
    expect(axios.post).toHaveBeenLastCalledWith('http://backend/conversations',
      { user_id: 1, title: 'Title', workspace: `mapped-${hostPath}` })
  })

  it('issues the POST immediately while the workspace mapping is pending', async () => {
    const mapping = deferredMapping()
    mocks.invoke.mockImplementation(channel =>
      channel === 'backend-workspace-path' ? mapping.promise : Promise.resolve(null))
    const { result } = mount()
    await act(async () => {
      const created = result.current.createNewConversation('Pending')
      expect(axios.post).toHaveBeenCalledExactlyOnceWith('http://backend/conversations',
        { user_id: 1, title: 'Pending' })
      expect(await created).toBe(42)
    })
    await act(async () => { mapping.resolve(backendPath) })
    await act(async () => { expect(await result.current.createNewConversation('Resolved')).toBe(42) })
    expect(axios.post).toHaveBeenLastCalledWith('http://backend/conversations',
      { user_id: 1, title: 'Resolved', workspace: backendPath })
  })

  it.each(['before', 'after'])('discards an old mapping resolved %s the current mapping', async order => {
    const oldMapping = deferredMapping()
    const currentMapping = deferredMapping()
    mocks.invoke.mockImplementation((channel, path) => {
      if (channel !== 'backend-workspace-path') return Promise.resolve(null)
      return path === 'old-project' ? oldMapping.promise : currentMapping.promise
    })
    const { result, rerender } = mount('old-project')
    rerender({ workspace: hostPath })
    if (order === 'before') await act(async () => { oldMapping.resolve('stale-backend') })
    await act(async () => {
      const created = result.current.createNewConversation('Pending')
      expect(axios.post).toHaveBeenCalledExactlyOnceWith('http://backend/conversations',
        { user_id: 1, title: 'Pending' })
      expect(await created).toBe(42)
    })
    await act(async () => { currentMapping.resolve(backendPath) })
    if (order === 'after') await act(async () => { oldMapping.resolve('stale-backend') })
    await act(async () => { expect(await result.current.createNewConversation('Current')).toBe(42) })
    expect(axios.post).toHaveBeenLastCalledWith('http://backend/conversations',
      { user_id: 1, title: 'Current', workspace: backendPath })
  })

  it('clears the resolved stamp while a different workspace mapping is pending', async () => {
    const mapping = deferredMapping()
    mocks.invoke.mockImplementation((channel, path) => {
      if (channel !== 'backend-workspace-path') return Promise.resolve(null)
      return path === hostPath ? Promise.resolve(backendPath) : mapping.promise
    })
    const { result, rerender } = mount()
    await act(async () => {})
    rerender({ workspace: 'new-project' })
    await act(async () => {
      const created = result.current.createNewConversation('Pending')
      expect(axios.post).toHaveBeenCalledExactlyOnceWith('http://backend/conversations',
        { user_id: 1, title: 'Pending' })
      expect(await created).toBe(42)
    })
    await act(async () => { mapping.resolve('new-backend') })
  })

  it('clears a resolved stamp when the open folder is closed', async () => {
    const { result, rerender } = mount()
    await act(async () => {})
    rerender({ workspace: null })
    await act(async () => { expect(await result.current.createNewConversation('Title')).toBe(42) })
    expect(axios.post).toHaveBeenCalledExactlyOnceWith('http://backend/conversations',
      { user_id: 1, title: 'Title' })
  })

  it.each(['null', 'empty', 'rejected'])('still creates the chat when mapping is %s', async failure => {
    mocks.invoke.mockImplementation(async channel => {
      if (channel !== 'backend-workspace-path') return null
      if (failure === 'rejected') throw new Error('mapping failed')
      return failure === 'null' ? null : ''
    })
    const { result } = mount()
    await act(async () => {})
    await act(async () => { expect(await result.current.createNewConversation('Title')).toBe(42) })
    expect(mocks.invoke).toHaveBeenCalledWith('backend-workspace-path', hostPath)
    expect(axios.post).toHaveBeenCalledExactlyOnceWith('http://backend/conversations',
      { user_id: 1, title: 'Title' })
  })

  it('omits workspace when there is no open folder', async () => {
    const { result } = mount(null)
    await act(async () => { expect(await result.current.createNewConversation('Title')).toBe(42) })
    expect(mocks.invoke).not.toHaveBeenCalledWith('backend-workspace-path', expect.anything())
    expect(axios.post).toHaveBeenCalledExactlyOnceWith('http://backend/conversations',
      { user_id: 1, title: 'Title' })
  })
})
