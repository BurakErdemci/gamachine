/* Build-time macOS sidecar. Verify the archive before reading any zip member. */
const fs = require('node:fs')
const path = require('node:path')
const os = require('node:os')
const https = require('node:https')
const { pipeline } = require('node:stream/promises')
const { execFileSync } = require('node:child_process')

const KEY = 'cua-driver/macos-universal2'

function download(url, target, redirects = 0) {
  if (redirects > 5 || new URL(url).protocol !== 'https:') return Promise.reject(new Error('Invalid download redirect'))
  return new Promise((resolve, reject) => {
    const request = https.get(url, response => {
      if (response.statusCode >= 300 && response.statusCode < 400 && response.headers.location) {
        response.resume()
        download(new URL(response.headers.location, url).href, target, redirects + 1).then(resolve, reject)
      } else if (response.statusCode !== 200) {
        response.resume()
        reject(new Error(`cua-driver download HTTP ${response.statusCode}`))
      } else {
        pipeline(response, fs.createWriteStream(target, { mode: 0o600 })).then(resolve, reject)
      }
    })
    request.setTimeout(60_000, () => request.destroy(new Error('cua-driver download timed out')))
    request.on('error', reject)
  })
}

async function fetchCuaDriver(options = {}) {
  const platform = options.platform ?? process.platform
  const log = options.log ?? console.log
  if (platform !== 'darwin') { log('[cua] skipped: macOS only'); return }
  const io = options.fs ?? fs
  const exec = options.execFileSync ?? execFileSync
  const fetch = options.download ?? download
  const root = options.repoRoot ?? path.resolve(__dirname, '../../..')
  const pin = JSON.parse(io.readFileSync(path.join(root, 'scripts/pinned_assets.json'), 'utf8')).assets[KEY]
  if (!pin || !/^sha256:[a-f0-9]{64}$/.test(pin.digest)) throw new Error('Missing or invalid cua-driver pin')
  const destination = path.join(root, 'Backend/vendor/cua')
  const binary = path.join(destination, 'cua-driver')
  const stamp = path.join(destination, '.fetched')
  if (io.existsSync(stamp) && io.readFileSync(stamp, 'utf8').trim() === pin.digest
    && io.existsSync(binary)) {
    io.chmodSync(binary, 0o755)
    log('[cua] pinned driver already fetched')
    return
  }
  const scratch = io.mkdtempSync(path.join(options.tmpdir ?? os.tmpdir(), 'fetch-cua-'))
  try {
    const wheel = path.join(scratch, 'driver.whl')
    await fetch(pin.url, wheel)
    exec('python3', [path.join(root, 'scripts/pinned_assets.py'), 'verify', KEY, wheel], { stdio: 'inherit' })
    const members = exec('unzip', ['-Z1', wheel], { encoding: 'utf8' }).trim().split(/\r?\n/)
    if (!members.includes('cua_driver/bin/cua-driver')) {
      throw new Error('cua-driver wheel is missing the binary')
    }
    const data = exec('unzip', ['-p', wheel, 'cua_driver/bin/cua-driver'], { maxBuffer: 128 * 1024 * 1024 })
    const extracted = path.join(scratch, 'cua-driver')
    io.writeFileSync(extracted, data)
    io.chmodSync(extracted, 0o755)
    io.mkdirSync(destination, { recursive: true })
    // copyFile works even when the system temp directory is on another volume.
    const staged = path.join(destination, 'cua-driver.new')
    io.copyFileSync(extracted, staged)
    io.chmodSync(staged, 0o755)
    io.renameSync(staged, binary)
    io.writeFileSync(stamp, `${pin.digest}\n`)
    log('[cua] verified and installed pinned driver')
  } finally {
    io.rmSync(scratch, { recursive: true, force: true })
  }
}

module.exports = { fetchCuaDriver }
if (require.main === module) fetchCuaDriver().catch(error => { console.error('[cua]', error.message); process.exitCode = 1 })
