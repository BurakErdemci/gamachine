const path = require('node:path')
const fs = require('node:fs')
const { execFileSync } = require('node:child_process')

// Mach-O is detected by its first bytes, not by extension: the bundled backend,
// uv, cua-driver and the .NET host have no extension at all.
const THIN_MAGICS = new Set([0xfeedface, 0xcefaedfe, 0xfeedfacf, 0xcffaedfe])
const FAT_MAGIC = 0xcafebabe
const FAT_MAGIC_64 = 0xcafebabf

function isMachO(header) {
  if (header.length < 8) return false
  const magic = header.readUInt32BE(0)
  if (THIN_MAGICS.has(magic)) return true
  if (magic === FAT_MAGIC_64) return true
  // A Java .class file shares 0xcafebabe; there the next word is its version
  // (>= 45), while a universal binary stores its arch count there.
  return magic === FAT_MAGIC && header.readUInt32BE(4) < 45
}

function readHeader(fsApi, file) {
  const fd = fsApi.openSync(file, 'r')
  try {
    const header = Buffer.alloc(8)
    const read = fsApi.readSync(fd, header, 0, 8, 0)
    return header.subarray(0, read)
  } finally {
    fsApi.closeSync(fd)
  }
}

// Symlinks are skipped, not followed: inside a bundle they point at files the
// walk reaches anyway, and codesign rejects a symlink as a signing target.
function findMachOFiles(fsApi, dir) {
  const found = []
  for (const entry of fsApi.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isSymbolicLink()) continue
    if (entry.isDirectory()) found.push(...findMachOFiles(fsApi, full))
    else if (entry.isFile() && isMachO(readHeader(fsApi, full))) found.push(full)
  }
  return found
}

// Deepest first, so a binary is signed before anything that encloses it.
function deepestFirst(files) {
  const depth = file => file.split(path.sep).length
  return [...files].sort((a, b) => depth(b) - depth(a) || (a < b ? -1 : a > b ? 1 : 0))
}

// The same lookup electron-builder's MacPackager.sign() makes: `identity: null` opts
// out, CSC_LINK is imported into a temporary keychain, CSC_NAME or mac.identity
// narrows the search, CSC_IDENTITY_AUTO_DISCOVERY=false disables it. Its result is
// then held to the name prefix, because findIdentity('Developer ID Application')
// also returns any certificate missing from its Apple prefix list: measured 29 Oct
// 2026 on the owner's Mac, it returned "Apple Development: …", which cannot be
// notarized.
async function resolveIdentity(packager, findIdentity) {
  const qualifier = packager.platformSpecificBuildOptions.identity
  if (qualifier === null) return null
  const { keychainFile } = await packager.codeSigningInfo.value
  const identity = await findIdentity('Developer ID Application', qualifier, keychainFile)
  if (!identity || !identity.name.startsWith('Developer ID Application:')) return null
  return { name: identity.hash || identity.name, keychainFile }
}

async function signMacNested(context, deps = {}) {
  if (context.electronPlatformName !== 'darwin') return

  const exec = deps.execFileSync ?? execFileSync
  const fsApi = deps.fs ?? fs
  const log = deps.log ?? console.log
  const findIdentity = deps.findIdentity ?? require('app-builder-lib/out/codeSign/macCodeSign').findIdentity
  const { packager } = context
  const appPath = path.join(context.appOutDir, `${packager.appInfo.productFilename}.app`)

  const codesign = (args, target) => {
    try {
      exec('codesign', [...args, target], { stdio: 'inherit' })
    } catch (error) {
      throw new Error(`[sign-mac-nested] codesign failed for ${target}: ${error.message}`, { cause: error })
    }
  }

  const identity = await resolveIdentity(packager, findIdentity)
  const nested = deepestFirst(findMachOFiles(fsApi, path.join(appPath, 'Contents', 'Resources')))

  if (identity) {
    const options = packager.platformSpecificBuildOptions
    if (!options.entitlementsInherit) {
      throw new Error('[sign-mac-nested] mac.entitlementsInherit must be set to sign with a Developer ID')
    }
    const entitlements = path.resolve(packager.projectDir, options.entitlementsInherit)
    const args = ['--force', '--options', 'runtime', '--timestamp', '--entitlements', entitlements,
      '--sign', identity.name, ...(identity.keychainFile ? ['--keychain', identity.keychainFile] : [])]
    log(`[sign-mac-nested] Developer ID: signing ${nested.length} nested Mach-O files under Resources`)
    for (const file of nested) codesign(args, file)
    // Frameworks, helper apps and the app itself are signed next by electron-builder
    // (@electron/osx-sign) with the same identity, hardened runtime and entitlements.
    return
  }

  // No Developer ID: ad-hoc, as before this hook signed nested resources. The
  // identifier on helpers and the app keeps macOS permissions (microphone,
  // accessibility for the computer-use driver) attached to com.gamachine.app.
  // electron-builder reads mac.identity again when it signs, after this hook; null
  // makes it skip signing and notarization ("identity explicitly is set to null")
  // instead of re-signing this ad-hoc app with whatever certificate it discovered.
  packager.platformSpecificBuildOptions.identity = null
  log(`[sign-mac-nested] No Developer ID identity: ad-hoc signing ${nested.length} nested Mach-O files under Resources`)
  for (const file of nested) codesign(['--force', '--sign', '-'], file)

  const frameworksDir = path.join(appPath, 'Contents', 'Frameworks')
  const entries = fsApi.readdirSync(frameworksDir).sort()
  for (const name of entries.filter(entry => entry.endsWith('.framework'))) {
    const frameworkPath = path.join(frameworksDir, name)
    log(`[sign-mac-nested] Signing framework: ${frameworkPath}`)
    codesign(['--force', '--sign', '-'], frameworkPath)
  }
  for (const name of entries.filter(entry => entry.endsWith('.app'))) {
    const helperPath = path.join(frameworksDir, name)
    const plistPath = path.join(helperPath, 'Contents', 'Info.plist')
    log(`[sign-mac-nested] Reading helper identifier: ${name}`)
    const id = exec(path.join(path.sep, 'usr', 'libexec', 'PlistBuddy'),
      ['-c', 'Print CFBundleIdentifier', plistPath], { encoding: 'utf8' }).trim()
    if (!id) throw new Error(`[sign-mac-nested] Empty CFBundleIdentifier for helper: ${name}`)
    log(`[sign-mac-nested] Signing helper: ${helperPath}`)
    codesign(['--force', '--sign', '-', '--identifier', id], helperPath)
  }

  log(`[sign-mac-nested] Signing app: ${appPath}`)
  codesign(['--force', '--sign', '-', '--identifier', packager.appInfo.id], appPath)
  log(`[sign-mac-nested] Verifying app: ${appPath}`)
  codesign(['--verify'], appPath)
}

module.exports = signMacNested
module.exports.signMacNested = signMacNested
module.exports.isMachO = isMachO
module.exports.deepestFirst = deepestFirst
