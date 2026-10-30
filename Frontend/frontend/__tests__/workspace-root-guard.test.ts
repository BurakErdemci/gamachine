import fs from 'fs'
import os from 'os'
import path from 'path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * delete-entry, delete-file, rename-entry and move-entry must never act on the
 * workspace root itself (the web remote can send delete-entry too), and the two
 * deletes go to the OS Trash instead of being permanent. The REAL handlers in
 * `background.ts` are exercised with electron mocked as in profile-reset-channel.test.ts.
 */

const handlers = new Map<string, (...args: any[]) => any>()
const trashItem = vi.fn(async (_p: string) => {})

vi.mock('axios', () => {
  const instance = { post: vi.fn(), get: vi.fn(), defaults: { headers: { common: {} } } }
  return { default: instance, ...instance }
})
vi.mock('electron', () => ({
  app: {
    getPath: vi.fn(() => os.tmpdir()), setPath: vi.fn(),
    requestSingleInstanceLock: vi.fn(() => false), quit: vi.fn(), on: vi.fn(),
  },
  ipcMain: { handle: vi.fn((channel: string, listener: (...args: any[]) => any) => handlers.set(channel, listener)) },
  dialog: {},
  shell: { trashItem: (p: string) => trashItem(p) },
  BrowserWindow: { getAllWindows: vi.fn(() => []), fromWebContents: vi.fn(() => null) },
  Notification: class { static isSupported = () => false },
}))
vi.mock('electron-serve', () => ({ default: vi.fn() }))
vi.mock('electron-updater', () => ({ autoUpdater: {} }))
vi.mock('node-pty', () => ({ spawn: vi.fn() }))
vi.mock('../main/helpers', () => ({ createWindow: vi.fn() }))
vi.mock('../main/helpers/ipc-trust', () => ({
  confirmLegacyRoot: vi.fn(() => false),
  isOwnFrame: vi.fn((event: any) => event?.senderFrame?.url === 'app://./home'),
  isTrustedRoot: vi.fn(() => true), registerTrustedRoot: vi.fn(),
}))
vi.mock('../main/helpers/csp', () => ({ applyContentSecurityPolicy: vi.fn() }))

const own = { senderFrame: { url: 'app://./home' }, sender: {} }
const ROOT_ERROR = 'Proje klasörünün kendisi silinemez/taşınamaz.'

let base = ''
let ws = ''
let rmSync: ReturnType<typeof vi.spyOn>
let unlinkSync: ReturnType<typeof vi.spyOn>

async function handler(channel: string) {
  await import('../main/background')
  const listener = handlers.get(channel)
  expect(listener).toBeTypeOf('function')
  return (...args: any[]) => listener!(own, ...args)
}

beforeEach(() => {
  base = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'workspace-root-guard-')))
  ws = path.join(base, 'Project')
  fs.mkdirSync(path.join(ws, 'Assets'), { recursive: true })
  fs.writeFileSync(path.join(ws, 'Assets', 'a.txt'), 'a')
  fs.writeFileSync(path.join(ws, 'b.txt'), 'b')
  trashItem.mockClear()
  rmSync = vi.spyOn(fs, 'rmSync')
  unlinkSync = vi.spyOn(fs, 'unlinkSync')
})

afterEach(() => {
  vi.restoreAllMocks()
  fs.rmSync(base, { recursive: true, force: true })
})

// Built per test: `ws` only exists once beforeEach has run.
const rootForms: Record<string, () => string> = {
  absolute: () => ws,
  'trailing separator': () => ws + path.sep,
  dot: () => '.',
  'dot segment': () => path.join(ws, 'Assets', '..'),
}

