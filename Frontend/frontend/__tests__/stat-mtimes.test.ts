import { describe, expect, it, vi } from 'vitest'
import { collectMtimes } from '../main/helpers/stat-mtimes'

describe('collectMtimes', () => {
  it('runs concurrent chunks, waiting for each chunk before starting the next', async () => {
    let active = 0
    let peak = 0
    const finished: string[] = []
    const stat = vi.fn(async (p: string) => {
      if (p === 'File2') expect(finished).toEqual(['File0', 'File1'])
      if (p === 'File4') expect(finished).toEqual(['File0', 'File1', 'File2', 'File3'])
      active++
      peak = Math.max(peak, active)
      await Promise.resolve()
      active--
      finished.push(p)
      return { mtimeMs: 12.9 }
    })
    const entries: Array<[string, string]> = Array.from({ length: 5 }, (_, i) => [`key${i}`, `File${i}`])
    expect(await collectMtimes(entries, stat, 2)).toEqual({ key0: 12, key1: 12, key2: 12, key3: 12, key4: 12 })
    expect(peak).toBe(2)
    expect(stat.mock.calls.map(([p]) => p)).toEqual(entries.map(([, p]) => p))
  })

  it('defaults to at most 64 simultaneous stats', async () => {
    let active = 0
    let peak = 0
    const stat = async () => {
      active++
      peak = Math.max(peak, active)
      await Promise.resolve()
      active--
      return { mtimeMs: 1 }
    }
    const entries: Array<[string, string]> = Array.from({ length: 130 }, (_, i) => [`key${i}`, `File${i}`])
    expect(Object.keys(await collectMtimes(entries, stat))).toHaveLength(130)
    expect(peak).toBe(64)
  })

  it('omits rejected stats and floors successful mtimes under the supplied key', async () => {
    const stat = vi.fn(async (p: string) => {
      if (p === 'Deleted.cs') throw new Error('missing')
      return { mtimeMs: 123.987 }
    })
    expect(await collectMtimes([['mixed.cs', 'Mixed.cs'], ['deleted.cs', 'Deleted.cs']], stat, 1))
      .toEqual({ 'mixed.cs': 123 })
    expect(stat).toHaveBeenCalledWith('Mixed.cs')
  })

  it('does not stat anything for an empty list', async () => {
    const stat = vi.fn()
    expect(await collectMtimes([], stat)).toEqual({})
    expect(stat).not.toHaveBeenCalled()
  })
})
