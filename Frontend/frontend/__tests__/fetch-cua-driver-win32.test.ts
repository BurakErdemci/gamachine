import { createRequire } from 'module'
import { createHash } from 'node:crypto'
import { existsSync, readFileSync } from 'node:fs'
import path from 'path'
import { describe, expect, it, vi } from 'vitest'

const require = createRequire(import.meta.url)
const { fetchCuaDriver } = require('../scripts/fetch-cua-driver.js')
const KEY = 'cua-driver/windows-x64'
const MEMBER = 'cua_driver/bin/cua-driver.exe'
const EXE_SHA = '31d5f3df16c486d06b59c0b431ea2deed139ae8f992bee50153c8f666cbbee20'
const digest = `sha256:${'b'.repeat(64)}`

// The fake extractor writes whatever bytes the case hands it to the path python was asked to write.
function fixture(exe: Buffer | string, stamped = false) {
  const root = path.resolve('fake-repo')
  const destination = path.join(root, 'Backend/vendor/cua')
  const files = new Map<string, any>([[path.join(root, 'scripts/pinned_assets.json'), JSON.stringify({ assets: {
    [KEY]: { url: 'https://offline.invalid/win.whl', digest, size: 100 },
  } })]])
  if (stamped) {
    files.set(path.join(destination, '.fetched'), digest)
    files.set(path.join(destination, 'cua-driver.exe'), Buffer.from('binary'))
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
    if (args[0] === '-c') files.set(args[4], Buffer.from(exe))
    return undefined
  })
  const options = { platform: 'win32', repoRoot: root, fs, download, execFileSync, log: vi.fn() }
  return { files, destination, fs, download, execFileSync, options }
}

describe('fetch-cua-driver on win32 (offline, no real subprocesses)', () => {
  it('has the pinned Windows wheel in pinned_assets.json', () => {
    const pins = JSON.parse(readFileSync(path.resolve(__dirname, '../../../scripts/pinned_assets.json'), 'utf8')).assets
    expect(pins[KEY]).toEqual({
      url: 'https://files.pythonhosted.org/packages/11/70/97a2aae9d289623151028ec1b8ba5a12763843bacf66b2ac9b3250f991b4/cua_driver-0.35.4-py3-none-win_amd64.whl',
      digest: 'sha256:617ba777b0b711c55f8d7c7ec7d51b037e0c7b016140f9a819fcfe49d3ea9738',
      size: 32176026, kaynak: 'pypi',
    })
  })

  it('verifies the windows pin with python, extracts the member with zipfile and installs the exe', async () => {
    const exe = 'exe-bytes'
    const f = fixture(exe)
    const realHash = createHash('sha256').update(exe).digest('hex')
    const crypto = require('node:crypto')
    const spy = vi.spyOn(crypto, 'createHash').mockImplementation(() => ({
      update: () => ({ digest: () => EXE_SHA }),
    }))
    try {
      await fetchCuaDriver(f.options)
    } finally { spy.mockRestore() }
    expect(realHash).not.toBe(EXE_SHA)
    expect(f.download).toHaveBeenCalledWith('https://offline.invalid/win.whl', expect.stringContaining('driver.whl'))
    const wheel = path.join(path.resolve('fake-scratch'), 'driver.whl')
    const extracted = path.join(path.resolve('fake-scratch'), 'cua-driver.exe')
    expect(f.execFileSync.mock.calls[0]).toEqual(['python', [expect.stringContaining('pinned_assets.py'), 'verify', KEY, wheel], { stdio: 'inherit' }])
    expect(f.execFileSync.mock.calls[1]).toEqual(['python', ['-c', expect.not.stringContaining(wheel), wheel, MEMBER, extracted], { stdio: 'inherit' }])
    expect(f.execFileSync.mock.calls).toHaveLength(2)
    expect(f.files.get(path.join(f.destination, 'cua-driver.exe'))).toEqual(Buffer.from(exe))
    expect(f.files.get(path.join(f.destination, '.fetched'))).toBe(digest + '\n')
    expect(f.fs.chmodSync).not.toHaveBeenCalled()
    expect(f.fs.rmSync).toHaveBeenCalledExactlyOnceWith(path.resolve('fake-scratch'), { recursive: true, force: true })
  })

  it('rejects an exe whose SHA-256 differs from the pin and installs nothing', async () => {
    const f = fixture('tampered')
    await expect(fetchCuaDriver(f.options)).rejects.toThrow('does not match the pinned hash')
    expect(f.fs.copyFileSync).not.toHaveBeenCalled()
    expect(f.fs.renameSync).not.toHaveBeenCalled()
    expect(f.files.has(path.join(f.destination, 'cua-driver.exe'))).toBe(false)
    expect(f.files.has(path.join(f.destination, '.fetched'))).toBe(false)
    expect(f.fs.rmSync).toHaveBeenCalledExactlyOnceWith(path.resolve('fake-scratch'), { recursive: true, force: true })
  })

  it('skips a matching complete stamp without chmod', async () => {
    const f = fixture('unused', true)
    await fetchCuaDriver(f.options)
    expect(f.download).not.toHaveBeenCalled()
    expect(f.execFileSync).not.toHaveBeenCalled()
    expect(f.fs.chmodSync).not.toHaveBeenCalled()
  })

  it('stops before extraction when the wheel verification fails', async () => {
    const f = fixture('unused')
    f.execFileSync.mockImplementation(() => { throw new Error('integrity failure') })
    await expect(fetchCuaDriver(f.options)).rejects.toThrow('integrity failure')
    expect(f.execFileSync).toHaveBeenCalledTimes(1)
    expect(f.fs.writeFileSync).not.toHaveBeenCalled()
  })

  it('ships the license and the Windows resources in the builder config', () => {
    expect(existsSync(require.resolve('../resources/licenses/cua-driver-LICENSE.md'))).toBe(true)
    const yml = readFileSync(path.resolve(__dirname, '../electron-builder.yml'), 'utf8')
    const win = yml.slice(yml.indexOf('\nwin:'), yml.indexOf('\nnsis:'))
    expect(win).toContain('from: ../../Backend/vendor/cua')
    expect(win).toContain('- cua-driver.exe')
    expect(win).toContain('to: cua/LICENSE')
  })
})
