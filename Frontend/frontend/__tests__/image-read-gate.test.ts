/**
 * The image channel's gate, measured the way the model channel's is.
 *
 * The two channels share ONE gate chain and one decision function; only the
 * whitelist and the cap differ. That sharing is exactly why this file exists
 * separately: a change made for one channel now silently reaches the other, and
 * only a per-channel assertion catches a whitelist that started answering for
 * both.
 */
import fs from 'fs'
import os from 'os'
import path from 'path'
import { afterEach, beforeEach, describe, it, expect } from 'vitest'
import {
  IMAGE_FILE_EXTENSIONS,
  IMAGE_MAX_BYTES,
  MODEL_MAX_BYTES,
  okumaKarariVer,
  readBinaryFileFromWorkspace,
} from '../main/helpers/file-security'

let ws = ''
let outside = ''

beforeEach(() => {
  ws = fs.mkdtempSync(path.join(os.tmpdir(), 'image-gate-ws-'))
  outside = fs.mkdtempSync(path.join(os.tmpdir(), 'image-gate-out-'))
})

afterEach(() => {
  for (const dir of [ws, outside]) {
    if (dir) { try { fs.rmSync(dir, { recursive: true, force: true }) } catch { /* best effort */ } }
  }
  ws = ''
  outside = ''
})

function write(dir: string, name: string, content: Buffer | string): string {
  const p = path.join(dir, name)
  fs.writeFileSync(p, content)
  return p
}

describe('image read gate — accepts', () => {
  for (const ext of IMAGE_FILE_EXTENSIONS) {
    it(`reads a ${ext} file inside the workspace whole`, () => {
      const bytes = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])
      const p = write(ws, `skin${ext}`, bytes)

      const result = readBinaryFileFromWorkspace(p, ws, 'image')

      expect('error' in result).toBe(false)
      if ('error' in result) return
      expect(result.name).toBe(`skin${ext}`)
      expect(result.path).toBe(p)
      // Not `toBeInstanceOf(ArrayBuffer)`: under jsdom the global ArrayBuffer is
      // a different class from Node's. The type tag is realm-independent.
      expect(Object.prototype.toString.call(result.data)).toBe('[object ArrayBuffer]')
      expect(Buffer.from(new Uint8Array(result.data)).equals(bytes)).toBe(true)
    })
  }

  it('accepts an uppercase extension', () => {
    const p = write(ws, 'Skin.PNG', Buffer.from([1, 2, 3]))
    expect(readBinaryFileFromWorkspace(p, ws, 'image')).not.toHaveProperty('error')
  })

  it('does not refuse a file exactly on the cap', () => {
    const p = write(ws, 'edge.png', Buffer.alloc(0))
    const fd = fs.openSync(p, 'r+')
    try { fs.ftruncateSync(fd, IMAGE_MAX_BYTES) } finally { fs.closeSync(fd) }

    const result = readBinaryFileFromWorkspace(p, ws, 'image')
    expect(result).not.toHaveProperty('error')
    if ('error' in result) return
    expect(result.data.byteLength).toBe(IMAGE_MAX_BYTES)
  })
})

describe('image read gate — size cap', () => {
  it(`refuses a file over ${IMAGE_MAX_BYTES} bytes with too-large`, () => {
    const p = write(ws, 'huge.png', Buffer.alloc(0))
    const fd = fs.openSync(p, 'r+')
    try { fs.ftruncateSync(fd, IMAGE_MAX_BYTES + 1) } finally { fs.closeSync(fd) }
    expect(fs.statSync(p).size).toBe(IMAGE_MAX_BYTES + 1)

    expect(readBinaryFileFromWorkspace(p, ws, 'image')).toEqual({ error: 'too-large' })
  })

  it('the image cap is its own number, half the model cap', () => {
    expect(IMAGE_MAX_BYTES).toBe(32 * 1024 * 1024)
    expect(MODEL_MAX_BYTES).toBe(64 * 1024 * 1024)
  })

  // The renderer's blocked list is a rendering fact, not a security one, so the
  // main process must not be relying on it: a file the browser cannot decode is
  // refused here only because it never made the whitelist.
  it('refuses a .tga through the image channel — the whitelist alone decides', () => {
    const p = write(ws, 'skin.tga', Buffer.from([0, 0, 2]))
    expect(readBinaryFileFromWorkspace(p, ws, 'image')).toEqual({ error: 'unsupported' })
  })
})

describe('image read gate — the channel keeps its own whitelist', () => {
  it('refuses a model file through the image channel', () => {
    const p = write(ws, 'hero.fbx', Buffer.from([1, 2, 3]))
    expect(readBinaryFileFromWorkspace(p, ws, 'image')).toEqual({ error: 'unsupported' })
  })

  it('refuses a text file through the image channel', () => {
    const p = write(ws, 'Player.cs', 'class A {}')
    expect(readBinaryFileFromWorkspace(p, ws, 'image')).toEqual({ error: 'unsupported' })
  })

  for (const name of ['.env', 'key.pem', 'secrets.json', 'app.exe']) {
    it(`refuses it even inside the workspace: ${name}`, () => {
      const p = write(ws, name, 'SECRET=1')
      expect(readBinaryFileFromWorkspace(p, ws, 'image')).toEqual({ error: 'unsupported' })
    })
  }

  it('the image kind does not widen the text or model gates', () => {
    const png = write(ws, 'skin.png', Buffer.from([1]))
    expect(okumaKarariVer(png, ws, 'model').izinli).toBe(false)
    expect(okumaKarariVer(png, ws, 'metin').izinli).toBe(false)
    expect(okumaKarariVer(png, ws, 'image').izinli).toBe(true)
  })
})

describe('image read gate — containment', () => {
  it('refuses an image outside the workspace', () => {
    const p = write(outside, 'outside.png', Buffer.from([1, 2]))
    expect(readBinaryFileFromWorkspace(p, ws, 'image')).toEqual({ error: 'denied' })
  })

  it('refuses traversal out with ..', () => {
    write(outside, 'outside.png', Buffer.from([1, 2]))
    const traversal = path.join(ws, '..', path.basename(outside), 'outside.png')
    expect(readBinaryFileFromWorkspace(traversal, ws, 'image')).toEqual({ error: 'denied' })
  })

  it('refuses an image reached through a junction out of the workspace', () => {
    const target = write(outside, 'secret.png', Buffer.from([7, 7, 7]))
    const link = path.join(ws, 'linked.png')
    try {
      fs.linkSync(target, link)
    } catch (err) {
      // NO SKIP, for the reason spelled out in model-read-gate.test.ts: this is
      // a security assertion, and a filesystem that cannot make a hardlink must
      // not silently stop testing the rule. A symlink trips the containment trap
      // where a hardlink trips the nlink trap — same rule, two traps. If neither
      // can be created the test THROWS rather than passing quietly.
      const code = (err as NodeJS.ErrnoException)?.code
      if (code !== 'EPERM' && code !== 'ENOTSUP' && code !== 'EXDEV') throw err
      fs.symlinkSync(target, link, 'file')
    }
    expect(readBinaryFileFromWorkspace(link, ws, 'image')).toEqual({ error: 'denied' })
  })

  it('refuses a directory named like an image', () => {
    const d = path.join(ws, 'fake.png')
    fs.mkdirSync(d)
    expect(readBinaryFileFromWorkspace(d, ws, 'image')).toHaveProperty('error')
  })

  it('refuses an empty workspace path', () => {
    const p = write(ws, 'a.png', Buffer.from([1]))
    expect(readBinaryFileFromWorkspace(p, '', 'image')).toHaveProperty('error')
  })
})
