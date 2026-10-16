import { createRequire } from 'module'
import path from 'node:path'
import { describe, expect, it, vi } from 'vitest'

const require = createRequire(import.meta.url)
const hook = require('../scripts/adhoc-sign-mac.js')
const { adhocSignMac } = hook
const plistBuddy = path.join(path.sep, 'usr', 'libexec', 'PlistBuddy')

function fixture() {
  const context = {
    electronPlatformName: 'darwin',
    appOutDir: path.join('build', 'mac-arm64'),
    packager: {
      appInfo: { productFilename: 'Gamachine', id: 'com.gamachine.app' },
      platformSpecificBuildOptions: { identity: null as string | null | undefined },
    },
  }
  const appPath = path.join(context.appOutDir, 'Gamachine.app')
  const frameworksDir = path.join(appPath, 'Contents', 'Frameworks')
  const readdirSync = vi.fn(() => [
    'Zulu Helper.app', 'zeta.framework', 'README', 'Alpha Helper.app',
    'alpha.framework', 'framework.framework.map', 'helper.app.backup',
  ])
  const execFileSync = vi.fn((command: string, args: string[], _options: object) => {
    if (command === plistBuddy) {
      return args[2] === path.join(frameworksDir, 'Alpha Helper.app', 'Contents', 'Info.plist')
        ? '  com.gamachine.app.helper.alpha\n' : 'com.gamachine.app.helper.zulu\n'
    }
    return undefined
  })
  const log = vi.fn()
  const deps = { readdirSync, execFileSync, log }
  const sign = (target: string, id?: string) => [
    'codesign', ['--force', '--sign', '-', ...(id ? ['--identifier', id] : []), target],
    { stdio: 'inherit' },
  ]
  const readId = (helper: string) => [
    plistBuddy, ['-c', 'Print CFBundleIdentifier', path.join(frameworksDir, helper, 'Contents', 'Info.plist')],
    { encoding: 'utf8' },
  ]
  const expectedCalls = [
    sign(path.join(frameworksDir, 'alpha.framework')),
    sign(path.join(frameworksDir, 'zeta.framework')),
    readId('Alpha Helper.app'),
    sign(path.join(frameworksDir, 'Alpha Helper.app'), 'com.gamachine.app.helper.alpha'),
    readId('Zulu Helper.app'),
    sign(path.join(frameworksDir, 'Zulu Helper.app'), 'com.gamachine.app.helper.zulu'),
    sign(appPath, context.packager.appInfo.id),
    ['codesign', ['--verify', appPath], { stdio: 'inherit' }],
  ]
  return { context, appPath, frameworksDir, deps, expectedCalls }
}

describe('adhoc-sign-mac (offline, no real subprocesses)', () => {
  it('exports a callable electron-builder hook and the named test entry point', () => {
    expect(typeof hook).toBe('function')
    expect(hook).toBe(adhocSignMac)
    expect(() => hook({ electronPlatformName: 'linux' })).not.toThrow()
  })

  it.each(['win32', 'mas', 'linux'])('does nothing on %s', electronPlatformName => {
    const f = fixture()
    adhocSignMac({ electronPlatformName }, f.deps)
    expect(f.deps.readdirSync).not.toHaveBeenCalled()
    expect(f.deps.execFileSync).not.toHaveBeenCalled()
    expect(f.deps.log).not.toHaveBeenCalled()
  })

  it.each(['Developer ID Application: Example', undefined])('does nothing with identity %s', identity => {
    const f = fixture()
    f.context.packager.platformSpecificBuildOptions.identity = identity
    adhocSignMac(f.context, f.deps)
    expect(f.deps.readdirSync).not.toHaveBeenCalled()
    expect(f.deps.execFileSync).not.toHaveBeenCalled()
    expect(f.deps.log).not.toHaveBeenCalled()
  })

  it('signs sorted frameworks, sorted helpers, the app, then verifies, ignoring other entries', () => {
    const f = fixture()
    adhocSignMac(f.context, f.deps)
    expect(f.deps.readdirSync).toHaveBeenCalledExactlyOnceWith(f.frameworksDir)
    expect(f.deps.execFileSync.mock.calls).toEqual(f.expectedCalls)
    const resources = path.sep + path.join('Contents', 'Resources') + path.sep
    for (const [, args] of f.deps.execFileSync.mock.calls) {
      expect(args).not.toContain('--deep')
      expect(args.some(arg => arg.includes(resources))).toBe(false)
    }
    expect(f.deps.log).toHaveBeenCalledTimes(f.expectedCalls.length)
    for (const [message] of f.deps.log.mock.calls) {
      expect(typeof message).toBe('string')
      expect(message.trim()).not.toBe('')
      expect(message).not.toContain('\n')
    }
  })

  it('uses the current architecture, product filename and app ID from the context', () => {
    const f = fixture()
    f.context.appOutDir = path.join('build', 'mac-x64')
    f.context.packager.appInfo = { productFilename: 'Other Product', id: 'com.example.other' }
    f.deps.readdirSync.mockReturnValue([])
    const appPath = path.join(f.context.appOutDir, 'Other Product.app')
    adhocSignMac(f.context, f.deps)
    expect(f.deps.readdirSync).toHaveBeenCalledExactlyOnceWith(path.join(appPath, 'Contents', 'Frameworks'))
    expect(f.deps.execFileSync.mock.calls).toEqual([
      ['codesign', ['--force', '--sign', '-', '--identifier', 'com.example.other', appPath], { stdio: 'inherit' }],
      ['codesign', ['--verify', appPath], { stdio: 'inherit' }],
    ])
  })

  it('rejects an empty helper identifier before signing the helper or main app', () => {
    const f = fixture()
    f.deps.execFileSync.mockImplementation(() => ' \n\t')
    expect(() => adhocSignMac(f.context, f.deps)).toThrow('Alpha Helper.app')
    expect(f.deps.execFileSync.mock.calls).toEqual(f.expectedCalls.slice(0, 3))
  })

  it.each([0, 2, 3, 6, 7])('propagates the original subprocess failure at step %s and stops', failureIndex => {
    const f = fixture()
    const failure = new Error('subprocess failed')
    const originalExec = f.deps.execFileSync.getMockImplementation()!
    let calls = 0
    f.deps.execFileSync.mockImplementation((...args) => {
      if (calls++ === failureIndex) throw failure
      return originalExec(...args)
    })
    expect(() => adhocSignMac(f.context, f.deps)).toThrow(failure)
    expect(f.deps.execFileSync.mock.calls).toEqual(f.expectedCalls.slice(0, failureIndex + 1))
  })
})
