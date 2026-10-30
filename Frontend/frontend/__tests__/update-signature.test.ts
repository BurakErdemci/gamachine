import { generateKeyPairSync, sign } from 'node:crypto'
import { describe, expect, it } from 'vitest'
import { checkAvailable, parseManifest, verifyManifest } from '../main/helpers/update-signature'

function keyPair() {
  const { publicKey, privateKey } = generateKeyPairSync('ed25519')
  return { publicKey, privateKey, pem: publicKey.export({ type: 'spki', format: 'pem' }).toString() }
}

const manifest = Buffer.from(`version: 4.1.0
files:
  - url: Gamachine-Setup-4.1.0.exe
    sha512: AAAA+/bb==
    size: 123
path: Gamachine-Setup-4.1.0.exe
sha512: AAAA+/bb==
releaseDate: '2026-10-30T10:00:00.000Z'
`)

describe('verifyManifest', () => {
  const primary = keyPair()
  const backup = keyPair()
  const sig = sign(null, manifest, primary.privateKey).toString('base64')

  it('accepts a valid signature', () => {
    expect(verifyManifest(manifest, sig, [primary.pem])).toBe(true)
  })

  it('rejects a tampered manifest', () => {
    const tampered = Buffer.from(manifest.toString().replace('4.1.0', '4.1.1'))
    expect(verifyManifest(tampered, sig, [primary.pem])).toBe(false)
  })

  it('rejects a signature from another key', () => {
    expect(verifyManifest(manifest, sig, [keyPair().pem])).toBe(false)
  })

  it('accepts a manifest signed by the backup key', () => {
    const backupSig = sign(null, manifest, backup.privateKey).toString('base64')
    expect(verifyManifest(manifest, backupSig, [primary.pem, backup.pem])).toBe(true)
  })

  it('refuses when no key is configured', () => {
    expect(verifyManifest(manifest, sig, [])).toBe(false)
  })

  it('refuses a malformed signature or key', () => {
    expect(verifyManifest(manifest, '', [primary.pem])).toBe(false)
    expect(verifyManifest(manifest, 'not base64 at all!', [primary.pem])).toBe(false)
    expect(verifyManifest(manifest, sig, ['not a pem'])).toBe(false)
    expect(verifyManifest(manifest, sig, ['not a pem', primary.pem])).toBe(true)
  })

  it('refuses a non-Ed25519 key', () => {
    const rsa = generateKeyPairSync('rsa', { modulusLength: 2048 }).publicKey.export({ type: 'spki', format: 'pem' }).toString()
    expect(verifyManifest(manifest, sig, [rsa])).toBe(false)
  })
})

describe('checkAvailable', () => {
  const text = manifest.toString()
  const exe = { name: 'Gamachine-Setup-4.1.0.exe', sha512: 'AAAA+/bb==' }

  it('accepts the signed version, file name and sha512', () => {
    expect(checkAvailable({ version: '4.1.0', files: [exe] }, text)).toEqual({ ok: true })
  })

  it('refuses a sha512 mismatch, a missing hash or an empty list', () => {
    expect(checkAvailable({ version: '4.1.0', files: [{ ...exe, sha512: 'BBBB' }] }, text).ok).toBe(false)
    expect(checkAvailable({ version: '4.1.0', files: [exe, { ...exe, sha512: undefined }] }, text).ok).toBe(false)
    expect(checkAvailable({ version: '4.1.0', files: [{ name: 'a.exe', sha512: '' }] }, 'version: 4.1.0\nsha512: \n').ok).toBe(false)
    expect(checkAvailable({ version: '4.1.0', files: [] }, text).ok).toBe(false)
  })

  it('refuses a file name that is not in the signed manifest even with a signed hash', () => {
    expect(checkAvailable({ version: '4.1.0', files: [{ ...exe, name: 'other.exe' }] }, text).ok).toBe(false)
    expect(checkAvailable({ version: '4.1.0', files: [{ ...exe, name: undefined }] }, text).ok).toBe(false)
  })

  it('binds the name to the hash of the same entry', () => {
    const mac = `version: 4.1.0\nfiles:\n  - url: a.zip\n    sha512: ZIP==\n  - url: a.dmg\n    sha512: DMG==\n`
    expect(checkAvailable({ version: '4.1.0', files: [{ name: 'a.zip', sha512: 'DMG==' }] }, mac).ok).toBe(false)
    expect(checkAvailable({ version: '4.1.0', files: [{ name: 'a.zip', sha512: 'ZIP==' }] }, mac).ok).toBe(true)
  })

  it('compares file names by their decoded base name', () => {
    const spaced = 'version: 4.1.0\nfiles:\n  - url: Game%20Setup.exe\n    sha512: H==\n'
    expect(checkAvailable({ version: '4.1.0', files: [{ name: 'Game Setup.exe', sha512: 'H==' }] }, spaced).ok).toBe(true)
    expect(checkAvailable({ version: '4.1.0', files: [{ name: 'https://x/y/Game%20Setup.exe', sha512: 'H==' }] }, spaced).ok).toBe(true)
  })

  it('refuses a version mismatch', () => {
    expect(checkAvailable({ version: '4.0.9', files: [exe] }, text).ok).toBe(false)
  })

  it('refuses a manifest without a version or a hash', () => {
    expect(checkAvailable({ version: '4.1.0', files: [exe] }, 'files: []').ok).toBe(false)
    expect(checkAvailable({ version: '4.1.0', files: [exe] }, 'version: 4.1.0\n').ok).toBe(false)
  })

  it('needs every file the updater may download in the manifest (mac)', () => {
    const mac = `version: 4.1.0\nfiles:\n  - url: a.zip\n    sha512: ZIP==\n    size: 1\n  - url: a.dmg\n    sha512: "DMG=="\n    size: 2\npath: a.zip\nsha512: ZIP==\n`
    expect(checkAvailable({ version: '4.1.0', files: [{ name: 'a.zip', sha512: 'ZIP==' }, { name: 'a.dmg', sha512: 'DMG==' }] }, mac).ok).toBe(true)
    expect(checkAvailable({ version: '4.1.0', files: [{ name: 'a.zip', sha512: 'ZIP==' }, { name: 'a.dmg', sha512: 'EVIL==' }] }, mac).ok).toBe(false)
    expect(parseManifest(mac).files).toEqual([
      { name: 'a.zip', sha512: 'ZIP==' }, { name: 'a.dmg', sha512: 'DMG==' }, { name: 'a.zip', sha512: 'ZIP==' }])
  })

  it('does not take hashes from sections other than files', () => {
    const web = 'version: 4.1.0\nfiles:\n  - url: a.exe\n    sha512: A==\npackages:\n  x64:\n    path: p.7z\n    sha512: PKG==\n'
    expect(parseManifest(web).files).toEqual([{ name: 'a.exe', sha512: 'A==' }])
  })

  it('refuses an ambiguous version line', () => {
    expect(checkAvailable({ version: '4.1.0', files: [exe] }, 'version: 4.1.0\nversion: 4.1.0\nsha512: AAAA+/bb==\npath: Gamachine-Setup-4.1.0.exe\n').ok).toBe(false)
  })
})
