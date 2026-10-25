import fs from 'fs'
import os from 'os'
import path from 'path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { readBinaryFileFromWorkspace } from '../main/helpers/file-security'

/**
 * Silent operational failure on the model read channel, from an external audit
 * probe.
 *
 * Every failure collapsed into `{ error: 'denied' }` with nothing written
 * anywhere, so a disk or permission failure looked exactly like a policy
 * refusal and left the main-process log empty. The renderer-visible value stays
 * generic on purpose; only the log gains the cause.
 */

let dir = ''

afterEach(() => {
  vi.restoreAllMocks()
  if (dir) { try { fs.rmSync(dir, { recursive: true, force: true }) } catch { /* best effort */ } }
  dir = ''
})

function workspaceWithModel(): string {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), 'model-diag-'))
  fs.writeFileSync(path.join(dir, 'allowed.glb'), Buffer.from([1, 2, 3]))
  return path.join(dir, 'allowed.glb')
}

describe('missing-failure-diagnostics', () => {
  it('logs the cause when an allowed model cannot be opened', () => {
    const model = workspaceWithModel()
    const logged: string[] = []
    vi.spyOn(console, 'error').mockImplementation((...args: unknown[]) => { logged.push(args.join(' ')) })
    vi.spyOn(fs, 'openSync').mockImplementationOnce(() => {
      const error = new Error('permission denied') as NodeJS.ErrnoException
      error.code = 'EACCES'
      throw error
    })

    const result = readBinaryFileFromWorkspace(model, dir, 'model')

    // Generic to the renderer...
    expect(result).toEqual({ error: 'denied' })
    // ...specific in the main-process log.
    expect(logged.length).toBeGreaterThan(0)
    expect(logged.join('\n')).toContain('EACCES')
    expect(logged.join('\n')).toContain(model)
  })

  it('logs the cause when the read comes up short', () => {
    const model = workspaceWithModel()
    const logged: string[] = []
    vi.spyOn(console, 'error').mockImplementation((...args: unknown[]) => { logged.push(args.join(' ')) })
    // The file shrinks to nothing after its size was checked, so the bounded
    // read cannot reach the size it was told to expect.
    vi.spyOn(fs, 'readSync').mockImplementation(() => 0)

    const result = readBinaryFileFromWorkspace(model, dir, 'model')

    expect(result).toEqual({ error: 'denied' })
    expect(logged.length).toBeGreaterThan(0)
  })

  // A policy refusal is a decision, not a failure: logging it would bury the
  // real failures under traffic the gate produces by design.
  it('stays silent for a policy refusal', () => {
    dir = fs.mkdtempSync(path.join(os.tmpdir(), 'model-diag-'))
    const secret = path.join(dir, '.env')
    fs.writeFileSync(secret, 'SECRET=1')
    const logged: string[] = []
    vi.spyOn(console, 'error').mockImplementation((...args: unknown[]) => { logged.push(args.join(' ')) })

    expect(readBinaryFileFromWorkspace(secret, dir, 'model')).toEqual({ error: 'unsupported' })
    expect(logged).toEqual([])
  })
})
