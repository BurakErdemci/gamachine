/**
 * Class: stale-workspace-selection.
 *
 * `selectWorkspace` awaits IPC at every step, so a close can land in the middle
 * of one. Closing is a decision about the workspace and must outrank the
 * selection still running: otherwise the pending selection commits afterwards
 * and reopens the workspace the user just closed, with no error on either side.
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

const mount = () => renderHook(() => useFileSystem('', null, () => {}))

const ertelenmis = <T,>() => {
  let coz!: (v: T) => void
  const sozu = new Promise<T>(r => { coz = r })
  return { sozu, coz }
}

describe('stale-workspace-selection', () => {
  beforeEach(() => { invoke.mockReset() })

  it('does not reopen a workspace whose existence check returns after the close', async () => {
    const varlik = ertelenmis<boolean>()
    invoke.mockImplementation((channel: string) => {
      if (channel === 'path-exists') return varlik.sozu
      if (channel === 'read-directory') return Promise.resolve([])
      throw new Error(`unexpected IPC channel: ${channel}`)
    })

    const { result } = mount()
    const seciliyor = result.current.selectWorkspace('/workspace')
    await Promise.resolve()
    act(() => { result.current.closeWorkspace() })

    await act(async () => {
      varlik.coz(true)
      await seciliyor
    })

    expect(result.current.workspacePath).toBeNull()
  })

  it('does not refill the file tree from a directory read that returns after the close', async () => {
    const okuma = ertelenmis<any[]>()
    invoke.mockImplementation((channel: string) => {
      if (channel === 'path-exists') return Promise.resolve(true)
      if (channel === 'read-directory') return okuma.sozu
      throw new Error(`unexpected IPC channel: ${channel}`)
    })

    const { result } = mount()
    let seciliyor!: Promise<boolean>
    await act(async () => {
      seciliyor = result.current.selectWorkspace('/workspace')
      await Promise.resolve()
      await Promise.resolve()
    })
    act(() => { result.current.closeWorkspace() })

    await act(async () => {
      okuma.coz([{ name: 'A.cs', path: 'A.cs', isDirectory: false }])
      await seciliyor
    })

    expect(result.current.workspacePath).toBeNull()
    expect(result.current.fileTree).toEqual([])
  })

  it('still selects normally when no close intervenes', async () => {
    invoke.mockImplementation(async (channel: string) => {
      if (channel === 'path-exists') return true
      if (channel === 'read-directory') return [{ name: 'A.cs', path: 'A.cs', isDirectory: false }]
      throw new Error(`unexpected IPC channel: ${channel}`)
    })

    const { result } = mount()
    await act(async () => { await result.current.selectWorkspace('/workspace') })

    expect(result.current.workspacePath).toBe('/workspace')
    expect(result.current.fileTree.map((e: any) => e.name)).toEqual(['A.cs'])
  })
})
