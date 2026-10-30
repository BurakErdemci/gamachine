import path from 'path'
import fs from 'fs'
import os from 'os'
import net from 'net'
import { randomUUID, createHash, randomBytes } from 'crypto'
import { app, ipcMain, dialog, shell, BrowserWindow, Notification, powerSaveBlocker, systemPreferences, globalShortcut, screen, desktopCapturer, WebContentsView, session } from 'electron'
import serve from 'electron-serve'
import { createWindow, applyNavigationPolicy } from './helpers'
import { spawn, ChildProcess, execFile } from 'child_process'
import { promisify } from 'util'
import axios from 'axios'
import { autoUpdater } from 'electron-updater'
import { setupAutoUpdate, type AutoUpdateHandle } from './helpers/auto-update'
import {
  TEXT_MAX_BYTES,
  isAllowedWorkspacePath,
  okumaKarariVer,
  readExactly,
  taniticiKapsamdaMi,
  isAllowedWorkspaceWriteFile,
  readModelFileGuarded,
  readImageFileGuarded,
} from './helpers/file-security'
import {
  confirmLegacyRoot,
  confirmRecentRoot,
  isOwnFrame,
  isTrustedRoot,
  registerTrustedRoot,
} from './helpers/ipc-trust'
import { applyContentSecurityPolicy } from './helpers/csp'
import { assertRegistrableChannel } from './helpers/ipc-whitelist'
import { inspectProject, isUnityProjectDir } from './helpers/unity-project'
import { findUnityHub } from './helpers/unity-hub'
import { listEditors, listTemplates, templatesDir, EditorDeps } from './helpers/unity-editors'
import { createProject, cancelCreate } from './helpers/unity-create'
import { openProject } from './helpers/unity-launch'
import { readUnityVersion } from './helpers/unity-project'
import * as pty from 'node-pty'
import {
  DOCKER_WORKSPACE_MOUNT,
  hostRootFrom,
  toBackendPath,
  toHostPath,
} from './helpers/workspace-mapping'
import {
  WORKSPACE_FINGERPRINT_ALGO,
  hostWorkspaceFingerprint,
} from './helpers/workspace-fingerprint'
import { createNotifier } from './helpers/notify'
import { createRemoteControl } from './helpers/remote-control'
import { ComputerUseHost } from './helpers/computer-use'
import { ComputerLive, LIVE_OPEN_EVENT } from './helpers/computer-use-live'
import { hideOrMinimize } from './helpers/hide-while-live'
import { reopenMain } from './helpers/reopen-main'
import { createProfileReset } from './helpers/profile-reset'
import { collectMtimes } from './helpers/stat-mtimes'
import { BrowserHost } from './helpers/browser-host'
import { BrowserHistory } from './helpers/browser-history'
import { startCommandServer, type CommandServer } from './helpers/browser-server'

const useDockerBackend = process.env.USE_DOCKER_BACKEND === 'true'


// A fresh token per launch is the whole point: it is never written to disk and
// it dies with the process. That stays true for the normal path, where the
// backend is a child process and inherits this value through its env.
//
// Docker mode is the one case where inheritance is impossible — the backend is
// already running in a container that this process did not start. There the
// value has to come from the outside, from the same variable Compose reads, or
// the two sides simply hold different secrets and every request 401s. That was
// the actual state of Docker mode until 31 Aug 2026: it had never worked.
//
// The env var is honoured ONLY in Docker mode, so the normal path cannot have
// its per-launch randomness weakened by a stray variable in someone's shell.
const localAppToken = useDockerBackend && process.env.LOCAL_APP_TOKEN
  ? process.env.LOCAL_APP_TOKEN
  : randomUUID()

// Second secret, only for flipping the global approval mode. LOCAL_APP_TOKEN
// is not enough: the Unity MCP server gets it in its env and model-run child
// processes can read its 0600 file, so any of them could switch itself into
// auto mode. This one lives only here and in the backend process (handed over
// stdin at spawn), and the renderer reaches it only through 'approval-mode-set'.
const uiSecret = randomUUID()

const isProd = process.env.NODE_ENV === 'production'
let pyBackendProcess: ChildProcess | null = null
let backendPort: number | null = null
const BACKEND_HOST = '127.0.0.1'

// --- DOSYA LOGLAMA (paketlenmiş app'te konsol görünmez) ---
// Logları hem temp hem userData altına yazar; başka PC'de teşhis için kalıcı.
const logFilePath = path.join(os.tmpdir(), 'gamachine.log')
function fileLog(level: string, args: any[]) {
  try {
    const line = `[${new Date().toISOString()}] [${level}] ` +
      args.map(a => (typeof a === 'string' ? a : (a instanceof Error ? `${a.message}\n${a.stack}` : (() => { try { return JSON.stringify(a) } catch { return String(a) } })()))).join(' ') + '\n'
    fs.appendFileSync(logFilePath, line)
  } catch { /* log yazılamadı, sessizce geç */ }
}
{
  const origLog = console.log.bind(console)
  const origErr = console.error.bind(console)
  const origWarn = console.warn.bind(console)
  console.log = (...a: any[]) => { fileLog('INFO', a); try { origLog(...a) } catch {} }
  console.error = (...a: any[]) => { fileLog('ERROR', a); try { origErr(...a) } catch {} }
  console.warn = (...a: any[]) => { fileLog('WARN', a); try { origWarn(...a) } catch {} }
  process.on('uncaughtException', (e) => fileLog('UNCAUGHT', [e]))
  process.on('unhandledRejection', (e) => fileLog('UNHANDLED_REJECTION', [e as any]))
  fileLog('INFO', [`=== APP BAŞLADI === isProd=${isProd} resourcesPath=${process.resourcesPath} platform=${process.platform}`])
}

// --- IPC KAPILARI (2026-07-27 denetimi, D grubu) ---

/**
 * `ipcMain.handle` yerine bunu kullan. Tek işi: çağrının bizim sayfamızdan
 * geldiğini doğrulamak.
 *
 * Sebebi somut — preload `window.ipc`'yi KOŞULSUZ expose ediyor, yani renderer'da
 * çalışan her belge (uzak bir sayfa, enjekte bir script) 24 kanalın hepsine
 * erişebiliyordu: `app-token-get` ile backend bearer'ı, `terminal-spawn` +
 * `terminal-write` ile kullanıcının kabuğu. Gezinme politikası bu belgenin
 * renderer'a GİRMESİNİ engelliyor; burası o politika delinirse diye ikinci kilit.
 *
 * Reddedilen çağrı sessiz kalmıyor: log'a düşüyor, çünkü sessiz bir ret meşru bir
 * regresyonla saldırıyı ayırt edilemez kılar.
 */
const handleSecure = (
  channel: string,
  listener: (event: Electron.IpcMainInvokeEvent, ...args: any[]) => any,
) => {
  // The permitted-channel list is enforced HERE, not only in the preload.
  //
  // Measured (external audit): with the list replaced by one that rejected
  // every channel, all 27 handlers of the day still registered and `read-model-file` still
  // returned file bytes — the receiving layer consulted the list zero times, so
  // whether a channel was on it made no difference to what the main process
  // would serve. Anything reaching `ipcMain` without going through this app's
  // preload (a context-isolation regression, a second preload, a devtools
  // surface) was unfiltered, and the list's own test suite read like an
  // enforcement proof it was not.
  //
  // Refusing at REGISTRATION rather than at invoke: an unlisted channel then
  // has no handler at all, and the declaration and the enforcement cannot drift
  // apart quietly — adding a handler without a list entry fails at startup,
  // which every test that loads this module already exercises.
  assertRegistrableChannel(channel)
  ipcMain.handle(channel, (event, ...args) => {
    if (!isOwnFrame(event)) {
      console.error(`[ipc-trust] '${channel}' reddedildi — gönderen:`, event.senderFrame?.url ?? '(bilinmiyor)')
      throw new Error('IPC reddedildi: gönderen çerçeve uygulamaya ait değil.')
    }
    return listener(event, ...args)
  })
}

/**
 * Workspace kökünün kendisi çağırandan geliyordu; yani hapsi hapsedilen
 * seçiyordu. `read-file($HOME/.codex/auth.json, $HOME)` bu yüzden kimlik
 * bilgisi okuyabiliyordu — yol "iddia edilen kök"ün içinde kalıyor.
 *
 * Artık kök, kullanıcının native diyalogla seçtiği kütüğe karşı doğrulanıyor.
 * Hata döndürürse çağıran handler kendi boş/başarısız değerini vermeli.
 */
const untrustedWorkspace = (workspacePath?: string): string | null => {
  if (!workspacePath) return 'Workspace path eksik.'
  if (isTrustedRoot(workspacePath) || confirmLegacyRoot(workspacePath)) return null
  return 'Bu klasör yetkili değil — klasörü uygulama içinden yeniden seçin.'
}

// --- TERMINAL YÖNETİMİ ---
const ptyProcesses: Map<string, pty.IPty> = new Map();

