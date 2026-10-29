import fs from 'fs'
import os from 'os'
import path from 'path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const electron = vi.hoisted(() => ({
  userData: '',
  app: { getPath: vi.fn(() => electron.userData) },
  dialog: { showMessageBoxSync: vi.fn() },
  BrowserWindow: {},
}))
vi.mock('electron', () => electron)

import { confirmRecentRoot, isTrustedRoot } from '../main/helpers/ipc-trust'

describe('confirmRecentRoot', () => {
  let base: string
  let project: string
  let plain: string

  beforeEach(() => {
    base = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'authorize-ws-')))
    electron.userData = path.join(base, 'userData')
    fs.mkdirSync(electron.userData)
    project = path.join(base, 'Game')
    fs.mkdirSync(path.join(project, 'Assets'), { recursive: true })
    fs.mkdirSync(path.join(project, 'ProjectSettings'))
    plain = path.join(base, 'Plain')
    fs.mkdirSync(plain)
    electron.dialog.showMessageBoxSync.mockReset()
  })
  afterEach(() => { fs.rmSync(base, { recursive: true, force: true }) })

  it('approve registers the project and returns true', () => {
    electron.dialog.showMessageBoxSync.mockReturnValue(0)
    expect(confirmRecentRoot(project)).toBe(true)
    expect(electron.dialog.showMessageBoxSync).toHaveBeenCalledTimes(1)
    const options = electron.dialog.showMessageBoxSync.mock.calls[0][0]
    expect(options.buttons).toEqual(['Yetkilendir', 'İptal'])
    expect(options.defaultId).toBe(1)
    expect(options.detail).toContain(project)
    expect(isTrustedRoot(project)).toBe(true)
  })

  it('already trusted returns true without a dialog', () => {
    electron.dialog.showMessageBoxSync.mockReturnValue(0)
    expect(confirmRecentRoot(project)).toBe(true)
    electron.dialog.showMessageBoxSync.mockClear()
    expect(confirmRecentRoot(project)).toBe(true)
    expect(electron.dialog.showMessageBoxSync).not.toHaveBeenCalled()
  })

  it('cancel returns false and registers nothing', () => {
    electron.dialog.showMessageBoxSync.mockReturnValue(1)
    expect(confirmRecentRoot(project)).toBe(false)
    expect(electron.dialog.showMessageBoxSync).toHaveBeenCalledTimes(1)
    expect(isTrustedRoot(project)).toBe(false)
  })

  it('non-Unity and missing directories are refused without a dialog', () => {
    expect(confirmRecentRoot(plain)).toBe(false)
    expect(confirmRecentRoot(path.join(base, 'Nope'))).toBe(false)
    expect(confirmRecentRoot('')).toBe(false)
    expect(electron.dialog.showMessageBoxSync).not.toHaveBeenCalled()
    expect(isTrustedRoot(plain)).toBe(false)
  })

  it('registers nothing when the path is swapped while the box is open', () => {
    electron.dialog.showMessageBoxSync.mockImplementation(() => {
      fs.renameSync(project, path.join(base, 'GameMoved'))
      fs.symlinkSync(plain, project)
      return 0
    })
    expect(confirmRecentRoot(project)).toBe(false)
    expect(isTrustedRoot(project)).toBe(false)
    expect(isTrustedRoot(plain)).toBe(false)
  })

  it('registers nothing when the folder stops being a Unity project while the box is open', () => {
    electron.dialog.showMessageBoxSync.mockImplementation(() => {
      fs.rmSync(path.join(project, 'ProjectSettings'), { recursive: true })
      return 0
    })
    expect(confirmRecentRoot(project)).toBe(false)
    expect(isTrustedRoot(project)).toBe(false)
  })

  it('passes the parent window to the native box', () => {
    electron.dialog.showMessageBoxSync.mockReturnValue(1)
    const parent = {} as any
    confirmRecentRoot(project, parent)
    expect(electron.dialog.showMessageBoxSync.mock.calls[0][0]).toBe(parent)
  })
})
