import { createHash, generateKeyPairSync, sign } from 'node:crypto'
import { EventEmitter } from 'node:events'
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setupAutoUpdate } from '../main/helpers/auto-update'

const AVAILABLE = { version: '9.9.9', files: [{ url: 'Gamachine-Setup-9.9.9.exe', sha512: 'HASH==' }], path: 'Gamachine-Setup-9.9.9.exe', sha512: 'HASH==' }
const INFO = { version: '9.9.9', downloadedFile: 'C:\\tmp\\Gamachine-Setup-9.9.9.exe' }
const sign64 = (m: Buffer, k: any) => Buffer.from(sign(null, m, k).toString('base64'))

// Runs the happy path up to the downloaded event (the manifest check, then the download).
async function setupDownload(updater: any) {
  updater.emit('update-available', AVAILABLE)
  await vi.waitFor(() => expect(updater.downloadUpdate).toHaveBeenCalled())
  updater.emit('update-downloaded', INFO)
}

// verified=false makes the fake signature check fail (no key configured).
function setup(response = 0, quitImpl?: () => void, stopImpl?: () => Promise<void>, verified = true) {
  const updater: any = new EventEmitter()
  updater.checkForUpdates = vi.fn(() => Promise.resolve(null))
  updater.downloadUpdate = vi.fn(() => Promise.resolve([]))
  updater.addQuitHandler = vi.fn()
  const order: string[] = []
  updater.quitAndInstall = vi.fn(() => { order.push('quitAndInstall'); quitImpl?.() })
  const stopBackend = vi.fn(stopImpl ?? (async () => { order.push('stopBackend') }))
  const dialog = { showMessageBox: vi.fn(async (..._args: any[]): Promise<any> => ({ response, checkboxChecked: false })) }
  const logger = { log: vi.fn(), error: vi.fn() }
  const relaunch = vi.fn()
  const openExternal = vi.fn(async () => {})
  const manifest = Buffer.from('version: 9.9.9\nfiles:\n  - url: a.exe\n    sha512: HASH==\npath: a.exe\nsha512: HASH==\n')
  const { publicKey, privateKey } = generateKeyPairSync('ed25519')
  const sig = sign64(manifest, privateKey)
  const fetchBytes = vi.fn(async (url: string) => (url.endsWith('.sig') ? sig : manifest))
  const hashFile = vi.fn(async (_file: string) => 'HASH==')
  const removeFile = vi.fn(async (_file: string) => {})
  const handle = setupAutoUpdate({
    autoUpdater: updater, dialog: dialog as any, stopBackend, logger, getMainWindow: () => null, relaunch,
    openExternal,
    publicKeys: verified ? [publicKey.export({ type: 'spki', format: 'pem' }).toString()] : [],
    platform: 'win32',
    fetchBytes,
    hashFile,
    removeFile,
  })
  return { updater, order, stopBackend, dialog, logger, handle, relaunch, openExternal, fetchBytes, hashFile, removeFile }
}

