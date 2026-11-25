import { generateKeyPairSync, verify } from 'node:crypto'
import { EventEmitter } from 'node:events'
import { existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync, symlinkSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const nodeRequire = createRequire(import.meta.url)
const keygen = nodeRequire('../scripts/update-keygen.js')
const { signRelease, parseArgs, embeddedPublicKeys } = nodeRequire('../scripts/sign-release.js')

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

  it('refuses a final-component symlink to a file in the repository or a synced folder', () => {
    const inside = path.join(repo, 'k.pem')
    writeFileSync(inside, 'x')
    const alias = path.join(dir, 'alias.pem')
    symlinkSync(inside, alias)
    expect(() => keygen.checkKeyLocation(alias, repo)).toThrow(/inside the repository/)
    mkdirSync(path.join(dir, 'Dropbox'))
    writeFileSync(path.join(dir, 'Dropbox', 'k.pem'), 'x')
    const alias2 = path.join(dir, 'alias2.pem')
    symlinkSync(path.join(dir, 'Dropbox', 'k.pem'), alias2)
    expect(() => keygen.checkKeyLocation(alias2, repo)).toThrow(/synced folder/)
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
  const pairFile = (folder: string, name: string) => {
    const { publicKey, privateKey } = generateKeyPairSync('ed25519', {
      publicKeyEncoding: { type: 'spki', format: 'pem' },
      privateKeyEncoding: { type: 'pkcs8', format: 'pem', cipher: 'aes-256-cbc', passphrase: PASS },
    })
    const file = path.join(folder, name)
    writeFileSync(file, privateKey)
    return { file, publicKey }
  }

  function rig(opts: { tag?: string; draft?: boolean; version?: string; publicKeys?: string[]; macPublicKeys?: string[]; assets?: string[] } = {}) {
    const own = mkdtempSync(path.join(dir, 'rig-'))
    const win = pairFile(own, 'key.pem')
    const mac = pairFile(own, 'mac-key.pem')
    const calls: string[][] = []
    const uploads: Record<string, string> = {}
    const scratch = path.join(own, 'scratch')
    mkdirSync(scratch)
    const gh = (args: string[]) => {
      calls.push(args)
      if (args[1] === 'list') return JSON.stringify([{ tagName: opts.tag ?? 'v1.2.3', isDraft: opts.draft ?? true }])
      const assets = opts.assets ?? ['latest.yml', 'latest-mac.yml']
      if (args[1] === 'view') return JSON.stringify({ assets: assets.map((name) => ({ name })) })
      if (args[1] === 'download') {
        const into = args[args.indexOf('--dir') + 1]
        const patterns = args.filter((_a, i) => args[i - 1] === '--pattern')
        for (const n of assets.filter((a) => patterns.includes(a))) writeFileSync(path.join(into, n), manifest(opts.version ?? '1.2.3'))
      }
      if (args[1] === 'upload') for (const f of args.filter((a) => a.endsWith('.sig'))) uploads[path.basename(f)] = readFileSync(f, 'utf8')
      return ''
    }
    const deps = {
      gh, promptHidden: vi.fn(async (_q: string) => PASS), log: vi.fn(), tmpDir: scratch,
      publicKeys: opts.publicKeys ?? [win.publicKey], macPublicKeys: opts.macPublicKeys ?? [mac.publicKey],
    }
    return { keyPath: win.file, macKeyPath: mac.file, calls, uploads, deps, publicKey: win.publicKey, macPublicKey: mac.publicKey, scratch }
  }
  const run = (r: ReturnType<typeof rig>, keyPath = r.keyPath, tag = 'v1.2.3', macKeyPath: string | undefined = r.macKeyPath) =>
    signRelease(tag, keyPath, macKeyPath, r.deps)
  const noGhCalls = (r: ReturnType<typeof rig>) => expect(r.calls).toEqual([])

  it('signs each manifest with its own key, verifies against its own key set and uploads the signatures', async () => {
    const r = rig()
    await expect(run(r)).resolves.toBe(true)
    expect(Object.keys(r.uploads).sort()).toEqual(['latest-mac.yml.sig', 'latest.yml.sig'])
    const bytes = Buffer.from(manifest('1.2.3'))
    const sigOf = (name: string) => Buffer.from(r.uploads[name]!, 'base64')
    expect(verify(null, bytes, r.publicKey, sigOf('latest.yml.sig'))).toBe(true)
    expect(verify(null, bytes, r.macPublicKey, sigOf('latest-mac.yml.sig'))).toBe(true)
    // The Windows key must not verify the Mac manifest (4.0.0-4.0.3 Macs refuse it that way) and vice versa.
    expect(verify(null, bytes, r.publicKey, sigOf('latest-mac.yml.sig'))).toBe(false)
    expect(verify(null, bytes, r.macPublicKey, sigOf('latest.yml.sig'))).toBe(false)
    expect(r.calls.at(-1)).toContain('--clobber')
    expect(readdirSync(r.scratch)).toEqual([])
  })

  it('asks the passphrase once per key file', async () => {
    const r = rig()
    await run(r)
    expect(r.deps.promptHidden).toHaveBeenCalledTimes(2)
    expect(r.deps.promptHidden.mock.calls[0]![0]).not.toBe(r.deps.promptHidden.mock.calls[1]![0])
  })

  it('a draft missing latest-mac.yml is refused and nothing is uploaded', async () => {
    const r = rig({ assets: ['latest.yml'] })
    await expect(run(r)).rejects.toThrow(/latest-mac\.yml is missing/)
    expect(r.calls.some((c) => c[1] === 'upload')).toBe(false)
    const noWin = rig({ assets: ['latest-mac.yml'] })
    await expect(run(noWin)).rejects.toThrow(/latest\.yml is missing/)
    expect(noWin.calls.some((c) => c[1] === 'upload')).toBe(false)
  })

  it('a manifest signed with the wrong key set is refused: the Windows key for the Mac manifest and the reverse', async () => {
    const swapped = rig()
    await expect(signRelease('v1.2.3', swapped.macKeyPath, swapped.keyPath, swapped.deps)).rejects.toThrow(/not one of the .*keys/)
    expect(swapped.calls.some((c) => c[1] === 'upload')).toBe(false)
    const empty = rig({ macPublicKeys: [] })
    await expect(run(empty)).rejects.toThrow(/latest-mac\.yml:.*no public key yet/)
    expect(empty.calls.some((c) => c[1] === 'upload')).toBe(false)
  })

  it('refuses a Mac key that old Macs would also accept, and the same file for both keys', async () => {
    const r = rig()
    const both = { ...r.deps, macPublicKeys: [r.macPublicKey], publicKeys: [r.publicKey, r.macPublicKey] }
    await expect(signRelease('v1.2.3', r.keyPath, r.macKeyPath, both)).rejects.toThrow(/also one of the Windows keys/)
    expect(r.calls.some((c) => c[1] === 'upload')).toBe(false)
    const same = rig()
    await expect(run(same, same.keyPath, 'v1.2.3', same.keyPath)).rejects.toThrow(/different key file/)
    noGhCalls(same)
  })

  it('without --mac-key nothing is asked and no gh call is made', async () => {
    const r = rig()
    await expect(signRelease('v1.2.3', r.keyPath, undefined, r.deps)).rejects.toThrow(/--mac-key/)
    await expect(run(r, r.keyPath, 'v1.2.3', '')).rejects.toThrow(/usage/)
    noGhCalls(r)
    expect(r.deps.promptHidden).not.toHaveBeenCalled()
  })

  it('parseArgs: --mac-key is required and takes a path; any other option is refused in any position', () => {
    expect(parseArgs(['v4.0.4', '/k.pem', '--mac-key', '/m.pem'])).toEqual({ tag: 'v4.0.4', keyPath: '/k.pem', macKeyPath: '/m.pem' })
    expect(parseArgs(['--mac-key', '/m.pem', 'v4.0.4', '/k.pem'])).toEqual({ tag: 'v4.0.4', keyPath: '/k.pem', macKeyPath: '/m.pem' })
    expect(parseArgs(['v4.0.4', '/k.pem', '--mac-key=/m.pem'])).toMatchObject({ macKeyPath: '/m.pem' })
    expect(() => parseArgs(['v4.0.4', '/k.pem'])).toThrow(/usage/)
    expect(() => parseArgs(['v4.0.4', '/k.pem', '--mac-key'])).toThrow(/needs a key file path/)
    expect(() => parseArgs(['v4.0.4', '/k.pem', '--mac-key', '--other'])).toThrow(/needs a key file path/)
    expect(() => parseArgs(['v4.0.4', '/k.pem', '--mac-key', '/a', '--mac-key', '/b'])).toThrow(/twice/)
    expect(() => parseArgs(['v4.0.4', '/k.pem', '--mac-key', '/m.pem', 'extra'])).toThrow(/unexpected argument/)
    for (const bad of ['--skip-mac-manifest', '--skip-mac', '-q', '-', '--']) {
      expect(() => parseArgs([bad, 'v4.0.4', '/k.pem', '--mac-key', '/m.pem']), bad).toThrow(/unknown option/)
      expect(() => parseArgs(['v4.0.4', '/k.pem', '--mac-key', '/m.pem', bad]), bad).toThrow(/unknown option/)
      expect(() => parseArgs(['v4.0.4', bad, '/k.pem', '--mac-key', '/m.pem']), bad).toThrow(/unknown option/)
    }
  })

  it('reads the Mac and Windows public keys from update-keys.ts separately, ignoring empty templates', () => {
    const file = path.join(dir, 'keys.ts')
    const pem = (body: string) => `-----BEGIN PUBLIC KEY-----\n${body}\n-----END PUBLIC KEY-----`
    writeFileSync(file, [
      '// `-----BEGIN PUBLIC KEY-----\nXXXX\n-----END PUBLIC KEY-----` in a comment is not a key',
      `export const PRIMARY_UPDATE_PUBLIC_KEY = \`\n${pem('AAAA')}\n\``,
      `export const BACKUP_UPDATE_PUBLIC_KEY = \`\n${pem('BBBB')}\n\``,
      `export const MAC_PRIMARY_UPDATE_PUBLIC_KEY = \`\n${pem('CCCC')}\n\``,
      'export const MAC_BACKUP_UPDATE_PUBLIC_KEY = `\n-----BEGIN PUBLIC KEY-----\n-----END PUBLIC KEY-----\n`',
    ].join('\n'))
    expect(embeddedPublicKeys(file, false)).toEqual([pem('AAAA'), pem('BBBB')])
    expect(embeddedPublicKeys(file, true)).toEqual([pem('CCCC')])
    // The checked-in file: the real Windows keys, and no Mac key may equal one of them.
    const win = embeddedPublicKeys()
    expect(win).toHaveLength(2)
    expect(embeddedPublicKeys(undefined, true).filter((k: string) => win.includes(k))).toEqual([])
  })

  it('refuses an unencrypted private key before asking for a passphrase', async () => {
    const r = rig()
    const plain = path.join(dir, 'plain.pem')
    writeFileSync(plain, generateKeyPairSync('ed25519').privateKey.export({ type: 'pkcs8', format: 'pem' }))
    await expect(run(r, plain)).rejects.toThrow(/encrypted/)
    await expect(run(r, r.keyPath, 'v1.2.3', plain)).rejects.toThrow(/Mac private key must be an encrypted/)
    expect(r.deps.promptHidden).not.toHaveBeenCalled()
    expect(r.calls.some((c) => c[1] === 'upload')).toBe(false)
  })

  it('refuses a key path inside the repository or a synced folder, for either key', async () => {
    const r = rig()
    await expect(run(r, path.join(__dirname, 'key.pem'))).rejects.toThrow(/inside the repository/)
    await expect(run(r, r.keyPath, 'v1.2.3', path.join(__dirname, 'key.pem'))).rejects.toThrow(/inside the repository/)
    const synced = path.join(dir, 'Dropbox')
    mkdirSync(synced)
    writeFileSync(path.join(synced, 'key.pem'), readFileSync(r.keyPath))
    await expect(run(r, path.join(synced, 'key.pem'))).rejects.toThrow(/synced folder/)
    await expect(run(r, r.keyPath, 'v1.2.3', path.join(synced, 'key.pem'))).rejects.toThrow(/synced folder/)
    expect(r.deps.promptHidden).not.toHaveBeenCalled()
  })

  it('refuses a key file that is a symlink into the repository', async () => {
    const r = rig()
    // sign-release guards the real repository, so the link targets a file inside it (never written).
    const alias = path.join(dir, 'innocent.pem')
    symlinkSync(__filename, alias)
    await expect(run(r, alias)).rejects.toThrow(/inside the repository/)
    await expect(run(r, r.keyPath, 'v1.2.3', alias)).rejects.toThrow(/inside the repository/)
    expect(r.deps.promptHidden).not.toHaveBeenCalled()
  })

  it('refuses a published release, a manifest of another version and a key that is not embedded', async () => {
    const published = rig({ draft: false })
    await expect(run(published)).rejects.toThrow(/already published/)
    const wrongVersion = rig({ version: '1.2.4' })
    await expect(run(wrongVersion)).rejects.toThrow(/does not match tag/)
    const stranger = rig({ publicKeys: [generateKeyPairSync('ed25519').publicKey.export({ type: 'spki', format: 'pem' }) as string] })
    await expect(run(stranger)).rejects.toThrow(/latest\.yml: this key is not one of the/)
    const noKeys = rig({ publicKeys: [] })
    await expect(run(noKeys)).rejects.toThrow(/no public key/)
    for (const r of [published, wrongVersion, stranger, noKeys]) expect(r.calls.some((c) => c[1] === 'upload')).toBe(false)
  })
})
