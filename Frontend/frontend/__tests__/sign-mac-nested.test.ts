import { createRequire } from 'module'
import path from 'node:path'
import { describe, expect, it, vi } from 'vitest'

const require = createRequire(import.meta.url)
const hook = require('../scripts/sign-mac-nested.js')
const { signMacNested, isMachO, deepestFirst } = hook
const plistBuddy = path.join(path.sep, 'usr', 'libexec', 'PlistBuddy')

const MACHO_64 = [0xcf, 0xfa, 0xed, 0xfe, 0x0c, 0x00, 0x00, 0x01]
const FAT = [0xca, 0xfe, 0xba, 0xbe, 0x00, 0x00, 0x00, 0x02]
const JAVA_CLASS = [0xca, 0xfe, 0xba, 0xbe, 0x00, 0x00, 0x00, 0x34]
const TEXT = [...Buffer.from('#!/bin/sh\n')]

type Node = { [name: string]: Node | number[] | { link: string } }

// In-memory tree behind the four fs calls the hook makes; no real files are read.
function fakeFs(root: string, tree: Node) {
  const lookup = (p: string) => {
    let node: Node | number[] | { link: string } = { [path.basename(root)]: tree }
    for (const part of [path.basename(root), ...path.relative(root, p).split(path.sep).filter(Boolean)]) {
      node = (node as Node)[part]
      if (node === undefined) throw new Error(`ENOENT ${p}`)
    }
    return node
  }
  const fds = new Map<number, number[]>()
  return {
    readdirSync: vi.fn((dir: string, options?: { withFileTypes?: boolean }) => {
      const names = Object.keys(lookup(dir) as Node)
      if (!options?.withFileTypes) return names
      return names.map(name => {
        const node = (lookup(dir) as Node)[name]
        const isLink = !Array.isArray(node) && 'link' in node
        return {
          name,
          isSymbolicLink: () => isLink,
          isDirectory: () => !isLink && !Array.isArray(node),
          isFile: () => Array.isArray(node),
        }
      })
    }),
    openSync: vi.fn((file: string) => {
      const fd = fds.size + 3
      fds.set(fd, lookup(file) as number[])
      return fd
    }),
    readSync: vi.fn((fd: number, buffer: Buffer) => Buffer.from(fds.get(fd)!).copy(buffer, 0, 0, 8)),
    closeSync: vi.fn(),
  }
}

function fixture() {
  const appOutDir = path.join('build', 'mac-arm64')
  const appPath = path.join(appOutDir, 'Gamachine.app')
  const resources = path.join(appPath, 'Contents', 'Resources')
  const frameworksDir = path.join(appPath, 'Contents', 'Frameworks')
  const fs = fakeFs(appPath, {
    Contents: {
      Resources: {
        uv: { 'darwin-arm64': { uv: MACHO_64, uvx: MACHO_64 } },
        Backend: {
          backend: MACHO_64,
          'run_mcp_server.sh': TEXT,
          _internal: {
            'libz.dylib': FAT,
            'Squirrel.class': JAVA_CLASS,
            'Python.framework': { Python: { link: 'Versions/Current/Python' }, Versions: { '3.13': { Python: MACHO_64 } } },
          },
        },
        'icon.ico': [0, 0, 1, 0],
        'tiny.bin': [0xcf, 0xfa],
      },
      Frameworks: {
        'Zulu Helper.app': {}, 'zeta.framework': {}, README: TEXT, 'Alpha Helper.app': {}, 'alpha.framework': {},
      },
    },
  })
  const nested = [
    path.join(resources, 'Backend', '_internal', 'Python.framework', 'Versions', '3.13', 'Python'),
    path.join(resources, 'Backend', '_internal', 'libz.dylib'),
    path.join(resources, 'uv', 'darwin-arm64', 'uv'),
    path.join(resources, 'uv', 'darwin-arm64', 'uvx'),
    path.join(resources, 'Backend', 'backend'),
  ]
  const execFileSync = vi.fn((command: string, args: string[], _options: object) => {
    if (command === plistBuddy) {
      return args[2].includes('Alpha Helper.app') ? ' com.gamachine.app.helper.alpha\n' : 'com.gamachine.app.helper.zulu\n'
    }
    return undefined
  })
  const findIdentity = vi.fn(async (..._args: unknown[]) => null as null | { name: string, hash?: string })
  const context = {
    electronPlatformName: 'darwin',
    appOutDir,
    packager: {
      appInfo: { productFilename: 'Gamachine', id: 'com.gamachine.app' },
      projectDir: path.join(path.sep, 'repo', 'Frontend', 'frontend'),
      platformSpecificBuildOptions: {
        identity: undefined as string | null | undefined,
        entitlementsInherit: 'resources/entitlements.mac.plist' as string | undefined,
      },
      codeSigningInfo: { value: Promise.resolve({ keychainFile: '/tmp/csc.keychain' as string | null }) },
    },
  }
  const deps = { fs, execFileSync, findIdentity, log: vi.fn() }
  const adhoc = (target: string, id?: string) =>
    ['codesign', ['--force', '--sign', '-', ...(id ? ['--identifier', id] : []), target], { stdio: 'inherit' }]
  const readId = (helper: string) =>
    [plistBuddy, ['-c', 'Print CFBundleIdentifier', path.join(frameworksDir, helper, 'Contents', 'Info.plist')], { encoding: 'utf8' }]
  const adhocCalls = [
    ...nested.map(file => adhoc(file)),
    adhoc(path.join(frameworksDir, 'alpha.framework')),
    adhoc(path.join(frameworksDir, 'zeta.framework')),
    readId('Alpha Helper.app'),
    adhoc(path.join(frameworksDir, 'Alpha Helper.app'), 'com.gamachine.app.helper.alpha'),
    readId('Zulu Helper.app'),
    adhoc(path.join(frameworksDir, 'Zulu Helper.app'), 'com.gamachine.app.helper.zulu'),
    adhoc(appPath, 'com.gamachine.app'),
    ['codesign', ['--verify', appPath], { stdio: 'inherit' }],
  ]
  return { context, deps, nested, adhocCalls, appPath }
}