handleSecure('terminal-spawn', (event, { id, cwd }) => {
  const isWin = process.platform === 'win32';
  
  // Profesyonel Shell Tespiti
  let shell = isWin ? 'powershell.exe' : (process.env.SHELL || '/bin/zsh');
  
  if (!isWin && !shell.startsWith('/')) {
    const commonPaths = ['/bin/zsh', '/usr/bin/zsh', '/bin/bash', '/usr/bin/bash', '/bin/sh'];
    for (const p of commonPaths) {
      if (fs.existsSync(p)) { shell = p; break; }
    }
  }

  // CWD ve Env Hazırlığı — HOME Windows'ta yok, USERPROFILE/os.homedir()'a düş
  const homeDir = process.env.HOME || process.env.USERPROFILE || os.homedir();
  let finalCwd = cwd || homeDir || '.';
  if (typeof finalCwd === 'string' && finalCwd.startsWith('~')) {
    finalCwd = path.join(homeDir || '', finalCwd.slice(1));
  }
  if (finalCwd && !fs.existsSync(finalCwd)) finalCwd = homeDir || '.';

  // cwd çağırandan geliyor ve kabuk o dizinde açılıyor. Yetkili bir workspace
  // değilse ev dizinine düşürülüyor: kabuk zaten kullanıcının yetkisiyle
  // çalışıyor, ama saldırganın SEÇTİĞİ bir dizinde başlaması (ör. bir git
  // reposunun içi, bir kimlik bilgisi klasörü) fazladan bilgi ve kolaylık verir.
  if (!isTrustedRoot(finalCwd)) {
    console.warn('[Terminal] yetkisiz cwd, ev dizinine düşülüyor:', finalCwd);
    finalCwd = homeDir || '.';
  }

  console.log(`[Terminal] Profesyonel Başlatma: ${shell} @ ${finalCwd}`);

  try {
    // Windows'ta PowerShell kendi ortamını kurar; Unix env değişkenleri (LANG, SHELL,
    // USER...) yalnızca POSIX shell'ler için anlamlı. Windows'ta sadece PATH/HOME geçir.
    const ptyEnv: Record<string, string> = isWin
      ? { ...process.env as Record<string, string> }
      : {
          ...process.env as Record<string, string>,
          HOME:      process.env.HOME      || homeDir,
          USER:      process.env.USER      || os.userInfo().username,
          LOGNAME:   process.env.LOGNAME   || os.userInfo().username,
          SHELL:     shell,
          PATH:      process.env.PATH      || '/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin',
          LANG:      'en_US.UTF-8',
          TERM:      'xterm-256color',
          COLORTERM: 'truecolor',
        };
    const ptyProcess = pty.spawn(shell, [], {
      name: 'xterm-256color',
      cols: 80,
      rows: 24,
      cwd: finalCwd,
      env: ptyEnv,
    });

    ptyProcesses.set(id, ptyProcess);

    ptyProcess.onData((data) => {
      event.sender.send(`terminal-data-${id}`, data);
    });

    ptyProcess.onExit(({ exitCode, signal }) => {
      event.sender.send(`terminal-exit-${id}`, { exitCode, signal });
      ptyProcesses.delete(id);
    });

    return { success: true, shell };
  } catch (err: any) {
    console.error(`[Terminal] Başlatma Hatası:`, err);
    return { success: false, error: err.message, shell };
  }
});

handleSecure('terminal-write', (_event, { id, data }) => {
  const ptyProcess = ptyProcesses.get(id);
  if (ptyProcess) {
    ptyProcess.write(data);
    return { success: true };
  }
  return { success: false, error: 'Terminal bulunamadı' };
});

handleSecure('terminal-resize', (_event, { id, cols, rows }) => {
  const ptyProcess = ptyProcesses.get(id);
  if (ptyProcess) {
    ptyProcess.resize(cols, rows);
    return { success: true };
  }
  return { success: false, error: 'Terminal bulunamadı' };
});

function getBackendBaseUrl(): string {
  if (!backendPort) {
    throw new Error('Backend URL henüz hazır değil.')
  }
  return `http://${BACKEND_HOST}:${backendPort}`
}

if (isProd) {
  serve({ directory: 'app' })
} else {
  app.setPath('userData', `${app.getPath('userData')} (development)`)
}

// --- IPC: DOSYA İŞLEMLERİ ---
handleSecure('open-file-dialog', async () => {
  const result = await dialog.showOpenDialog({
    properties: ['openFile'],
    filters: [
      { name: 'Kod ve Metin Dosyaları', extensions: ['cs', 'shader', 'json', 'txt', 'md', 'xml', 'yaml', 'yml', 'compute', 'asmdef', 'cginc', 'hlsl', 'uss', 'uxml'] }
    ]
  })
  if (result.canceled || result.filePaths.length === 0) return null
  const filePath = result.filePaths[0]
  const content = fs.readFileSync(filePath, 'utf-8')
  return { path: filePath, name: path.basename(filePath), content }
})

// Video için: içerik OKUNMAZ (video büyük) — sadece mutlak yol(lar) döner.
// Electron 34'te File.path kaldırıldığı için renderer yolu buradan alır.
handleSecure('open-video-dialog', async () => {
  const result = await dialog.showOpenDialog({
    properties: ['openFile', 'multiSelections'],
    filters: [
      { name: 'Video', extensions: ['mp4', 'mov', 'mkv', 'webm', 'avi', 'm4v'] }
    ]
  })
  if (result.canceled || result.filePaths.length === 0) return null
  return result.filePaths.map(p => ({ path: p, name: path.basename(p) }))
})

handleSecure('open-folder-dialog', async (_event, defaultPath: unknown) => {
  const result = await dialog.showOpenDialog({
    properties: ['openDirectory'],
    ...(typeof defaultPath === 'string' && path.isAbsolute(defaultPath) ? { defaultPath } : {})
  })
  if (result.canceled || result.filePaths.length === 0) return null
  // A suggested default path does not grant trust; only the selected result does.
  registerTrustedRoot(result.filePaths[0])
  return result.filePaths[0]
})

handleSecure('workspace-info', (_event, paths: unknown) => {
  if (!Array.isArray(paths)) return []
  return paths.filter((p): p is string => typeof p === 'string').slice(0, 50).map(p => {
    if (!isTrustedRoot(p)) return { path: p, status: 'untrusted', unityVersion: null }
    const info = inspectProject(p)
    return { path: p, status: info.exists ? 'ok' : 'missing', unityVersion: info.unityVersion }
  })
})

handleSecure('authorize-workspace', (event, p: unknown) => {
  if (typeof p !== 'string' || !p || !path.isAbsolute(p)) return { trusted: false }
  return { trusted: confirmRecentRoot(p, BrowserWindow.fromWebContents(event.sender) ?? undefined) }
})

handleSecure('open-unity-hub', async () => {
  try {
    const hub = findUnityHub(process.platform, process.env, os.homedir(), fs.existsSync)
    return { opened: hub ? await shell.openPath(hub) === '' : false }
  } catch {
    return { opened: false }
  }
})

// --- MINI UNITY HUB: editors, templates, project creation, launch ---
const editorDeps = (): EditorDeps => ({
  exists: fs.existsSync,
  readdir: (p) => fs.readdirSync(p),
  readFile: (p) => fs.readFileSync(p, 'utf-8'),
  platform: process.platform,
  env: process.env,
  homedir: os.homedir(),
})
// Only folders the native dialog returned may parent a new project.
const pickedParentDirs = new Set<string>()

const readLogTail = (file: string): string => {
  const fd = fs.openSync(file, 'r')
  try {
    const size = fs.fstatSync(fd).size
    const length = Math.min(size, 4096)
    const buffer = Buffer.alloc(length)
    fs.readSync(fd, buffer, 0, length, size - length)
    return buffer.toString('utf8')
  } finally {
    fs.closeSync(fd)
  }
}

handleSecure('hub-list-editors', () => listEditors(editorDeps()))

handleSecure('hub-list-templates', (_event, arg: any) => {
  if (!arg || typeof arg.version !== 'string') return { templates: [] }
  return listTemplates(arg.version, editorDeps())
})

handleSecure('hub-pick-parent-dir', async () => {
  const result = await dialog.showOpenDialog({ properties: ['openDirectory', 'createDirectory'] })
  if (result.canceled || result.filePaths.length === 0) return { path: null }
  pickedParentDirs.add(result.filePaths[0])
  return { path: result.filePaths[0] }
})

handleSecure('hub-create-project', async (event, arg: any) => {
  if (!arg || typeof arg.parentDir !== 'string' || typeof arg.name !== 'string' ||
      typeof arg.version !== 'string' || (arg.templateId !== undefined && typeof arg.templateId !== 'string')) {
    return { ok: false, error: 'Invalid arguments.' }
  }
  if (!pickedParentDirs.has(arg.parentDir)) return { ok: false, error: 'Choose the parent folder with the folder picker.' }
  const deps = editorDeps()
  const editor = listEditors(deps).editors.find(e => e.version === arg.version)
  if (!editor) return { ok: false, error: `Unity ${arg.version} is not installed.` }
  let templatePath: string | undefined
  if (arg.templateId) {
    const template = listTemplates(arg.version, deps).templates.find(t => t.id === arg.templateId)
    if (!template) return { ok: false, error: 'Unknown template.' }
    const dir = templatesDir(editor.path, process.platform)
    templatePath = (process.platform === 'win32' ? path.win32 : path.posix).join(dir, template.file)
  }
  return createProject({
    editorPath: editor.path,
    parentDir: arg.parentDir,
    name: arg.name,
    templatePath,
    onProgress: (progress) => {
      if (!event.sender.isDestroyed()) event.sender.send('hub-create-progress', progress)
    },
  }, {
    platform: process.platform,
    exists: fs.existsSync,
    realpath: fs.realpathSync,
    // To the Trash, not deleted: a project copied into the target during the attempt is born
    // after it started too, so no birth-time rule can tell it apart (measured 29 Oct 2026).
    removeDir: (dir) => { void shell.trashItem(dir).catch((e) => console.error('[hub] partial folder not moved to Trash:', dir, e?.message || e)) },
    spawn: (command, args) => spawn(command, args, { stdio: 'ignore' }),
    readLogTail,
    isProject: isUnityProjectDir,
    registerTrusted: registerTrustedRoot,
    logFile: () => path.join(os.tmpdir(), `gamachine-create-${randomUUID()}.log`),
    now: Date.now,
  })
})

