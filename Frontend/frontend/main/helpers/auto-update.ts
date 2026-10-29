import type { BrowserWindow, MessageBoxOptions, MessageBoxReturnValue } from 'electron'

const CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000
const INSTALL_TIMEOUT_MS = 30 * 1000

// Minimal surface of electron-updater's autoUpdater that this module uses,
// so tests can inject a fake EventEmitter without loading Electron.
export interface UpdaterLike {
  autoDownload: boolean
  autoInstallOnAppQuit: boolean
  on(event: string, listener: (...args: any[]) => void): unknown
  checkForUpdates(): Promise<unknown>
  quitAndInstall: (isSilent?: boolean, isForceRunAfter?: boolean) => void
}

export interface AutoUpdateDeps {
  autoUpdater: UpdaterLike
  dialog: {
    showMessageBox(win: BrowserWindow, options: MessageBoxOptions): Promise<MessageBoxReturnValue>
    showMessageBox(options: MessageBoxOptions): Promise<MessageBoxReturnValue>
  }
  stopBackend: () => Promise<void> | void
  logger: Pick<Console, 'log' | 'error'>
  getMainWindow: () => BrowserWindow | null | undefined
  // Restarts the app when an install attempt fails after the backend was stopped.
  relaunch: () => void
}

export interface AutoUpdateHandle {
  // Stops the periodic check; call on app quit.
  dispose: () => void
}

export function setupAutoUpdate(deps: AutoUpdateDeps): AutoUpdateHandle {
  const { autoUpdater, dialog, stopBackend, logger, getMainWindow, relaunch } = deps

  autoUpdater.autoDownload = true
  autoUpdater.autoInstallOnAppQuit = true

  // The backend is gone once an install attempt starts; if the app does not quit,
  // relaunch (once) so it does not keep running without its backend.
  let installing = false
  let relaunched = false
  // Set by dispose(): a quit is under way, so a late failure must not relaunch a second app.
  let disposed = false
  let installTimer: ReturnType<typeof setTimeout> | undefined
  const failInstall = (reason: string) => {
    if (!installing || relaunched || disposed) return
    relaunched = true
    if (installTimer) clearTimeout(installTimer)
    logger.error('[updater] kurulum başarısız, uygulama yeniden başlatılıyor:', reason)
    try { relaunch() } catch (e) { logger.error('[updater] yeniden başlatma hatası:', (e as any)?.message || e) }
  }

  autoUpdater.on('error', (err: any) => {
    logger.error('[updater] hata:', err?.message || err)
    failInstall(String(err?.message || err))
  })
  autoUpdater.on('update-not-available', () => logger.log('[updater] uygulama güncel'))
  autoUpdater.on('update-downloaded', async (info: any) => {
    logger.log('[updater] güncelleme indirildi:', info?.version)
    try {
      const options: MessageBoxOptions = {
        type: 'info',
        buttons: ['Şimdi yeniden başlat', 'Sonra'],
        defaultId: 0,
        cancelId: 1,
        title: 'Güncelleme hazır',
        message: `Yeni sürüm hazır: v${info?.version}`,
        detail: 'Şimdi yeniden başlatırsan güncelleme kurulur. "Sonra" dersen uygulamayı kapattığında kurulur.',
      }
      const win = getMainWindow()
      const { response } = win ? await dialog.showMessageBox(win, options) : await dialog.showMessageBox(options)
      if (response === 0) {
        if (installing) return
        installing = true
        // Armed before stopBackend so a hung stop still ends in a relaunch.
        installTimer = setTimeout(() => failInstall('30 sn içinde çıkış olmadı'), INSTALL_TIMEOUT_MS)
        await stopBackend()
        autoUpdater.quitAndInstall(false, true)
      }
    } catch (e) {
      logger.error('[updater] kurulum hatası:', (e as any)?.message || e)
      failInstall(String((e as any)?.message || e))
    }
  })

  const check = () => {
    // checkForUpdates can throw synchronously or reject; neither may crash the app.
    try {
      Promise.resolve(autoUpdater.checkForUpdates()).catch((e: any) =>
        logger.error('[updater] kontrol başarısız:', e?.message || e))
    } catch (e) {
      logger.error('[updater] kontrol başarısız:', (e as any)?.message || e)
    }
  }
  check()
  const timer = setInterval(check, CHECK_INTERVAL_MS)
  return {
    dispose: () => {
      disposed = true
      clearInterval(timer)
      if (installTimer) clearTimeout(installTimer)
    },
  }
}
