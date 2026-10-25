import { execFileSync } from 'child_process'
import fs from 'fs'
import os from 'os'
import path from 'path'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { readBinaryFileFromWorkspace } from '../main/helpers/file-security'

/**
 * NTFS 8.3 alias regression, from an external audit probe.
 *
 * The gate decides on the resolved path and then opens that same string, so a
 * spelling the resolver leaves alone is a spelling the gate can misjudge: an
 * 8.3 alias truncates the extension to three characters, which turned `.glbx`
 * into `.GLB` — on the whitelist. Only extensions whose first three characters
 * already match a whitelisted one are reachable that way (`.glbx`, `.objects`,
 * `.plyfile`); `.env`, `.pem` and `.key` alias to themselves and were never
 * reachable through it, so this pins whitelist precision, not a hole that ever
 * handed back a secret file.
 *
 * The same non-canonical resolution refused legitimate reads too — a workspace
 * named through its own short path stopped matching itself — so both directions
 * are pinned below.
 */

/**
 * The 8.3 alias NTFS generated for `name` inside `dir`, or '' if there is none.
 *
 * `dir /x` puts the alias in the column immediately left of the long name, so
 * the row is read from its tail. The `~` requirement is what separates a real
 * alias from the file-size column that sits there when generation is off — an
 * earlier left-to-right regex matched the size and produced a test that passed
 * against a path which did not exist.
 */
function shortNameOf(dir: string, name: string): string {
  if (process.platform !== 'win32') return ''
  try {
    const listing = execFileSync('cmd', ['/c', 'dir', '/x', dir], { encoding: 'utf-8' })
    const row = listing.split(/\r?\n/).map((line) => line.trimEnd())
      .find((line) => line.endsWith(` ${name}`))
    if (!row) return ''
    const token = row.slice(0, row.length - name.length).trimEnd().split(/\s+/).pop() ?? ''
    return token.includes('~') && token.length <= 12 ? token : ''
  } catch {
    return ''
  }
}

/**
 * Does this volume generate 8.3 aliases at all?
 *
 * An administrator can turn generation off (`fsutil 8dot3name`), and off Windows
 * the whole spelling does not exist. Measured once, outside the tests, so the
 * absence is REPORTED as a skip: returning early from inside `it()` would report
 * a green test that asserted nothing — the same silently-disabled-check shape
 * this suite already guards against for hardlinks.
 */
const shortNamesSupported = ((): boolean => {
  const probe = fs.mkdtempSync(path.join(os.tmpdir(), 'shortname-probe-'))
  try {
    fs.writeFileSync(path.join(probe, 'alias-generation-check.glbx'), 'x')
    return shortNameOf(probe, 'alias-generation-check.glbx') !== ''
  } catch {
    return false
  } finally {
    try { fs.rmSync(probe, { recursive: true, force: true }) } catch { /* best effort */ }
  }
})()

let ws = ''

beforeEach(() => {
  ws = fs.mkdtempSync(path.join(os.tmpdir(), 'shortname-ws-'))
})

afterEach(() => {
  if (ws) { try { fs.rmSync(ws, { recursive: true, force: true }) } catch { /* best effort */ } }
  ws = ''
})

describe.skipIf(!shortNamesSupported)('extension-whitelist-bypass', () => {
  it('refuses a non-whitelisted extension named through its 8.3 alias', () => {
    const longName = 'harvested-credentials.glbx'
    fs.writeFileSync(path.join(ws, longName), 'BEGIN PRIVATE KEY not-a-3d-model')
    const alias = shortNameOf(ws, longName)
    expect(alias).not.toBe('')

    // The long name is refused; the alias names the same bytes and so must
    // reach the same verdict.
    expect(readBinaryFileFromWorkspace(path.join(ws, longName), ws, 'model'))
      .toEqual({ error: 'unsupported' })
    expect(readBinaryFileFromWorkspace(path.join(ws, alias), ws, 'model'))
      .toEqual({ error: 'unsupported' })
  })

  it('allows a whitelisted model named through its 8.3 alias', () => {
    const longName = 'character-turntable.glb'
    const bytes = Buffer.from([0x67, 0x6c, 0x54, 0x46])
    fs.writeFileSync(path.join(ws, longName), bytes)
    const alias = shortNameOf(ws, longName)
    expect(alias).not.toBe('')

    const result = readBinaryFileFromWorkspace(path.join(ws, alias), ws, 'model')

    expect('error' in result).toBe(false)
    if ('error' in result) return
    expect(result.data.byteLength).toBe(bytes.length)
  })

  // The reverse of the same mismatch: the file resolved to its long spelling
  // while the workspace kept its short one, so containment failed on a
  // workspace that literally contained the file.
  it('allows a workspace reached through its own short path', () => {
    const longDir = path.join(ws, 'Unity Project Assets')
    fs.mkdirSync(longDir)
    const model = path.join(longDir, 'mesh.glb')
    fs.writeFileSync(model, Buffer.from([1, 2, 3, 4]))
    const alias = shortNameOf(ws, 'Unity Project Assets')
    expect(alias).not.toBe('')

    const shortWs = path.join(ws, alias)
    expect(readBinaryFileFromWorkspace(model, shortWs, 'model')).not.toHaveProperty('error')
    expect(readBinaryFileFromWorkspace(path.join(shortWs, 'mesh.glb'), longDir, 'model'))
      .not.toHaveProperty('error')
  })
})