handleSecure('hub-cancel-create', () => cancelCreate())

handleSecure('hub-open-project', async (_event, arg: any) => {
  if (!arg || typeof arg.path !== 'string' || (arg.version !== undefined && typeof arg.version !== 'string')) {
    return { opened: false, error: 'Invalid arguments.' }
  }
  return openProject({ path: arg.path, version: arg.version }, {
    editors: editorDeps(),
    isTrusted: isTrustedRoot,
    isProject: isUnityProjectDir,
    readVersion: readUnityVersion,
    spawn: (command, args, options) => spawn(command, args, options),
  })
})

handleSecure('register-dropped-folder', (_event, p: unknown) => {
  if (typeof p !== 'string' || !p || !path.isAbsolute(p)) return { error: 'not-a-folder' }
  try {
    const real = fs.realpathSync(p)
    if (!fs.statSync(real).isDirectory()) return { error: 'not-a-folder' }
    if (!isUnityProjectDir(real)) return { error: 'not-a-unity-project' }
    registerTrustedRoot(real)
    return { path: real }
  } catch {
    return { error: 'not-a-folder' }
  }
})

handleSecure('read-directory', async (_event, dirPath: string, workspacePath?: string) => {
  try {
    if (!workspacePath) return [];
    const _ws = untrustedWorkspace(workspacePath); if (_ws) return [];
    const fullPath = path.isAbsolute(dirPath) ? dirPath : path.join(workspacePath, dirPath);
    if (!isAllowedWorkspacePath(fullPath, workspacePath)) {
      return []
    }
    const entries = fs.readdirSync(fullPath, { withFileTypes: true })
    const items = entries
      .filter(e => !e.name.startsWith('.'))
      // Ağaç HER dosyayı gösterir (prefab/anim/fbx/png…) — yalnız .meta gürültüsü
      // gizli (her asset'in yanında bir tane var, ağacı ikiye katlar).
      .filter(e => e.isDirectory() || path.extname(e.name).toLowerCase() !== '.meta')
      .map(e => ({
        name: e.name,
        path: path.join(fullPath, e.name),
        isDirectory: e.isDirectory(),
        extension: e.isDirectory() ? '' : path.extname(e.name).toLowerCase()
      }))
      .sort((a, b) => {
        if (a.isDirectory && !b.isDirectory) return -1
        if (!a.isDirectory && b.isDirectory) return 1
        return a.name.localeCompare(b.name)
      })
    return items
  } catch { return [] }
})

// VSCode tarzı git durum rozetleri: workspace bir git reposuysa değişen/yeni/
// silinen dosyaların mutlak-yol → durum haritasını döner (dosya ağacı boyar).
handleSecure('git-status', async (_event, workspacePath?: string) => {
  const empty = { isRepo: false, files: {}, dirs: {}, mtimes: {} }
  try {
    if (!workspacePath || !fs.existsSync(workspacePath)) return empty
    // Kök doğrulanmasaydı `git -C <herhangi bir dizin>` çalıştırılabilirdi:
    // kullanıcının makinesindeki başka bir reponun dosya listesi ve durumu.
    if (untrustedWorkspace(workspacePath)) return empty
    const { execFile } = require('child_process') as typeof import('child_process')
    const run = (args: string[]) => new Promise<string>((resolve, reject) => {
      execFile('git', ['-C', workspacePath, ...args], {
        timeout: 8000, maxBuffer: 8 * 1024 * 1024, windowsHide: true,
      }, (err, stdout) => err ? reject(err) : resolve(stdout))
    })

    const toplevel = (await run(['rev-parse', '--show-toplevel'])).trim()
    if (!toplevel) return empty
    // `--show-toplevel` seçili klasörün DEĞİL, onu içeren reponun kökünü döndürür.
    // Workspace bir monorepo'nun alt dizini ise burada dışarıdaki bir yol ve onun
    // altındaki tüm değişiklikler görünür hâle geliyordu — yani kullanıcının
    // yetkilendirdiğinden fazlası. Kök kütüğün dışına çıkıyorsa repo durumu yok.
    if (!isTrustedRoot(toplevel)) {
      console.warn('[git-status] repo kökü yetkili alan dışında, atlanıyor:', toplevel)
      return empty
    }
    // -z: NUL ayraçlı (boşluklu/unicode yollar güvenli); untracked dosyalar tek tek
    const raw = await run(['status', '--porcelain', '-z', '--untracked-files=all'])

    const files: Record<string, string> = {}
    const dirs: Record<string, string> = {}
    const statEntries: Array<[string, string]> = []
    const parts = raw.split('\0')
    let count = 0
    for (let i = 0; i < parts.length && count < 8000; i++) {
      const p = parts[i]
      if (p.length < 4) continue
      const xy = p.slice(0, 2)
      const rel = p.slice(3)
      if (!rel) continue
      // Rename kayıtlarında sonraki eleman ESKİ yoldur → atla
      if (xy[0] === 'R' || xy[0] === 'C') i++
      let status = 'modified'
      if (xy === '??') status = 'untracked'
      else if (xy.includes('D')) status = 'deleted'
      else if (xy.includes('A')) status = 'added'
      // Anahtarlar lowercase: renderer'daki entry.path ile sürücü-harfi/case
      // farklarından bağımsız eşleşme (renderer da lowercase ile arar).
      const originalAbs = path.join(toplevel, rel)
      const abs = path.normalize(originalAbs).toLowerCase()
      files[abs] = status
      statEntries.push([abs, originalAbs])
      count++
      // Üst klasörleri işaretle (workspace köküne kadar) — klasörde nokta rozeti.
      // Windows'ta sürücü harfi büyük/küçük gelebilir (C:\ vs c:\) → karşılaştırma
      // case-insensitive, anahtarlar orijinal (path.join ile aynı) case'te tutulur.
      let dir = path.dirname(abs)
      const rootLower = path.normalize(workspacePath).toLowerCase()
      while (dir.startsWith(rootLower) && !dirs[dir]) {
        dirs[dir] = 'contains'
        const parent = path.dirname(dir)
        if (parent === dir) break
        dir = parent
      }
    }
    const mtimes = await collectMtimes(statEntries, p => fs.promises.stat(p))
    return { isRepo: true, files, dirs, mtimes }
  } catch {
    return empty // git yok / repo değil / timeout → rozetsiz devam
  }
})

handleSecure('read-file', async (_event, filePath: string, workspacePath?: string) => {
  try {
    if (!workspacePath) return null;
    const _ws = untrustedWorkspace(workspacePath); if (_ws) return null;
    const fullPath = path.isAbsolute(filePath) ? filePath : path.join(workspacePath, filePath);
    // ⚠️ Kapı ve açış AYNI çözümü paylaşmak ZORUNDA (doğrulama turu bulgusu
    // `check-open-resolve-divergence`). Eskiden kapı kendi içinde bir kez,
    // çağıran ikinci kez çözüyordu; aradaki pencerede bir ara bileşen
    // junction'a çevrilince kontrol edilen dosya ile açılan dosya ayrışıyordu.
    const karar = okumaKarariVer(fullPath, workspacePath)
    if (!karar.izinli) {
      return { error: 'unsupported' }
    }
    // ⚠️ KONTROL EDİLEN ile OKUNAN aynı dosya olmalı (dış denetim bulgusu
    // `path-check-use-race`, probe ile üretildi). Eskiden kapı çözülmüş yol
    // üzerinde karar veriyor, `statSync`/`readFileSync` ise HAM yolu okuyordu;
    // aradaki pencerede bir junction hedefini değiştirirse ürün workspace
    // dışındaki dosyayı döndürüyordu.
    //
    // Çözüm bir kez AÇIP tanıtıcıdan ilerlemek: `fstat` ve okuma artık kapının
    // gördüğü inode'a bağlı, yola değil. Aynı desen backend'de zaten var
    // (`local_token_file` fd üzerinden doğruluyor) — burada tekrar edilmesinin
    // sebebi iki ayrı süreç olması, kopyalama değil.
    // Kapının kararını verdiği yol açılıyor — ham yol DEĞİL.
    const okunacak = karar.cozulmusYol
    let fd: number | null = null
    try {
      fd = fs.openSync(okunacak, 'r')
      // `{ bigint: true }` so the descriptor identity check below compares exact
      // 64-bit ids; the reason is in `taniticiKapsamdaMi`.
      const st = fs.fstatSync(fd, { bigint: true })
      if (!st.isFile()) return { error: 'unsupported' }
      // Gerekçesi sabitin tanımında (`TEXT_MAX_BYTES`, file-security.ts).
      // The cap is enforced BEFORE any Number() conversion: a size above 2^53
      // would round on the way through a double, so converting first could turn
      // an over-cap file into an under-cap one.
      if (st.size > BigInt(TEXT_MAX_BYTES)) {
        return { error: 'too-large' }
      }
      // Sabit bağ kapıda da reddediliyor; burada tekrar bakmak, açış ile kapı
      // arasında yaratılmış bir ikinci adı da yakalıyor.
      // `BigInt(1)`, not `1n`: tsconfig targets ES2017, where TS rejects the
      // literal form.
      if (st.nlink > BigInt(1)) return { error: 'unsupported' }

      // Sınıfı kapatan kontrol; gerekçesi fonksiyonun başında.
      if (!taniticiKapsamdaMi(st, okunacak, workspacePath)) {
        return { error: 'unsupported' }
      }

      // Read bounded by the size the cap was checked against — the SAME shape
      // the model path uses, and for the same reason: `readFileSync(fd)` reads
      // until EOF, so a file appended to between `fstatSync` and the read
      // returned more than `TEXT_MAX_BYTES`. Measured: a 1-byte file grown by
      // 9 MiB in that window came back whole.
      const bytes = readExactly(fd, Number(st.size))
      if (!bytes) return { error: 'unsupported' }
      const content = Buffer.from(bytes.buffer, bytes.byteOffset, bytes.byteLength).toString('utf-8')
      // `fullPath` döndürülüyor: kapsama kanıtlandıktan sonra o ad da aynı
      // inode'u gösteriyor, ve dosya ağacı da bu adı kullanıyor — çözülmüş yolu
      // döndürmek workspace'in kendisi bir junction olduğunda ağaçla editörün
      // yolunu ayrıştırırdı.
      return { path: fullPath, name: path.basename(fullPath), content }
    } finally {
      if (fd !== null) { try { fs.closeSync(fd) } catch { /* kapatma hatası okumayı geçersizleştirmez */ } }
    }
  } catch { return null }
})

