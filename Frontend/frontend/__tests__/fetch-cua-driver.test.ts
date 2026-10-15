import { createRequire } from 'module'
import path from 'path'
import { describe, expect, it, vi } from 'vitest'

const require = createRequire(import.meta.url)
const { fetchCuaDriver } = require('../scripts/fetch-cua-driver.js')
const digest = `sha256:${'a'.repeat(64)}`

function fixture(stamped = false) {
  const root = path.resolve('fake-repo')
  const destination = path.join(root, 'Backend/vendor/cua')
  const files = new Map<string, any>([[path.join(root, 'scripts/pinned_assets.json'), JSON.stringify({ assets: {
    'cua-driver/macos-universal2': { url: 'https://offline.invalid/wheel.whl', digest, size: 100 },
  } })]])
  if (stamped) {
    files.set(path.join(destination, '.fetched'), digest)
    files.set(path.join(destination, 'cua-driver'), Buffer.from('binary'))
    files.set(path.join(destination, 'LICENSE'), 'MIT')
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
    if (args[0] === '-Z1') return 'cua_driver/bin/cua-driver\ncua_driver-0.35.4.dist-info/licenses/LICENSE\n'
    return Buffer.from(args[2].endsWith('LICENSE') ? 'MIT' : 'binary')
  })
  const options = { platform: 'darwin', repoRoot: root, fs, download, execFileSync, log: vi.fn() }
  return { files, destination, fs, download, execFileSync, options }
}

describe('fetch-cua-driver (offline, no real subprocesses)', () => {
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
  it.each(['stale', 'incomplete'])('fetches a %s stamp, verifies before extracting only binary and license', async condition => {
    const f = fixture(true)
    if (condition === 'stale') f.files.set(path.join(f.destination, '.fetched'), 'old-digest')
    else f.files.delete(path.join(f.destination, 'LICENSE'))
    await fetchCuaDriver(f.options)
    expect(f.download).toHaveBeenCalledTimes(1)
    expect(f.execFileSync.mock.calls[0]).toEqual(['python3', [expect.stringContaining('pinned_assets.py'),
      'verify', 'cua-driver/macos-universal2', expect.stringContaining('driver.whl')], { stdio: 'inherit' }])
    expect(f.execFileSync.mock.calls.filter(([, args]) => args[0] === '-p').map(([, args]) => args[2])).toEqual([
      'cua_driver/bin/cua-driver', 'cua_driver-0.35.4.dist-info/licenses/LICENSE',
    ])
    expect(f.files.get(path.join(f.destination, '.fetched'))).toBe(digest + '\n')
    expect(f.fs.chmodSync).toHaveBeenCalledWith(path.join(f.destination, 'cua-driver.new'), 0o755)
    expect(f.fs.rmSync).toHaveBeenCalledTimes(1)
  })
  it('integrity failure fails without extracting or writing a stamp', async () => {
    const f = fixture()
    f.execFileSync.mockImplementation(() => { throw new Error('integrity failure') })
    await expect(fetchCuaDriver(f.options)).rejects.toThrow('integrity failure')
    expect(f.execFileSync).toHaveBeenCalledTimes(1)
    expect(f.fs.writeFileSync).not.toHaveBeenCalled()
    expect(f.fs.rmSync).toHaveBeenCalledTimes(1)
  })
  it('network failure fails and removes scratch files', async () => {
    const f = fixture()
    f.download.mockRejectedValue(new Error('offline'))
    await expect(fetchCuaDriver(f.options)).rejects.toThrow('offline')
    expect(f.execFileSync).not.toHaveBeenCalled()
    expect(f.fs.writeFileSync).not.toHaveBeenCalled()
    expect(f.fs.rmSync).toHaveBeenCalledTimes(1)
  })
})
