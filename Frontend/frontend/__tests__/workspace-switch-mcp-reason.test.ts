import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook } from '@testing-library/react'

const mocks = vi.hoisted(() => {
  const invoke = vi.fn()
  ;(window as any).ipc = { invoke }
  return { invoke }
})
vi.mock('axios', () => ({ default: { post: vi.fn() } }))
vi.mock('../renderer/lib/backendWorkspacePath', () => ({
  backendWorkspacePath: vi.fn(async (path: string) => path),
  hostWorkspacePath: vi.fn(async (path: string) => path),
}))

import axios from 'axios'
import { useFileSystem } from '../renderer/hooks/home/useFileSystem'
import type { UserData } from '../renderer/components/home/types'

const user = { id: 1, sessionToken: 'token' } as UserData

beforeEach(() => {
  vi.mocked(axios.post).mockReset()
  mocks.invoke.mockReset().mockImplementation(async (channel: string) => {
    if (channel === 'path-exists') return true
    if (channel === 'read-directory') return []
    return null
  })
})
afterEach(cleanup)

it('shows one warning with the reason when the workspace switch turns MCP off', async () => {
  const reason = "Unity'de bu proje açık değil: BenchmarkTest4."
  vi.mocked(axios.post).mockResolvedValue({ data: { status: 'success', mcp: { state: 'off', reason } } })
  const showToast = vi.fn()
  const { result } = renderHook(() => useFileSystem('backend', user, showToast))
  await act(async () => { await result.current.selectWorkspace('BenchmarkTest4') })
  expect(axios.post).toHaveBeenCalledExactlyOnceWith('backend/save-workspace',
    { user_id: 1, path: 'BenchmarkTest4' }, { headers: { 'X-Session-Token': 'token' } })
  expect(showToast).toHaveBeenCalledExactlyOnceWith(reason, 'warning')
})

it.each([
  undefined, null, {}, { mcp: null },
  { mcp: { state: 'switched', reason: 'Already connected' } },
  { mcp: { state: 'off' } },
  { mcp: { state: 'off', reason: '' } },
  { mcp: { state: 'off', reason: 42 } },
  { mcp: { state: 'off', reason: {} } },
])('ignores a response without an MCP refusal reason: %j', async data => {
  vi.mocked(axios.post).mockResolvedValue({ data })
  const showToast = vi.fn()
  const { result } = renderHook(() => useFileSystem('backend', user, showToast))
  await act(async () => { await result.current.selectWorkspace('BenchmarkTest4') })
  expect(axios.post).toHaveBeenCalledOnce()
  expect(showToast).not.toHaveBeenCalled()
})
