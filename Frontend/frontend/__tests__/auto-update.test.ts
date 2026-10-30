import { generateKeyPairSync, sign } from 'node:crypto'
import { EventEmitter } from 'node:events'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setupAutoUpdate } from '../main/helpers/auto-update'

const INFO = { version: '9.9.9', downloadedFile: 'C:\\tmp\\Gamachine-Setup-9.9.9.exe' }

// verified=false makes the fake signature check fail (no key configured).
function setup(response = 0, quitImpl?: () => void, stopImpl?: () => Promise<void>, verified = true) {
  const updater: any = new EventEmitter()
  updater.checkForUpdates = vi.fn(() => Promise.resolve(null))
  const order: string[] = []
  updater.quitAndInstall = vi.fn(() => { order.push('quitAndInstall'); quitImpl?.() })
  const stopBackend = vi.fn(stopImpl ?? (async () => { order.push('stopBackend') }))
  const dialog = { showMessageBox: vi.fn(async (..._args: any[]): Promise<any> => ({ response, checkboxChecked: false })) }
  const logger = { log: vi.fn(), error: vi.fn() }
  const relaunch = vi.fn()
  const openExternal = vi.fn(async () => {})
  const manifest = Buffer.from('version: 9.9.9\nfiles:\n  - url: a.exe\n    sha512: HASH==\npath: a.exe\nsha512: HASH==\n')
  const { publicKey, privateKey } = generateKeyPairSync('ed25519')
  const sig = Buffer.from(sign(null, manifest, privateKey).toString('base64'))
  const fetchBytes = vi.fn(async (url: string) => {
    if (url.endsWith('.sig')) return sig
    return manifest
  })
  const handle = setupAutoUpdate({
    autoUpdater: updater, dialog: dialog as any, stopBackend, logger, getMainWindow: () => null, relaunch,
    openExternal,
    publicKeys: verified ? [publicKey.export({ type: 'spki', format: 'pem' }).toString()] : [],
    platform: 'win32',
    fetchBytes,
    hashFile: async () => 'HASH==',
  })
  return { updater, order, stopBackend, dialog, logger, handle, relaunch, openExternal, fetchBytes }
}