describe('setupAutoUpdate', () => {
  beforeEach(() => { vi.useFakeTimers() })
  afterEach(() => { vi.useRealTimers() })

  it('downloads and installs nothing by itself and checks once at startup', () => {
    const { updater, handle } = setup()
    expect(updater.autoDownload).toBe(false)
    expect(updater.autoInstallOnAppQuit).toBe(false)
    expect(updater.checkForUpdates).toHaveBeenCalledTimes(1)
    handle.dispose()
  })

  describe('signature gate', () => {
    it('fetches the signed manifest of the available version before downloading', async () => {
      const { updater, fetchBytes, handle } = setup(1)
      updater.emit('update-available', AVAILABLE)
      await vi.waitFor(() => expect(updater.downloadUpdate).toHaveBeenCalledTimes(1))
      const urls = fetchBytes.mock.calls.map((c) => c[0])
      expect(urls).toContain('https://github.com/BurakErdemci/gamachine/releases/download/v9.9.9/latest.yml')
      expect(urls).toContain('https://github.com/BurakErdemci/gamachine/releases/download/v9.9.9/latest.yml.sig')
      expect(updater.autoInstallOnAppQuit).toBe(false)
      handle.dispose()
    })

    it('never arms install-on-quit before the hash matched and the user chose "later"', async () => {
      const { updater, dialog, hashFile, handle } = setup(1)
      let release!: (h: string) => void
      let answer!: (v: any) => void
      hashFile.mockImplementation(() => new Promise<string>((r) => { release = r }))
      dialog.showMessageBox.mockImplementation(() => new Promise((r) => { answer = r }) as any)
      updater.emit('update-available', AVAILABLE)
      await vi.waitFor(() => expect(updater.downloadUpdate).toHaveBeenCalled())
      expect(updater.autoInstallOnAppQuit).toBe(false)
      updater.emit('update-downloaded', INFO)
      await vi.waitFor(() => expect(hashFile).toHaveBeenCalled())
      expect(updater.autoInstallOnAppQuit).toBe(false)
      release('HASH==')
      await vi.waitFor(() => expect(answer).toBeTypeOf('function'))
      expect(dialog.showMessageBox.mock.calls[0]![0].title).toBe('Güncelleme hazır')
      expect(updater.autoInstallOnAppQuit).toBe(false)
      expect(updater.addQuitHandler).not.toHaveBeenCalled()
      answer({ response: 1 })
      await vi.waitFor(() => expect(updater.autoInstallOnAppQuit).toBe(true))
      expect(updater.addQuitHandler).toHaveBeenCalledTimes(1)
      handle.dispose()
    })

    it('an unverifiable update is not downloaded and offers the release page once', async () => {
      const { updater, dialog, stopBackend, openExternal, logger, handle } = setup(0, undefined, undefined, false)
      updater.emit('update-available', AVAILABLE)
      await vi.waitFor(() => expect(openExternal).toHaveBeenCalledWith('https://github.com/BurakErdemci/gamachine/releases/latest'))
      expect(dialog.showMessageBox.mock.calls[0]![0].title).toBe('Güncelleme doğrulanamadı')
      updater.emit('update-available', AVAILABLE)
      await vi.advanceTimersByTimeAsync(0)
      expect(dialog.showMessageBox).toHaveBeenCalledTimes(1)
      expect(updater.downloadUpdate).not.toHaveBeenCalled()
      expect(updater.autoInstallOnAppQuit).toBe(false)
      expect(updater.quitAndInstall).not.toHaveBeenCalled()
      expect(stopBackend).not.toHaveBeenCalled()
      expect(logger.error).toHaveBeenCalled()
      handle.dispose()
    })

    it('dismissing the failure dialog opens nothing', async () => {
      const { updater, dialog, openExternal, handle } = setup(1, undefined, undefined, false)
      updater.emit('update-available', AVAILABLE)
      await vi.waitFor(() => expect(dialog.showMessageBox).toHaveBeenCalledTimes(1))
      await Promise.resolve()
      expect(openExternal).not.toHaveBeenCalled()
      handle.dispose()
    })

    it('a missing signature file and a network error refuse', async () => {
      for (const impl of [
        async (u: string) => { if (u.endsWith('.sig')) throw new Error('404'); return Buffer.from('x') },
        async () => { throw new Error('offline') },
      ]) {
        const s = setup(1)
        s.fetchBytes.mockImplementation(impl)
        s.updater.emit('update-available', AVAILABLE)
        await vi.waitFor(() => expect(s.dialog.showMessageBox).toHaveBeenCalledTimes(1))
        expect(s.dialog.showMessageBox.mock.calls[0]![0].title).toBe('Güncelleme doğrulanamadı')
        expect(s.updater.downloadUpdate).not.toHaveBeenCalled()
        expect(s.updater.autoInstallOnAppQuit).toBe(false)
        s.handle.dispose()
      }
    })

    it('refuses when the updater sees a file hash or version the signed manifest does not carry', async () => {
      for (const info of [
        { ...AVAILABLE, files: [{ url: 'a.exe', sha512: 'EVIL==' }], sha512: 'EVIL==' },
        { ...AVAILABLE, files: [{ url: 'a.exe', sha512: 'HASH==' }, { url: 'b.exe' }] },
        { ...AVAILABLE, version: '9.9.8' },
      ]) {
        const s = setup(1)
        s.updater.emit('update-available', info)
        await vi.waitFor(() => expect(s.dialog.showMessageBox).toHaveBeenCalledTimes(1))
        expect(s.updater.downloadUpdate).not.toHaveBeenCalled()
        s.handle.dispose()
      }
    })

    it('a downloaded file that differs from the verified hash installs nothing', async () => {
      const { updater, dialog, hashFile, stopBackend, removeFile, handle } = setup(0)
      hashFile.mockResolvedValue('OTHER==')
      await setupDownload(updater)
      await vi.waitFor(() => expect(dialog.showMessageBox).toHaveBeenCalledTimes(1))
      expect(dialog.showMessageBox.mock.calls[0]![0].title).toBe('Güncelleme doğrulanamadı')
      expect(removeFile).toHaveBeenCalledWith(INFO.downloadedFile)
      expect(updater.addQuitHandler).not.toHaveBeenCalled()
      expect(updater.autoInstallOnAppQuit).toBe(false)
      expect(updater.quitAndInstall).not.toHaveBeenCalled()
      expect(stopBackend).not.toHaveBeenCalled()
      handle.dispose()
    })

    it('a download for another version than the verified one installs nothing', async () => {
      const { updater, dialog, removeFile, handle } = setup(0)
      updater.emit('update-available', AVAILABLE)
      await vi.waitFor(() => expect(updater.downloadUpdate).toHaveBeenCalled())
      updater.emit('update-downloaded', { ...INFO, version: '10.0.0' })
      await vi.waitFor(() => expect(dialog.showMessageBox).toHaveBeenCalledTimes(1))
      expect(removeFile).toHaveBeenCalledWith(INFO.downloadedFile)
      expect(dialog.showMessageBox.mock.calls[0]![0].title).toBe('Güncelleme doğrulanamadı')
      expect(updater.autoInstallOnAppQuit).toBe(false)
      expect(updater.quitAndInstall).not.toHaveBeenCalled()
      handle.dispose()
    })

    it('an unreadable downloaded file is treated as a mismatch', async () => {
      const { updater, dialog, hashFile, removeFile, handle } = setup(0)
      hashFile.mockRejectedValue(new Error('EACCES'))
      await setupDownload(updater)
      await vi.waitFor(() => expect(dialog.showMessageBox).toHaveBeenCalledTimes(1))
      expect(dialog.showMessageBox.mock.calls[0]![0].title).toBe('Güncelleme doğrulanamadı')
      expect(removeFile).toHaveBeenCalledWith(INFO.downloadedFile)
      expect(updater.quitAndInstall).not.toHaveBeenCalled()
      handle.dispose()
    })

    it('a download nobody verified is ignored', async () => {
      const { updater, dialog, hashFile, handle } = setup(0)
      updater.emit('update-downloaded', INFO)
      await vi.advanceTimersByTimeAsync(0)
      expect(hashFile).not.toHaveBeenCalled()
      expect(dialog.showMessageBox).not.toHaveBeenCalled()
      expect(updater.autoInstallOnAppQuit).toBe(false)
      handle.dispose()
    })

    it('a verified update in flight blocks other updates and further checks', async () => {
      const { updater, fetchBytes, handle } = setup(1)
      updater.emit('update-available', AVAILABLE)
      await vi.waitFor(() => expect(updater.downloadUpdate).toHaveBeenCalledTimes(1))
      fetchBytes.mockClear()
      updater.emit('update-available', { ...AVAILABLE, version: '10.0.0' })
      vi.advanceTimersByTime(6 * 60 * 60 * 1000)
      expect(fetchBytes).not.toHaveBeenCalled()
      expect(updater.downloadUpdate).toHaveBeenCalledTimes(1)
      expect(updater.checkForUpdates).toHaveBeenCalledTimes(1)
      handle.dispose()
    })

    it('a failed download disarms install-on-quit and allows a new check', async () => {
      const { updater, handle } = setup(1)
      updater.downloadUpdate.mockRejectedValue(new Error('network'))
      updater.emit('update-available', AVAILABLE)
      await vi.waitFor(() => expect(updater.downloadUpdate).toHaveBeenCalled())
      await vi.waitFor(() => expect(updater.autoInstallOnAppQuit).toBe(false))
      vi.advanceTimersByTime(6 * 60 * 60 * 1000)
      expect(updater.checkForUpdates).toHaveBeenCalledTimes(2)
      handle.dispose()
    })

    it('shows one ready dialog per version, even if the event repeats', async () => {
      const { updater, dialog, handle } = setup(1)
      await setupDownload(updater)
      updater.emit('update-downloaded', INFO)
      await vi.waitFor(() => expect(dialog.showMessageBox).toHaveBeenCalledTimes(1))
      await vi.advanceTimersByTimeAsync(0)
      expect(dialog.showMessageBox).toHaveBeenCalledTimes(1)
      handle.dispose()
    })

    it('uses latest-mac.yml on macOS', async () => {
      const updater: any = new EventEmitter()
      updater.checkForUpdates = vi.fn(() => Promise.resolve(null))
      updater.downloadUpdate = vi.fn()
      const fetchBytes = vi.fn(async () => { throw new Error('stop') })
      const handle = setupAutoUpdate({
        autoUpdater: updater, dialog: { showMessageBox: vi.fn(async () => ({ response: 1 })) } as any, stopBackend: vi.fn(),
        logger: { log: vi.fn(), error: vi.fn() }, getMainWindow: () => null, relaunch: vi.fn(), openExternal: vi.fn(),
        platform: 'darwin', publicKeys: ['k'], fetchBytes,
      })
      updater.emit('update-available', AVAILABLE)
      await vi.waitFor(() => expect(fetchBytes).toHaveBeenCalled())
      expect(fetchBytes.mock.calls.map((c: any) => c[0])).toContain('https://github.com/BurakErdemci/gamachine/releases/download/v9.9.9/latest-mac.yml.sig')
      handle.dispose()
    })
  })

  it('"now" stops the backend before quitAndInstall', async () => {
    const { updater, order, dialog, handle } = setup(0)
    await setupDownload(updater)
    await vi.waitFor(() => expect(updater.quitAndInstall).toHaveBeenCalledWith(false, true))
    expect(dialog.showMessageBox).toHaveBeenCalledTimes(1)
    expect(order).toEqual(['stopBackend', 'quitAndInstall'])
    expect(updater.autoInstallOnAppQuit).toBe(false)
    expect(updater.addQuitHandler).not.toHaveBeenCalled()
    handle.dispose()
  })

  it('"later" does not quit and arms install-on-quit', async () => {
    const { updater, stopBackend, dialog, handle } = setup(1)
    await setupDownload(updater)
    await vi.waitFor(() => expect(dialog.showMessageBox).toHaveBeenCalledTimes(1))
    await vi.waitFor(() => expect(updater.addQuitHandler).toHaveBeenCalledTimes(1))
    expect(updater.autoInstallOnAppQuit).toBe(true)
    expect(stopBackend).not.toHaveBeenCalled()
    expect(updater.quitAndInstall).not.toHaveBeenCalled()
    handle.dispose()
  })

  it('"later" stays disarmed when the library has no quit handler to register', async () => {
    const { updater, dialog, logger, handle } = setup(1)
    delete updater.addQuitHandler
    await setupDownload(updater)
    await vi.waitFor(() => expect(dialog.showMessageBox).toHaveBeenCalledTimes(1))
    await vi.waitFor(() => expect(logger.error).toHaveBeenCalledWith(expect.stringContaining('çıkışta kurulum kaydedilemedi')))
    expect(updater.autoInstallOnAppQuit).toBe(false)
    handle.dispose()
  })

  it('error is logged without a dialog or throw; no dialog for progress', () => {
    const { updater, dialog, logger, handle } = setup()
    expect(() => updater.emit('error', new Error('boom'))).not.toThrow()
    updater.emit('download-progress', { percent: 10 })
    expect(logger.error).toHaveBeenCalled()
    expect(dialog.showMessageBox).not.toHaveBeenCalled()
    handle.dispose()
  })

  it('re-checks every 6 hours until disposed', () => {
    const { updater, handle } = setup()
    vi.advanceTimersByTime(6 * 60 * 60 * 1000)
    expect(updater.checkForUpdates).toHaveBeenCalledTimes(2)
    handle.dispose()
    vi.advanceTimersByTime(12 * 60 * 60 * 1000)
    expect(updater.checkForUpdates).toHaveBeenCalledTimes(2)
  })

  describe('failed install attempt', () => {
    it('quitAndInstall throwing relaunches once', async () => {
      const { updater, relaunch, handle } = setup(0, () => { throw new Error('nope') })
      await setupDownload(updater)
      await vi.waitFor(() => expect(relaunch).toHaveBeenCalledTimes(1))
      handle.dispose()
    })

    it('error after the install attempt relaunches once, even if repeated', async () => {
      const { updater, relaunch, handle } = setup(0)
      await setupDownload(updater)
      await vi.waitFor(() => expect(updater.quitAndInstall).toHaveBeenCalled())
      updater.emit('error', new Error('signature mismatch'))
      updater.emit('error', new Error('again'))
      vi.advanceTimersByTime(60 * 1000)
      expect(relaunch).toHaveBeenCalledTimes(1)
      handle.dispose()
    })

    it('error without an install attempt does not relaunch', () => {
      const { updater, relaunch, handle } = setup(0)
      updater.emit('error', new Error('network'))
      expect(relaunch).not.toHaveBeenCalled()
      handle.dispose()
    })

    it('no quit within 30 s relaunches once', async () => {
      const { updater, relaunch, handle } = setup(0)
      await setupDownload(updater)
      await vi.waitFor(() => expect(updater.quitAndInstall).toHaveBeenCalled())
      vi.advanceTimersByTime(29 * 1000)
      expect(relaunch).not.toHaveBeenCalled()
      vi.advanceTimersByTime(2 * 1000)
      expect(relaunch).toHaveBeenCalledTimes(1)
      vi.advanceTimersByTime(60 * 1000)
      expect(relaunch).toHaveBeenCalledTimes(1)
      handle.dispose()
    })
    it('a hung stopBackend still relaunches after 30 s', async () => {
      const { updater, relaunch, stopBackend, handle } = setup(0, undefined, () => new Promise<void>(() => {}))
      await setupDownload(updater)
      await vi.waitFor(() => expect(stopBackend).toHaveBeenCalled())
      vi.advanceTimersByTime(31 * 1000)
      expect(relaunch).toHaveBeenCalledTimes(1)
      expect(updater.quitAndInstall).not.toHaveBeenCalled()
      handle.dispose()
    })

    it('dispose during a quit in progress cancels the relaunch', async () => {
      let release!: () => void
      const { updater, relaunch, handle } = setup(0, undefined, () => new Promise<void>((r) => { release = r }))
      await setupDownload(updater)
      await vi.waitFor(() => expect(release).toBeTypeOf('function'))
      handle.dispose()
      vi.advanceTimersByTime(60 * 1000)
      release()
      await vi.waitFor(() => expect(updater.quitAndInstall).toHaveBeenCalled())
      updater.emit('error', new Error('late'))
      vi.advanceTimersByTime(60 * 1000)
      expect(relaunch).not.toHaveBeenCalled()
    })

    it('a download event after dispose shows no dialog', async () => {
      const { updater, dialog, stopBackend, handle } = setup(0)
      handle.dispose()
      updater.emit('update-available', AVAILABLE)
      updater.emit('update-downloaded', INFO)
      await vi.advanceTimersByTimeAsync(0)
      expect(updater.downloadUpdate).not.toHaveBeenCalled()
      expect(dialog.showMessageBox).not.toHaveBeenCalled()
      expect(stopBackend).not.toHaveBeenCalled()
    })

    it('a dialog answered after dispose starts nothing', async () => {
      let answer!: (v: any) => void
      const { updater, dialog, stopBackend, handle } = setup(0)
      dialog.showMessageBox.mockImplementation(() => new Promise((r) => { answer = r }) as any)
      await setupDownload(updater)
      await vi.waitFor(() => expect(answer).toBeTypeOf('function'))
      handle.dispose()
      answer({ response: 0 })
      await Promise.resolve()
      await Promise.resolve()
      expect(stopBackend).not.toHaveBeenCalled()
      expect(updater.quitAndInstall).not.toHaveBeenCalled()
    })
  })
})

