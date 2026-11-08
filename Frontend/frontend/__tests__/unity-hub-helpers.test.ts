import { EventEmitter } from 'node:events'
import fs from 'fs'
import os from 'os'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const cp = vi.hoisted(() => ({ execFileSync: vi.fn(), spawn: vi.fn() }))
vi.mock('child_process', () => ({ ...cp, default: cp }))

import { isHubRunning, isLicensingReady, startHub, UNITY_HUB_MSIX_FAMILY, UNITY_HUB_MSIX_URI } from '../main/helpers/unity-hub'

const realPlatform = process.platform
const realLocalAppData = process.env.LOCALAPPDATA
const realProgramFiles = process.env.ProgramFiles
const setPlatform = (value: string) => Object.defineProperty(process, 'platform', { value, configurable: true })

beforeEach(() => {
  cp.execFileSync.mockReset()
  cp.spawn.mockReset()
  vi.spyOn(os, 'userInfo').mockReturnValue({ username: 'Burak' } as any)
})
afterEach(() => {
  vi.restoreAllMocks()
  setPlatform(realPlatform)
  if (realLocalAppData === undefined) delete process.env.LOCALAPPDATA
  else process.env.LOCALAPPDATA = realLocalAppData
  if (realProgramFiles === undefined) delete process.env.ProgramFiles
  else process.env.ProgramFiles = realProgramFiles
})

describe('isLicensingReady (win32)', () => {
  it('is true when the licensing pipe is listed', () => {
    vi.spyOn(fs, 'readdirSync').mockReturnValue(['other', 'Unity-LicenseClient-burak'] as any)
    expect(isLicensingReady('win32')).toBe(true)
  })

  it('matches the pipe name case-insensitively', () => {
    vi.spyOn(fs, 'readdirSync').mockReturnValue(['UNITY-LICENSECLIENT-BURAK'] as any)
    expect(isLicensingReady('win32')).toBe(true)
  })

  it('is false when no pipe matches', () => {
    vi.spyOn(fs, 'readdirSync').mockReturnValue(['Unity-LicenseClient-someoneelse', 'other'] as any)
    expect(isLicensingReady('win32')).toBe(false)
  })

  it('is false when listing the pipe directory throws', () => {
    vi.spyOn(fs, 'readdirSync').mockImplementation(() => { throw new Error('EPERM') })
    expect(isLicensingReady('win32')).toBe(false)
  })
})

describe('isHubRunning (win32)', () => {
  it('is true when tasklist lists the Hub, whatever the case', () => {
    cp.execFileSync.mockReturnValue('"unity hub.exe","1234","Console","1","90,000 K"\r\n')
    expect(isHubRunning('win32')).toBe(true)
  })

  it('is false when tasklist reports no match', () => {
    cp.execFileSync.mockReturnValue('INFO: No tasks are running which match the specified criteria.\r\n')
    expect(isHubRunning('win32')).toBe(false)
  })

  it('is false when tasklist throws or times out', () => {
    cp.execFileSync.mockImplementation(() => { throw Object.assign(new Error('spawnSync tasklist ETIMEDOUT'), { code: 'ETIMEDOUT' }) })
    expect(isHubRunning('win32')).toBe(false)
  })

  it('bounds the tasklist call with a timeout', () => {
    cp.execFileSync.mockReturnValue('')
    isHubRunning('win32')
    expect(cp.execFileSync.mock.calls[0][0]).toBe('tasklist')
    expect(cp.execFileSync.mock.calls[0][2]).toMatchObject({ timeout: 2000 })
  })
})

describe('startHub (win32)', () => {
  const hubExe = 'C:\\Program Files\\Unity Hub\\Unity Hub.exe'
  const asWindows = (installed: string[]) => {
    setPlatform('win32')
    process.env.LOCALAPPDATA = 'C:\\Users\\Burak\\AppData\\Local'
    vi.spyOn(fs, 'existsSync').mockImplementation(p => installed.includes(String(p)))
  }

  it('starts an MSIX Hub through explorer.exe and the AppsFolder URI', async () => {
    asWindows(['C:\\Users\\Burak\\AppData\\Local\\Packages\\' + UNITY_HUB_MSIX_FAMILY])
    const child = Object.assign(new EventEmitter(), { unref: vi.fn() })
    cp.spawn.mockReturnValue(child)
    const openPath = vi.fn(async () => '')
    expect(await startHub(openPath)).toBe(true)
    expect(cp.spawn).toHaveBeenCalledWith('explorer.exe', [UNITY_HUB_MSIX_URI], expect.objectContaining({ detached: true }))
    expect(child.unref).toHaveBeenCalled()
    expect(openPath).not.toHaveBeenCalled()
  })

  it('opens a classic install with openPath: empty answer is success', async () => {
    asWindows([hubExe])
    process.env.ProgramFiles = 'C:\\Program Files'
    const openPath = vi.fn(async () => '')
    expect(await startHub(openPath)).toBe(true)
    expect(openPath).toHaveBeenCalledWith(hubExe)
  })

  it('is false when openPath answers with an error text', async () => {
    asWindows([hubExe])
    process.env.ProgramFiles = 'C:\\Program Files'
    expect(await startHub(async () => 'Failed to open path')).toBe(false)
  })

  it('is false when no Hub is installed', async () => {
    asWindows([])
    const openPath = vi.fn(async () => '')
    expect(await startHub(openPath)).toBe(false)
    expect(openPath).not.toHaveBeenCalled()
    expect(cp.spawn).not.toHaveBeenCalled()
  })
})
