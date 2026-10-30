#!/usr/bin/env node
/**
 * Signs the update manifests of a DRAFT GitHub release on the owner's own PC.
 *
 *   node scripts/sign-release.js <tag> <encrypted-key-path>
 *
 * Downloads latest.yml and latest-mac.yml with the gh CLI, signs each with the
 * Ed25519 private key (passphrase asked, hidden), uploads <name>.sig back to the
 * draft. Each signature is first verified against the public keys embedded in
 * main/helpers/update-keys.ts and a manifest whose version differs from the tag
 * is refused; nothing is uploaded otherwise. Publishing the release stays a manual step.
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

// update-keys.ts is TypeScript, so the PEM blocks are read out of its text; only
// template literals count, so a key mentioned in a comment is not one.
function embeddedPublicKeys(file = KEYS_FILE) {
  const text = fs.readFileSync(file, 'utf8');
  return [...text.matchAll(/`\s*(-----BEGIN PUBLIC KEY-----[\s\S]*?-----END PUBLIC KEY-----)\s*`/g)].map((m) => m[1]);
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
      const bytes = fs.readFileSync(file);
      const text = bytes.toString('utf8');
      const version = (text.match(/^version:[ \t]*['"]?([^\s'"]+)/m) || [])[1];
      if (`v${version}` !== tag) throw new Error(`${name} says version ${version}, which does not match tag ${tag}; refusing to sign`);
      log(`${name}: version ${version}, sha512 ${[...text.matchAll(/^[ \t]*(?:-[ \t]+)?sha512:[ \t]*(\S+)/gm)].map((m) => m[1].slice(0, 12)).join(' ')}...`);
      const signature = crypto.sign(null, bytes, privateKey).toString('base64');
      if (!verifies(bytes, signature, publicKeys)) {
        throw new Error(publicKeys.length ? `${name}: this key is not one of the public keys in update-keys.ts; nothing uploaded` : 'update-keys.ts holds no public key yet; nothing uploaded');
      }
      const sigFile = `${file}.sig`;
      fs.writeFileSync(sigFile, signature);
      sigFiles.push(sigFile);
    }
    run(['release', 'upload', tag, '--repo', REPO, '--clobber', ...sigFiles]);
    MANIFESTS.forEach((name) => log(`${name}.sig uploaded; verified against update-keys.ts`));
    return true;
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
