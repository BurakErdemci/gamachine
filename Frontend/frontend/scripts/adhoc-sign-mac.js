const path = require('node:path')
const { readdirSync } = require('node:fs')
const { execFileSync } = require('node:child_process')

function adhocSignMac(context, deps = {}) {
  if (context.electronPlatformName !== 'darwin') return
  if (context.packager.platformSpecificBuildOptions.identity !== null) return

  const exec = deps.execFileSync ?? execFileSync
  const readDir = deps.readdirSync ?? readdirSync
  const log = deps.log ?? console.log
  const appPath = path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`)
  const frameworksDir = path.join(appPath, 'Contents', 'Frameworks')
  const entries = readDir(frameworksDir).sort()

  for (const name of entries.filter(entry => entry.endsWith('.framework'))) {
    const frameworkPath = path.join(frameworksDir, name)
    log(`[adhoc-sign] Signing framework: ${frameworkPath}`)
    exec('codesign', ['--force', '--sign', '-', frameworkPath], { stdio: 'inherit' })
  }

  for (const name of entries.filter(entry => entry.endsWith('.app'))) {
    const helperPath = path.join(frameworksDir, name)
    const plistPath = path.join(helperPath, 'Contents', 'Info.plist')
    log(`[adhoc-sign] Reading helper identifier: ${name}`)
    const id = exec(path.join(path.sep, 'usr', 'libexec', 'PlistBuddy'),
      ['-c', 'Print CFBundleIdentifier', plistPath], { encoding: 'utf8' }).trim()
    if (!id) throw new Error(`Empty CFBundleIdentifier for helper: ${name}`)
    log(`[adhoc-sign] Signing helper: ${helperPath}`)
    exec('codesign', ['--force', '--sign', '-', '--identifier', id, helperPath], { stdio: 'inherit' })
  }

  log(`[adhoc-sign] Signing app: ${appPath}`)
  exec('codesign', ['--force', '--sign', '-', '--identifier', context.packager.appInfo.id, appPath], { stdio: 'inherit' })
  log(`[adhoc-sign] Verifying app: ${appPath}`)
  exec('codesign', ['--verify', appPath], { stdio: 'inherit' })
}

module.exports = adhocSignMac
module.exports.adhocSignMac = adhocSignMac
