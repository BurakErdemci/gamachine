import fs from 'fs'
import path from 'path'
import os from 'os'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ALLOWED_INVOKE_CHANNELS, PRELOAD_ONLY_CHANNELS,
  assertAllowedInvokeChannel, assertRegistrableChannel,
} from '../main/helpers/ipc-whitelist'

const electron = vi.hoisted(() => ({
  handlers: new Map<string, (...args: any[]) => any>(),
  app: {
    getPath: vi.fn(() => '.'), setPath: vi.fn(),
    requestSingleInstanceLock: vi.fn(() => false), quit: vi.fn(), on: vi.fn(),
  },
  ipcMain: { handle: vi.fn() },
  dialog: { showOpenDialog: vi.fn() },
  shell: { openPath: vi.fn() },
  BrowserWindow: { getAllWindows: vi.fn(() => []) },
  contextBridge: { exposeInMainWorld: vi.fn() },
  ipcRenderer: { invoke: vi.fn(async () => ({ path: 'resolved' })), on: vi.fn(), removeListener: vi.fn() },
  webUtils: { getPathForFile: vi.fn() },
}))
const trust = vi.hoisted(() => ({
  confirmLegacyRoot: vi.fn(() => false), isOwnFrame: vi.fn((event: any) => event.own === true),
  isTrustedRoot: vi.fn(() => true), registerTrustedRoot: vi.fn(),
}))
vi.mock('electron', () => electron)
vi.mock('electron-serve', () => ({ default: vi.fn() }))
vi.mock('electron-updater', () => ({ autoUpdater: {} }))
vi.mock('node-pty', () => ({ spawn: vi.fn() }))
vi.mock('../main/helpers', () => ({ createWindow: vi.fn() }))
vi.mock('../main/helpers/ipc-trust', () => trust)
vi.mock('../main/helpers/csp', () => ({ applyContentSecurityPolicy: vi.fn() }))

