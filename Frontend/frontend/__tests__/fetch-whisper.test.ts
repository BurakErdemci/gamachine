import { createRequire } from 'module'
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import os from 'node:os'
import path from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const require = createRequire(import.meta.url)
const { fetchWhisper } = require('../scripts/fetch-whisper.js')

const repoRoot = path.resolve(__dirname, '..', '..', '..')

// The stamp header is a contract between the build scripts and this hook:
// the pinned commit (build_whisper.ps1) and the pinned model digest.
function stampHeader() {
  const ps1 = readFileSync(path.join(repoRoot, 'Backend/vendor/build_whisper.ps1'), 'utf8')
  const commit = /\$Commit\s*=\s*'([0-9a-f]{40})'/.exec(ps1)![1]
  const ledger = JSON.parse(readFileSync(path.join(repoRoot, 'scripts/pinned_assets.json'), 'utf8'))
  return `commit=${commit} model=${ledger.assets['whisper-model/large-v3-turbo-q8_0'].digest}`
}

// Writes an engine tree the way build_whisper.sh does: files, then a stamp
// listing each file's SHA-256.
function writeEngine(outDir: string) {
  const files: Record<string, string> = {
    'bin/whisper-server': 'mach-o',
    'bin/LICENSE-whisper.cpp.txt': 'MIT',
    'models/ggml-large-v3-turbo-q8_0.bin': 'model',
  }
  const lines = [stampHeader()]
  for (const [rel, body] of Object.entries(files)) {
    mkdirSync(path.dirname(path.join(outDir, rel)), { recursive: true })
    writeFileSync(path.join(outDir, rel), body)
    lines.push(`${rel} sha256:${createHash('sha256').update(body).digest('hex')}`)
  }
  writeFileSync(path.join(outDir, '.built'), lines.join('\n') + '\n')
}

describe('fetch-whisper prebuild hook on macOS', () => {
  let outDir: string
  beforeEach(() => {
    outDir = mkdtempSync(path.join(os.tmpdir(), 'gm-fetch-whisper-'))
    vi.spyOn(console, 'log').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.spyOn(console, 'error').mockImplementation(() => {})
  })
  afterEach(() => {
    rmSync(outDir, { recursive: true, force: true })
    vi.restoreAllMocks()
  })

  it('fails packaging when the engine cannot be built', () => {
    const spawn = vi.fn(() => ({ status: 1 }))
    expect(fetchWhisper({ platform: 'darwin', env: {}, spawn, outDir })).toBe(1)
  })

  it('packages without dictation only when GAMACHINE_ALLOW_NO_DICTATION=1', () => {
    const spawn = vi.fn(() => ({ status: 1 }))
    expect(fetchWhisper({ platform: 'darwin', env: { GAMACHINE_ALLOW_NO_DICTATION: '1' }, spawn, outDir })).toBe(0)
  })

  it('builds with build_whisper.sh and accepts the tree it stamps', () => {
    const spawn = vi.fn((cmd: string, args: string[]) => {
      expect(cmd).toBe('bash')
      expect(path.basename(args[0])).toBe('build_whisper.sh')
      writeEngine(outDir)
      return { status: 0 }
    })
    expect(fetchWhisper({ platform: 'darwin', env: {}, spawn, outDir })).toBe(0)
    expect(spawn).toHaveBeenCalledTimes(1)
  })

  it('skips the build when the stamped tree is intact', () => {
    writeEngine(outDir)
    const spawn = vi.fn(() => ({ status: 0 }))
    expect(fetchWhisper({ platform: 'darwin', env: {}, spawn, outDir })).toBe(0)
    expect(spawn).not.toHaveBeenCalled()
  })

  it('rebuilds when a stamped file no longer matches its hash', () => {
    writeEngine(outDir)
    writeFileSync(path.join(outDir, 'bin/whisper-server'), 'tampered')
    const spawn = vi.fn(() => ({ status: 1 }))
    expect(fetchWhisper({ platform: 'darwin', env: {}, spawn, outDir })).toBe(1)
    expect(spawn).toHaveBeenCalledTimes(1)
  })
})
