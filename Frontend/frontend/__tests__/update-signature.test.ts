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

  it('accepts the signed version and sha512', () => {
    expect(checkAvailable({ version: '4.1.0', sha512: ['AAAA+/bb=='] }, text)).toEqual({ ok: true })
  })

  it('refuses a sha512 mismatch, a missing hash or an empty list', () => {
    expect(checkAvailable({ version: '4.1.0', sha512: ['BBBB'] }, text).ok).toBe(false)
    expect(checkAvailable({ version: '4.1.0', sha512: ['AAAA+/bb==', undefined] }, text).ok).toBe(false)
    expect(checkAvailable({ version: '4.1.0', sha512: [''] }, 'version: 4.1.0\nsha512: \n').ok).toBe(false)
    expect(checkAvailable({ version: '4.1.0', sha512: [] }, text).ok).toBe(false)
  })

  it('refuses a version mismatch', () => {
    expect(checkAvailable({ version: '4.0.9', sha512: ['AAAA+/bb=='] }, text).ok).toBe(false)
  })

  it('refuses a manifest without a version or a hash', () => {
    expect(checkAvailable({ version: '4.1.0', sha512: ['x'] }, 'files: []').ok).toBe(false)
    expect(checkAvailable({ version: '4.1.0', sha512: ['x'] }, 'version: 4.1.0\n').ok).toBe(false)
  })

  it('needs every file the updater may download in the manifest (mac)', () => {
    const mac = `version: 4.1.0\nfiles:\n  - url: a.zip\n    sha512: ZIP==\n    size: 1\n  - url: a.dmg\n    sha512: "DMG=="\n    size: 2\npath: a.zip\nsha512: ZIP==\n`
    expect(checkAvailable({ version: '4.1.0', sha512: ['ZIP==', 'DMG=='] }, mac).ok).toBe(true)
    expect(checkAvailable({ version: '4.1.0', sha512: ['ZIP==', 'EVIL=='] }, mac).ok).toBe(false)
    expect(parseManifest(mac).sha512).toEqual(['ZIP==', 'DMG==', 'ZIP=='])
  })

  it('refuses an ambiguous version line', () => {
    expect(checkAvailable({ version: '4.1.0', sha512: ['AAAA+/bb=='] }, 'version: 4.1.0\nversion: 4.1.0\nsha512: AAAA+/bb==\n').ok).toBe(false)
  })
})