// 3D model preview channel. The gate chain is the SAME one `read-file` runs;
// only the extension whitelist and the size cap differ, and both are parameters
// of the shared decision path in `file-security`. Every "why" for the checks
// below is written once, at the `read-file` handler and in `file-security` —
// not repeated here.
handleSecure('read-model-file', async (_event, filePath: string, workspacePath?: string) => {
  try {
    if (!workspacePath) return null;
    const _ws = untrustedWorkspace(workspacePath); if (_ws) return null;
    const fullPath = path.isAbsolute(filePath) ? filePath : path.join(workspacePath, filePath);
    // Bounded in number as well as in per-file size; the count and the reason
    // for it are at `MODEL_READ_MAX_IN_FLIGHT`.
    return await readModelFileGuarded(fullPath, workspacePath)
  } catch { return null }
})

// Image preview channel. Same gate chain, same in-flight budget as the model
// read; only the extension whitelist (IMAGE_FILE_EXTENSIONS) and the size cap
// (IMAGE_MAX_BYTES) differ, and both are parameters of the shared decision path
// in `file-security`. Every "why" is written once, there.
handleSecure('read-image-file', async (_event, filePath: string, workspacePath?: string) => {
  try {
    if (!workspacePath) return null;
    const _ws = untrustedWorkspace(workspacePath); if (_ws) return null;
    const fullPath = path.isAbsolute(filePath) ? filePath : path.join(workspacePath, filePath);
    return await readImageFileGuarded(fullPath, workspacePath)
  } catch { return null }
})

handleSecure('write-file', async (_event, filePath: string, content: string, workspacePath?: string) => {
  try {
    const isAbsolute = path.isAbsolute(filePath);

    if (!workspacePath && !isAbsolute) return { success: false, error: 'Workspace path eksik.' };
    const _ws = untrustedWorkspace(workspacePath); if (_ws) return { success: false, error: _ws };

    const fullPath = isAbsolute ? filePath : path.join(workspacePath!, filePath);

    if (!isAllowedWorkspaceWriteFile(fullPath, workspacePath || "")) {
      return { success: false, error: 'Dosya yalnızca seçili workspace içindeki kod/metin dosyalarına yazılabilir (.cs için Assets/Scripts altı).' }
    }
    const dir = path.dirname(fullPath)
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true })
    }
    fs.writeFileSync(fullPath, content, 'utf-8')
    return { success: true, path: fullPath }
  } catch (err: any) {
    return { success: false, error: err.message }
  }
})

handleSecure('file-exists', async (_event, filePath: string, workspacePath?: string) => {
  if (!workspacePath) return false;
  const _ws = untrustedWorkspace(workspacePath); if (_ws) return false;
  const fullPath = path.isAbsolute(filePath) ? filePath : path.join(workspacePath, filePath);
  if (!isAllowedWorkspaceWriteFile(fullPath, workspacePath)) {
    return false
  }
  return fs.existsSync(fullPath)
})

// --- IPC: DOSYA YÖNETİMİ ---
handleSecure('create-file', async (_event, filePath: string, workspacePath?: string) => {
  if (!workspacePath) return { success: false, error: 'Workspace path eksik.' }
  const _ws = untrustedWorkspace(workspacePath); if (_ws) return { success: false, error: _ws };
  const fullPath = path.isAbsolute(filePath) ? filePath : path.join(workspacePath, filePath)
  if (!isAllowedWorkspacePath(fullPath, workspacePath)) return { success: false, error: 'Dosya workspace dışında.' }
  if (fs.existsSync(fullPath)) return { success: false, error: 'Dosya zaten var.' }
  fs.mkdirSync(path.dirname(fullPath), { recursive: true })
  fs.writeFileSync(fullPath, '', 'utf-8')
  return { success: true, path: fullPath }
})

handleSecure('delete-file', async (_event, relativePath: string, workspacePath?: string) => {
  try {
    if (!workspacePath) return { success: false, error: 'Workspace path eksik.' };
    const _ws = untrustedWorkspace(workspacePath); if (_ws) return { success: false, error: _ws };
    const fullPath = path.isAbsolute(relativePath) ? relativePath : path.join(workspacePath, relativePath);
    if (!isAllowedWorkspacePath(fullPath, workspacePath)) {
      return { success: false, error: 'Dosya workspace dışında.' };
    }
    const exists = fs.existsSync(fullPath);
    if (exists) {
      fs.unlinkSync(fullPath);
      return { success: true };
    }
    return { success: false, error: 'Dosya bulunamadı' };
  } catch (err: any) {
    return { success: false, error: err.message };
  }
});

handleSecure('create-folder', async (_event, folderPath: string, workspacePath?: string) => {
  if (!workspacePath) return { success: false, error: 'Workspace path eksik.' }
  const _ws = untrustedWorkspace(workspacePath); if (_ws) return { success: false, error: _ws };
  const fullPath = path.isAbsolute(folderPath) ? folderPath : path.join(workspacePath, folderPath)
  if (!isAllowedWorkspacePath(fullPath, workspacePath)) return { success: false, error: 'Klasör workspace dışında.' }
  if (fs.existsSync(fullPath)) return { success: false, error: 'Klasör zaten var.' }
  fs.mkdirSync(fullPath, { recursive: true })
  return { success: true, path: fullPath }
})

handleSecure('rename-entry', async (_event, oldPath: string, newName: string, workspacePath?: string) => {
  if (!workspacePath) return { success: false, error: 'Workspace path eksik.' }
  const _ws = untrustedWorkspace(workspacePath); if (_ws) return { success: false, error: _ws };
  const fullOldPath = path.isAbsolute(oldPath) ? oldPath : path.join(workspacePath, oldPath)
  if (!isAllowedWorkspacePath(fullOldPath, workspacePath)) return { success: false, error: 'Dosya workspace dışında.' }
  if (!fs.existsSync(fullOldPath)) return { success: false, error: 'Dosya bulunamadı.' }
  // `newName` bir AD olmalı, yol değil. Doğrulanmadığında Node traversal
  // segmentlerini normalize ediyordu ve dosya workspace dışına taşınıyordu:
  //   path.join('/trusted/workspace', '../../escaped.txt') → '/escaped.txt'
  // Bu, meşru arayüzden de tetiklenir — kullanıcı adlandırma kutusuna '../'
  // yazması yeterdi. Önce ayraç/traversal reddediliyor, sonra hedef de tıpkı
  // kaynak gibi workspace hapsine sokuluyor (biri yeterli değil: ayraçsız bir
  // sembolik bağ da dışarı çıkabilir).
  if (!newName || newName.includes('/') || newName.includes('\\') || newName === '..' || newName === '.') {
    return { success: false, error: 'Geçersiz ad: yol ayracı içeremez.' }
  }
  const newPath = path.join(path.dirname(fullOldPath), newName)
  if (!isAllowedWorkspacePath(newPath, workspacePath)) return { success: false, error: 'Hedef workspace dışında.' }
  if (fs.existsSync(newPath)) return { success: false, error: 'Bu isimde bir dosya zaten var.' }
  fs.renameSync(fullOldPath, newPath)
  return { success: true, newPath }
})

handleSecure('delete-entry', async (_event, entryPath: string, workspacePath?: string) => {
  if (!workspacePath) return { success: false, error: 'Workspace path eksik.' }
  const _ws = untrustedWorkspace(workspacePath); if (_ws) return { success: false, error: _ws };
  const fullPath = path.isAbsolute(entryPath) ? entryPath : path.join(workspacePath, entryPath)
  if (!isAllowedWorkspacePath(fullPath, workspacePath)) return { success: false, error: 'Dosya workspace dışında.' }
  if (!fs.existsSync(fullPath)) return { success: false, error: 'Dosya bulunamadı.' }
  fs.rmSync(fullPath, { recursive: true, force: true })
  return { success: true }
})