describe('workspace root is refused', () => {
  for (const channel of ['delete-entry', 'delete-file']) {
    for (const [name, form] of Object.entries(rootForms)) {
      it(`${channel}: ${name}`, async () => {
        const call = await handler(channel)
        const result = await call(form(), ws)
        expect(result).toEqual({ success: false, error: ROOT_ERROR })
        expect(trashItem).not.toHaveBeenCalled()
        expect(fs.existsSync(path.join(ws, 'b.txt'))).toBe(true)
      })
    }
  }

  for (const [name, form] of Object.entries(rootForms)) {
    it(`rename-entry: ${name}`, async () => {
      const call = await handler('rename-entry')
      const result = await call(form(), 'Renamed', ws)
      expect(result).toEqual({ success: false, error: ROOT_ERROR })
      expect(fs.existsSync(ws)).toBe(true)
    })

    it(`move-entry source: ${name}`, async () => {
      const call = await handler('move-entry')
      const result = await call(form(), path.join(ws, 'Assets'), ws)
      expect(result).toEqual({ success: false, error: ROOT_ERROR })
      expect(fs.existsSync(ws)).toBe(true)
    })
  }

  it('case-different spelling of the root is refused where paths are case-insensitive', async () => {
    const insensitive = fs.existsSync(ws.toUpperCase())
    const call = await handler('delete-entry')
    const result = await call(ws.toUpperCase(), ws)
    expect(result.success).toBe(false)
    if (insensitive) expect(result.error).toBe(ROOT_ERROR)
    expect(trashItem).not.toHaveBeenCalled()
    expect(fs.existsSync(path.join(ws, 'b.txt'))).toBe(true)
  })

  it('a symlink to the root is refused by every channel', async () => {
    const link = path.join(base, 'link')
    try {
      fs.symlinkSync(ws, link, 'junction')
    } catch {
      return
    }
    for (const channel of ['delete-entry', 'delete-file']) {
      const result = await (await handler(channel))(link, ws)
      expect(result.success).toBe(false)
    }
    expect((await (await handler('rename-entry'))(link, 'X', ws)).success).toBe(false)
    expect((await (await handler('move-entry'))(link, path.join(ws, 'Assets'), ws)).success).toBe(false)
    expect(trashItem).not.toHaveBeenCalled()
    expect(fs.existsSync(path.join(ws, 'b.txt'))).toBe(true)
  })
})

describe('entries inside the workspace still work', () => {
  it('delete-entry moves a folder to the Trash and never deletes permanently', async () => {
    const call = await handler('delete-entry')
    const target = path.join(ws, 'Assets')
    expect(await call(target, ws)).toEqual({ success: true })
    expect(trashItem).toHaveBeenCalledExactlyOnceWith(target)
    expect(rmSync).not.toHaveBeenCalledWith(target, expect.anything())
    expect(unlinkSync).not.toHaveBeenCalledWith(target)
  })

  it('delete-file moves a file to the Trash and never unlinks it', async () => {
    const call = await handler('delete-file')
    const target = path.join(ws, 'b.txt')
    expect(await call('b.txt', ws)).toEqual({ success: true })
    expect(trashItem).toHaveBeenCalledExactlyOnceWith(target)
    expect(unlinkSync).not.toHaveBeenCalledWith(target)
  })

  it('a Trash failure returns an error and deletes nothing', async () => {
    trashItem.mockRejectedValueOnce(new Error('trash unavailable'))
    const call = await handler('delete-entry')
    const result = await call(path.join(ws, 'Assets'), ws)
    expect(result).toEqual({ success: false, error: 'trash unavailable' })
    expect(fs.existsSync(path.join(ws, 'Assets', 'a.txt'))).toBe(true)
    expect(rmSync).not.toHaveBeenCalledWith(path.join(ws, 'Assets'), expect.anything())
  })

  it('rename-entry renames an inner entry', async () => {
    const call = await handler('rename-entry')
    const result = await call(path.join(ws, 'b.txt'), 'c.txt', ws)
    expect(result.success).toBe(true)
    expect(fs.existsSync(path.join(ws, 'c.txt'))).toBe(true)
  })

  it('move-entry may target the root itself', async () => {
    const call = await handler('move-entry')
    const result = await call(path.join(ws, 'Assets', 'a.txt'), ws, ws)
    expect(result.success).toBe(true)
    expect(fs.existsSync(path.join(ws, 'a.txt'))).toBe(true)
  })
})
