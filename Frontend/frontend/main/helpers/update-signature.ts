import { createPublicKey, verify } from 'node:crypto'

// Every unknown answers false/refused: an update installs only on an explicit pass.

export function verifyManifest(manifestBytes: Uint8Array, signatureB64: string, publicKeysPem: string[]): boolean {
  const signature = Buffer.from(String(signatureB64).trim(), 'base64')
  if (signature.length !== 64) return false
  for (const pem of publicKeysPem) {
    try {
      const key = createPublicKey(pem)
      if (key.asymmetricKeyType === 'ed25519' && verify(null, manifestBytes, key, signature)) return true
    } catch {
      // An unparseable key is skipped; the others may still match.
    }
  }
  return false
}

export interface CheckResult { ok: boolean; reason?: string }

const unquote = (s: string) => s.trim().replace(/^(['"])(.*)\1$/, '$2')

// Reads the fixed shape electron-builder writes: a top-level `version:` and
// `sha512:` values (top-level and per `files:` entry). Anything else is ignored.
export function parseManifest(text: string): { version: string; sha512: string[] } {
  const versions = [...text.matchAll(/^version:[ \t]*(.+?)[ \t]*$/gm)].map((m) => unquote(m[1]))
  const sha512 = [...text.matchAll(/^[ \t]*(?:-[ \t]+)?sha512:[ \t]*(.+?)[ \t]*$/gm)].map((m) => unquote(m[1]))
  return { version: versions.length === 1 ? versions[0] : '', sha512 }
}

// `info.sha512` is the base64 SHA-512 of the file that was actually downloaded.
export function checkDownloaded(info: { version?: string; sha512?: string }, manifestText: string): CheckResult {
  const signed = parseManifest(manifestText)
  if (!signed.version) return { ok: false, reason: 'imzalı manifestte sürüm yok' }
  if (!info?.version || info.version !== signed.version) {
    return { ok: false, reason: `sürüm uyuşmuyor (${info?.version} / ${signed.version})` }
  }
  if (!info.sha512 || !signed.sha512.includes(info.sha512)) {
    return { ok: false, reason: 'indirilen dosyanın sha512 değeri imzalı manifestte yok' }
  }
  return { ok: true }
}