handleSecure('move-entry', async (_event, sourcePath: string, targetDir: string, workspacePath?: string) => {
  if (!workspacePath) return { success: false, error: 'Workspace path eksik.' }
  const _ws = untrustedWorkspace(workspacePath); if (_ws) return { success: false, error: _ws };
  const fullSource = path.isAbsolute(sourcePath) ? sourcePath : path.join(workspacePath, sourcePath)
  const fullTarget = path.isAbsolute(targetDir) ? targetDir : path.join(workspacePath, targetDir)
  if (!isAllowedWorkspacePath(fullSource, workspacePath) || !isAllowedWorkspacePath(fullTarget, workspacePath)) {
    return { success: false, error: 'Dosya workspace dışında.' }
  }
  const newPath = path.join(fullTarget, path.basename(fullSource))
  if (fs.existsSync(newPath)) return { success: false, error: 'Hedef konumda aynı isimde bir dosya var.' }
  fs.renameSync(fullSource, newPath)
  return { success: true, newPath }
})

// Bir klasörün hâlâ var olup olmadığını kontrol eder (workspace silinmiş mi?).
handleSecure('path-exists', async (_event, targetPath: string) => {
  try {
    if (!targetPath) return false
    // Bu handler yetkili kök kapısından MUAFTI: `workspacePath` parametresi
    // olmadığı için untrustedWorkspace() ona hiç uygulanmamıştı. Sonuç, çağıranın
    // dosya sisteminin herhangi bir yerinde dizin varlığını sorgulayabilmesiydi
    // — tek başına küçük, ama keşif adımı olarak diğer bulguları besliyor.
    //
    // Meşru kullanım DAR: renderer bunu yalnız "önceki workspace hâlâ duruyor mu"
    // diye çağırıyor (useFileSystem.selectWorkspace). Yetkili köklerle sınırlamak
    // o kullanımı bozmuyor, çünkü sorulan yol zaten bir zamanlar seçilmiş kök.
    if (!isTrustedRoot(targetPath)) return false
    return fs.existsSync(targetPath) && fs.statSync(targetPath).isDirectory()
  } catch {
    return false
  }
})

handleSecure('app-token-get', () => localAppToken)

handleSecure('app-zoom-set', (event, factor: unknown) => {
  if (factor !== 0.9 && factor !== 1 && factor !== 1.1) {
    throw new Error('Invalid text size factor.')
  }
  event.sender.setZoomFactor(factor)
  return true
})

handleSecure('approval-mode-set', async (_event, mode: unknown, source: unknown) => {
  if (mode !== 'auto' && mode !== 'balanced' && mode !== 'step') {
    throw new Error('Geçersiz çalışma modu.')
  }
  const src = source === 'settings' || source === 'chat' || source === 'migrate' ? source : 'ui'
  let response
  try {
    response = await axios.post(
      `${getBackendBaseUrl()}/approval-mode`,
      { mode, source: src },
      {
        timeout: 10000,
        headers: { 'X-Session-Token': localAppToken, 'X-Gamachine-UI-Secret': uiSecret },
      },
    )
    console.log(`[approval-mode] ${response.data?.previous} -> ${response.data?.mode} (source=${src})`)
  } catch (error) {
    const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
    if (detail && typeof detail === 'object' && typeof (detail as { code?: unknown }).code === 'string') {
      // A coded refusal (the flip to step was refused): returned, not thrown, so
      // the renderer can show it in its own language; an IPC error only carries
      // a message string.
      console.error('[approval-mode] refused:', (detail as { code: string }).code)
      return { refused: detail }
    }
    const text = typeof detail === 'string' ? detail : undefined
    console.error('[approval-mode] write failed:', text || (error as Error)?.message)
    throw new Error(text || 'Çalışma modu backend’e iletilemedi.')
  }
  try {
    await computerUse.reconcile()
  } catch (error) {
    console.error('[cua] reconcile after mode change failed', (error as Error)?.message)
  }
  return response.data
})

// Maker profile reset (Settings > Account): the UI secret stays here (helpers/profile-reset.ts).
const profileReset = createProfileReset({
  baseUrl: getBackendBaseUrl,
  appToken: localAppToken,
  uiSecret,
  http: axios,
  log: (...args) => console.error(...args),
})
handleSecure('profile-reset', () => profileReset())

// Remote control: every call under /remote/, so the UI secret stays here and the
// keep-awake blocker sees every answer (helpers/remote-control.ts).
const remoteControl = createRemoteControl({
  baseUrl: getBackendBaseUrl,
  appToken: localAppToken,
  uiSecret,
  http: axios,
  // Read on use: tests load this module with electron mocks that lack it.
  blocker: {
    start: type => powerSaveBlocker.start(type),
    stop: id => powerSaveBlocker.stop(id),
    isStarted: id => powerSaveBlocker.isStarted(id),
  },
  log: (...args) => console.log(...args),
})
handleSecure('remote-control', (_event, action: unknown, arg: unknown) => remoteControl.invoke(action, arg))

const computerUse = new ComputerUseHost({
  platform: process.platform, spawn, connect: (socket, connected) => net.createConnection(socket, connected),
  isPackaged: app.isPackaged,
  execFile: (cmd, args, opts) => promisify(execFile)(cmd, args, { ...opts, encoding: 'utf8' }) as Promise<{ stdout: string }>,
  fs, http: axios, baseUrl: getBackendBaseUrl, appToken: localAppToken, uiSecret,
  paths: {
    resourcesPath: app.isPackaged ? process.resourcesPath : undefined,
    repoRoot: path.resolve(app.getAppPath?.() ?? process.cwd(), '..', '..'),
    userData: app.getPath('userData'), tmpdir: os.tmpdir(),
  },
  systemPreferences: {
    isTrustedAccessibilityClient: prompt => systemPreferences.isTrustedAccessibilityClient(prompt),
    getMediaAccessStatus: media => systemPreferences.getMediaAccessStatus(media),
  },
  requestScreenCapture: () => desktopCapturer.getSources({ types: ['screen'], thumbnailSize: { width: 1, height: 1 } }),
  shell: { openExternal: url => shell.openExternal(url) },
  log: (...args) => console.log(...args),
})
handleSecure('computer-use', (_event, req: unknown) => computerUse.invoke(req))

// Live presence: the mirrored window, the floating indicator and the halt
// shortcut (helpers/computer-use-live.ts). Stop goes through the host's halt op.
let mainWindowRef: BrowserWindow | null = null
const computerLive = new ComputerLive({
  host: computerUse, http: axios, baseUrl: getBackendBaseUrl, appToken: localAppToken,
  platform: process.platform, userData: app.getPath('userData'), spawn,
  // Read on use: tests load this module with electron mocks that lack these.
  windows: () => BrowserWindow.getAllWindows(),
  newIndicator: options => {
    const win = new BrowserWindow(options as Electron.BrowserWindowConstructorOptions)
    applyNavigationPolicy(win)
    return win
  },
  loadIndicator: win => (isProd
    ? (win as BrowserWindow).loadURL('app://./computer-live')
    : (win as BrowserWindow).loadURL(`http://localhost:${process.argv[2]}/computer-live`)),
  preload: path.join(__dirname, 'preload.js'),
  workAreas: () => ({ primary: screen.getPrimaryDisplay().workArea, all: screen.getAllDisplays().map(d => d.workArea) }),
  shortcut: {
    register: (accelerator, callback) => globalShortcut.register(accelerator, callback),
    unregister: accelerator => globalShortcut.unregister(accelerator),
  },
  fs,
  mainVisible: () => {
    const w = mainWindowRef
    return !!w && !w.isDestroyed() && w.isVisible() && !w.isMinimized()
  },
  focusMain: () => {
    const win = mainWindowRef
    if (!win || win.isDestroyed()) return
    if (win.isMinimized()) win.restore()
    win.show()
    win.focus()
    win.webContents.send(LIVE_OPEN_EVENT)
  },
  onHalt: () => browserHost.halt(),
  log: (...args) => console.log(...args),
})
handleSecure('computer-use-live', (_event, req: unknown) => computerLive.invoke(req))

// Agent browser (helpers/browser-host.ts): a page the agent drives over the main
// window, commanded through a loopback server whose port and per-launch token
// reach the backend only through POST /browser/runtime.
const browserToken = randomBytes(32).toString('hex')
let browserServer: CommandServer | null = null
const browserHost = new BrowserHost({
  window: () => mainWindowRef,
  // Read on use: tests load this module with electron mocks that lack these.
  createView: options => new WebContentsView(options),
  fromPartition: partition => session.fromPartition(partition),
  http: axios, baseUrl: getBackendBaseUrl, appToken: localAppToken, uiSecret,
  onHalt: conversationId => browserServer?.cancelPending(conversationId ?? undefined),
  history: new BrowserHistory(path.join(app.getPath('userData'), 'browser-history.json')),
  log: (...args) => console.log(...args),
})
handleSecure('browser-live', (_event, req: unknown) => browserHost.invoke(req))

