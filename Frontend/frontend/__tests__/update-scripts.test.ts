import { generateKeyPairSync, verify } from 'node:crypto'
import { EventEmitter } from 'node:events'
import { existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync, symlinkSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const nodeRequire = createRequire(import.meta.url)
const keygen = nodeRequire('../scripts/update-keygen.js')
const { signRelease } = nodeRequire('../scripts/sign-release.js')

let dir: string
let repo: string
beforeEach(() => {
  dir = mkdtempSync(path.join(tmpdir(), 'gamachine-scripts-'))
  repo = path.join(dir, 'repo')
  mkdirSync(repo)
})
afterEach(() => rmSync(dir, { recursive: true, force: true }))

describe('update-keygen key path guard', () => {
  const guard = (target: string) => keygen.checkKeyPath(target, repo)

  it('refuses a path inside the repository, also through a symlink and a respelled path', () => {
    expect(() => guard(path.join(repo, 'k.pem'))).toThrow(/inside the repository/)
    expect(() => guard(path.join(repo, 'a', 'b', 'k.pem'))).toThrow(/inside the repository/)
    const link = path.join(dir, 'link')
    symlinkSync(repo, link)
    expect(() => guard(path.join(link, 'k.pem'))).toThrow(/inside the repository/)
  })

  it('a directory whose name merely starts with ".." is not the parent directory', () => {
    expect(() => guard(path.join(repo, '..keys', 'k.pem'))).toThrow(/inside the repository/)
    expect(guard(path.join(dir, '..keys', 'k.pem'))).toMatch(/\.\.keys/)
  })

  it('refuses synced folders', () => {
    for (const name of ['My Drive', 'Dropbox (Personal)', 'OneDrive - X', 'Box Sync', 'Box', 'MEGA', 'MEGAsync', 'Sync', 'CloudStorage', 'Google Drive', 'GoogleDrive-a@b.c', 'iCloudDrive', 'pCloud', 'Nextcloud']) {
      expect(() => guard(path.join(dir, name, 'k.pem')), name).toThrow(/synced folder/)
    }
  })

  it('refuses an existing file and accepts a fresh path outside the repo', () => {
    const existing = path.join(dir, 'k.pem')
    writeFileSync(existing, 'x')
    expect(() => guard(existing)).toThrow(/overwrite/)
    expect(guard(path.join(dir, 'fresh', 'k.pem'))).toMatch(/fresh/)
  })

  it('checkKeyLocation accepts an existing file outside the repo', () => {
    const existing = path.join(dir, 'k.pem')
    writeFileSync(existing, 'x')
    expect(keygen.checkKeyLocation(existing, repo)).toMatch(/k\.pem$/)
  })
})

describe('update-keygen hidden prompt', () => {
  function typed(...chunks: string[]) {
    const input: any = new EventEmitter()
    Object.assign(input, { isTTY: true, setRawMode: vi.fn(), resume: vi.fn(), pause: vi.fn(), setEncoding: vi.fn() })
    const written: string[] = []
    const result = keygen.promptHidden('Passphrase: ', input, { write: (t: string) => written.push(t) })
    for (const c of chunks) input.emit('data', c)
    return { result, written }
  }

  it('returns what was typed and echoes nothing but the question and a newline', async () => {
    const { result, written } = typed('abc\r')
    await expect(result).resolves.toBe('abc')
    expect(written).toEqual(['Passphrase: ', '\n'])
  })

  it('drops arrow keys, Delete, function keys and paste markers', async () => {
    await expect(typed('ab\u001b[A\u001b[3~c\u001bOP\u001b[200~de\u001b[201~\r').result).resolves.toBe('abcde')
  })

  it('drops an escape sequence split across reads', async () => {
    await expect(typed('ab\u001b', '[Ac\r').result).resolves.toBe('abc')
    await expect(typed('ab\u001b[', '3', '~c\r').result).resolves.toBe('abc')
  })

  it('ignores control characters such as Ctrl-D', async () => {
    await expect(typed('ab\u0004c\u0001\r').result).resolves.toBe('abc')
  })

  it('backspace removes a whole code point', async () => {
    await expect(typed('a\u{1F600}\u007fb\r').result).resolves.toBe('ab')
    await expect(typed('ax\bb\r').result).resolves.toBe('ab')
  })

  it('Ctrl-C cancels and a non-terminal is refused', async () => {
    await expect(typed('ab\u0003').result).rejects.toThrow('cancelled')
    await expect(keygen.promptHidden('q', { isTTY: false }, { write() {} })).rejects.toThrow(/terminal/)
  })
})

describe('update-keygen main', () => {
  const io = (answers: string[]) => {
    const out: string[] = []
    return { out, io: { promptHidden: vi.fn(async () => answers.shift() ?? ''), stdout: { write: (t: string) => out.push(t) }, stderr: { write() {} } } }
  }

  it('writes an encrypted private key (owner-only) and prints only the public key', async () => {
    const { io: fake, out } = io(['correct horse battery', 'correct horse battery'])
    const target = path.join(dir, 'keys', 'primary.pem')
    mkdirSync(path.dirname(target))
    const written = await keygen.main([target], fake)
    expect(readFileSync(written, 'utf8')).toContain('BEGIN ENCRYPTED PRIVATE KEY')
    expect(out.join('')).toContain('BEGIN PUBLIC KEY')
    expect(out.join('')).not.toContain('PRIVATE')
    if (process.platform !== 'win32') expect(statSync(written).mode & 0o077).toBe(0)
  })

  it('refuses a short passphrase and a mismatch without writing anything', async () => {
    const target = path.join(dir, 'k.pem')
    await expect(keygen.main([target], io(['short', 'short']).io)).rejects.toThrow(/at least/)
    await expect(keygen.main([target], io(['correct horse battery', 'other horse battery']).io)).rejects.toThrow(/do not match/)
    expect(existsSync(target)).toBe(false)
  })
})

describe('sign-release', () => {
  const PASS = 'correct horse battery'
  const manifest = (version: string) => `version: ${version}\nfiles:\n  - url: a.exe\n    sha512: AAAABBBBCCCCDDDD==\npath: a.exe\nsha512: AAAABBBBCCCCDDDD==\n`

  function rig(opts: { tag?: string; draft?: boolean; version?: string; publicKeys?: string[] } = {}) {
    const { publicKey, privateKey } = generateKeyPairSync('ed25519', {
      publicKeyEncoding: { type: 'spki', format: 'pem' },
      privateKeyEncoding: { type: 'pkcs8', format: 'pem', cipher: 'aes-256-cbc', passphrase: PASS },
    })
    const own = mkdtempSync(path.join(dir, 'rig-'))
    const keyPath = path.join(own, 'key.pem')
    writeFileSync(keyPath, privateKey)
    const calls: string[][] = []
    const uploads: Record<string, string> = {}
    const scratch = path.join(own, 'scratch')
    mkdirSync(scratch)
    const gh = (args: string[]) => {
      calls.push(args)
      if (args[1] === 'list') return JSON.stringify([{ tagName: opts.tag ?? 'v1.2.3', isDraft: opts.draft ?? true }])
      if (args[1] === 'download') {
        const into = args[args.indexOf('--dir') + 1]
        for (const n of ['latest.yml', 'latest-mac.yml']) writeFileSync(path.join(into, n), manifest(opts.version ?? '1.2.3'))
      }
      if (args[1] === 'upload') for (const f of args.filter((a) => a.endsWith('.sig'))) uploads[path.basename(f)] = readFileSync(f, 'utf8')
      return ''
    }
    const deps = { gh, promptHidden: vi.fn(async () => PASS), publicKeys: opts.publicKeys ?? [publicKey], log: vi.fn(), tmpDir: scratch }
    return { keyPath, calls, uploads, deps, publicKey, scratch }
  }
  const run = (r: ReturnType<typeof rig>, keyPath = r.keyPath, tag = 'v1.2.3') => signRelease(tag, keyPath, r.deps)

  it('signs both manifests of a draft, verifies against the embedded keys and uploads the signatures', async () => {
    const r = rig()
    await expect(run(r)).resolves.toBe(true)
    expect(Object.keys(r.uploads).sort()).toEqual(['latest-mac.yml.sig', 'latest.yml.sig'])
    for (const [name, sig] of Object.entries(r.uploads)) {
      expect(verify(null, Buffer.from(manifest('1.2.3')), r.publicKey, Buffer.from(sig, 'base64')), name).toBe(true)
    }
    expect(r.calls.at(-1)).toContain('--clobber')
    expect(readdirSync(r.scratch)).toEqual([])
  })

  it('refuses an unencrypted private key before asking for a passphrase', async () => {
    const r = rig()
    const plain = path.join(dir, 'plain.pem')
    writeFileSync(plain, generateKeyPairSync('ed25519').privateKey.export({ type: 'pkcs8', format: 'pem' }))
    await expect(run(r, plain)).rejects.toThrow(/encrypted/)
    expect(r.deps.promptHidden).not.toHaveBeenCalled()
    expect(r.calls.some((c) => c[1] === 'upload')).toBe(false)
  })

  it('refuses a key path inside the repository or a synced folder', async () => {
    const r = rig()
    await expect(run(r, path.join(__dirname, 'key.pem'))).rejects.toThrow(/inside the repository/)
    const synced = path.join(dir, 'Dropbox')
    mkdirSync(synced)
    writeFileSync(path.join(synced, 'key.pem'), readFileSync(r.keyPath))
    await expect(run(r, path.join(synced, 'key.pem'))).rejects.toThrow(/synced folder/)
    expect(r.deps.promptHidden).not.toHaveBeenCalled()
  })

  it('refuses a published release, a manifest of another version and a key that is not embedded', async () => {
    const published = rig({ draft: false })
    await expect(run(published)).rejects.toThrow(/already published/)
    const wrongVersion = rig({ version: '1.2.4' })
    await expect(run(wrongVersion)).rejects.toThrow(/does not match tag/)
    const stranger = rig({ publicKeys: [generateKeyPairSync('ed25519').publicKey.export({ type: 'spki', format: 'pem' }) as string] })
    await expect(run(stranger)).rejects.toThrow(/not one of the public keys/)
    const noKeys = rig({ publicKeys: [] })
    await expect(run(noKeys)).rejects.toThrow(/no public key/)
    for (const r of [published, wrongVersion, stranger, noKeys]) expect(r.calls.some((c) => c[1] === 'upload')).toBe(false)
  })
})