// The same flow driven through electron-updater's real NsisUpdater / MacUpdater:
// only the network download, the installer launch and Squirrel are faked.
describe('with the real electron-updater classes', () => {
  const nodeRequire = createRequire(import.meta.url)
  const Module = nodeRequire('node:module')
  const realLoad = Module._load
  const sha512 = (b: Buffer | string) => createHash('sha512').update(b).digest('base64')
  let dir: string
  let native: any

  beforeEach(() => {
    dir = mkdtempSync(path.join(tmpdir(), 'gamachine-updater-'))
    writeFileSync(path.join(dir, 'app-update.yml'), 'provider: generic\nurl: https://example.invalid/\nupdaterCacheDirName: gm-updater\n')
    native = Object.assign(new EventEmitter(), { setFeedURL: vi.fn(), checkForUpdates: vi.fn(), quitAndInstall: vi.fn() })
    Module._load = function (request: string, ...rest: any[]) {
      return request === 'electron' ? { autoUpdater: native, app: {}, shell: { openPath: async () => {} } } : realLoad.call(this, request, ...rest)
    }
  })
  afterEach(() => {
    Module._load = realLoad
    rmSync(dir, { recursive: true, force: true })
  })

  // `content` is what the fake network serves; `signedAs` is what the signed manifest says it is.
  async function rig(kind: 'nsis' | 'mac', opts: { response: number; content?: string; signedAs?: string; hashGate?: Promise<void> }) {
    const { NsisUpdater, MacUpdater } = nodeRequire('electron-updater')
    const quitHandlers: Array<(code: number) => void> = []
    const app = {
      name: 'gamachine', version: '1.0.0', isPackaged: true, appUpdateConfigPath: path.join(dir, 'app-update.yml'),
      userDataPath: dir, baseCachePath: dir, whenReady: async () => {}, quit: vi.fn(), relaunch: vi.fn(),
      onQuit: (h: (code: number) => void) => quitHandlers.push(h),
    }
    const updater: any = new (kind === 'nsis' ? NsisUpdater : MacUpdater)(null, app)
    updater.disableDifferentialDownload = true
    updater.disableWebInstaller = true
    updater._logger = { info() {}, warn() {}, error() {}, debug() {} }
    updater.spawnLog = vi.fn(async () => true)
    const served = { content: opts.content ?? 'installer-v2' }
    updater.httpExecutor = { download: async (_url: URL, dest: string) => writeFileSync(dest, served.content) }
    const ext = kind === 'nsis' ? 'exe' : 'zip'
    const releaseInfo = (version: string, hash: string) => ({
      version, path: `Gamachine-${version}.${ext}`, sha512: hash,
      files: [{ url: `Gamachine-${version}.${ext}`, sha512: hash, size: 1 }],
    })
    let latest = releaseInfo('2.0.0', sha512(opts.signedAs ?? 'installer-v2'))
    updater.clientPromise = Promise.resolve({
      setRequestHeaders() {},
      getLatestVersion: async () => latest,
      resolveFiles: (info: any) => info.files.map((f: any) => ({ url: new URL(f.url, 'https://example.invalid/'), info: f })),
    })
    const { publicKey, privateKey } = generateKeyPairSync('ed25519')
    const fetchBytes = async (url: string) => {
      const manifest = Buffer.from(`version: ${latest.version}\nfiles:\n  - url: ${latest.files[0].url}\n    sha512: ${latest.sha512}\npath: ${latest.path}\nsha512: ${latest.sha512}\n`)
      return url.endsWith('.sig') ? sign64(manifest, privateKey) : manifest
    }
    const realCheck = updater.checkForUpdates.bind(updater)
    updater.checkForUpdates = async () => null
    const stopBackend = vi.fn(async () => {})
    let answer: ((r: number) => void) | undefined
    const dialog = {
      showMessageBox: vi.fn(async () => ({ response: opts.response >= 0 ? opts.response : await new Promise<number>((r) => { answer = r }), checkboxChecked: false })),
    }
    const handle = setupAutoUpdate({
      autoUpdater: updater, dialog: dialog as any, stopBackend, logger: { log() {}, error() {} }, getMainWindow: () => null,
      relaunch: vi.fn(), openExternal: vi.fn(), platform: kind === 'nsis' ? 'win32' : 'darwin',
      stageNative: () => native.checkForUpdates(),
      publicKeys: [publicKey.export({ type: 'spki', format: 'pem' }).toString()], fetchBytes,
      hashFile: async (file) => { await opts.hashGate; return sha512(readFileSync(file)) },
    })
    return {
      updater, quitHandlers, dialog, stopBackend, handle, realCheck, served, answer: (r: number) => answer!(r),
      offer: (version: string, content: string) => { served.content = content; latest = releaseInfo(version, sha512(content)) },
      dialogTitle: (n = 0) => (dialog.showMessageBox.mock.calls[n] as any)[0].title,
      installerCalls: () => updater.spawnLog.mock.calls.map((c: any[]) => ({ file: c[0], args: c[1] })),
    }
  }

  it('NSIS, "later": no quit handler until the hash matched and "later" was chosen, then it installs the verified file on quit', async () => {
    const r = await rig('nsis', { response: -1 })
    await r.realCheck()
    await vi.waitFor(() => expect(r.dialog.showMessageBox).toHaveBeenCalledTimes(1))
    expect(r.dialogTitle()).toBe('Güncelleme hazır')
    expect(r.quitHandlers).toHaveLength(0)
    expect(r.updater.autoInstallOnAppQuit).toBe(false)
    r.answer(1)
    await vi.waitFor(() => expect(r.quitHandlers).toHaveLength(1))
    expect(r.updater.autoInstallOnAppQuit).toBe(true)
    expect(r.installerCalls()).toEqual([])
    r.quitHandlers[0]!(0)
    expect(r.installerCalls()).toHaveLength(1)
    expect(r.installerCalls()[0].file).toMatch(/Gamachine-2\.0\.0\.exe$/)
    expect(readFileSync(r.installerCalls()[0].file, 'utf8')).toBe('installer-v2')
    r.handle.dispose()
  })

  it('NSIS, "now": stops the backend, then the real quitAndInstall launches the verified installer', async () => {
    const r = await rig('nsis', { response: 0 })
    await r.realCheck()
    await vi.waitFor(() => expect(r.installerCalls()).toHaveLength(1))
    expect(r.stopBackend).toHaveBeenCalledTimes(1)
    expect(r.installerCalls()[0].args).toContain('--force-run')
    expect(readFileSync(r.installerCalls()[0].file, 'utf8')).toBe('installer-v2')
    r.handle.dispose()
  })

  it('NSIS: a file that differs from the signed hash installs nothing, on quit or by "now"', async () => {
    const r = await rig('nsis', { response: 0, content: 'tampered', signedAs: 'installer-v2' })
    await r.realCheck()
    await vi.waitFor(() => expect(r.dialog.showMessageBox).toHaveBeenCalledTimes(1))
    expect(r.dialogTitle()).toBe('Güncelleme doğrulanamadı')
    expect(r.updater.autoInstallOnAppQuit).toBe(false)
    expect(r.quitHandlers).toHaveLength(0)
    expect(existsSync(r.updater.downloadedUpdateHelper.file)).toBe(false)
    expect(r.installerCalls()).toEqual([])
    expect(r.stopBackend).not.toHaveBeenCalled()
    r.handle.dispose()
  })

  it('NSIS: after a mismatch, a later verified download in flight cannot make quit install the old file', async () => {
    for (const newerRelease of [true, false]) {
      const r = await rig('nsis', { response: 1, content: 'tampered', signedAs: 'installer-v2' })
      await r.realCheck()
      await vi.waitFor(() => expect(r.dialog.showMessageBox).toHaveBeenCalledTimes(1))
      expect(r.dialogTitle()).toBe('Güncelleme doğrulanamadı')
      const stale = r.updater.downloadedUpdateHelper.file
      let release!: () => void
      r.updater.httpExecutor.download = async (_u: URL, dest: string) => {
        await new Promise<void>((res) => { release = res })
        writeFileSync(dest, r.served.content)
      }
      r.offer(newerRelease ? '3.0.0' : '2.0.0', newerRelease ? 'installer-v3' : 'installer-v2')
      await r.realCheck()
      await vi.waitFor(() => expect(release).toBeTypeOf('function'))
      expect(r.updater.autoInstallOnAppQuit).toBe(false)
      r.quitHandlers.forEach((h) => h(0))
      expect(r.installerCalls()).toEqual([])
      expect(existsSync(stale) && readFileSync(stale, 'utf8') === 'tampered').toBe(false)
      release()
      await vi.waitFor(() => expect(r.dialog.showMessageBox).toHaveBeenCalledTimes(2))
      expect(r.dialogTitle(1)).toBe('Güncelleme hazır')
      expect(r.installerCalls()).toEqual([])
      r.handle.dispose()
    }
  })

  it('NSIS: quitting while the downloaded file is still being hashed installs nothing', async () => {
    let release!: () => void
    const r = await rig('nsis', { response: 1, hashGate: new Promise<void>((res) => { release = res }) })
    await r.realCheck()
    await vi.waitFor(() => expect(r.updater.downloadedUpdateHelper?.file).toBeTruthy())
    await new Promise((res) => setTimeout(res, 20))
    expect(r.quitHandlers).toHaveLength(0)
    expect(r.updater.autoInstallOnAppQuit).toBe(false)
    release()
    await vi.waitFor(() => expect(r.quitHandlers).toHaveLength(1))
    expect(r.installerCalls()).toEqual([])
    expect(r.updater.autoInstallOnAppQuit).toBe(true)
    r.handle.dispose()
  })

  it('NSIS: a newer release found while the verified one waits cannot replace it', async () => {
    const r = await rig('nsis', { response: -1 })
    await r.realCheck()
    await vi.waitFor(() => expect(r.dialog.showMessageBox).toHaveBeenCalledTimes(1))
    r.offer('3.0.0', 'installer-v3-unsigned-by-nobody')
    await r.realCheck()
    await new Promise((res) => setTimeout(res, 20))
    expect(r.dialog.showMessageBox).toHaveBeenCalledTimes(1)
    r.answer(0)
    await vi.waitFor(() => expect(r.installerCalls()).toHaveLength(1))
    expect(readFileSync(r.installerCalls()[0].file, 'utf8')).toBe('installer-v2')
    r.handle.dispose()
  })

  it('macOS, "later": nothing is staged before the hash matched and "later" was chosen', async () => {
    const r = await rig('mac', { response: -1 })
    await r.realCheck()
    await vi.waitFor(() => expect(r.dialog.showMessageBox).toHaveBeenCalledTimes(1))
    expect(r.dialogTitle()).toBe('Güncelleme hazır')
    expect(native.checkForUpdates).not.toHaveBeenCalled()
    expect(r.updater.autoInstallOnAppQuit).toBe(false)
    r.answer(1)
    await vi.waitFor(() => expect(native.checkForUpdates).toHaveBeenCalledTimes(1))
    expect(r.updater.autoInstallOnAppQuit).toBe(true)
    expect(native.quitAndInstall).not.toHaveBeenCalled()
    r.updater.closeServerIfExists()
    r.handle.dispose()
  })

  it('macOS, "now": the real quitAndInstall hands over to Squirrel once it has the update', async () => {
    const r = await rig('mac', { response: 0 })
    await r.realCheck()
    await vi.waitFor(() => expect(r.stopBackend).toHaveBeenCalledTimes(1))
    await vi.waitFor(() => expect(native.listenerCount('update-downloaded')).toBeGreaterThan(1))
    expect(native.checkForUpdates).toHaveBeenCalledTimes(1)
    expect(native.quitAndInstall).not.toHaveBeenCalled()
    native.emit('update-downloaded')
    expect(native.quitAndInstall).toHaveBeenCalledTimes(1)
    r.updater.closeServerIfExists()
    r.handle.dispose()
  })

  it('macOS: a file that differs from the signed hash is never offered or installed', async () => {
    const r = await rig('mac', { response: 0, content: 'tampered', signedAs: 'installer-v2' })
    await r.realCheck()
    await vi.waitFor(() => expect(r.dialog.showMessageBox).toHaveBeenCalledTimes(1))
    expect(r.dialogTitle()).toBe('Güncelleme doğrulanamadı')
    expect(r.updater.autoInstallOnAppQuit).toBe(false)
    expect(r.stopBackend).not.toHaveBeenCalled()
    expect(native.checkForUpdates).not.toHaveBeenCalled()
    expect(native.quitAndInstall).not.toHaveBeenCalled()
    r.updater.closeServerIfExists()
    r.handle.dispose()
  })
})