async function startBrowserHost(): Promise<void> {
  if (browserServer) return
  try {
    browserServer = await startCommandServer({
      token: browserToken,
      handle: cmd => browserHost.handle(cmd),
      immediate: cmd => cmd.op === 'halt',
      // A tab the user has taken over answers user_control at once instead of queueing.
      refuse: cmd => browserHost.userControlReply(cmd),
    })
    browserHost.setRuntime({ port: browserServer.port, token: browserToken })
    browserHost.beginPolling()
  } catch (err) {
    console.error('[browser] command server failed to start', err)
  }
}

function stopBrowserHost(): Promise<unknown> {
  const server = browserServer
  browserServer = null
  return Promise.all([server?.close(), browserHost.shutdown()]).catch(() => undefined)
}

// The renderer picks a folder with a HOST path, but in Docker mode the backend
// is a different filesystem namespace where that path does not exist — only the
// mount does. Sending the host path unchanged is what an audit caught on
// 31 Aug 2026: the mount was there, the contract test was green, and the file
// tools addressed a path no process in the container could see.
//
// So the two consumers are told apart explicitly. Electron's own file tree keeps
// the host path, because Electron reads the disk directly. Anything the BACKEND
// will resolve goes through here.
//
// The rules are lexical: nothing in the mapping module touches the disk, on
// purpose, so it can be tested by behaviour. But containment across a bind
// mount is a FILESYSTEM property, and the gap between the two was FINDING 3 of
// the 31 Aug 2026 audit. A symlink at `<root>/linked-project` pointing outside
// the mounted tree relativises to `linked-project` and was accepted as
// `/workspace/linked-project` — but the mount exposes the real tree, so that
// container path names a different file than the one Electron opened, and both
// halves report success. The mirror case: when GAMACHINE_WORKSPACE is itself a
// symlink and the folder dialog hands back the canonical spelling, a legitimate
// selection was refused.
//
// So the IO half lives here, before the pure rules run, and it is applied to
// BOTH the root and the incoming path. The handlers still decide nothing about
// containment — they hand canonical spellings to the module and take its answer.
function canonicalPath(p: string): string {
  if (!p) return ''
  try {
    // `.native` so Windows returns the on-disk spelling (drive letter, case),
    // which is what the two sides have to agree on.
    return fs.realpathSync.native(p)
  } catch {
    // ENOENT, a broken link, or no permission to traverse. Inventing a
    // canonical spelling for a path we could not stat would put the lexical
    // hole straight back, so this is "no answer" and the callers refuse.
    return ''
  }
}

function canonicalHostRoot(): string {
  return canonicalPath(hostRootFrom(process.env.GAMACHINE_WORKSPACE))
}

handleSecure('backend-workspace-path', (_event, hostPath: unknown) => {
  const p = String(hostPath ?? '')
  if (!useDockerBackend) return p
  // A path that does not exist cannot be symlink-resolved, and this direction
  // never legitimately receives one: it is called with a folder the user just
  // picked in the dialog. So an unresolvable input is refused ('') rather than
  // guessed — the backend would be told a container path addressing nothing.
  return toBackendPath(canonicalPath(p), canonicalHostRoot())
})

// The reverse, and it is not optional: the backend stores what it was given, so
// in Docker mode "last workspace" comes back as `/workspace` — a path that does
// not exist on the host. Without this the restore would fail its existence check
// and quietly drop the user's last project every launch.
handleSecure('host-workspace-path', (_event, backendPath: unknown) => {
  const p = String(backendPath ?? '')
  if (!useDockerBackend) return p
  const root = canonicalHostRoot()
  const candidate = toHostPath(p, root)
  if (!candidate) return ''
  const real = canonicalPath(candidate)
  // This is the handler that CAN legitimately be handed a path that no longer
  // exists: it restores the last workspace, and the folder may have been
  // deleted or renamed since the backend stored it. Such a path has no symlink
  // to resolve, and refusing it here would turn "your project moved" into
  // "there was never a project". The lexical answer under an already canonical
  // root is safe, and the caller's own `path-exists` check rejects it anyway.
  if (!real) return candidate
  // It does exist, so a symlink INSIDE the tree could still lead out of the
  // mount. Re-check the resolved spelling through the same module rather than
  // restating the containment rule here — one home for the rule, one for the IO.
  return toBackendPath(real, root) ? real : ''
})
handleSecure('get-backend-base-url', () => getBackendBaseUrl())

// `Notification` is read inside the closures, not here: test harnesses load
// this module with an `electron` mock that has no such export.
const notifier = createNotifier({
  isSupported: () => Notification.isSupported(),
  create: (options) => new Notification(options),
})

// The window to raise on click is the one that asked, not "the first window".
handleSecure('notify', (event, payload: unknown) =>
  notifier.show(payload, BrowserWindow.fromWebContents(event.sender)))

function findAvailablePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.listen(0, BACKEND_HOST, () => {
      const address = server.address()
      if (!address || typeof address === 'string') {
        server.close(() => reject(new Error('Boş port bulunamadı.')))
        return
      }

      const selectedPort = address.port
      server.close((error) => {
        if (error) {
          reject(error)
          return
        }
        resolve(selectedPort)
      })
    })
    server.on('error', reject)
  })
}

async function waitForBackendHealth(timeoutMs: number): Promise<void> {
  const interval = 500
  const maxAttempts = timeoutMs / interval

  for (let i = 0; i < maxAttempts; i++) {
    try {
      const response = await axios.get(`${getBackendBaseUrl()}/health`, { timeout: interval })
      if (response.data?.status === 'ok' && response.data?.service === 'gamachine') {
        console.log(`--- BACKEND HAZIR (${(i * interval / 1000).toFixed(1)}s) ${getBackendBaseUrl()} ---`)
        return
      }
    } catch {
      // backend henüz hazır değil
    }

    await new Promise(resolve => setTimeout(resolve, interval))
  }

  throw new Error(`Backend ${timeoutMs}ms içinde hazır olmadı.`)
}

/** Proves the reachable backend holds the same token this process does. */
async function assertBackendSharesOurToken(): Promise<void> {
  try {
    await axios.get(`${getBackendBaseUrl()}/health/auth`, {
      timeout: 5000,
      headers: { 'X-Session-Token': localAppToken },
    })
  } catch (error) {
    const status = (error as { response?: { status?: number } })?.response?.status
    if (status === 401 || status === 503) {
      throw new Error(
        'The backend on this port is running with a different LOCAL_APP_TOKEN. ' +
        'It is almost certainly a container left over from an earlier session ' +
        '(`restart: unless-stopped`). Run `docker compose down`, export the token, ' +
        'and bring it back up. See docs/building.md.'
      )
    }
    throw error
  }
}

// ── Which tree is behind the mount ───────────────────────────────────────────
//
// FINDING D4-03. This process trusts its OWN environment as the truth about
// what is mounted: canonicalHostRoot() reads the current GAMACHINE_WORKSPACE
// and the handlers map that root onto the container mount. But a container's
// bind source is fixed when the container is CREATED, and `restart:
// unless-stopped` keeps one alive across the shell that started it. Export a
// new GAMACHINE_WORKSPACE, relaunch, pick project B: the mapping succeeds and
// the live backend reads and writes project A. Both halves report success.
//
// assertBackendSharesOurToken does not cover it — a shared token says nothing
// about which tree is mounted.
//
// The container cannot be asked its bind source; from inside, only the mount
// exists. So identity is established by comparing something observable on both
// sides: a fingerprint of the directory layout, computed here over the host
// root and by GET /health/workspace over the container mount.
//
// WHAT THE COMPARISON PROVES. A mismatch is strong evidence of two different
// trees (confirmed once, because the tree can change between the two samples).
// A match is WEAK: it proves the two directories agree on entry names and kinds
// two levels down, not that they are the same directory. Two copies of one
// project, or two untouched projects from the same template, are identical to
// it. File content is never read, so trees with matching layout and completely
// different contents pass.
//
// Stronger read-only options were considered and rejected: st_dev/st_ino is
// conclusive across a Linux bind mount but Docker Desktop synthesises inode
// numbers, so it would fail the CORRECT tree on the platforms this is developed
// on. A marker file would be conclusive on every platform and is deliberately
// not written — a Unity project reacts to new files by importing them.

async function fetchBackendWorkspaceFingerprint(): Promise<any> {
  const response = await axios.get(`${getBackendBaseUrl()}/health/workspace`, {
    timeout: 20000,
    headers: { 'X-Session-Token': localAppToken },
  })
  return response.data ?? {}
}

