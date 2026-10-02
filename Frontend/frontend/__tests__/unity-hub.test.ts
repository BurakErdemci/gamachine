import path from 'path'
import { describe, expect, it, vi } from 'vitest'
import { findUnityHub } from '../main/helpers/unity-hub'

describe('Unity Hub discovery', () => {
  const env = { ProgramFiles: 'C:\\PF', 'ProgramFiles(x86)': 'C:\\PF86', LOCALAPPDATA: 'C:\\Local' }
  const windows = [
    path.win32.join(env.ProgramFiles, 'Unity Hub', 'Unity Hub.exe'),
    path.win32.join(env['ProgramFiles(x86)'], 'Unity Hub', 'Unity Hub.exe'),
    path.win32.join(env.LOCALAPPDATA, 'Programs', 'Unity Hub', 'Unity Hub.exe'),
  ]

  it.each([0, 1, 2])('finds Windows candidate %s in order', index => {
    const exists = vi.fn((p: string) => p === windows[index])
    expect(findUnityHub('win32', env, 'unused', exists)).toBe(windows[index])
    expect(exists.mock.calls.map(args => args[0])).toEqual(windows.slice(0, index + 1))
  })

  it('skips unset Windows environment variables', () => {
    const exists = vi.fn(() => true)
    expect(findUnityHub('win32', { LOCALAPPDATA: env.LOCALAPPDATA }, '', exists)).toBe(windows[2])
    expect(exists).toHaveBeenCalledExactlyOnceWith(windows[2])
    exists.mockClear()
    expect(findUnityHub('win32', {}, '', exists)).toBeNull()
    expect(exists).not.toHaveBeenCalled()
  })

  it.each([0, 1])('finds macOS candidate %s in order', index => {
    const home = path.posix.join(path.posix.sep, 'Users', 'tester')
    const candidates = [path.posix.join(path.posix.sep, 'Applications', 'Unity Hub.app'),
      path.posix.join(home, 'Applications', 'Unity Hub.app')]
    const exists = vi.fn((p: string) => p === candidates[index])
    expect(findUnityHub('darwin', {}, home, exists)).toBe(candidates[index])
    expect(exists.mock.calls.map(args => args[0])).toEqual(candidates.slice(0, index + 1))
  })

  it('returns null when no candidate exists', () => {
    expect(findUnityHub('win32', env, '', () => false)).toBeNull()
    expect(findUnityHub('darwin', {}, '', () => false)).toBeNull()
  })

  it('does not probe other platforms', () => {
    const exists = vi.fn(() => true)
    expect(findUnityHub('linux', env, '', exists)).toBeNull()
    expect(exists).not.toHaveBeenCalled()
  })
})
