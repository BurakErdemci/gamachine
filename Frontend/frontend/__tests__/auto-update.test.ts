import { EventEmitter } from 'node:events'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setupAutoUpdate } from '../main/helpers/auto-update'

function setup(response = 0, quitImpl?: () => void, stopImpl?: () => Promise<void>) {
  const updater: any = new EventEmitter()
  updater.checkForUpdates = vi.fn(() => Promise.resolve(null))
  const order: string[] = []
  updater.quitAndInstall = vi.fn(() => { order.push('quitAndInstall'); quitImpl?.() })
  const stopBackend = vi.fn(stopImpl ?? (async () => { order.push('stopBackend') }))
  const dialog = { showMessageBox: vi.fn(async () => ({ response, checkboxChecked: false })) }
  const logger = { log: vi.fn(), error: vi.fn() }
  const relaunch = vi.fn()
  const handle = setupAutoUpdate({
    autoUpdater: updater, dialog: dialog as any, stopBackend, logger, getMainWindow: () => null, relaunch,
  })
  return { updater, order, stopBackend, dialog, logger, handle, relaunch }
}

describe('setupAutoUpdate', () => {
  beforeEach(() => { vi.useFakeTimers() })
  afterEach(() => { vi.useRealTimers() })

  it('sets download flags and checks once at startup', () => {
    const { updater, handle } = setup()
    expect(updater.autoDownload).toBe(true)
    expect(updater.autoInstallOnAppQuit).toBe(true)
    expect(updater.checkForUpdates).toHaveBeenCalledTimes(1)
    handle.dispose()
  })

  it('"now" stops the backend before quitAndInstall', async () => {
    const { updater, order, dialog, handle } = setup(0)
    updater.emit('update-downloaded', { version: '9.9.9' })
    await vi.waitFor(() => expect(updater.quitAndInstall).toHaveBeenCalledWith(false, true))
    expect(dialog.showMessageBox).toHaveBeenCalledTimes(1)
    expect(order).toEqual(['stopBackend', 'quitAndInstall'])
    handle.dispose()
  })

  it('"later" does not quit', async () => {
    const { updater, stopBackend, dialog, handle } = setup(1)
    updater.emit('update-downloaded', { version: '9.9.9' })
    await vi.waitFor(() => expect(dialog.showMessageBox).toHaveBeenCalledTimes(1))
    await Promise.resolve()
    expect(stopBackend).not.toHaveBeenCalled()
    expect(updater.quitAndInstall).not.toHaveBeenCalled()
    handle.dispose()
  })

  it('error is logged without a dialog or throw; no dialog for update-available', () => {
    const { updater, dialog, logger, handle } = setup()
    expect(() => updater.emit('error', new Error('boom'))).not.toThrow()
    updater.emit('update-available', { version: '9.9.9' })
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
      updater.emit('update-downloaded', { version: '9.9.9' })
      await vi.waitFor(() => expect(relaunch).toHaveBeenCalledTimes(1))
      handle.dispose()
    })

    it('error after the install attempt relaunches once, even if repeated', async () => {
      const { updater, relaunch, handle } = setup(0)
      updater.emit('update-downloaded', { version: '9.9.9' })
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
      updater.emit('update-downloaded', { version: '9.9.9' })
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
      updater.emit('update-downloaded', { version: '9.9.9' })
      await vi.waitFor(() => expect(stopBackend).toHaveBeenCalled())
      vi.advanceTimersByTime(31 * 1000)
      expect(relaunch).toHaveBeenCalledTimes(1)
      expect(updater.quitAndInstall).not.toHaveBeenCalled()
      handle.dispose()
    })

    it('dispose during a quit in progress cancels the relaunch', async () => {
      let release!: () => void
      const { updater, relaunch, handle } = setup(0, undefined, () => new Promise<void>((r) => { release = r }))
      updater.emit('update-downloaded', { version: '9.9.9' })
      await vi.waitFor(() => expect(release).toBeTypeOf('function'))
      handle.dispose()
      vi.advanceTimersByTime(60 * 1000)
      release()
      await vi.waitFor(() => expect(updater.quitAndInstall).toHaveBeenCalled())
      updater.emit('error', new Error('late'))
      vi.advanceTimersByTime(60 * 1000)
      expect(relaunch).not.toHaveBeenCalled()
    })
  })
})