/** Refuses to proceed when the container's mount is not the tree we think it is. */
async function assertBackendMountsOurWorkspace(): Promise<void> {
  const hostRoot = canonicalHostRoot()
  if (!hostRoot) {
    throw new Error(
      'USE_DOCKER_BACKEND=true but GAMACHINE_WORKSPACE is unset, relative, or does not ' +
      'exist on this machine. Export the absolute path of the project Compose mounts, ' +
      'then run `docker compose down` and `docker compose up` so the service is recreated ' +
      'with that bind. See docs/building.md.'
    )
  }

  const recreate =
    'Run `docker compose down`, then `docker compose up`, so the service is RECREATED ' +
    'with the new bind. A bind source is fixed when the container is created, and ' +
    '`restart: unless-stopped` keeps the old container alive across the shell that ' +
    'started it, so `up` alone reuses it. See docs/building.md.'

  // Two rounds before refusing. A single mismatch can mean the tree changed
  // between the host sample and the container sample rather than that the
  // mount is wrong; a wrong mount reproduces, a passing write does not.
  let last = ''
  for (let attempt = 0; attempt < 2; attempt++) {
    const ours = hostWorkspaceFingerprint(hostRoot)
    const theirs = await fetchBackendWorkspaceFingerprint()

    if (theirs.mounted !== true) {
      throw new Error(
        `The backend has nothing mounted at ${DOCKER_WORKSPACE_MOUNT}, so it cannot see ` +
        `${hostRoot} or any other project. ` + recreate
      )
    }
    if (theirs.algo !== WORKSPACE_FINGERPRINT_ALGO) {
      throw new Error(
        `The backend computes workspace identity with '${theirs.algo}' and this app uses ` +
        `'${WORKSPACE_FINGERPRINT_ALGO}', so the two answers cannot be compared. The ` +
        'container is running older backend code than this app expects. ' + recreate
      )
    }
    if (theirs.fingerprint === ours.fingerprint) return

    last =
      `host ${hostRoot} -> ${ours.fingerprint.slice(0, 12)} (${ours.entries} entries), ` +
      `container ${DOCKER_WORKSPACE_MOUNT} -> ${String(theirs.fingerprint).slice(0, 12)} ` +
      `(${theirs.entries} entries)`
    console.warn(`--- WORKSPACE IDENTITY MISMATCH (attempt ${attempt + 1}) --- ${last}`)
  }

  throw new Error(
    `The container is serving a DIFFERENT tree at ${DOCKER_WORKSPACE_MOUNT} than the one ` +
    `this app is configured for. Everything would look fine while the editor worked in ` +
    `one project and the agents worked in another. ${recreate}\n\nMeasured twice: ${last}`
  )
}

// --- BACKEND YOLLARINI BUL ---
function getBackendPaths(): { pythonExec: string; pythonScript: string; backendDir: string; sitePackages: string } {
  const isWin = process.platform === 'win32'

  if (isProd) {
    // PyInstaller ile derlenmiş tek binary — Python kurulu olmasına gerek yok
    const resourcesPath = process.resourcesPath
    const backendDir = path.join(resourcesPath, 'Backend')
    const backendExec = isWin ? 'backend.exe' : 'backend'
    const pythonExec = path.join(backendDir, backendExec)
    // pythonScript boş: binary doğrudan çalıştırılır, script argümanı gerekmez
    return { pythonExec, pythonScript: '', backendDir, sitePackages: '' }
  } else {
    const appPath = app.getAppPath()
    const projectRoot = path.resolve(appPath, '..', '..')
    const backendDir = path.join(projectRoot, 'Backend')
    const pythonExec = isWin
      ? path.join(backendDir, 'venv', 'Scripts', 'python.exe')
      : path.join(backendDir, 'venv', 'bin', 'python3')
    const pythonScript = path.join(backendDir, 'app', 'main.py')
    return { pythonExec, pythonScript, backendDir, sitePackages: '' }
  }
}

// Resolves once the backend is gone (the Windows installer cannot overwrite a locked
// backend.exe), or after 10 s so a hung kill never blocks quitting. Never rejects.
const killBackend = (): Promise<void> => {
  const proc = pyBackendProcess
  if (!proc) return Promise.resolve()
  return new Promise<void>((resolve) => {
    // Armed first: nothing below may skip the cap.
    const timer = setTimeout(resolve, 10000)
    const settle = () => { clearTimeout(timer); resolve() }
    const gone = (child: ChildProcess) => child.exitCode != null || child.signalCode != null
    const onceEvent = (emitter: ChildProcess, event: string) =>
      new Promise<void>((done) => { emitter.once(event, () => done()) })
    // Test doubles and already-exited children have nothing to listen on.
    const waits: Promise<void>[] = [gone(proc) || typeof proc.once !== 'function' ? Promise.resolve() : onceEvent(proc, 'exit')]
    const pid = proc.pid
    if (!gone(proc)) {
      const fallbackKill = (signal?: NodeJS.Signals) => {
        try { proc.kill(signal) } catch (e) {
          console.error('[backend] kill failed:', (e as any)?.message || e)
          settle()
        }
      }
      if (process.platform === 'win32' && pid) {
        // Windows: negatif PID (process group) yok — taskkill /T tüm child ağacını öldürür
        // (PyInstaller bootloader + uvicorn worker'ları dahil). Aksi halde orphan kalır.
        try {
          const killer = spawn('taskkill', ['/pid', String(pid), '/T', '/F'], { stdio: 'ignore' })
          waits.push(new Promise<void>((done) => {
            killer.once('exit', () => done())
            killer.once('error', () => done())
          }))
        } catch {
          fallbackKill()
        }
      } else {
        // POSIX: tüm process grubunu öldür (uvicorn reload child'ları dahil)
        try {
          process.kill(-pid!, 'SIGKILL')
        } catch {
          fallbackKill('SIGKILL')
        }
      }
    }
    void Promise.all(waits).then(settle)
  }).then(() => { if (pyBackendProcess === proc) pyBackendProcess = null })
}

