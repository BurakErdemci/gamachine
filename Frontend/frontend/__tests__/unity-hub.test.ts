import path from 'path'
import { describe, expect, it, vi } from 'vitest'
import { UNITY_HUB_MSIX_FAMILY, UNITY_HUB_MSIX_URI, findUnityHub, unityHubMsixCache } from '../main/helpers/unity-hub'

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

  describe('MSIX package', () => {
    const pkg = path.win32.join(env.LOCALAPPDATA, 'Packages', UNITY_HUB_MSIX_FAMILY)

    it('returns the AppsFolder URI when only the package folder exists', () => {
      expect(UNITY_HUB_MSIX_URI).toBe('shell:AppsFolder\\UnityTechnologies.UnityHub_2vrhnee42bhxm!UnityHub')
      expect(findUnityHub('win32', env, '', p => p === pkg)).toBe(UNITY_HUB_MSIX_URI)
    })

    it('prefers a classic install over the MSIX package', () => {
      expect(findUnityHub('win32', env, '', p => p === pkg || p === windows[1])).toBe(windows[1])
    })

    it('does not probe the package without LOCALAPPDATA', () => {
      const exists = vi.fn((_p: string) => false)
      expect(findUnityHub('win32', { ProgramFiles: env.ProgramFiles }, '', exists)).toBeNull()
      expect(exists.mock.calls.map(args => args[0])).toEqual([windows[0]])
    })

    it('does not probe the package on macOS', () => {
      const exists = vi.fn((_p: string) => false)
      expect(findUnityHub('darwin', env, '/Users/t', exists)).toBeNull()
      expect(exists.mock.calls.every(args => !String(args[0]).includes('Packages'))).toBe(true)
    })

    it('derives the LocalCache folder only when LOCALAPPDATA is set', () => {
      expect(unityHubMsixCache(env)).toBe(`${pkg}\\LocalCache`)
      expect(unityHubMsixCache({})).toBeNull()
    })
  })

  it('does not probe other platforms', () => {
    const exists = vi.fn(() => true)
    expect(findUnityHub('linux', env, '', exists)).toBeNull()
    expect(exists).not.toHaveBeenCalled()
  })
})
