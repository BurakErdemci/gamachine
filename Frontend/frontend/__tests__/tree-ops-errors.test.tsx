/**
 * Rename, delete and move go through the same failure path as create: a rejected IPC call and a
 * success:false result both show the error toast, keep the rename box open and skip the refresh.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'

vi.mock('axios', () => ({
  __esModule: true,
  default: { get: vi.fn().mockResolvedValue({ data: {} }), post: vi.fn().mockResolvedValue({ data: {} }) },
}))
vi.mock('../renderer/components/ui/ConfirmDialog', () => ({ confirmDialog: vi.fn().mockResolvedValue(true) }))

const invoke = vi.hoisted(() => {
  const fn = vi.fn()
  ;(globalThis as any).window.ipc = { invoke: fn }
  return fn
})

import { useFileSystem } from '../renderer/hooks/home/useFileSystem'

type Mode = 'success' | 'failed' | 'failed-no-error' | 'rejected'
const OPS = ['rename-entry', 'delete-entry', 'move-entry']

const arrange = async (channel: string, mode: Mode) => {
  invoke.mockReset()
  invoke.mockImplementation(async (ch: string) => {
    if (ch === 'path-exists') return true
    if (ch === 'read-directory') return []
    if (ch !== channel) return undefined
    if (mode === 'rejected') throw new Error('injected EIO')
    if (mode === 'failed') return { success: false, error: 'Bu isimde bir dosya zaten var.' }
    if (mode === 'failed-no-error') return { success: false }
    return { success: true, newPath: '/ws/new.md' }
  })
  const toast = vi.fn()
  const h = renderHook(() => useFileSystem('http://x', null, toast))
  await act(async () => { await h.result.current.selectWorkspace('/ws') })
  invoke.mockClear()
  return { h, toast }
}

const refreshed = () => invoke.mock.calls.some(c => c[0] === 'read-directory')
const entry = { name: 'old.md', path: '/ws/old.md', isDirectory: false } as any
const folder = { name: 'dst', path: '/ws/dst', isDirectory: true } as any

const run = async (channel: string, h: any) => {
  if (channel === 'rename-entry') {
    act(() => { h.result.current.startRename(entry); h.result.current.setRenameValue('exists.md') })
    await act(async () => { await h.result.current.submitRename() })
  } else if (channel === 'delete-entry') {
    await act(async () => { await h.result.current.handleTreeDelete(entry) })
  } else {
    act(() => { h.result.current.handleTreeDragStart({ stopPropagation() {}, dataTransfer: { setData() {} } }, entry) })
    await act(async () => {
      await h.result.current.handleTreeDrop({ preventDefault() {}, stopPropagation() {} }, folder)
    })
  }
}

describe.each(OPS)('%s failure handling', (channel) => {
  beforeEach(() => invoke.mockReset())

  it.each(['failed', 'failed-no-error', 'rejected'] as Mode[])('%s: toast shown, no refresh, no throw', async (mode) => {
    const { h, toast } = await arrange(channel, mode)
    await run(channel, h)
    expect(toast).toHaveBeenCalledTimes(1)
    expect(toast).toHaveBeenCalledWith(expect.any(String), 'error')
    if (mode === 'failed') expect(toast.mock.calls[0][0]).toContain('Bu isimde bir dosya zaten var.')
    if (mode === 'rejected') expect(toast.mock.calls[0][0]).toContain('injected EIO')
    expect(refreshed()).toBe(false)
    if (channel === 'rename-entry') expect(h.result.current.renamingPath).toBe('/ws/old.md')
  })

  it('success: no toast, tree refreshed, rename box closed', async () => {
    const { h, toast } = await arrange(channel, 'success')
    await run(channel, h)
    expect(toast).not.toHaveBeenCalled()
    expect(refreshed()).toBe(true)
    if (channel === 'rename-entry') expect(h.result.current.renamingPath).toBeNull()
  })
})
