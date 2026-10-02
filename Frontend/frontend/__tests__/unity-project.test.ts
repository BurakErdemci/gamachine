import fs from 'fs'
import os from 'os'
import path from 'path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { inspectProject, isUnityProjectDir, readUnityVersion } from '../main/helpers/unity-project'

describe('Unity project inspection', () => {
  let root: string
  beforeEach(() => { root = fs.mkdtempSync(path.join(os.tmpdir(), 'unity-project-')) })
  afterEach(() => {
    vi.restoreAllMocks()
    fs.rmSync(root, { recursive: true, force: true })
  })

  function makeProject() {
    fs.mkdirSync(path.join(root, 'Assets'))
    fs.mkdirSync(path.join(root, 'ProjectSettings'))
  }

  function versionFile() { return path.join(root, 'ProjectSettings', 'ProjectVersion.txt') }

  it('requires a directory with Assets and ProjectSettings directories', () => {
    expect(isUnityProjectDir(root)).toBe(false)
    fs.mkdirSync(path.join(root, 'Assets'))
    fs.writeFileSync(path.join(root, 'ProjectSettings'), '')
    expect(isUnityProjectDir(root)).toBe(false)
    fs.unlinkSync(path.join(root, 'ProjectSettings'))
    fs.mkdirSync(path.join(root, 'ProjectSettings'))
    expect(isUnityProjectDir(root)).toBe(true)
    expect(isUnityProjectDir(path.join(root, 'absent'))).toBe(false)
    fs.writeFileSync(versionFile(), 'm_EditorVersion: 6000.1.1f1')
    expect(isUnityProjectDir(versionFile())).toBe(false)
  })

  it('parses and trims only the editor version line', () => {
    makeProject()
    fs.writeFileSync(versionFile(), 'm_EditorVersionWithRevision: ignored\r\nm_EditorVersion:  6000.1.1f1  \r\n')
    expect(readUnityVersion(root)).toBe('6000.1.1f1')
    expect(inspectProject(root)).toEqual({ exists: true, unityVersion: '6000.1.1f1' })
  })

  it('returns null for missing, absent or empty version', () => {
    makeProject()
    expect(readUnityVersion(root)).toBeNull()
    fs.writeFileSync(versionFile(), 'm_EditorVersionWithRevision: ignored')
    expect(readUnityVersion(root)).toBeNull()
    fs.writeFileSync(versionFile(), 'm_EditorVersion:   \n')
    expect(readUnityVersion(root)).toBeNull()
  })

  it('reads at most 4096 bytes while parsing the first line of a larger file', () => {
    makeProject()
    fs.writeFileSync(versionFile(), 'm_EditorVersion: 2022.3.0f1\n' + 'x'.repeat(8000))
    const read = vi.spyOn(fs, 'readSync')
    expect(readUnityVersion(root)).toBe('2022.3.0f1')
    expect(read.mock.calls.reduce((total, args) => total + Number((args as unknown[])[3]), 0)).toBeLessThanOrEqual(4096)
    fs.writeFileSync(versionFile(), 'x'.repeat(4096) + '\nm_EditorVersion: outside-bound\n')
    expect(readUnityVersion(root)).toBeNull()
  })

  it('never throws on unreadable files', () => {
    makeProject()
    fs.mkdirSync(versionFile())
    expect(readUnityVersion(root)).toBeNull()
    vi.spyOn(fs, 'openSync').mockImplementation(() => { throw new Error('denied') })
    expect(readUnityVersion(root)).toBeNull()
  })

  it('inspects directory existence independently of Unity markers', () => {
    expect(inspectProject(root)).toEqual({ exists: true, unityVersion: null })
    expect(inspectProject(path.join(root, 'missing'))).toEqual({ exists: false, unityVersion: null })
    fs.writeFileSync(path.join(root, 'file'), '')
    expect(inspectProject(path.join(root, 'file'))).toEqual({ exists: false, unityVersion: null })
  })

  it('rejects a directory version file before attempting to open it', () => {
    makeProject()
    fs.mkdirSync(versionFile())
    const open = vi.spyOn(fs, 'openSync')
    expect(readUnityVersion(root)).toBeNull()
    expect(open).not.toHaveBeenCalled()
  })
})
