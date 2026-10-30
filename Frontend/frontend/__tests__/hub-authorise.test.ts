import fs from 'fs'
import os from 'os'
import path from 'path'
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

// The real background.ts handlers with the real trust registry under a temp userData; only the
// native box is faked. A recent project from the backend list is untrusted until the user approves.
const electron = vi.hoisted(() => ({
  userData: '',
  handlers: new Map<string, (...args: any[]) => any>(),
  app: {
    getPath: vi.fn(() => electron.userData), setPath: vi.fn(),
    requestSingleInstanceLock: vi.fn(() => false), quit: vi.fn(), on: vi.fn(),
  },
  ipcMain: { handle: vi.fn() },
  dialog: { showOpenDialog: vi.fn(), showMessageBoxSync: vi.fn() },
  shell: { openPath: vi.fn() },
  BrowserWindow: { getAllWindows: vi.fn(() => []), fromWebContents: vi.fn(() => electron.win) },
  win: { id: 'main-window' },
  contextBridge: { exposeInMainWorld: vi.fn() },
  ipcRenderer: { invoke: vi.fn(), on: vi.fn(), removeListener: vi.fn() },
  webUtils: { getPathForFile: vi.fn() },
}))
vi.mock('electron', () => electron)
vi.mock('electron-serve', () => ({ default: vi.fn() }))
vi.mock('electron-updater', () => ({ autoUpdater: {} }))
vi.mock('node-pty', () => ({ spawn: vi.fn() }))
vi.mock('../main/helpers', () => ({ createWindow: vi.fn() }))
vi.mock('../main/helpers/csp', () => ({ applyContentSecurityPolicy: vi.fn() }))
vi.mock('../main/helpers/ipc-trust', async (original) => ({
  ...(await original<typeof import('../main/helpers/ipc-trust')>()),
  isOwnFrame: (event: any) => event.own === true,
}))

const own = { own: true, sender: {} }
const call = (channel: string, ...args: unknown[]) => electron.handlers.get(channel)!(own, ...args)
let base: string
let n = 0
const project = () => {
  const dir = path.join(base, `Game${++n}`)
  fs.mkdirSync(path.join(dir, 'Assets'), { recursive: true })
  fs.mkdirSync(path.join(dir, 'ProjectSettings'))
  return dir
}
const status = async (p: string) => (await call('workspace-info', [p]))[0].status

beforeAll(async () => {
  base = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'hub-authorise-')))
  electron.userData = path.join(base, 'userData')
  fs.mkdirSync(electron.userData)
  electron.ipcMain.handle.mockImplementation((channel: string, listener: any) => electron.handlers.set(channel, listener))
  await import('../main/background')
  // Not the first run: a registry already exists, so the one-time legacy box stays out of the way.
  fs.writeFileSync(path.join(electron.userData, 'trusted-workspaces.json'), JSON.stringify([path.join(base, 'elsewhere')]))
}, 30_000)
afterAll(() => { fs.rmSync(base, { recursive: true, force: true }) })
beforeEach(() => { electron.dialog.showMessageBoxSync.mockReset() })

describe('authorising an untrusted recent project', () => {
  it('approval asks once over the app window, records the trust and unlocks launching', async () => {
    const dir = project()
    expect(await status(dir)).toBe('untrusted')
    expect(await call('hub-open-project', { path: dir })).toEqual({ opened: false, error: 'Project folder is not trusted.' })

    electron.dialog.showMessageBoxSync.mockReturnValue(0)
    expect(await call('authorize-workspace', dir)).toEqual({ trusted: true })
    expect(electron.dialog.showMessageBoxSync).toHaveBeenCalledTimes(1)
    const [parent, options] = electron.dialog.showMessageBoxSync.mock.calls[0]
    expect(parent).toBe(electron.win)
    expect(options.detail).toContain(dir)

    expect(await status(dir)).toBe('ok')
    const record = JSON.parse(fs.readFileSync(path.join(electron.userData, 'trusted-workspaces.json'), 'utf8'))
    expect(record).toContain(dir)
    // Past the trust gate now: the launcher stops at the next check (no ProjectVersion.txt here).
    expect((await call('hub-open-project', { path: dir })).error).not.toBe('Project folder is not trusted.')

    // Asked again, it answers from the record without another box.
    expect(await call('authorize-workspace', dir)).toEqual({ trusted: true })
    expect(electron.dialog.showMessageBoxSync).toHaveBeenCalledTimes(1)
  })

  it('cancel changes nothing', async () => {
    const dir = project()
    const before = fs.readFileSync(path.join(electron.userData, 'trusted-workspaces.json'), 'utf8')
    electron.dialog.showMessageBoxSync.mockReturnValue(1)
    expect(await call('authorize-workspace', dir)).toEqual({ trusted: false })
    expect(await status(dir)).toBe('untrusted')
    expect(fs.readFileSync(path.join(electron.userData, 'trusted-workspaces.json'), 'utf8')).toBe(before)
  })

  it('refuses a relative path, a non-string and a folder that is not a Unity project without asking', async () => {
    const plain = path.join(base, 'Plain')
    fs.mkdirSync(plain)
    for (const arg of ['Game1', 42, null, plain]) {
      expect(await call('authorize-workspace', arg)).toEqual({ trusted: false })
    }
    expect(electron.dialog.showMessageBoxSync).not.toHaveBeenCalled()
  })

  it('a frame that is not the app is refused before any box', () => {
    expect(() => electron.handlers.get('authorize-workspace')!({ own: false }, project())).toThrow()
    expect(electron.dialog.showMessageBoxSync).not.toHaveBeenCalled()
  })
})
