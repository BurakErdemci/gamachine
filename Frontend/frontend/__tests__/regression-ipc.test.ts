import fs from 'fs'
import { afterAll, beforeAll, describe, it, expect, vi } from 'vitest'
import {
  isAllowedUnityScriptPath,
  isAllowedWorkspacePath,
  isAllowedWorkspaceReadFile,
} from '../main/helpers/file-security'
import { ALLOWED_INVOKE_CHANNELS } from '../main/helpers/ipc-whitelist'

const zoomHandlers = new Map<string, (...args: any[]) => any>()
vi.mock('electron', () => ({
  app: {
    getPath: vi.fn(() => '.'), setPath: vi.fn(),
    requestSingleInstanceLock: vi.fn(() => false), quit: vi.fn(), on: vi.fn(),
  },
  ipcMain: { handle: vi.fn((channel: string, listener: (...args: any[]) => any) => zoomHandlers.set(channel, listener)) },
  dialog: {}, shell: {}, BrowserWindow: { getAllWindows: vi.fn(() => []) },
}))
vi.mock('electron-serve', () => ({ default: vi.fn() }))
vi.mock('electron-updater', () => ({ autoUpdater: {} }))
vi.mock('node-pty', () => ({ spawn: vi.fn() }))
vi.mock('../main/helpers', () => ({ createWindow: vi.fn() }))
vi.mock('../main/helpers/ipc-trust', () => ({
  confirmLegacyRoot: vi.fn(() => false), isOwnFrame: vi.fn((event: any) => event.own === true),
  isTrustedRoot: vi.fn(() => true), registerTrustedRoot: vi.fn(),
}))
vi.mock('../main/helpers/csp', () => ({ applyContentSecurityPolicy: vi.fn() }))

describe('app-zoom-set registration and validation', () => {
  beforeAll(async () => {
    // Import the real registrations without launching Electron or writing logs.
    vi.spyOn(fs, 'appendFileSync').mockImplementation(() => {})
    await import('../main/background')
  })
  afterAll(() => vi.restoreAllMocks())

  function handler() {
    const listener = zoomHandlers.get('app-zoom-set')
    expect(listener).toBeTypeOf('function')
    return listener!
  }

  it('is whitelisted', () => {
    expect(ALLOWED_INVOKE_CHANNELS.has('app-zoom-set')).toBe(true)
  })

  it.each([0.9, 1, 1.1])('sets the sender zoom factor to %s', factor => {
    const setZoomFactor = vi.fn()
    expect(handler()({ own: true, sender: { setZoomFactor } }, factor)).toBe(true)
    expect(setZoomFactor).toHaveBeenCalledExactlyOnceWith(factor)
  })

  it.each([2, '1'])('rejects invalid factor %s without changing zoom', factor => {
    const setZoomFactor = vi.fn()
    expect(() => handler()({ own: true, sender: { setZoomFactor } }, factor)).toThrow('Invalid text size factor.')
    expect(setZoomFactor).not.toHaveBeenCalled()
  })

  it('refuses a foreign sender before changing zoom', () => {
    const setZoomFactor = vi.fn()
    expect(() => handler()({ own: false, sender: { setZoomFactor } }, 1)).toThrow(/IPC reddedildi/)
    expect(setZoomFactor).not.toHaveBeenCalled()
  })
})

/**
 * IPC Regresyon Testleri
 *
 * background.ts'deki her IPC handler'ın beklenen giriş/çıkış davranışını
 * doğrular. UI akışlarını simüle eder:
 * - Workspace seçme
 * - Dosya ağacı okuma (klasör gezintisi)
 * - Dosya açma
 * - Tek dosya export (write-file)
 * - Çoklu dosya export (write-multiple-files)
 * - Dosya varlık kontrolü (file-exists)
 */

const WS = '/project/MyUnityGame'
const SCRIPTS = `${WS}/Assets/Scripts`

// ─── Whitelist kanalları (background.ts ile senkron kalmalı) ─────────────────
const BACKGROUND_CHANNELS = [
  'get-backend-base-url',
  'open-file-dialog',
  'open-folder-dialog',
  'read-directory',
  'read-file',
  'write-file',
  'file-exists',
  'write-multiple-files',
] as const

