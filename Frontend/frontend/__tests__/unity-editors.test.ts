import path from 'path'
import { describe, expect, it } from 'vitest'
import { EditorDeps, listEditors, listTemplates } from '../main/helpers/unity-editors'

function fakeDeps(platform: NodeJS.Platform, files: Record<string, string | string[]>, extra: Partial<EditorDeps> = {}): EditorDeps {
  return {
    platform,
    env: {},
    homedir: '/Users/tester',
    exists: p => p in files,
    readdir: p => {
      const v = files[p]
      if (!Array.isArray(v)) throw new Error('ENOENT')
      return v
    },
    readFile: p => {
      const v = files[p]
      if (typeof v !== 'string') throw new Error('ENOENT')
      return v
    },
    ...extra,
  }
}

const w = path.win32
const winRoot = 'C:\\Program Files\\Unity\\Hub\\Editor'
const winExe = (root: string, v: string) => w.join(root, v, 'Editor', 'Unity.exe')
const macRoot = '/Applications/Unity/Hub/Editor'
const macExe = (v: string) => `${macRoot}/${v}/Unity.app/Contents/MacOS/Unity`

describe('listEditors', () => {
  it('finds Windows editors newest first and skips folders without Unity.exe', () => {
    const deps = fakeDeps('win32', {
      [winRoot]: ['2022.3.10f1', '6000.0.62f1', '6000.0.9f1', 'Broken'],
      [winExe(winRoot, '2022.3.10f1')]: '',
      [winExe(winRoot, '6000.0.62f1')]: '',
      [winExe(winRoot, '6000.0.9f1')]: '',
    })
    expect(listEditors(deps).editors).toEqual([
      { version: '6000.0.62f1', path: winExe(winRoot, '6000.0.62f1') },
      { version: '6000.0.9f1', path: winExe(winRoot, '6000.0.9f1') },
      { version: '2022.3.10f1', path: winExe(winRoot, '2022.3.10f1') },
    ])
  })

  it('adds the Windows secondaryInstallPath root and ignores an empty one', () => {
    const second = 'D:\\Editors'
    const secondFile = 'C:\\Users\\t\\AppData\\Roaming\\UnityHub\\secondaryInstallPath.json'
    const base = {
      [winRoot]: ['6000.0.62f1'],
      [winExe(winRoot, '6000.0.62f1')]: '',
      [second]: ['6000.1.0f1'],
      [winExe(second, '6000.1.0f1')]: '',
    }
    const env = { APPDATA: 'C:\\Users\\t\\AppData\\Roaming' }
    const withSecond = fakeDeps('win32', { ...base, [secondFile]: JSON.stringify(second) }, { env })
    expect(listEditors(withSecond).editors.map(e => e.version)).toEqual(['6000.1.0f1', '6000.0.62f1'])
    const empty = fakeDeps('win32', { ...base, [secondFile]: '""' }, { env })
    expect(listEditors(empty).editors.map(e => e.version)).toEqual(['6000.0.62f1'])
  })

  it('honours ProgramFiles and survives an unreadable root', () => {
    const deps = fakeDeps('win32', {}, { env: { ProgramFiles: 'E:\\PF' } })
    expect(listEditors(deps)).toEqual({ editors: [] })
  })

  it('finds macOS editors, including a secondary root', () => {
    const secondFile = '/Users/tester/Library/Application Support/UnityHub/secondaryInstallPath.json'
    const deps = fakeDeps('darwin', {
      [macRoot]: ['6000.4.4f1'],
      [macExe('6000.4.4f1')]: '',
      '/Volumes/X/Editors': ['2022.3.1f1'],
      '/Volumes/X/Editors/2022.3.1f1/Unity.app/Contents/MacOS/Unity': '',
      [secondFile]: '"/Volumes/X/Editors"',
    })
    expect(listEditors(deps).editors).toEqual([
      { version: '6000.4.4f1', path: macExe('6000.4.4f1') },
      { version: '2022.3.1f1', path: '/Volumes/X/Editors/2022.3.1f1/Unity.app/Contents/MacOS/Unity' },
    ])
  })

  it('returns nothing on other platforms', () => {
    expect(listEditors(fakeDeps('linux', {}))).toEqual({ editors: [] })
  })
})

describe('listTemplates', () => {
  it('classifies the macOS templates', () => {
    const dir = `${macRoot}/6000.4.4f1/Unity.app/Contents/Resources/PackageManager/ProjectTemplates`
    const deps = fakeDeps('darwin', {
      [macRoot]: ['6000.4.4f1'],
      [macExe('6000.4.4f1')]: '',
      [dir]: [
        'com.unity.template.3d-high-end-17.0.7.tgz',
        'com.unity.template.2d-cross-platform-2d-6.1.3.tgz',
        'com.unity.template.3d-cross-platform-17.0.14.tgz',
        'com.unity.template.vr-1.0.0.tgz',
        'libcache',
      ],
    })
    expect(listTemplates('6000.4.4f1', deps).templates).toEqual([
      { id: 'com.unity.template.2d-cross-platform-2d-6.1.3', label: '2d', file: 'com.unity.template.2d-cross-platform-2d-6.1.3.tgz' },
      { id: 'com.unity.template.3d-cross-platform-17.0.14', label: '3d', file: 'com.unity.template.3d-cross-platform-17.0.14.tgz' },
      { id: 'com.unity.template.3d-high-end-17.0.7', label: 'hdrp', file: 'com.unity.template.3d-high-end-17.0.7.tgz' },
      { id: 'com.unity.template.vr-1.0.0', label: 'other', file: 'com.unity.template.vr-1.0.0.tgz' },
    ])
  })

  it('reads the Windows template folder beside Unity.exe', () => {
    const dir = w.join(winRoot, '6000.0.62f1', 'Editor', 'Data', 'Resources', 'PackageManager', 'ProjectTemplates')
    const deps = fakeDeps('win32', {
      [winRoot]: ['6000.0.62f1'],
      [winExe(winRoot, '6000.0.62f1')]: '',
      [dir]: ['com.unity.template.3d-cross-platform-17.0.14.tgz'],
    })
    expect(listTemplates('6000.0.62f1', deps).templates.map(t => t.label)).toEqual(['3d'])
  })

  it('is empty for an unknown version or a missing folder', () => {
    const deps = fakeDeps('darwin', { [macRoot]: ['6000.4.4f1'], [macExe('6000.4.4f1')]: '' })
    expect(listTemplates('1.0.0f1', deps)).toEqual({ templates: [] })
    expect(listTemplates('6000.4.4f1', deps)).toEqual({ templates: [] })
  })
})
