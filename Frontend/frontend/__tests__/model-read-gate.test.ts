import fs from 'fs'
import os from 'os'
import path from 'path'
import { afterEach, beforeEach, describe, it, expect } from 'vitest'
import {
  MODEL_FILE_EXTENSIONS,
  MODEL_MAX_BYTES,
  TEXT_MAX_BYTES,
  okumaKarariVer,
  readBinaryFileFromWorkspace,
} from '../main/helpers/file-security'

let ws = ''
let outside = ''

beforeEach(() => {
  ws = fs.mkdtempSync(path.join(os.tmpdir(), 'model-gate-ws-'))
  outside = fs.mkdtempSync(path.join(os.tmpdir(), 'model-gate-out-'))
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

describe('model read gate — accepts', () => {
  for (const ext of MODEL_FILE_EXTENSIONS) {
    it(`reads a ${ext} file inside the workspace`, () => {
      const bytes = Buffer.from([0x67, 0x6c, 0x54, 0x46, 0x02, 0x00, 0x00, 0x00])
      const p = write(ws, `mesh${ext}`, bytes)

      const result = readBinaryFileFromWorkspace(p, ws, 'model')

      expect('error' in result).toBe(false)
      if ('error' in result) return
      expect(result.name).toBe(`mesh${ext}`)
      expect(result.path).toBe(p)
      // `toBeInstanceOf(ArrayBuffer)` is avoided: under jsdom the global
      // ArrayBuffer is a different class from Node's, so a real ArrayBuffer
      // fails that check. The type tag is realm-independent.
      expect(Object.prototype.toString.call(result.data)).toBe('[object ArrayBuffer]')
      expect(result.data.byteLength).toBe(bytes.length)
      expect(Buffer.from(new Uint8Array(result.data)).equals(bytes)).toBe(true)
    })
  }

  it('accepts an uppercase extension', () => {
    const p = write(ws, 'Mesh.GLB', Buffer.from([1, 2, 3]))
    expect(readBinaryFileFromWorkspace(p, ws, 'model')).not.toHaveProperty('error')
  })

  it('reads a model in a subdirectory', () => {
    fs.mkdirSync(path.join(ws, 'Assets', 'Models'), { recursive: true })
    const p = write(path.join(ws, 'Assets', 'Models'), 'char.fbx', Buffer.from([9, 9]))
    expect(readBinaryFileFromWorkspace(p, ws, 'model')).not.toHaveProperty('error')
  })

  // Zero bytes passes all three of the gate's questions (containment, extension,
  // size), so the channel DOES read it. The behaviour is pinned here because
  // "empty file" is a parser problem: the panel already promises to show a parse
  // error, and adding an emptiness refusal to the gate has to be a deliberate
  // change rather than a silent drift.
  it('does not refuse a zero-byte file — emptiness is the parser\'s business, not the gate\'s', () => {
    const p = write(ws, 'empty.glb', Buffer.alloc(0))

    const result = readBinaryFileFromWorkspace(p, ws, 'model')

    expect('error' in result).toBe(false)
    if ('error' in result) return
    expect(result.data.byteLength).toBe(0)
  })

  it('does not refuse a file exactly on the cap', () => {
    const p = write(ws, 'edge.stl', Buffer.alloc(0))
    const fd = fs.openSync(p, 'r+')
    try { fs.ftruncateSync(fd, MODEL_MAX_BYTES) } finally { fs.closeSync(fd) }

    expect(readBinaryFileFromWorkspace(p, ws, 'model')).not.toHaveProperty('error')
  })
})

describe('model read gate — extension whitelist', () => {
  // A model channel that can hand back a secret file even from INSIDE the
  // workspace is a leak surface; the channel names what it accepts rather than
  // what it refuses.
  for (const name of ['.env', 'key.pem', 'secrets.json', 'Player.cs', 'notes.md', 'app.exe']) {
    it(`refuses it even inside the workspace: ${name}`, () => {
      const p = write(ws, name, 'SECRET=1')
      expect(readBinaryFileFromWorkspace(p, ws, 'model')).toEqual({ error: 'unsupported' })
    })
  }

  it('refuses a file with no extension', () => {
    const p = write(ws, 'model', Buffer.from([1]))
    expect(readBinaryFileFromWorkspace(p, ws, 'model')).toEqual({ error: 'unsupported' })
  })
})

describe('model read gate — containment', () => {
  it('refuses a model outside the workspace', () => {
    const p = write(outside, 'outside.glb', Buffer.from([1, 2]))
    expect(readBinaryFileFromWorkspace(p, ws, 'model')).toEqual({ error: 'denied' })
  })

  it('refuses traversal out with ..', () => {
    write(outside, 'outside.obj', Buffer.from([1, 2]))
    const traversal = path.join(ws, '..', path.basename(outside), 'outside.obj')
    expect(readBinaryFileFromWorkspace(traversal, ws, 'model')).toEqual({ error: 'denied' })
  })

  it('refuses a directory', () => {
    const d = path.join(ws, 'fake.glb')
    fs.mkdirSync(d)
    expect(readBinaryFileFromWorkspace(d, ws, 'model')).toHaveProperty('error')
  })

  it('refuses a hardlink pointing outside the workspace', () => {
    const target = write(outside, 'secret.glb', Buffer.from([7, 7, 7]))
    const link = path.join(ws, 'linked.glb')
    try {
      fs.linkSync(target, link)
    } catch (err) {
      // NO SKIP (audit finding `silently-skipped-security-test`). This is a
      // security assertion: a link whose real bytes live outside the workspace
      // must be refused. The earlier shape wrapped this whole `it()` in
      // `it.skipIf(!hardlinksSupported)`, so on any filesystem that cannot make
      // a hardlink (a container volume, FAT/exFAT, some cross-device temp
      // setups) the rule silently stopped being tested and the suite still
      // reported green.
      //
      // A hardlink and a symlink exercise the same rule here — the gate's
      // `nlink > 1` check and its containment check are two independent traps
      // for the same "real bytes are outside" fact, and a symlink to the target
      // trips the containment trap instead. So on ENOTSUP/EPERM/EXDEV we fall
      // back to a symlink rather than skip the assertion. If that also fails,
      // the test THROWS: an inability to test this rule at all must be loud,
      // never a silent pass.
      const code = (err as NodeJS.ErrnoException)?.code
      if (code !== 'EPERM' && code !== 'ENOTSUP' && code !== 'EXDEV') throw err
      fs.symlinkSync(target, link, 'file')
    }
    expect(readBinaryFileFromWorkspace(link, ws, 'model')).toEqual({ error: 'denied' })
  })

  it('refuses an empty workspace path', () => {
    const p = write(ws, 'a.glb', Buffer.from([1]))
    expect(readBinaryFileFromWorkspace(p, '', 'model')).toHaveProperty('error')
  })
})

describe('model read gate — size cap', () => {
  it(`refuses a file over ${MODEL_MAX_BYTES} bytes with too-large`, () => {
    const p = write(ws, 'huge.fbx', Buffer.alloc(0))
    const fd = fs.openSync(p, 'r+')
    try {
      fs.ftruncateSync(fd, MODEL_MAX_BYTES + 1)
    } finally {
      fs.closeSync(fd)
    }
    expect(fs.statSync(p).size).toBe(MODEL_MAX_BYTES + 1)

    expect(readBinaryFileFromWorkspace(p, ws, 'model')).toEqual({ error: 'too-large' })
  })
})

describe('text path regression', () => {
  it('the text gate does not accept a model extension', () => {
    const p = write(ws, 'mesh.glb', Buffer.from([1]))
    expect(okumaKarariVer(p, ws).izinli).toBe(false)
  })

  it('the model gate does not accept a text extension', () => {
    const p = write(ws, 'Player.cs', 'class A {}')
    expect(okumaKarariVer(p, ws, 'model').izinli).toBe(false)
  })

  it('the default kind is text — .cs still passes', () => {
    const p = write(ws, 'Player.cs', 'class A {}')
    expect(okumaKarariVer(p, ws).izinli).toBe(true)
  })

  // Both caps are named constants now, so both can be pinned from here; the
  // text cap used to be an inline literal in `background.ts` and could not be.
  it('the model cap stays at 64 MiB and the text cap at 8 MiB', () => {
    expect(MODEL_MAX_BYTES).toBe(64 * 1024 * 1024)
    expect(TEXT_MAX_BYTES).toBe(8 * 1024 * 1024)
  })
})