describe('setupAutoUpdate', () => {
  beforeEach(() => { vi.useFakeTimers() })
  afterEach(() => { vi.useRealTimers() })

  it('sets download flags and checks once at startup', () => {
    const { updater, handle } = setup()
    expect(updater.autoDownload).toBe(true)
    expect(updater.autoInstallOnAppQuit).toBe(false)
    expect(updater.checkForUpdates).toHaveBeenCalledTimes(1)
    handle.dispose()
  })

  describe('signature gate', () => {
    it('fetches the signed manifest of the downloaded version', async () => {
      const { updater, fetchBytes, handle } = setup(1)
      updater.emit('update-downloaded', INFO)
      await vi.waitFor(() => expect(fetchBytes).toHaveBeenCalledTimes(2))
      const urls = fetchBytes.mock.calls.map((c) => c[0])
      expect(urls).toContain('https://github.com/BurakErdemci/gamachine/releases/download/v9.9.9/latest.yml')
      expect(urls).toContain('https://github.com/BurakErdemci/gamachine/releases/download/v9.9.9/latest.yml.sig')
      handle.dispose()
    })

    it('installs on quit only after verification', async () => {
      const { updater, dialog, handle } = setup(1)
      updater.emit('update-downloaded', INFO)
      expect(updater.autoInstallOnAppQuit).toBe(false)
      await vi.waitFor(() => expect(dialog.showMessageBox).toHaveBeenCalledTimes(1))
      expect(updater.autoInstallOnAppQuit).toBe(true)
      expect(dialog.showMessageBox.mock.calls[0]![0].title).toBe('Güncelleme hazır')
      handle.dispose()
    })

    it('an unverifiable update installs nothing and offers the release page', async () => {
      const { updater, dialog, stopBackend, openExternal, logger, handle } = setup(0, undefined, undefined, false)
      updater.emit('update-downloaded', INFO)
      await vi.waitFor(() => expect(openExternal).toHaveBeenCalledWith('https://github.com/BurakErdemci/gamachine/releases/latest'))
      expect(dialog.showMessageBox).toHaveBeenCalledTimes(1)
      expect(dialog.showMessageBox.mock.calls[0]![0].title).toBe('Güncelleme doğrulanamadı')
      expect(updater.autoInstallOnAppQuit).toBe(false)
      expect(updater.quitAndInstall).not.toHaveBeenCalled()
      expect(stopBackend).not.toHaveBeenCalled()
      expect(logger.error).toHaveBeenCalled()
      handle.dispose()
    })

    it('dismissing the failure dialog opens nothing', async () => {
      const { updater, dialog, openExternal, handle } = setup(1, undefined, undefined, false)
      updater.emit('update-downloaded', INFO)
      await vi.waitFor(() => expect(dialog.showMessageBox).toHaveBeenCalledTimes(1))
      await Promise.resolve()
      expect(openExternal).not.toHaveBeenCalled()
      handle.dispose()
    })

    it('a missing signature file, a hash mismatch and a network error all refuse', async () => {
      for (const mutate of [
        (s: any) => s.fetchBytes.mockImplementation(async (u: string) => { if (u.endsWith('.sig')) throw new Error('404'); return Buffer.from('x') }),
        (s: any) => s.fetchBytes.mockImplementation(async () => { throw new Error('offline') }),
      ]) {
        const s = setup(1)
        mutate(s)
        s.updater.emit('update-downloaded', INFO)
        await vi.waitFor(() => expect(s.dialog.showMessageBox).toHaveBeenCalledTimes(1))
        expect(s.dialog.showMessageBox.mock.calls[0]![0].title).toBe('Güncelleme doğrulanamadı')
        expect(s.updater.autoInstallOnAppQuit).toBe(false)
        s.handle.dispose()
      }
      const updater: any = new EventEmitter()
      updater.checkForUpdates = vi.fn(() => Promise.resolve(null))
      updater.quitAndInstall = vi.fn()
      const dialog = { showMessageBox: vi.fn(async () => ({ response: 1, checkboxChecked: false })) }
      const manifest = Buffer.from('version: 9.9.9\nsha512: HASH==\n')
      const { publicKey, privateKey } = generateKeyPairSync('ed25519')
      const handle = setupAutoUpdate({
        autoUpdater: updater, dialog: dialog as any, stopBackend: vi.fn(), logger: { log: vi.fn(), error: vi.fn() },
        getMainWindow: () => null, relaunch: vi.fn(), openExternal: vi.fn(), platform: 'win32',
        publicKeys: [publicKey.export({ type: 'spki', format: 'pem' }).toString()],
        fetchBytes: async (u) => (u.endsWith('.sig') ? Buffer.from(sign(null, manifest, privateKey).toString('base64')) : manifest),
        hashFile: async () => 'OTHER==',
      })
      updater.emit('update-downloaded', INFO)
      await vi.waitFor(() => expect(dialog.showMessageBox).toHaveBeenCalledTimes(1))
      expect(updater.autoInstallOnAppQuit).toBe(false)
      handle.dispose()
    })

    it('uses latest-mac.yml on macOS', async () => {
      const updater: any = new EventEmitter()
      updater.checkForUpdates = vi.fn(() => Promise.resolve(null))
      const fetchBytes = vi.fn(async () => { throw new Error('stop') })
      const handle = setupAutoUpdate({
        autoUpdater: updater, dialog: { showMessageBox: vi.fn(async () => ({ response: 1 })) } as any, stopBackend: vi.fn(),
        logger: { log: vi.fn(), error: vi.fn() }, getMainWindow: () => null, relaunch: vi.fn(), openExternal: vi.fn(),
        platform: 'darwin', publicKeys: ['k'], fetchBytes,
      })
      updater.emit('update-downloaded', INFO)
      await vi.waitFor(() => expect(fetchBytes).toHaveBeenCalled())
      expect(fetchBytes.mock.calls.map((c: any) => c[0])).toContain('https://github.com/BurakErdemci/gamachine/releases/download/v9.9.9/latest-mac.yml.sig')
      handle.dispose()
    })
  })

  it('"now" stops the backend before quitAndInstall', async () => {
    const { updater, order, dialog, handle } = setup(0)
    updater.emit('update-downloaded', INFO)
    await vi.waitFor(() => expect(updater.quitAndInstall).toHaveBeenCalledWith(false, true))
    expect(dialog.showMessageBox).toHaveBeenCalledTimes(1)
    expect(order).toEqual(['stopBackend', 'quitAndInstall'])
    handle.dispose()
  })

  it('"later" does not quit', async () => {
    const { updater, stopBackend, dialog, handle } = setup(1)
    updater.emit('update-downloaded', INFO)
    await vi.waitFor(() => expect(dialog.showMessageBox).toHaveBeenCalledTimes(1))
    await Promise.resolve()
    expect(stopBackend).not.toHaveBeenCalled()
    expect(updater.quitAndInstall).not.toHaveBeenCalled()
    handle.dispose()
  })

  it('error is logged without a dialog or throw; no dialog for update-available', () => {
    const { updater, dialog, logger, handle } = setup()
    expect(() => updater.emit('error', new Error('boom'))).not.toThrow()
    updater.emit('update-available', INFO)
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
      updater.emit('update-downloaded', INFO)
      await vi.waitFor(() => expect(relaunch).toHaveBeenCalledTimes(1))
      handle.dispose()
    })

    it('error after the install attempt relaunches once, even if repeated', async () => {
      const { updater, relaunch, handle } = setup(0)
      updater.emit('update-downloaded', INFO)
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
      updater.emit('update-downloaded', INFO)
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
      updater.emit('update-downloaded', INFO)
      await vi.waitFor(() => expect(stopBackend).toHaveBeenCalled())
      vi.advanceTimersByTime(31 * 1000)
      expect(relaunch).toHaveBeenCalledTimes(1)
      expect(updater.quitAndInstall).not.toHaveBeenCalled()
      handle.dispose()
    })

    it('dispose during a quit in progress cancels the relaunch', async () => {
      let release!: () => void
      const { updater, relaunch, handle } = setup(0, undefined, () => new Promise<void>((r) => { release = r }))
      updater.emit('update-downloaded', INFO)
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
      updater.emit('update-downloaded', INFO)
      await Promise.resolve()
      expect(dialog.showMessageBox).not.toHaveBeenCalled()
      expect(stopBackend).not.toHaveBeenCalled()
    })

    it('a dialog answered after dispose starts nothing', async () => {
      let answer!: (v: any) => void
      const { updater, dialog, stopBackend, handle } = setup(0)
      dialog.showMessageBox.mockImplementation(() => new Promise((r) => { answer = r }) as any)
      updater.emit('update-downloaded', INFO)
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
