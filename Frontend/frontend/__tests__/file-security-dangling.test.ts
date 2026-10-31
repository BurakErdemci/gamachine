import fs from 'fs'
import os from 'os'
import path from 'path'
import { afterEach, beforeEach, describe, it, expect } from 'vitest'
import {
  isAllowedWorkspacePath,
  isAllowedWorkspaceWriteFile,
  isWritableExistingTarget,
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
})
