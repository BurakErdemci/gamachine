/**
 * Opening another file or a preview replaces the editor buffer. With unsaved edits that used to
 * drop them silently; "Open in panel" and tool "Details" in the chat made it easy to hit (P2
 * audit). The open paths now ask through the app's confirm dialog first.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'

vi.mock('axios', () => ({
  __esModule: true,
  default: { get: vi.fn().mockResolvedValue({ data: {} }), post: vi.fn().mockResolvedValue({ data: {} }) },
}))

const confirm = vi.hoisted(() => vi.fn())
vi.mock('../renderer/components/ui/ConfirmDialog', () => ({ confirmDialog: confirm }))

const invoke = vi.hoisted(() => {
  const fn = vi.fn()
  ;(globalThis as any).window.ipc = { invoke: fn }
  return fn
})

import { useFileSystem } from '../renderer/hooks/home/useFileSystem'

const mount = () => renderHook(() => useFileSystem('http://x', null, () => {}))

const openDirty = async () => {
  const h = mount()
  invoke.mockResolvedValueOnce({ path: 'Assets/A.cs', content: 'class A {}' })
  await act(async () => { await h.result.current.openFile('Assets/A.cs') })
  act(() => { h.result.current.setCode('class A { int edited; }') })
  expect(h.result.current.isDirty).toBe(true)
  return h
}

describe('opening over unsaved edits', () => {
  beforeEach(() => { invoke.mockReset(); confirm.mockReset() })

  it('a clean buffer opens without asking', async () => {
    const h = mount()
    invoke.mockResolvedValueOnce({ path: 'Assets/A.cs', content: 'a' })
    await act(async () => { await h.result.current.openFile('Assets/A.cs') })
    invoke.mockResolvedValueOnce({ path: 'Assets/B.cs', content: 'b' })
    await act(async () => { await h.result.current.openFile('Assets/B.cs') })
    expect(confirm).not.toHaveBeenCalled()
    expect(h.result.current.openedFilePath).toBe('Assets/B.cs')
  })

  it('declining keeps the edits and the open file', async () => {
    const h = await openDirty()
    confirm.mockResolvedValue(false)
    await act(async () => { await h.result.current.openFile('Assets/B.cs') })
    expect(confirm).toHaveBeenCalledTimes(1)
    expect(h.result.current.openedFilePath).toBe('Assets/A.cs')
    expect(h.result.current.code).toBe('class A { int edited; }')
  })

  it('accepting opens the other file', async () => {
    const h = await openDirty()
    confirm.mockResolvedValue(true)
    invoke.mockResolvedValueOnce({ path: 'Assets/B.cs', content: 'b' })
    await act(async () => { await h.result.current.openFile('Assets/B.cs') })
    expect(h.result.current.openedFilePath).toBe('Assets/B.cs')
  })

  it('a preview asks too, and declining keeps the buffer', async () => {
    const h = await openDirty()
    confirm.mockResolvedValue(false)
    await act(async () => { h.result.current.openPreview('Assets/hero.fbx'); await Promise.resolve() })
    expect(confirm).toHaveBeenCalledTimes(1)
    expect(h.result.current.previewFile).toBeNull()
    expect(h.result.current.code).toBe('class A { int edited; }')
  })

  it('re-opening the dirty file itself only shows it: no question, no re-read', async () => {
    const h = await openDirty()
    invoke.mockClear()
    await act(async () => { await h.result.current.openFile('Assets/A.cs') })
    expect(confirm).not.toHaveBeenCalled()
    expect(invoke).not.toHaveBeenCalledWith('read-file', expect.anything(), expect.anything())
    expect(h.result.current.code).toBe('class A { int edited; }')
  })
})
