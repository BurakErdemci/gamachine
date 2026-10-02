import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook } from '@testing-library/react'

const mocks = vi.hoisted(() => {
  const invoke = vi.fn()
  ;(globalThis as any).window.ipc = { invoke, on: vi.fn(() => () => {}) }
  return { invoke }
})
vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() } }))

import axios from 'axios'
import { useChat } from '../renderer/hooks/home/useChat'
import type { AIConfig } from '../renderer/components/home/types'

const hostPath = ['host', 'project'].join('/')
const backendPath = ['backend', 'project'].join('/')
const user = { id: 1, name: 'User', sessionToken: 'token' }
const config = { provider_type: 'api', model_name: 'test' } as AIConfig
const mount = (workspace: string | null = hostPath) => renderHook(() =>
  useChat('http://backend', user, config, workspace, vi.fn(), vi.fn(), name => name))

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
  it('sends the backend-mapped workspace and uses the current workspace after rerender', async () => {
    const { result, rerender } = renderHook(({ workspace }) =>
      useChat('http://backend', user, config, workspace, vi.fn(), vi.fn(), name => name),
    { initialProps: { workspace: 'old-project' } })
    rerender({ workspace: hostPath })
    await act(async () => { expect(await result.current.createNewConversation('Title')).toBe(42) })
    expect(mocks.invoke).toHaveBeenCalledWith('backend-workspace-path', hostPath)
    expect(axios.post).toHaveBeenCalledExactlyOnceWith('http://backend/conversations',
      { user_id: 1, title: 'Title', workspace: backendPath })
  })

  it.each(['null', 'empty', 'rejected'])('still creates the chat when mapping is %s', async failure => {
    mocks.invoke.mockImplementation(async channel => {
      if (channel !== 'backend-workspace-path') return null
      if (failure === 'rejected') throw new Error('mapping failed')
      return failure === 'null' ? null : ''
    })
    const { result } = mount()
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