// --- BACKEND BAŞLATMA ---
async function startPythonBackend() {
  // Docker modu: Python spawn etme, doğrudan Docker backend'e bağlan
  if (useDockerBackend) {
    // Fail here, with the fix in the message, rather than connecting to a
    // healthy container that will reject every subsequent call. A 401 storm
    // reads like a broken app; this reads like a missing step.
    if (!process.env.LOCAL_APP_TOKEN) {
      throw new Error(
        'USE_DOCKER_BACKEND=true but LOCAL_APP_TOKEN is unset. The container and this ' +
        'process must share one token — export the same value for both, then start ' +
        'Compose. See docs/building.md.'
      )
    }
    const dockerPort = parseInt(process.env.DOCKER_BACKEND_PORT || '8000', 10)
    console.log(`--- DOCKER BACKEND MODU: port ${dockerPort} ---`)
    backendPort = dockerPort
    // A cold `docker compose up --build` installs the whole requirements set,
    // which is minutes, not seconds. 30 s timed out on a first run and looked
    // like a failure; the health probe is cheap, so waiting longer is free.
    await waitForBackendHealth(180000)
    // `/health` is unauthenticated, so reaching it proves a backend is there and
    // nothing about WHICH secret it holds. `restart: unless-stopped` outlives the
    // shell that exported the token, so a container from an earlier session
    // answers this happily and then 401s every real call — measured 31 Aug 2026,
    // every startup signal green while the app was unusable.
    await assertBackendSharesOurToken()
    // Sharing a token proves the two sides are the same INSTALLATION; it says
    // nothing about which tree is behind the mount. FINDING D4-03: a container
    // kept alive by `restart: unless-stopped` still serves the bind source it
    // was created with, so a newly exported GAMACHINE_WORKSPACE changes only
    // what this process believes.
    await assertBackendMountsOurWorkspace()
    return
  }

  // Port'u geçici değişkende tut — sağlıklı başlarsa backendPort'a yaz
  const selectedPort = await findAvailablePort()
  console.log(`--- BACKEND İÇİN PORT SEÇİLDİ: ${selectedPort} ---`)

  const { pythonExec, pythonScript, backendDir } = getBackendPaths()

  if (pythonScript) {
    // Dev modu: python script'i kontrol et
    if (!fs.existsSync(pythonScript)) {
      throw new Error(`Backend script bulunamadı: ${pythonScript}`)
    }
    if (!fs.existsSync(pythonExec)) {
      throw new Error(`Python bulunamadı: ${pythonExec}`)
    }
  } else {
    // Prod modu: PyInstaller binary'sini kontrol et
    if (!fs.existsSync(pythonExec)) {
      throw new Error(`Backend binary bulunamadı: ${pythonExec}`)
    }
  }

  const spawnArgs = pythonScript ? [pythonScript] : []
  console.log(`--- BACKEND BAŞLATILIYOR: ${pythonExec} ${spawnArgs.join(' ')} ---`)
  console.log(`--- BACKEND CWD: ${backendDir} ---`)

  // Port'u şimdi yaz ki waitForBackendHealth kullanabilsin
  backendPort = selectedPort

  pyBackendProcess = spawn(pythonExec, spawnArgs, {
    // stdin is a pipe only to hand over the UI secret (see `uiSecret`); it is
    // closed right after, so nothing the backend spawns can read it later.
    stdio: ['pipe', 'pipe', 'pipe'],
    cwd: backendDir,
    // POSIX: its own process group, so killBackend's kill(-pid) also ends what the backend
    // started (measured 29 Oct 2026: with false a descendant outlived the quit). Windows uses
    // taskkill /T instead, and detached there would open a console window.
    detached: process.platform !== 'win32',
    env: { ...process.env, PYTHONUNBUFFERED: '1', PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8', PORT: String(selectedPort), LOCAL_APP_TOKEN: localAppToken, GAMACHINE_UI_SECRET_STDIN: '1' },
  });
  pyBackendProcess.stdin?.on('error', (err) => console.error('[approval-mode] UI secret not delivered:', err))
  pyBackendProcess.stdin?.end(`${uiSecret}\n`)

  const safeLog = (...args: any[]) => { try { console.log(...args) } catch { /* EIO — socket kapandı */ } }
  const safeErr = (...args: any[]) => { try { console.error(...args) } catch { /* EIO — socket kapandı */ } }

  pyBackendProcess.stdout?.on('data', (data) => {
    safeLog(`[Backend] ${data.toString().trim()}`)
  })
  pyBackendProcess.stderr?.on('data', (data) => {
    safeErr(`[Backend] ${data.toString().trim()}`)
  })
  pyBackendProcess.on('error', (err) => {
    safeErr('Backend başlatılamadı:', err)
  })
  pyBackendProcess.on('exit', (code) => {
    safeLog(`--- BACKEND KAPANDI (exit code: ${code}) ---`)
  })

  try {
    await waitForBackendHealth(30000)
  } catch (err) {
    // Sağlık kontrolü başarısız — port'u sıfırla, pencere yine de açılacak
    backendPort = null
    // Same wait as quit/install: the handle stays until the process exited or the cap passed.
    await killBackend()
    throw err
  }
}

// --- TEK INSTANCE KİLİDİ (Nextron çift restart'ı önler) ---
const gotTheLock = app.requestSingleInstanceLock()

if (!gotTheLock) {
  console.log('--- BAŞKA BİR INSTANCE ZATEN ÇALIŞIYOR, ÇIKILIYOR ---')
  app.quit()
} else {
  app.on('activate', () => { reopenMain(mainWindowRef) })

  app.on('second-instance', () => {
    if (mainWindowRef) {
      reopenMain(mainWindowRef)
      return
    }
    const allWindows = require('electron').BrowserWindow.getAllWindows()
    if (allWindows.length > 0) {
      const win = allWindows[0]
      if (win.isMinimized()) win.restore()
      win.focus()
    }
  })

  let quitReady = false
  let updateHandle: AutoUpdateHandle | undefined

    ; (async () => {
      await app.whenReady()

      // CSP, pencere açılmadan ÖNCE kuruluyor: politika ilk belge yanıtına
      // yetişmezse ana sayfa korumasız yüklenir ve bu sessizce olur.
      applyContentSecurityPolicy(isProd)

      // Windows görev çubuğu ikonu: AppUserModelId set edilmezse taskbar default
      // Electron ikonunu gösterir (window/exe ikonundan bağımsız). appId ile eşle.
      if (process.platform === 'win32') {
        app.setAppUserModelId('com.gamachine.app')
      }

      try {
        await startPythonBackend()
        remoteControl.start()
        computerUse.beginPolling()
        computerLive.beginPolling()
      } catch (err) {
        console.error('--- BACKEND BAŞLATILAMADI ---', err)
        backendPort = null
        // Docker mode stops here instead of opening the window, and says why in
        // a dialog. Every refusal on that path (no workspace, nothing mounted,
        // algorithm drift, a wrong tree behind the mount) names a fix the user
        // has to apply in a SHELL — `docker compose down` then `up` — and the
        // window renders none of that: the message was thrown, caught here,
        // logged, and the renderer showed its generic "the backend is
        // unreachable, please restart the app". Restarting is precisely the
        // thing that does not work, because `restart: unless-stopped` keeps the
        // stale container alive across it. There is also nothing useful to do in
        // the window: with the mount wrong, every editor action would be aimed
        // at the wrong project. Non-Docker startup keeps the old lenient
        // behaviour — the window opens and the renderer shows the hint.
        if (useDockerBackend) {
          dialog.showErrorBox(
            'Docker backend refused to start',
            (err as any)?.message || String(err)
          )
          app.quit()
          return
        }
      }

      // Pencere ikonu: prod'da paketlenmiş resources/icon.ico, dev'de proje resources'ı.
      // (Set edilmezse dev'de + bazı Windows durumlarında eski Electron ikonu görünür.)
      const iconPath = isProd
        ? path.join(process.resourcesPath, 'icon.ico')
        : path.join(__dirname, '..', 'resources', 'icon.ico')

      const mainWindow = createWindow('main', {
        width: 1280,
        height: 850,
        // Below ~600 CSS px the top bar cannot fit its controls even with every label hidden
        // (measured 15 Oct 2026); 680 keeps 618 CSS px at the largest text size (zoom 1.1).
        minWidth: 680,
        icon: iconPath,
        webPreferences: {
          preload: path.join(__dirname, 'preload.js'),
          // Agents drive the app while it is hidden; throttled timers would stall the scene and status polls.
          backgroundThrottling: false,
        },
      })
      mainWindowRef = mainWindow
      for (const ev of ['minimize', 'restore', 'hide', 'show'] as const) {
        mainWindow.on(ev as any, () => computerLive.mainVisibilityChanged())
      }
      // The indicator alone must not keep the app alive once the main window is gone.
      mainWindow.on('closed', () => { mainWindowRef = null; computerLive.shutdown(); browserHost.destroyView() })
      void startBrowserHost()

      if (isProd) {
        await mainWindow.loadURL('app://./home')
      } else {
        const port = process.argv[2]
        const maxRetries = 5
        for (let i = 0; i < maxRetries; i++) {
          try {
            await mainWindow.loadURL(`http://localhost:${port}/home`)
            break
          } catch (err) {
            console.log(`--- SAYFA YÜKLENEMEDI (deneme ${i + 1}/${maxRetries}), 1sn bekleniyor... ---`)
            if (i === maxRetries - 1) {
              console.error('--- SAYFA YÜKLENEMEDI, son deneme de başarısız ---')
            }
            await new Promise(resolve => setTimeout(resolve, 1000))
          }
        }
      }

      // --- GLOBAL MENÜ TASARIMI ---
      const { Menu, MenuItem } = require('electron');
      const template: any[] = [
        ...(process.platform === 'darwin' ? [{
          label: app.name,
          submenu: [
            { role: 'about' },
            { type: 'separator' },
            { role: 'services' },
            { type: 'separator' },
            {
              label: `Hide ${app.name}`,
              accelerator: 'Command+H',
              click: () => hideOrMinimize({
                platform: process.platform,
                live: () => computerLive.isLive(),
                hideApp: () => app.hide(),
                minimizeMain: () => {
                  if (!mainWindowRef || mainWindowRef.isDestroyed()) return false
                  mainWindowRef.minimize()
                  return true
                },
              }),
            },
            { role: 'hideOthers' },
            { role: 'unhide' },
            { type: 'separator' },
            { role: 'quit' }
          ]
        }] : []),
        {
          label: 'File',
          submenu: [
            isProd ? { role: 'quit' } : { role: 'close' }
          ]
        },
        {
          label: 'Edit',
          submenu: [
            { role: 'undo' },
            { role: 'redo' },
            { type: 'separator' },
            { role: 'cut' },
            { role: 'copy' },
            { role: 'paste' },
            { role: 'selectAll' }
          ]
        },
        {
          label: 'Terminal',
          submenu: [
            {
              label: 'Toggle Terminal',
              accelerator: 'Control+`',
              click: () => {
                mainWindow.webContents.send('menu-toggle-terminal');
              }
            },
            {
              label: 'New Terminal',
              accelerator: 'CmdOrCtrl+Shift+`',
              click: () => {
                mainWindow.webContents.send('menu-open-terminal');
              }
            },
            { type: 'separator' },
            {
              label: 'Clear Terminal',
              click: () => {
                mainWindow.webContents.send('menu-clear-terminal');
              }
            }
          ]
        },
        {
          label: 'View',
          submenu: [
            { role: 'reload' },
            { role: 'forceReload' },
            { role: 'toggleDevTools' },
            { type: 'separator' },
            { role: 'resetZoom' },
            { role: 'zoomIn' },
            { role: 'zoomOut' },
            { type: 'separator' },
            { role: 'togglefullscreen' }
          ]
        },
        {
          role: 'window',
          submenu: [
            { role: 'minimize' },
            { role: 'close' }
          ]
        }
      ];

      const menu = Menu.buildFromTemplate(template);
      Menu.setApplicationMenu(menu);

      // Owner decision (29 Oct 2026): in-app auto-update; Windows unsigned (sha512 only), mac signed.
      if (isProd) {
        updateHandle = setupAutoUpdate({
          autoUpdater,
          dialog,
          // Run the same teardown before-quit would, so its preventDefault has nothing left to wait for.
          stopBackend: async () => {
            remoteControl.shutdown()
            computerLive.shutdown()
            await Promise.all([computerUse.shutdown(), stopBrowserHost()])
            await killBackend()
            // Only now: a quit during the kill wait must go through before-quit's own wait.
            quitReady = true
          },
          logger: console,
          relaunch: () => { app.relaunch(); app.exit(0) },
          getMainWindow: () => BrowserWindow.getAllWindows()[0],
        })
      }
    })()

  app.on('window-all-closed', () => {
    remoteControl.shutdown()
    computerLive.shutdown()
    void Promise.all([computerUse.shutdown(), stopBrowserHost()]).finally(async () => { await killBackend(); app.quit() })
  })

  app.on('before-quit', (event) => {
    updateHandle?.dispose()
    remoteControl.shutdown()
    computerLive.shutdown()
    if (quitReady) { void killBackend(); return }
    event.preventDefault()
    void Promise.all([computerUse.shutdown(), stopBrowserHost()]).finally(async () => {
      await killBackend()
      quitReady = true
      app.quit()
    })
  })

  // Beklenmedik çıkışlarda da temizle
  process.on('exit', () => { remoteControl.shutdown(); computerLive.shutdown(true); void computerUse.shutdown(true); void stopBrowserHost(); void killBackend() })
  process.on('SIGTERM', () => {
    remoteControl.shutdown()
    computerLive.shutdown()
    void Promise.all([computerUse.shutdown(), stopBrowserHost()]).finally(async () => { await killBackend(); process.exit(0) })
    // A second signal while waiting exits at once.
    process.once('SIGTERM', () => process.exit(0))
  })
}
