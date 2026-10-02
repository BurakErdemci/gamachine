import fs from 'fs'
import os from 'os'
import { expect, it, vi } from 'vitest'

/**
 * The permitted-channel list was declared but never consulted by the receiver,
 * from an external audit probe.
 *
 * `assertAllowedInvokeChannel` ran in exactly one place — inside `invoke`, in
 * the preload, in the renderer process. Anything reaching `ipcMain` without
 * going through this app's preload was unfiltered.
 *
 * The experiment: make the list refuse EVERY channel, then load the main
 * process. If the receiving layer consults the list, nothing registers and
 * nothing is served. Before the fix all 27 handlers registered anyway and
 * `read-model-file` still returned file bytes.
 */

const registered = new Map<string, (...args: any[]) => any>()
vi.mock('electron', () => ({
  app: {
    getPath: vi.fn(() => os.tmpdir()), setPath: vi.fn(),
    requestSingleInstanceLock: vi.fn(() => false), quit: vi.fn(), on: vi.fn(),
  },
  ipcMain: { handle: vi.fn((c: string, l: (...a: any[]) => any) => registered.set(c, l)) },
  dialog: {}, shell: {}, BrowserWindow: { getAllWindows: vi.fn(() => []) },
}))
vi.mock('electron-serve', () => ({ default: vi.fn() }))
vi.mock('electron-updater', () => ({ autoUpdater: {} }))
vi.mock('node-pty', () => ({ spawn: vi.fn() }))
vi.mock('../main/helpers', () => ({ createWindow: vi.fn() }))
vi.mock('../main/helpers/ipc-trust', () => ({
  confirmLegacyRoot: vi.fn(() => false), isOwnFrame: vi.fn(() => true),
  isTrustedRoot: vi.fn(() => true), registerTrustedRoot: vi.fn(),
}))
vi.mock('../main/helpers/csp', () => ({ applyContentSecurityPolicy: vi.fn() }))

const consulted: string[] = []
const refuse = (channel: string) => {
  consulted.push(channel)
  throw new Error(`IPC channel izinsiz: ${channel}`)
}
// Registration goes through assertRegistrableChannel since the preload-only drop
// channel (2 Oct 2026); both checks refuse everything here.
vi.mock('../main/helpers/ipc-whitelist', () => ({
  ALLOWED_INVOKE_CHANNELS: new Set<string>(),
  PRELOAD_ONLY_CHANNELS: new Set<string>(),
  assertAllowedInvokeChannel: refuse,
  assertRegistrableChannel: refuse,
}))

it('the main process serves no channel the permitted list rejects', async () => {
  const originalAppend = fs.appendFileSync.bind(fs)
  vi.spyOn(fs, 'appendFileSync').mockImplementation(((file: fs.PathOrFileDescriptor, data: any, options?: any) => {
    if (typeof file === 'string' && file.endsWith('gamachine.log')) return
    return originalAppend(file, data, options)
  }) as typeof fs.appendFileSync)

  await expect(import('../main/background')).rejects.toThrow(/izinsiz/)

  // The list is consulted by the receiving layer, and nothing it rejected is
  // reachable: no handler for it exists to be invoked at all.
  expect(consulted.length).toBeGreaterThan(0)
  expect(registered.has('read-model-file')).toBe(false)
  expect(registered.size).toBe(0)
})
