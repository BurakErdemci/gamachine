#!/usr/bin/env node
/**
 * Signs the update manifests of a DRAFT GitHub release on the owner's own PC.
 *
 *   node scripts/sign-release.js <tag> <encrypted-key-path>
 *
 * Downloads latest.yml and latest-mac.yml with the gh CLI, signs each with the
 * Ed25519 private key (passphrase asked, hidden), uploads <name>.sig back to the
 * draft, then verifies the signatures against the public keys embedded in
 * main/helpers/update-keys.ts. Publishing the release stays a manual step.
 * See docs/releasing.md.
 */
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { promptHidden } = require('./update-keygen');

const REPO = 'BurakErdemci/gamachine';
const MANIFESTS = ['latest.yml', 'latest-mac.yml'];
const KEYS_FILE = path.join(__dirname, '..', 'main', 'helpers', 'update-keys.ts');

function gh(args) {
  return execFileSync('gh', args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'inherit'] });
}

// update-keys.ts is TypeScript, so the PEM blocks are read out of its text.
function embeddedPublicKeys(file = KEYS_FILE) {
  const text = fs.readFileSync(file, 'utf8');
  return text.match(/-----BEGIN PUBLIC KEY-----[\s\S]*?-----END PUBLIC KEY-----/g) || [];
}

// Same check as verifyManifest in main/helpers/update-signature.ts.
function verifies(bytes, signatureB64, publicKeys) {
  const signature = Buffer.from(signatureB64.trim(), 'base64');
  if (signature.length !== 64) return false;
  return publicKeys.some((pem) => {
    try {
      const key = crypto.createPublicKey(pem);
      return key.asymmetricKeyType === 'ed25519' && crypto.verify(null, bytes, key, signature);
    } catch {
      return false;
    }
  });
}

async function signRelease(tag, keyPath, io = {}) {
  const run = io.gh || gh;
  const ask = io.promptHidden || promptHidden;
  const publicKeys = io.publicKeys || embeddedPublicKeys();
  const log = io.log || console.log;

  if (!tag || !keyPath) throw new Error('usage: node scripts/sign-release.js <tag> <encrypted-key-path>');
  const releases = JSON.parse(run(['release', 'list', '--repo', REPO, '--limit', '100', '--json', 'tagName,isDraft']));
  const release = releases.find((r) => r.tagName === tag);
  if (!release) throw new Error(`release ${tag} not found`);
  if (!release.isDraft) throw new Error(`release ${tag} is already published; refusing to sign it`);

  const privateKey = crypto.createPrivateKey({ key: fs.readFileSync(path.resolve(keyPath), 'utf8'), passphrase: await ask('Passphrase: ') });
  if (privateKey.asymmetricKeyType !== 'ed25519') throw new Error('not an Ed25519 key');

  const dir = fs.mkdtempSync(path.join(io.tmpDir || os.tmpdir(), 'gamachine-sign-'));
  try {
    run(['release', 'download', tag, '--repo', REPO, '--dir', dir, ...MANIFESTS.flatMap((m) => ['--pattern', m])]);
    const sigFiles = [];
    for (const name of MANIFESTS) {
      const file = path.join(dir, name);
      if (!fs.existsSync(file)) throw new Error(`${name} is missing from the release`);
      const sigFile = `${file}.sig`;
      fs.writeFileSync(sigFile, crypto.sign(null, fs.readFileSync(file), privateKey).toString('base64'));
      sigFiles.push(sigFile);
    }
    run(['release', 'upload', tag, '--repo', REPO, '--clobber', ...sigFiles]);

    let allOk = true;
    for (const name of MANIFESTS) {
      const ok = verifies(fs.readFileSync(path.join(dir, name)), fs.readFileSync(path.join(dir, `${name}.sig`), 'utf8'), publicKeys);
      allOk = allOk && ok;
      log(`${name}.sig uploaded; verification against update-keys.ts: ${ok ? 'OK' : 'FAILED'}`);
    }
    if (!publicKeys.length) log('update-keys.ts holds no public key yet; the app would refuse every update.');
    return allOk;
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

module.exports = { signRelease, verifies, embeddedPublicKeys };

if (require.main === module) {
  signRelease(process.argv[2], process.argv[3]).then(
    (ok) => process.exit(ok ? 0 : 1),
    (e) => {
      console.error(`sign-release: ${e.message}`);
      process.exit(1);
    },
  );
}
