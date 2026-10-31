import fs from 'fs'
import os from 'os'
import path from 'path'
import { afterEach, beforeEach, describe, it, expect, vi } from 'vitest'
import {
  isAllowedUnityScriptPath,
  isAllowedWorkspacePath,
  isAllowedWorkspaceWriteFile,
  isInsideWorkspace,
  isWritableExistingTarget,
  okumaKarariVer,
} from '../main/helpers/file-security'

let root = ''
let ws = ''
let outside = ''

beforeEach(() => {
  root = fs.realpathSync.native(fs.mkdtempSync(path.join(os.tmpdir(), 'unity-dangling-')))
  ws = path.join(root, 'ws')
  outside = path.join(root, 'outside')
  fs.mkdirSync(ws)
  fs.mkdirSync(outside)
})

afterEach(() => {
  vi.restoreAllMocks()
  if (root) fs.rmSync(root, { recursive: true, force: true })
  root = ''
})

function link(target: string, linkPath: string, type?: 'dir'): boolean {
  try {
    fs.symlinkSync(target, linkPath, type)
    return true
  } catch (err: any) {
    if (err?.code === 'EPERM') return false
    throw err
  }
}

const canWrite = (p: string) => isAllowedWorkspaceWriteFile(p, ws) && isWritableExistingTarget(p)

describe('dangling symlinks', () => {
  it('refuses write and create through a dangling file link pointing outside', () => {
    if (!link(path.join(outside, 'escaped.md'), path.join(ws, 'notes.md'))) return
    expect(canWrite(path.join(ws, 'notes.md'))).toBe(false)
    expect(isAllowedWorkspacePath(path.join(ws, 'notes.md'), ws)).toBe(false)
  })

  it('refuses a path under a dangling directory link pointing outside', () => {
    if (!link(path.join(outside, 'newdir'), path.join(ws, 'dir'), 'dir')) return
    expect(canWrite(path.join(ws, 'dir', 'a.md'))).toBe(false)
    expect(isAllowedWorkspacePath(path.join(ws, 'dir', 'a.md'), ws)).toBe(false)
  })

  it('allows a dangling link to a missing file inside the workspace', () => {
    if (!link(path.join(ws, 'later.md'), path.join(ws, 'inside-link.md'))) return
    expect(canWrite(path.join(ws, 'inside-link.md'))).toBe(true)
    expect(isAllowedWorkspacePath(path.join(ws, 'inside-link.md'), ws)).toBe(true)
  })

  it('refuses a two-link cycle', () => {
    if (!link(path.join(ws, 'loop-b.md'), path.join(ws, 'loop-a.md'))) return
    if (!link(path.join(ws, 'loop-a.md'), path.join(ws, 'loop-b.md'))) return
    expect(canWrite(path.join(ws, 'loop-a.md'))).toBe(false)
    expect(isAllowedWorkspacePath(path.join(ws, 'loop-a.md'), ws)).toBe(false)
  })

  it('allows a plain new file', () => {
    expect(canWrite(path.join(ws, 'fresh.md'))).toBe(true)
    expect(isAllowedWorkspacePath(path.join(ws, 'fresh.md'), ws)).toBe(true)
  })

  it('still allows an existing symlink to an inside file', () => {
    fs.writeFileSync(path.join(ws, 'real.md'), 'x')
    if (!link(path.join(ws, 'real.md'), path.join(ws, 'alias.md'))) return
    expect(canWrite(path.join(ws, 'alias.md'))).toBe(true)
  })

  it('still refuses an existing symlink to an outside file', () => {
    fs.writeFileSync(path.join(outside, 'real.md'), 'x')
    if (!link(path.join(outside, 'real.md'), path.join(ws, 'alias.md'))) return
    expect(canWrite(path.join(ws, 'alias.md'))).toBe(false)
  })

  it("refuses a dangling link whose target goes through '..' past a directory link", () => {
    fs.mkdirSync(path.join(outside, 'deep'))
    if (!link(path.join(outside, 'deep'), path.join(ws, 'd'), 'dir')) return
    if (!link('d/../escaped.md', path.join(ws, 'dotdot.md'))) return
    expect(canWrite(path.join(ws, 'dotdot.md'))).toBe(false)
    expect(isAllowedWorkspacePath(path.join(ws, 'dotdot.md'), ws)).toBe(false)
  })

  it('refuses a dangling link reached through an inside directory link whose target climbs out', () => {
    if (!link('.', path.join(ws, 'inl'), 'dir')) return
    if (!link('../zz.md', path.join(ws, 'dl.md'))) return
    expect(canWrite(path.join(ws, 'inl', 'dl.md'))).toBe(false)
  })

  it('refuses everything when the workspace itself is a link cycle', () => {
    const a = path.join(root, 'wsA')
    const b = path.join(root, 'wsB')
    if (!link(b, a, 'dir') || !link(a, b, 'dir')) return
    const c1 = path.join(root, 'c1')
    const c2 = path.join(root, 'c2')
    if (!link(c2, c1) || !link(c1, c2)) return
    const target = path.join(c1, 'x.md')
    expect(isAllowedWorkspacePath(target, a)).toBe(false)
    expect(isAllowedWorkspaceWriteFile(target, a)).toBe(false)
    expect(isAllowedUnityScriptPath(path.join(c1, 'x.cs'), a)).toBe(false)
    expect(isInsideWorkspace(target, a)).toBe(false)
  })

  it('refuses a cycle target in the read policy', () => {
    if (!link(path.join(ws, 'r2.md'), path.join(ws, 'r1.md'))) return
    if (!link(path.join(ws, 'r1.md'), path.join(ws, 'r2.md'))) return
    expect(okumaKarariVer(path.join(ws, 'r1.md'), ws).izinli).toBe(false)
  })
})