describe('isMachO', () => {
  it.each([
    ['64-bit little-endian', [0xcf, 0xfa, 0xed, 0xfe, 0, 0, 0, 0], true],
    ['32-bit little-endian', [0xce, 0xfa, 0xed, 0xfe, 0, 0, 0, 0], true],
    ['64-bit big-endian', [0xfe, 0xed, 0xfa, 0xcf, 0, 0, 0, 0], true],
    ['universal', FAT, true],
    ['universal 64', [0xca, 0xfe, 0xba, 0xbf, 0, 0, 0, 2], true],
    ['Java class (same magic)', JAVA_CLASS, false],
    ['shell script', TEXT, false],
    ['truncated', [0xcf, 0xfa, 0xed, 0xfe], false],
  ])('%s', (_name, bytes, expected) => {
    expect(isMachO(Buffer.from(bytes))).toBe(expected)
  })
})

describe('deepestFirst', () => {
  it('orders by depth, then by path', () => {
    const files = [path.join('a', 'x'), path.join('a', 'b', 'c', 'y'), path.join('a', 'b', 'z'), path.join('a', 'b', 'c', 'w')]
    expect(deepestFirst(files)).toEqual([
      path.join('a', 'b', 'c', 'w'), path.join('a', 'b', 'c', 'y'), path.join('a', 'b', 'z'), path.join('a', 'x'),
    ])
  })
})

