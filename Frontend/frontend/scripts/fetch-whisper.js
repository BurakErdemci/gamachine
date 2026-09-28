#!/usr/bin/env node
/**
 * Prebuild step: makes sure `Backend/vendor/whisper/` holds the dictation engine
 * (whisper.cpp server + DLLs + the pinned q8_0 model) that electron-builder
 * ships as `resources/whisper` (electron-builder.yml, extraResources).
 *
 * WHY IT EXISTS: a build script and a packaging rule can both be in place while
 * nothing calls the script — `vendor/bin` once shipped empty for a month that
 * way. This hook is the caller.
 *
 * NOT BEST EFFORT on Windows: the product rule (docs/architecture.md) is that
 * the installer carries everything a feature needs, so an installer without
 * the engine is a broken build, not a degraded one. GAMACHINE_ALLOW_NO_DICTATION=1
 * is the explicit escape hatch for a local package without the toolchain.
 * macOS/Linux: the Metal/other builds are not implemented yet (docs/building.md);
 * those packages ship without dictation and the mic button says the files are missing.
 *
 * ⚠️ `pwsh` IS NOT ASSUMED: PowerShell 7 is not installed on the owner's
 * machine (measured 30 Aug 2026), so Windows' own `powershell` is the fallback.
 */
const { spawnSync } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');
const crypto = require('node:crypto');

const repoRoot = path.resolve(__dirname, '..', '..', '..');
const vendorDir = path.join(repoRoot, 'Backend', 'vendor');
const OUT_DIR = path.join(vendorDir, 'whisper');
const STAMP = path.join(OUT_DIR, '.built');
const SCRIPT = path.join(vendorDir, 'build_whisper.ps1');
const MODEL_KEY = 'whisper-model/large-v3-turbo-q8_0';

// The commit lives in the build script only; reading it from there keeps one
// source for "which whisper.cpp ships".
function expectedHeader() {
  try {
    const script = fs.readFileSync(SCRIPT, 'utf8');
    const commit = /\$Commit\s*=\s*'([0-9a-f]{40})'/.exec(script)?.[1];
    const ledger = JSON.parse(fs.readFileSync(path.join(repoRoot, 'scripts', 'pinned_assets.json'), 'utf8'));
    const digest = ledger.assets[MODEL_KEY]?.digest;
    if (!commit || !digest) return null;
    return `commit=${commit} model=${digest}`;
  } catch {
    return null;
  }
}

function sha256(file) {
  const h = crypto.createHash('sha256');
  const fd = fs.openSync(file, 'r');
  const buf = Buffer.alloc(8 * 1024 * 1024);
  try {
    let n;
    while ((n = fs.readSync(fd, buf, 0, buf.length, null)) > 0) h.update(buf.subarray(0, n));
  } finally {
    fs.closeSync(fd);
  }
  return h.digest('hex');
}

/**
 * The stamp names the pins that were built AND the bytes that were installed.
 * Checking only the header would let a tampered or half-copied tree sit next
 * to a matching stamp and be skipped (the audit finding on fetch-video-bins.js,
 * 30 Aug 2026).
 */
function alreadyCurrent(header) {
  if (!header) return false;
  try {
    const lines = fs.readFileSync(STAMP, 'utf8').trim().split(/\r?\n/);
    if (lines[0].trim() !== header) return false;
    const files = lines.slice(1).filter(Boolean);
    if (!files.some(l => l.startsWith('bin/whisper-server')) || !files.some(l => l.startsWith('models/'))) return false;
    return files.every(line => {
      const m = /^(\S+) sha256:([0-9a-f]{64})$/.exec(line.trim());
      if (!m) return false;
      const file = path.join(OUT_DIR, ...m[1].split('/'));
      return fs.existsSync(file) && sha256(file) === m[2];
    });
  } catch {
    return false;
  }
}

function runBuild() {
  for (const shell of ['pwsh', 'powershell']) {
    const r = spawnSync(shell, ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', SCRIPT],
      { stdio: 'inherit', cwd: repoRoot });
    if (r.error) continue;               // shell absent → try the next one
    return r.status === 0;
  }
  console.error('[fetch-whisper] neither pwsh nor powershell could be started');
  return false;
}

function main() {
  if (process.platform !== 'win32') {
    console.warn(
      '[fetch-whisper] WARNING: the dictation engine is only built for Windows so far\n' +
      '  (the Metal build for macOS is described in docs/building.md, not implemented).\n' +
      '  This package ships WITHOUT dictation; the mic button reports the files as missing.');
    return 0;
  }
  const header = expectedHeader();
  if (alreadyCurrent(header)) {
    console.log('[fetch-whisper] engine already built at the pinned commit and model — skipped.');
    return 0;
  }
  if (runBuild() && alreadyCurrent(header)) return 0;
  const msg =
    '[fetch-whisper] the dictation engine could not be built or verified.\n' +
    '  See docs/building.md ("Dictation engine") for the toolchain it needs.';
  if (process.env.GAMACHINE_ALLOW_NO_DICTATION === '1') {
    console.warn(`${msg}\n  GAMACHINE_ALLOW_NO_DICTATION=1 → packaging WITHOUT dictation.`);
    return 0;
  }
  console.error(`${msg}\n  Set GAMACHINE_ALLOW_NO_DICTATION=1 to package without it on purpose.`);
  return 1;
}

process.exit(main());
