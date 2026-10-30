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

const baseName = (p: string) => {
  let name = p.trim()
  try { name = decodeURIComponent(name) } catch { /* keep the raw spelling */ }
  return name.split(/[\\/]/).pop() ?? ''
}

export interface ManifestFile { name: string; sha512: string }

// Reads the fixed shape electron-builder writes: a top-level `version:`, the `files:` entries
// (`url` + `sha512`) and the top-level `path` + `sha512`. Other sections (`packages:` ...) are ignored.
export function parseManifest(text: string): { version: string; files: ManifestFile[] } {
  const versions = [...text.matchAll(/^version:[ \t]*(.+?)[ \t]*$/gm)].map((m) => unquote(m[1]))
  const files: ManifestFile[] = []
  const top: Record<string, string> = {}
  let section = ''
  let cur: ManifestFile | null = null
  for (const line of text.split(/\r?\n/)) {
    const key = /^([A-Za-z]\w*):[ \t]*(.*)$/.exec(line)
    if (key) {
      section = key[1]
      cur = null
      top[section] = unquote(key[2])
      continue
    }
    if (section !== 'files') continue
    const field = /^[ \t]*(-[ \t]+)?(url|sha512):[ \t]*(.*?)[ \t]*$/.exec(line)
    if (!field) continue
    if (field[1]) files.push((cur = { name: '', sha512: '' }))
    if (!cur) continue
    if (field[2] === 'url') cur.name = baseName(unquote(field[3]))
    else cur.sha512 = unquote(field[3])
  }
  if (top.path && top.sha512) files.push({ name: baseName(top.path), sha512: top.sha512 })
  return { version: versions.length === 1 ? versions[0] : '', files }
}

// `info.files` lists every file electron-updater may download for this update (name and
// sha512, from the manifest it fetched). The signed manifest must carry the same version and,
// for each of them, an entry with that same file name and sha512.
export function checkAvailable(info: { version: string; files: { name: unknown; sha512: unknown }[] }, manifestText: string): CheckResult {
  const signed = parseManifest(manifestText)
  if (!signed.version) return { ok: false, reason: 'imzalı manifestte sürüm yok' }
  if (info.version !== signed.version) return { ok: false, reason: `sürüm uyuşmuyor (${info.version} / ${signed.version})` }
  const listed = info.files.length > 0 && info.files.every((f) =>
    typeof f.name === 'string' && typeof f.sha512 === 'string' && f.name !== '' && f.sha512 !== '' &&
    signed.files.some((s) => s.name === baseName(f.name as string) && s.sha512 === f.sha512))
  return listed ? { ok: true } : { ok: false, reason: 'indirilecek dosyanın adı ve sha512 değeri imzalı manifestte yok' }
}