describe('sign-mac-nested (offline, no real subprocesses)', () => {
  it('exports a callable electron-builder hook', async () => {
    expect(typeof hook).toBe('function')
    expect(hook).toBe(signMacNested)
    await expect(hook({ electronPlatformName: 'linux' })).resolves.toBeUndefined()
  })

  it.each(['win32', 'mas', 'linux'])('does nothing on %s', async electronPlatformName => {
    const f = fixture()
    await signMacNested({ electronPlatformName }, f.deps)
    expect(f.deps.findIdentity).not.toHaveBeenCalled()
    expect(f.deps.execFileSync).not.toHaveBeenCalled()
  })

  it('ad-hoc signs nested Mach-O deepest-first, then frameworks, helpers and the app when no identity is found', async () => {
    const f = fixture()
    await signMacNested(f.context, f.deps)
    expect(f.deps.findIdentity).toHaveBeenCalledExactlyOnceWith('Developer ID Application', undefined, '/tmp/csc.keychain')
    expect(f.deps.execFileSync.mock.calls).toEqual(f.adhocCalls)
    for (const [, args] of f.deps.execFileSync.mock.calls) {
      expect(args).not.toContain('--timestamp')
      expect(args).not.toContain('--options')
      expect(args).not.toContain('--deep')
    }
    // electron-builder must not re-sign the ad-hoc app with a certificate it discovers.
    expect(f.context.packager.platformSpecificBuildOptions.identity).toBeNull()
  })

  it('treats identity: null as ad-hoc without looking up a certificate', async () => {
    const f = fixture()
    f.context.packager.platformSpecificBuildOptions.identity = null
    await signMacNested(f.context, f.deps)
    expect(f.deps.findIdentity).not.toHaveBeenCalled()
    expect(f.deps.execFileSync.mock.calls).toEqual(f.adhocCalls)
  })

  it('stays ad-hoc when the discovered certificate is not a Developer ID', async () => {
    const f = fixture()
    f.deps.findIdentity.mockResolvedValue({ name: 'Apple Development: someone (TEAM)', hash: 'ABC' })
    await signMacNested(f.context, f.deps)
    expect(f.deps.execFileSync.mock.calls).toEqual(f.adhocCalls)
    expect(f.context.packager.platformSpecificBuildOptions.identity).toBeNull()
  })

  it('signs only nested Resources Mach-O with the Developer ID, hardened runtime, timestamp and entitlements', async () => {
    const f = fixture()
    f.context.packager.platformSpecificBuildOptions.identity = 'Example Co'
    f.deps.findIdentity.mockResolvedValue({ name: 'Developer ID Application: Example Co (TEAM)', hash: 'HASH123' })
    await signMacNested(f.context, f.deps)
    expect(f.deps.findIdentity).toHaveBeenCalledExactlyOnceWith('Developer ID Application', 'Example Co', '/tmp/csc.keychain')
    const entitlements = path.resolve(f.context.packager.projectDir, 'resources/entitlements.mac.plist')
    expect(f.deps.execFileSync.mock.calls).toEqual(f.nested.map(file => ['codesign', [
      '--force', '--options', 'runtime', '--timestamp', '--entitlements', entitlements,
      '--sign', 'HASH123', '--keychain', '/tmp/csc.keychain', file,
    ], { stdio: 'inherit' }]))
    expect(f.context.packager.platformSpecificBuildOptions.identity).toBe('Example Co')
  })

  it('uses the identity name and no --keychain when there is no temporary keychain', async () => {
    const f = fixture()
    f.context.packager.codeSigningInfo.value = Promise.resolve({ keychainFile: null })
    f.deps.findIdentity.mockResolvedValue({ name: 'Developer ID Application: Example Co (TEAM)' })
    await signMacNested(f.context, f.deps)
    const [, args] = f.deps.execFileSync.mock.calls[0]
    expect(args).not.toContain('--keychain')
    expect(args[args.indexOf('--sign') + 1]).toBe('Developer ID Application: Example Co (TEAM)')
  })

  it('refuses Developer ID signing without entitlementsInherit', async () => {
    const f = fixture()
    f.context.packager.platformSpecificBuildOptions.entitlementsInherit = undefined
    f.deps.findIdentity.mockResolvedValue({ name: 'Developer ID Application: Example Co (TEAM)' })
    await expect(signMacNested(f.context, f.deps)).rejects.toThrow('entitlementsInherit')
    expect(f.deps.execFileSync).not.toHaveBeenCalled()
  })

  it.each([0, 4, 5, 7, 11, 12])('fails the build at codesign/PlistBuddy step %s and stops', async failureIndex => {
    const f = fixture()
    const failure = new Error('subprocess failed')
    const original = f.deps.execFileSync.getMockImplementation()!
    let calls = 0
    f.deps.execFileSync.mockImplementation((...args) => {
      if (calls++ === failureIndex) throw failure
      return original(...args)
    })
    const run = signMacNested(f.context, f.deps)
    const [command, args] = f.adhocCalls[failureIndex] as [string, string[]]
    if (command === 'codesign') {
      await expect(run).rejects.toThrow(`codesign failed for ${args[args.length - 1]}: subprocess failed`)
    } else {
      await expect(run).rejects.toThrow(failure)
    }
    expect(f.deps.execFileSync.mock.calls).toEqual(f.adhocCalls.slice(0, failureIndex + 1))
  })

  it('fails the build when a Developer ID codesign call fails', async () => {
    const f = fixture()
    f.deps.findIdentity.mockResolvedValue({ name: 'Developer ID Application: Example Co (TEAM)' })
    f.deps.execFileSync.mockImplementation(() => { throw new Error('timestamp service unavailable') })
    await expect(signMacNested(f.context, f.deps)).rejects.toThrow(`codesign failed for ${f.nested[0]}: timestamp service unavailable`)
    expect(f.deps.execFileSync).toHaveBeenCalledTimes(1)
  })

  it('rejects an empty helper identifier before signing the helper or the app', async () => {
    const f = fixture()
    f.deps.execFileSync.mockImplementation((command: string) => (command === plistBuddy ? ' \n' : undefined) as never)
    await expect(signMacNested(f.context, f.deps)).rejects.toThrow('Alpha Helper.app')
    expect(f.deps.execFileSync.mock.calls).toEqual(f.adhocCalls.slice(0, 8))
  })
})