function failOn(name: 'lstatSync' | 'statSync', victim: string, code = 'EIO'): void {
  const original = fs[name] as (...args: any[]) => any
  vi.spyOn(fs, name).mockImplementation(((p: fs.PathLike, ...rest: any[]) => {
    if (String(p) === victim) throw Object.assign(new Error(`injected ${code}`), { code })
    return original(p, ...rest)
  }) as any)
}

const everyGuard = (target: string) => [
  isAllowedWorkspacePath(target, ws),
  isAllowedWorkspaceWriteFile(target, ws),
  isInsideWorkspace(target, ws),
  okumaKarariVer(target, ws).izinli,
]

describe('metadata errors other than ENOENT/ENOTDIR refuse', () => {
  it('refuses a dangling outside link when lstat on it fails with EIO', () => {
    const alias = path.join(ws, 'alias.md')
    if (!link(path.join(outside, 'escaped.md'), alias)) return
    failOn('lstatSync', alias)
    expect(everyGuard(alias)).toEqual([false, false, false, false])
  })

  it('refuses when lstat fails with EIO on a parent segment (a dangling dir link)', () => {
    const dir = path.join(ws, 'dir')
    if (!link(path.join(outside, 'newdir'), dir, 'dir')) return
    failOn('lstatSync', dir)
    expect(everyGuard(path.join(dir, 'a.md'))).toEqual([false, false, false, false])
  })

  it('refuses when stat fails with EACCES on an existing parent directory', () => {
    const sub = path.join(ws, 'sub')
    fs.mkdirSync(sub)
    const target = path.join(sub, 'new.md')
    expect(isAllowedWorkspacePath(target, ws)).toBe(true)
    failOn('statSync', sub, 'EACCES')
    expect(isAllowedWorkspacePath(target, ws)).toBe(false)
    expect(isAllowedWorkspaceWriteFile(target, ws)).toBe(false)
  })

  it('refuses when stat fails with EIO on the workspace itself', () => {
    failOn('statSync', ws)
    expect(isAllowedWorkspacePath(path.join(ws, 'new.md'), ws)).toBe(false)
  })

  it('still treats ENOTDIR under a regular file as missing', () => {
    fs.writeFileSync(path.join(ws, 'plain'), 'x')
    expect(isAllowedWorkspacePath(path.join(ws, 'plain', 'child.md'), ws)).toBe(true)
  })
})
