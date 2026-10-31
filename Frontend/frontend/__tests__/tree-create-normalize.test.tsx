/**
 * The tree "new file/folder" form sends what the user typed. The main process refuses any
 * '..' segment, so the hook normalizes the name first, keeps the form open on a name that
 * leaves the parent, and reports an IPC failure instead of closing silently.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'

vi.mock('axios', () => ({
  __esModule: true,
  default: { get: vi.fn().mockResolvedValue({ data: {} }), post: vi.fn().mockResolvedValue({ data: {} }) },
}))

const invoke = vi.hoisted(() => {
  const fn = vi.fn()
  ;(globalThis as any).window.ipc = { invoke: fn }
  return fn
})

import { useFileSystem } from '../renderer/hooks/home/useFileSystem'

const submit = async (value: string, type: 'file' | 'folder' = 'file') => {
  const toast = vi.fn()
  const h = renderHook(() => useFileSystem('http://x', null, toast))
  act(() => {
    h.result.current.setTreeCreating({ parentPath: '/ws/dir', type } as any)
    h.result.current.setTreeCreateValue(value)
  })
  await act(async () => { await h.result.current.submitTreeCreate() })
  return { h, toast }
}

const created = () => invoke.mock.calls.filter(c => c[0] === 'create-file' || c[0] === 'create-folder')

describe('submitTreeCreate name normalization', () => {
  beforeEach(() => { invoke.mockReset(); invoke.mockResolvedValue({ success: true }) })

  it("sends 'sub/../new.txt' as parent/new.txt and closes the form", async () => {
    const { h } = await submit('sub/../new.txt')
    expect(created()[0]).toEqual(['create-file', '/ws/dir/new.txt', null])
    expect(h.result.current.treeCreating).toBeNull()
  })

  it("drops '.' and doubled separators, accepts backslashes", async () => {
    await submit('./a\\\\b//c.txt')
    expect(created()[0][1]).toBe('/ws/dir/a/b/c.txt')
  })

  it.each(['../x.txt', 'a/../../x.txt', '..', 'a/..', '.'])('%s leaves the form open with a message and sends nothing', async (v) => {
    const { h, toast } = await submit(v)
    expect(created()).toHaveLength(0)
    expect(h.result.current.treeCreating).not.toBeNull()
    expect(toast).toHaveBeenCalledWith(expect.any(String), 'warning')
  })

  it('shows the IPC error and keeps the form open on failure', async () => {
    invoke.mockResolvedValue({ success: false, error: 'boom' })
    const { h, toast } = await submit('x.txt')
    expect(h.result.current.treeCreating).not.toBeNull()
    expect(toast).toHaveBeenCalledWith(expect.stringContaining('boom'), 'error')
  })

  it.each(['file', 'folder'] as const)('shows the error and keeps the form open when %s create rejects', async (type) => {
    invoke.mockRejectedValue(Object.assign(new Error('EEXIST: file already exists'), { code: 'EEXIST' }))
    const { h, toast } = await submit('blocked/child', type)
    expect(created()).toHaveLength(1)
    expect(invoke.mock.calls.every(c => c[0] === 'create-file' || c[0] === 'create-folder')).toBe(true)
    expect(h.result.current.treeCreating).not.toBeNull()
    expect(toast).toHaveBeenCalledWith(expect.stringContaining('EEXIST'), 'error')
  })
})