describe('Regresyon — background.ts kanalları whitelist\'te', () => {
  for (const ch of BACKGROUND_CHANNELS) {
    it(`"${ch}" kanalı preload whitelist\'inde mevcut`, () => {
      expect(ALLOWED_INVOKE_CHANNELS.has(ch)).toBe(true)
    })
  }
})

describe('Regresyon — Workspace seçme akışı', () => {
  it('open-folder-dialog kanalı whitelist\'te', () => {
    expect(ALLOWED_INVOKE_CHANNELS.has('open-folder-dialog')).toBe(true)
  })
})

describe('Regresyon — Dosya ağacı okuma (read-directory)', () => {
  it('read-directory kanalı whitelist\'te', () => {
    expect(ALLOWED_INVOKE_CHANNELS.has('read-directory')).toBe(true)
  })

  it('workspace kök dizini okuma için geçerlidir', () => {
    expect(isAllowedWorkspacePath(WS, WS)).toBe(true)
  })

  it('workspace dışındaki klasör okuması engellenir', () => {
    expect(isAllowedWorkspacePath('/tmp', WS)).toBe(false)
  })
})

describe('Regresyon — Dosya açma (read-file / open-file-dialog)', () => {
  it('open-file-dialog kanalı whitelist\'te', () => {
    expect(ALLOWED_INVOKE_CHANNELS.has('open-file-dialog')).toBe(true)
  })

  it('read-file kanalı whitelist\'te', () => {
    expect(ALLOWED_INVOKE_CHANNELS.has('read-file')).toBe(true)
  })

  it('workspace içindeki .cs dosyası okunabilir', () => {
    expect(isAllowedWorkspaceReadFile(`${SCRIPTS}/PlayerController.cs`, WS)).toBe(true)
  })

  it('workspace dışındaki dosya okunamaz', () => {
    expect(isAllowedWorkspaceReadFile('/etc/passwd', WS)).toBe(false)
  })
})

describe('Regresyon — Tek dosya export (write-file + isAllowedUnityScriptPath)', () => {
  it('write-file kanalı whitelist\'te', () => {
    expect(ALLOWED_INVOKE_CHANNELS.has('write-file')).toBe(true)
  })

  it('geçerli Assets/Scripts yoluna yazma izni var', () => {
    expect(isAllowedUnityScriptPath(`${SCRIPTS}/PlayerController.cs`, WS)).toBe(true)
  })

  it('Assets/Scripts dışına yazma engellenir', () => {
    expect(isAllowedUnityScriptPath(`${WS}/Assets/Data/config.cs`, WS)).toBe(false)
  })

  it('workspace dışına yazma engellenir', () => {
    expect(isAllowedUnityScriptPath('/etc/cron.cs', WS)).toBe(false)
  })
})

describe('Regresyon — Çoklu dosya export (write-multiple-files)', () => {
  it('write-multiple-files kanalı whitelist\'te', () => {
    expect(ALLOWED_INVOKE_CHANNELS.has('write-multiple-files')).toBe(true)
  })

  it('batch\'teki her dosya güvenlik kontrolünden geçer', () => {
    const files = [
      `${SCRIPTS}/Player.cs`,
      `${SCRIPTS}/Enemy/Boss.cs`,
      `${SCRIPTS}/UI/HUD.cs`,
    ]
    for (const f of files) {
      expect(isAllowedUnityScriptPath(f, WS)).toBe(true)
    }
  })

  it('batch\'te tek bir kötü niyetli dosya varsa engellenir', () => {
    const malicious = `${WS}/Assets/Scripts/../../secrets.cs`
    expect(isAllowedUnityScriptPath(malicious, WS)).toBe(false)
  })
})

describe('Regresyon — Dosya varlık kontrolü (file-exists)', () => {
  it('file-exists kanalı whitelist\'te', () => {
    expect(ALLOWED_INVOKE_CHANNELS.has('file-exists')).toBe(true)
  })

  it('geçerli yol için güvenlik kontrolünü geçer', () => {
    expect(isAllowedUnityScriptPath(`${SCRIPTS}/PlayerController.cs`, WS)).toBe(true)
  })

  it('workspace dışı yol güvenlik kontrolünde engellenir', () => {
    expect(isAllowedUnityScriptPath('/tmp/exploit.cs', WS)).toBe(false)
  })
})
