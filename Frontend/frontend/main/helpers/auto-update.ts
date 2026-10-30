import { createHash } from 'node:crypto'
import { createReadStream } from 'node:fs'
import type { BrowserWindow, MessageBoxOptions, MessageBoxReturnValue } from 'electron'
import { UPDATE_PUBLIC_KEYS } from './update-keys'
import { checkDownloaded, verifyManifest } from './update-signature'

const CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000
const INSTALL_TIMEOUT_MS = 30 * 1000
const FETCH_TIMEOUT_MS = 30 * 1000
const MAX_MANIFEST_BYTES = 1024 * 1024
const RELEASES_URL = 'https://github.com/BurakErdemci/gamachine/releases/latest'
const DOWNLOAD_BASE = 'https://github.com/BurakErdemci/gamachine/releases/download'
const VERSION_RE = /^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/

async function fetchBytes(url: string): Promise<Uint8Array> {
  const res = await fetch(url, { signal: AbortSignal.timeout(FETCH_TIMEOUT_MS) })
  if (!res.ok) throw new Error(`${url} -> HTTP ${res.status}`)
  const bytes = new Uint8Array(await res.arrayBuffer())
  if (bytes.length > MAX_MANIFEST_BYTES) throw new Error(`${url} çok büyük`)
  return bytes
}

function sha512Base64(file: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const hash = createHash('sha512')
    createReadStream(file).on('error', reject).on('data', (c) => hash.update(c)).on('end', () => resolve(hash.digest('base64')))
  })
}

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
  // Opens the release page when an update could not be verified.
  openExternal: (url: string) => Promise<void> | void
  // The rest default to the real implementations; tests inject fakes.
  publicKeys?: string[]
  platform?: string
  fetchBytes?: (url: string) => Promise<Uint8Array>
  hashFile?: (file: string) => Promise<string>
}

export interface AutoUpdateHandle {
  // Stops the periodic check; call on app quit.
  dispose: () => void
}

export function setupAutoUpdate(deps: AutoUpdateDeps): AutoUpdateHandle {
  const { autoUpdater, dialog, stopBackend, logger, getMainWindow, relaunch, openExternal } = deps
  const publicKeys = deps.publicKeys ?? UPDATE_PUBLIC_KEYS
  const platform = deps.platform ?? process.platform
  const getBytes = deps.fetchBytes ?? fetchBytes
  const hashFile = deps.hashFile ?? sha512Base64

  // Turned on only for a downloaded update whose signed manifest checked out.
  const setInstallOnQuit = (on: boolean) => { autoUpdater.autoInstallOnAppQuit = on }
  autoUpdater.autoDownload = true
  setInstallOnQuit(false)

  // Returns null when the downloaded file matches the signed manifest, else the reason.
  const verifyDownload = async (info: any): Promise<string | null> => {
    try {
      if (publicKeys.length === 0) return 'imza anahtarı tanımlı değil'
      const version = String(info?.version ?? '')
      if (!VERSION_RE.test(version)) return `geçersiz sürüm: ${version}`
      if (!info?.downloadedFile) return 'indirilen dosya yolu yok'
      const name = platform === 'darwin' ? 'latest-mac.yml' : 'latest.yml'
      const base = `${DOWNLOAD_BASE}/v${version}`
      const [manifest, sig] = await Promise.all([getBytes(`${base}/${name}`), getBytes(`${base}/${name}.sig`)])
      if (!verifyManifest(manifest, Buffer.from(sig).toString('utf8'), publicKeys)) return 'manifest imzası geçersiz'
      const result = checkDownloaded(
        { version, sha512: await hashFile(info.downloadedFile) },
        Buffer.from(manifest).toString('utf8'),
      )
      return result.ok ? null : result.reason ?? 'doğrulama başarısız'
    } catch (e) {
      return String((e as any)?.message || e)
    }
  }

  const showFailure = async () => {
    const options: MessageBoxOptions = {
      type: 'warning',
      buttons: ['Sürüm sayfasını aç', 'Kapat'],
      defaultId: 0,
      cancelId: 1,
      title: 'Güncelleme doğrulanamadı',
      message: 'İndirilen güncelleme doğrulanamadı, bu yüzden kurulmadı.',
      detail: 'Yeni sürümü sürüm sayfasından elle indirebilirsin.',
    }
    const win = getMainWindow()
    const { response } = win ? await dialog.showMessageBox(win, options) : await dialog.showMessageBox(options)
    if (disposed) return
    if (response === 0) await openExternal(RELEASES_URL)
  }

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
    if (disposed) return
    logger.log('[updater] güncelleme indirildi:', info?.version)
    setInstallOnQuit(false)
    try {
      const failure = await verifyDownload(info)
      if (disposed) return
      if (failure) {
        logger.error('[updater] güncelleme doğrulanamadı, kurulmayacak:', failure)
        await showFailure()
        return
      }
      setInstallOnQuit(true)
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
      if (disposed) return
      if (response === 0) {
        if (installing) return
        installing = true
        // Armed before stopBackend so a hung stop still ends in a relaunch.
        installTimer = setTimeout(() => failInstall('30 sn içinde çıkış olmadı'), INSTALL_TIMEOUT_MS)
        // Not checked for dispose here: an install the user already accepted still runs during a quit.
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
