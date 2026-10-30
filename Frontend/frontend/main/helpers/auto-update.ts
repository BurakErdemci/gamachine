import { createHash } from 'node:crypto'
import { createReadStream } from 'node:fs'
import { rm } from 'node:fs/promises'
import type { BrowserWindow, MessageBoxOptions, MessageBoxReturnValue } from 'electron'
import { UPDATE_PUBLIC_KEYS } from './update-keys'
import { checkAvailable, verifyManifest } from './update-signature'

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
  downloadUpdate(): Promise<unknown>
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
  removeFile?: (file: string) => Promise<void>
  // macOS: asks Squirrel (electron's native autoUpdater) to fetch and stage the update from
  // electron-updater's local proxy, so it installs on the next quit.
  stageNative?: () => void
}

export interface AutoUpdateHandle {
  // Stops the periodic check; call on app quit.
  dispose: () => void
}

// The update this session verified and is downloading or holding for the user.
interface VerifiedUpdate { version: string; sha512: string[] }

export function setupAutoUpdate(deps: AutoUpdateDeps): AutoUpdateHandle {
  const { autoUpdater, dialog, stopBackend, logger, getMainWindow, relaunch, openExternal } = deps
  const publicKeys = deps.publicKeys ?? UPDATE_PUBLIC_KEYS
  const platform = deps.platform ?? process.platform
  const getBytes = deps.fetchBytes ?? fetchBytes
  const hashFile = deps.hashFile ?? sha512Base64
  const removeFile = deps.removeFile ?? ((file: string) => rm(file, { force: true }))

  // Nothing downloads or installs on its own: an update is downloaded only after
  // its signed manifest checked out (update-available), and install-on-quit is armed
  // only after the app hashed the downloaded file itself (see update-downloaded).
  autoUpdater.autoDownload = false
  autoUpdater.autoInstallOnAppQuit = false

  const ask = (options: MessageBoxOptions) => {
    const win = getMainWindow()
    return win ? dialog.showMessageBox(win, options) : dialog.showMessageBox(options)
  }

  // Returns the verified update, or the reason it cannot be trusted.
  const verifyAvailable = async (info: any): Promise<VerifiedUpdate | string> => {
    try {
      if (publicKeys.length === 0) return 'imza anahtarı tanımlı değil'
      const version = String(info?.version ?? '')
      if (!VERSION_RE.test(version)) return `geçersiz sürüm: ${version}`
      const name = platform === 'darwin' ? 'latest-mac.yml' : 'latest.yml'
      const base = `${DOWNLOAD_BASE}/v${version}`
      const [manifest, sig] = await Promise.all([getBytes(`${base}/${name}`), getBytes(`${base}/${name}.sig`)])
      if (!verifyManifest(manifest, Buffer.from(sig).toString('utf8'), publicKeys)) return 'manifest imzası geçersiz'
      // electron-updater downloads (and hash-checks) the file named by its own copy of the manifest.
      const files: any[] = Array.isArray(info.files) && info.files.length ? info.files : [info]
      const sha512 = files.map((f) => f?.sha512)
      const result = checkAvailable({ version, sha512 }, Buffer.from(manifest).toString('utf8'))
      return result.ok ? { version, sha512 } : result.reason!
    } catch (e) {
      return String((e as any)?.message || e)
    }
  }

  // At most one failure dialog per version, however often the check repeats.
  const warned = new Set<string>()
  const showFailure = async (version: string) => {
    if (warned.has(version)) return
    warned.add(version)
    const { response } = await ask({
      type: 'warning',
      buttons: ['Sürüm sayfasını aç', 'Kapat'],
      defaultId: 0,
      cancelId: 1,
      title: 'Güncelleme doğrulanamadı',
      message: 'Yeni sürümün imzası doğrulanamadı, bu yüzden indirilip kurulmadı.',
      detail: 'Yeni sürümü sürüm sayfasından elle indirebilirsin.',
    })
    if (disposed) return
    if (response === 0) await openExternal(RELEASES_URL)
  }

  let verified: VerifiedUpdate | null = null
  let downloaded = false
  const forget = () => {
    verified = null
    downloaded = false
    autoUpdater.autoInstallOnAppQuit = false
  }
  // Install-on-quit for the hash-checked file, armed only after the user picked "later".
  // NSIS/Linux: BaseUpdater.addQuitHandler registers the quit-time install only if the flag is
  // true at that moment (it is protected, so reached by name). macOS: MacUpdater has no quit
  // handler; Squirrel installs a staged update on quit, so staging is asked for here.
  const armInstallOnQuit = () => {
    const register = platform === 'darwin' ? deps.stageNative : (autoUpdater as any).addQuitHandler?.bind(autoUpdater)
    autoUpdater.autoInstallOnAppQuit = register !== undefined
    if (!register) {
      logger.error('[updater] çıkışta kurulum kaydedilemedi, güncelleme çıkışta kurulmayacak')
      return
    }
    register()
  }
  // Bumped per update-available so only the latest one proceeds after its async check.
  let latestCheck = 0

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
  autoUpdater.on('update-available', async (info: any) => {
    // While a verified update is downloading or waiting for the user, no other one is considered.
    if (disposed || verified) return
    const mine = ++latestCheck
    const result = await verifyAvailable(info)
    if (disposed || verified || mine !== latestCheck) return
    if (typeof result === 'string') {
      logger.error('[updater] güncelleme doğrulanamadı, indirilmeyecek:', result)
      await showFailure(String(info?.version))
      return
    }
    logger.log('[updater] imzalı güncelleme bulundu, indiriliyor:', result.version)
    verified = result
    Promise.resolve(autoUpdater.downloadUpdate()).catch((e: any) => {
      logger.error('[updater] indirme başarısız:', e?.message || e)
      if (verified === result) forget()
    })
  })
  autoUpdater.on('update-downloaded', async (info: any) => {
    const generation = verified
    if (disposed || !generation || downloaded) return
    downloaded = true
    logger.log('[updater] güncelleme indirildi:', info?.version)
    try {
      let matches = false
      try {
        matches = info?.version === generation.version && !!info?.downloadedFile && generation.sha512.includes(await hashFile(info.downloadedFile))
      } catch (e) {
        logger.error('[updater] indirilen dosya okunamadı:', (e as any)?.message || e)
      }
      if (disposed) return
      if (!matches) {
        logger.error('[updater] indirilen dosya doğrulanan güncellemeyle uyuşmuyor, kurulmayacak')
        forget()
        if (info?.downloadedFile) await removeFile(info.downloadedFile).catch((e: any) => logger.error('[updater] dosya silinemedi:', e?.message || e))
        await showFailure(generation.version)
        return
      }
      const { response } = await ask({
        type: 'info',
        buttons: ['Şimdi yeniden başlat', 'Sonra'],
        defaultId: 0,
        cancelId: 1,
        title: 'Güncelleme hazır',
        message: `Yeni sürüm hazır: v${generation.version}`,
        detail: 'Şimdi yeniden başlatırsan güncelleme kurulur. "Sonra" dersen uygulamayı kapattığında kurulur.',
      })
      if (disposed) return
      if (response !== 0) {
        if (verified === generation) armInstallOnQuit()
      } else {
        if (installing || verified !== generation) return
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
    if (verified) return
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