describe('preload-only channel boundary', () => {
  it('refuses dropped-folder registration through generic invoke', () => {
    expect(() => assertAllowedInvokeChannel('register-dropped-folder')).toThrow()
  })

  it('permits registration of both channel sets, without overlap', () => {
    expect([...PRELOAD_ONLY_CHANNELS]).toEqual(['register-dropped-folder'])
    for (const channel of [...ALLOWED_INVOKE_CHANNELS, ...PRELOAD_ONLY_CHANNELS]) {
      expect(() => assertRegistrableChannel(channel)).not.toThrow()
    }
    for (const channel of PRELOAD_ONLY_CHANNELS) {
      expect(ALLOWED_INVOKE_CHANNELS.has(channel)).toBe(false)
    }
    expect(() => assertRegistrableChannel('unknown')).toThrow()
  })

  it('preload source invokes the restricted channel directly via ipcRenderer', () => {
    const source = fs.readFileSync(path.join(process.cwd(), 'main', 'preload.ts'), 'utf8')
    expect(source).toContain("ipcRenderer.invoke('register-dropped-folder', path)")
    expect(source).not.toMatch(/(?:handler\.|this\.)invoke\(['"]register-dropped-folder/)
    const generic = source.slice(source.indexOf('  invoke(channel:'), source.indexOf('  registerDroppedFolder('))
    expect(generic).toContain('assertAllowedInvokeChannel(channel)')
    expect(generic).not.toContain('register-dropped-folder')
  })

  it('uses the real File path and avoids IPC for an empty path', async () => {
    await import('../main/preload')
    const handler = electron.contextBridge.exposeInMainWorld.mock.calls[0][1]
    const file = new File([], 'dropped-folder')
    electron.ipcRenderer.invoke.mockClear()
    electron.webUtils.getPathForFile.mockReturnValue('')
    await expect(handler.registerDroppedFolder(file)).resolves.toEqual({ error: 'not-a-folder' })
    expect(electron.ipcRenderer.invoke).not.toHaveBeenCalled()
    electron.webUtils.getPathForFile.mockReturnValue('dropped-path')
    await expect(handler.registerDroppedFolder(file)).resolves.toEqual({ path: 'resolved' })
    expect(electron.webUtils.getPathForFile).toHaveBeenLastCalledWith(file)
    expect(electron.ipcRenderer.invoke).toHaveBeenCalledExactlyOnceWith('register-dropped-folder', 'dropped-path')
    await expect(handler.invoke('register-dropped-folder', 'forged')).rejects.toThrow()
    expect(electron.ipcRenderer.invoke).toHaveBeenCalledTimes(1)
  })
})

describe('invalid dropped files', () => {
  it('resolves non-File path resolution errors without IPC', async () => {
    await import('../main/preload')
    const handler = electron.contextBridge.exposeInMainWorld.mock.calls[0][1]
    const invalid = { name: 'forged' }
    electron.ipcRenderer.invoke.mockClear()
    electron.webUtils.getPathForFile.mockImplementationOnce(() => {
      throw new TypeError('Expected a File')
    })
    await expect(handler.registerDroppedFolder(invalid)).resolves.toEqual({ error: 'not-a-folder' })
    expect(electron.webUtils.getPathForFile).toHaveBeenLastCalledWith(invalid)
    expect(electron.ipcRenderer.invoke).not.toHaveBeenCalled()
  })
})

describe('welcome screen main-process actions', () => {
  let root: string
  beforeAll(async () => {
    electron.ipcMain.handle.mockImplementation((channel, listener) => electron.handlers.set(channel, listener))
    vi.spyOn(fs, 'appendFileSync').mockImplementation(() => {})
    await import('../main/background')
  })
  beforeEach(() => {
    root = fs.mkdtempSync(path.join(os.tmpdir(), 'welcome-ipc-'))
    trust.isTrustedRoot.mockReturnValue(true)
    trust.registerTrustedRoot.mockClear()
    electron.shell.openPath.mockReset()
    electron.dialog.showOpenDialog.mockReset()
  })
  afterEach(() => {
    fs.rmSync(root, { recursive: true, force: true })
  })
  afterAll(() => { vi.restoreAllMocks() })

  function call(channel: string, ...args: unknown[]) {
    const handler = electron.handlers.get(channel)
    expect(handler).toBeTypeOf('function')
    return handler!({ own: true }, ...args)
  }

  it('refuses foreign frames for every new action', () => {
    for (const channel of ['workspace-info', 'open-unity-hub', 'register-dropped-folder']) {
      expect(() => electron.handlers.get(channel)!({ own: false }, root)).toThrow(/IPC reddedildi/)
    }
    expect(trust.registerTrustedRoot).not.toHaveBeenCalled()
    expect(electron.shell.openPath).not.toHaveBeenCalled()
  })

  it('inspects trusted directories and reports missing paths', () => {
    fs.mkdirSync(path.join(root, 'ProjectSettings'))
    fs.writeFileSync(path.join(root, 'ProjectSettings', 'ProjectVersion.txt'), 'm_EditorVersion: 6000.0.1f1\n')
    const missing = path.join(root, 'missing')
    expect(call('workspace-info', [root, 1, null, missing])).toEqual([
      { path: root, status: 'ok', unityVersion: '6000.0.1f1' },
      { path: missing, status: 'missing', unityVersion: null },
    ])
    expect(call('workspace-info', root)).toEqual([])
  })

  it('does not access the filesystem for untrusted paths and caps strings at 50', () => {
    trust.isTrustedRoot.mockReturnValue(false)
    const stat = vi.spyOn(fs, 'statSync')
    const open = vi.spyOn(fs, 'openSync')
    try {
      const result = call('workspace-info', [null, ...Array.from({ length: 55 }, (_, i) => `untrusted-${i}`)])
      expect(result).toHaveLength(50)
      expect(result[49]).toEqual({ path: 'untrusted-49', status: 'untrusted', unityVersion: null })
      expect(stat).not.toHaveBeenCalled()
      expect(open).not.toHaveBeenCalled()
    } finally {
      stat.mockRestore()
      open.mockRestore()
    }
  })

  it('registers the real path only for Unity directories', () => {
    expect(call('register-dropped-folder', root)).toEqual({ error: 'not-a-unity-project' })
    expect(trust.registerTrustedRoot).not.toHaveBeenCalled()
    fs.mkdirSync(path.join(root, 'Assets'))
    fs.mkdirSync(path.join(root, 'ProjectSettings'))
    const real = fs.realpathSync(root)
    expect(call('register-dropped-folder', root)).toEqual({ path: real })
    expect(trust.registerTrustedRoot).toHaveBeenCalledExactlyOnceWith(real)
  })

  it('rejects invalid and missing dropped folders without registering anything', () => {
    const file = path.join(root, 'file')
    fs.writeFileSync(file, '')
    for (const input of ['', null, 123, 'relative-folder', file, path.join(root, 'missing')]) {
      expect(call('register-dropped-folder', input)).toEqual({ error: 'not-a-folder' })
    }
    expect(trust.registerTrustedRoot).not.toHaveBeenCalled()
  })

  it('opens only a discovered Hub and reports openPath failures', async () => {
    const hub = process.platform === 'darwin'
      ? path.posix.join(path.posix.sep, 'Applications', 'Unity Hub.app')
      : path.win32.join('C:', 'Program Files', 'Unity Hub', 'Unity Hub.exe')
    const originalProgramFiles = process.env.ProgramFiles
    process.env.ProgramFiles = path.win32.join('C:', 'Program Files')
    const exists = vi.spyOn(fs, 'existsSync').mockImplementation(p => p === hub)
    try {
      if (process.platform !== 'darwin' && process.platform !== 'win32') {
        expect(await call('open-unity-hub', root)).toEqual({ opened: false })
        expect(electron.shell.openPath).not.toHaveBeenCalled()
        return
      }
      electron.shell.openPath.mockResolvedValue('')
      expect(await call('open-unity-hub', root)).toEqual({ opened: true })
      expect(electron.shell.openPath).toHaveBeenCalledExactlyOnceWith(hub)
      electron.shell.openPath.mockResolvedValue('could not open')
      expect(await call('open-unity-hub')).toEqual({ opened: false })
      electron.shell.openPath.mockRejectedValue(new Error('failed'))
      expect(await call('open-unity-hub')).toEqual({ opened: false })
      electron.shell.openPath.mockClear()
      exists.mockReturnValue(false)
      expect(await call('open-unity-hub')).toEqual({ opened: false })
      expect(electron.shell.openPath).not.toHaveBeenCalled()
    } finally {
      exists.mockRestore()
      if (originalProgramFiles === undefined) delete process.env.ProgramFiles
      else process.env.ProgramFiles = originalProgramFiles
    }
  })

  it('uses only absolute dialog defaults and registers only the selected folder', async () => {
    const chosen = path.join(root, 'chosen')
    for (const input of [root, 'relative', null, 1]) {
      electron.dialog.showOpenDialog.mockResolvedValue({ canceled: false, filePaths: [chosen] })
      expect(await call('open-folder-dialog', input)).toBe(chosen)
      expect(electron.dialog.showOpenDialog).toHaveBeenLastCalledWith(
        input === root ? { properties: ['openDirectory'], defaultPath: root } : { properties: ['openDirectory'] })
    }
    expect(trust.registerTrustedRoot.mock.calls.map(args => args[0])).toEqual([chosen, chosen, chosen, chosen])
    trust.registerTrustedRoot.mockClear()
    electron.dialog.showOpenDialog.mockResolvedValue({ canceled: true, filePaths: [] })
    expect(await call('open-folder-dialog', root)).toBeNull()
    expect(trust.registerTrustedRoot).not.toHaveBeenCalled()
  })
})
