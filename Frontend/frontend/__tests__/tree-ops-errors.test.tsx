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

describe('write-file failure handling (saveFile and export)', () => {
  type WriteMode = 'ok' | 'failed' | 'rejected'
  const writeImpl = (modes: WriteMode[]) => {
    let i = 0
    invoke.mockReset()
    invoke.mockImplementation(async (ch: string) => {
      if (ch === 'path-exists') return true
      if (ch === 'read-directory') return []
      if (ch === 'read-file') return { content: 'x', path: '/ws/a.md' }
      if (ch !== 'write-file') return undefined
      const m = modes[i++] ?? 'ok'
      if (m === 'rejected') throw new Error('injected EIO-write')
      if (m === 'failed') return { success: false, error: 'injected EACCES' }
      return { success: true }
    })
  }
  const mount = async (modes: WriteMode[]) => {
    writeImpl(modes)
    const toast = vi.fn()
    const h = renderHook(() => useFileSystem('http://x', null, toast))
    await act(async () => { await h.result.current.selectWorkspace('/ws') })
    invoke.mockClear()
    return { h, toast }
  }
  const openModal = (h: any, multi: boolean) => {
    const files = [{ name: 'A.cs', code: 'a', path: '/ws/Assets/Scripts/A.cs' }, { name: 'B.cs', code: 'b', path: '/ws/Assets/Scripts/B.cs' }]
    act(() => {
      h.result.current.setExportModal({
        isOpen: true, codeString: 'a', suggestedName: 'A.cs', targetDir: '/ws/Assets/Scripts',
        existingFile: false, multiFile: multi, files: multi ? files : [files[0]], exportResult: null,
      })
    })
  }
  const writes = () => invoke.mock.calls.filter(c => c[0] === 'write-file').length

  it.each(['failed', 'rejected'] as WriteMode[])('saveFile %s: returns false and toasts the error', async (mode) => {
    const { h, toast } = await mount([mode])
    await act(async () => { await h.result.current.openFile('/ws/a.md') })
    toast.mockClear()
    act(() => { h.result.current.setCode('edited') })
    let ok: any
    await act(async () => { ok = await h.result.current.saveFile() })
    expect(ok).toBe(false)
    expect(toast).toHaveBeenCalledTimes(1)
    expect(toast).toHaveBeenCalledWith(expect.stringContaining(mode === 'failed' ? 'injected EACCES' : 'injected EIO-write'), 'error')
  })

  it.each(['failed', 'rejected'] as WriteMode[])('exportSingleFile %s: failed result, no toast, no refresh', async (mode) => {
    const { h, toast } = await mount([mode])
    openModal(h, false)
    await act(async () => { await h.result.current.exportSingleFile() })
    expect(h.result.current.exportModal.exportResult.success).toBe(false)
    expect(toast).not.toHaveBeenCalled()
    expect(refreshed()).toBe(false)
  })

  it('exportMultipleFiles all fail: error result and toast, no refresh', async () => {
    const { h, toast } = await mount(['failed', 'rejected'])
    openModal(h, true)
    await act(async () => { await h.result.current.exportMultipleFiles() })
    expect(writes()).toBe(2)
    expect(h.result.current.exportModal.exportResult.success).toBe(false)
    expect(toast).toHaveBeenCalledTimes(1)
    expect(toast).toHaveBeenCalledWith(expect.stringContaining('injected EACCES'), 'error')
    expect(refreshed()).toBe(false)
  })

  it('exportMultipleFiles some fail: failure count reported, tree refreshed', async () => {
    const { h, toast } = await mount(['ok', 'failed'])
    openModal(h, true)
    await act(async () => { await h.result.current.exportMultipleFiles() })
    expect(h.result.current.exportModal.exportResult.success).toBe(false)
    expect(toast).toHaveBeenCalledTimes(1)
    expect(toast).toHaveBeenCalledWith(expect.stringMatching(/1.*injected EACCES/), 'error')
    expect(refreshed()).toBe(true)
  })

  it('exportMultipleFiles none fail: success result, success toast, refresh', async () => {
    const { h, toast } = await mount(['ok', 'ok'])
    openModal(h, true)
    await act(async () => { await h.result.current.exportMultipleFiles() })
    expect(h.result.current.exportModal.exportResult.success).toBe(true)
    expect(toast).toHaveBeenCalledTimes(1)
    expect(toast).toHaveBeenCalledWith(expect.any(String), 'success')
    expect(refreshed()).toBe(true)
  })
})
