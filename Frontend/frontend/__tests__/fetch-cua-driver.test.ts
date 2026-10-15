import { createRequire } from 'module'
import { existsSync, readFileSync } from 'node:fs'
import path from 'path'
import { describe, expect, it, vi } from 'vitest'

const require = createRequire(import.meta.url)
const { fetchCuaDriver } = require('../scripts/fetch-cua-driver.js')
const digest = `sha256:${'a'.repeat(64)}`
const wheelMembers = [
  'cua_driver/__init__.py',
  'cua_driver/__main__.py',
  'cua_driver/_daemon.py',
  'cua_driver/_native.py',
  'cua_driver/_native_contract.py',
  'cua_driver/fleet.py',
  'cua_driver/wrapper.py',
  'cua_driver/libcua_driver_sdk.dylib',
  'cua_driver/bin/cua-driver',
  'cua_driver-0.35.4.dist-info/METADATA',
  'cua_driver-0.35.4.dist-info/WHEEL',
  'cua_driver-0.35.4.dist-info/entry_points.txt',
  'cua_driver-0.35.4.dist-info/RECORD',
]

function fixture(stamped = false, members = wheelMembers) {
  const root = path.resolve('fake-repo')
  const destination = path.join(root, 'Backend/vendor/cua')
  const files = new Map<string, any>([[path.join(root, 'scripts/pinned_assets.json'), JSON.stringify({ assets: {
    'cua-driver/macos-universal2': { url: 'https://offline.invalid/wheel.whl', digest, size: 100 },
  } })]])
  if (stamped) {
    files.set(path.join(destination, '.fetched'), digest)
    files.set(path.join(destination, 'cua-driver'), Buffer.from('binary'))
  }
  const fs = {
    readFileSync: vi.fn((p: string) => files.get(p)), existsSync: vi.fn((p: string) => files.has(p)),
    chmodSync: vi.fn(), mkdirSync: vi.fn(), mkdtempSync: vi.fn(() => path.resolve('fake-scratch')),
    writeFileSync: vi.fn((p: string, data: any) => { files.set(p, data) }),
    copyFileSync: vi.fn((source: string, target: string) => { files.set(target, files.get(source)) }),
    renameSync: vi.fn((source: string, target: string) => { files.set(target, files.get(source)); files.delete(source) }),
    rmSync: vi.fn(),
  }
  const download = vi.fn(async () => {})
  const execFileSync = vi.fn((command: string, args: string[]) => {
    if (command === 'python3') return undefined
    if (args[0] === '-Z1') return members.join('\n') + '\n'
    return Buffer.from('binary')
  })
  const options = { platform: 'darwin', repoRoot: root, fs, download, execFileSync, log: vi.fn() }
  return { files, destination, fs, download, execFileSync, options }
}

describe('fetch-cua-driver (offline, no real subprocesses)', () => {
  it('ships the upstream MIT license as a separate resource', () => {
    const license = require.resolve('../resources/licenses/cua-driver-LICENSE.md')
    expect(existsSync(license)).toBe(true)
    expect(readFileSync(license, 'utf8').startsWith('MIT License')).toBe(true)
  })
  it('is a no-op on other platforms', async () => {
    const f = fixture()
    await fetchCuaDriver({ ...f.options, platform: 'win32' })
    expect(f.fs.readFileSync).not.toHaveBeenCalled()
    expect(f.download).not.toHaveBeenCalled()
    expect(f.options.log).toHaveBeenCalled()
  })
  it('skips a matching complete stamp and retains executable permissions', async () => {
    const f = fixture(true)
    await fetchCuaDriver(f.options)
    expect(f.download).not.toHaveBeenCalled()
    expect(f.execFileSync).not.toHaveBeenCalled()
    expect(f.fs.chmodSync).toHaveBeenCalledWith(path.join(f.destination, 'cua-driver'), 0o755)
  })
  it.each(['stale', 'incomplete'])('fetches a %s stamp, verifies before reading wheel members and extracts only the binary', async condition => {
    const f = fixture(true)
    if (condition === 'stale') f.files.set(path.join(f.destination, '.fetched'), 'old-digest')
    else f.files.delete(path.join(f.destination, 'cua-driver'))
    await fetchCuaDriver(f.options)
    expect(f.download).toHaveBeenCalledTimes(1)
    expect(f.execFileSync.mock.calls[0]).toEqual(['python3', [expect.stringContaining('pinned_assets.py'),
      'verify', 'cua-driver/macos-universal2', expect.stringContaining('driver.whl')], { stdio: 'inherit' }])
    expect(f.execFileSync.mock.calls.map(([command, args]) => [command, args[0]])).toEqual([
      ['python3', expect.stringContaining('pinned_assets.py')], ['unzip', '-Z1'], ['unzip', '-p'],
    ])
    expect(f.execFileSync.mock.calls.filter(([, args]) => args[0] === '-p').map(([, args]) => args[2])).toEqual([
      'cua_driver/bin/cua-driver',
    ])
    expect(f.files.get(path.join(f.destination, 'cua-driver'))).toEqual(Buffer.from('binary'))
    expect(f.files.get(path.join(f.destination, '.fetched'))).toBe(digest + '\n')
    expect(f.fs.chmodSync).toHaveBeenCalledWith(path.join(f.destination, 'cua-driver.new'), 0o755)
    expect(f.fs.rmSync).toHaveBeenCalledExactlyOnceWith(path.resolve('fake-scratch'), { recursive: true, force: true })
  })
  it('rejects a wheel without the binary and removes scratch files without installing a binary', async () => {
    const f = fixture(false, wheelMembers.filter(member => member !== 'cua_driver/bin/cua-driver'))
    await expect(fetchCuaDriver(f.options)).rejects.toThrow('cua-driver wheel is missing the binary')
    expect(f.execFileSync.mock.calls.map(([command, args]) => [command, args[0]])).toEqual([
      ['python3', expect.stringContaining('pinned_assets.py')], ['unzip', '-Z1'],
    ])
    expect(f.files.has(path.join(f.destination, 'cua-driver'))).toBe(false)
    expect(f.fs.writeFileSync).not.toHaveBeenCalled()
    expect(f.fs.copyFileSync).not.toHaveBeenCalled()
    expect(f.fs.rmSync).toHaveBeenCalledExactlyOnceWith(path.resolve('fake-scratch'), { recursive: true, force: true })
  })
  it('integrity failure fails without extracting or writing a stamp', async () => {
    const f = fixture()
    f.execFileSync.mockImplementation(() => { throw new Error('integrity failure') })
    await expect(fetchCuaDriver(f.options)).rejects.toThrow('integrity failure')
    expect(f.execFileSync).toHaveBeenCalledTimes(1)
    expect(f.fs.writeFileSync).not.toHaveBeenCalled()
    expect(f.fs.rmSync).toHaveBeenCalledExactlyOnceWith(path.resolve('fake-scratch'), { recursive: true, force: true })
  })
  it('network failure fails and removes scratch files', async () => {
    const f = fixture()
    f.download.mockRejectedValue(new Error('offline'))
    await expect(fetchCuaDriver(f.options)).rejects.toThrow('offline')
    expect(f.execFileSync).not.toHaveBeenCalled()
    expect(f.fs.writeFileSync).not.toHaveBeenCalled()
    expect(f.fs.rmSync).toHaveBeenCalledExactlyOnceWith(path.resolve('fake-scratch'), { recursive: true, force: true })
  })
})
