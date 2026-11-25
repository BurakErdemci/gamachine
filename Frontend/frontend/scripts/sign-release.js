#!/usr/bin/env node
/**
 * Signs the update manifests of a DRAFT GitHub release on the owner's own PC.
 *
 *   node scripts/sign-release.js <tag> <encrypted-key-path> --mac-key <encrypted-mac-key-path>
 *
 * Downloads latest.yml and latest-mac.yml with the gh CLI and uploads <name>.sig back to
 * the draft. latest.yml is signed with the positional key and checked against
 * PRIMARY/BACKUP_UPDATE_PUBLIC_KEY; latest-mac.yml is signed with the --mac-key key and
 * checked against MAC_PRIMARY/BACKUP_UPDATE_PUBLIC_KEY (both in main/helpers/update-keys.ts).
 * The two sets are separate on purpose: 4.0.0-4.0.3 Macs verify latest-mac.yml with the
 * Windows keys, so a Mac manifest signed with the Mac key makes them show the "download
 * manually" dialog instead of crashing in Squirrel.Mac. Each key's passphrase is asked once
 * (hidden). A manifest whose version differs from the tag is refused; nothing is uploaded
 * otherwise. Publishing the release stays a manual step. See docs/releasing.md.
 */
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { checkKeyLocation, promptHidden } = require('./update-keygen');

const REPO = 'BurakErdemci/gamachine';
const KEYS_FILE = path.join(__dirname, '..', 'main', 'helpers', 'update-keys.ts');

function gh(args) {
  return execFileSync('gh', args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'inherit'] });
}

// update-keys.ts is TypeScript, so the PEM blocks are read out of its text; only the template
// literals assigned to `export const <NAME>_UPDATE_PUBLIC_KEY` count, so a key mentioned in a
// comment is not one, and an empty template (no base64 body) is not a key. mac selects the
// MAC_* constants, otherwise the others.
function embeddedPublicKeys(file = KEYS_FILE, mac = false) {
  const text = fs.readFileSync(file, 'utf8');
  const decls = text.matchAll(/export const (\w+_UPDATE_PUBLIC_KEY)\s*=\s*`\s*(-----BEGIN PUBLIC KEY-----[\s\S]*?-----END PUBLIC KEY-----)\s*`/g);
  return [...decls]
    .filter((m) => m[1].startsWith('MAC_') === mac)
    .map((m) => m[2])
    .filter((pem) => pem.replace(/-----(BEGIN|END) PUBLIC KEY-----|\s/g, '') !== '');
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

const USAGE = 'usage: node scripts/sign-release.js <tag> <encrypted-key-path> --mac-key <encrypted-mac-key-path>';

// An encrypted PKCS#8 Ed25519 key, loaded with its passphrase asked once.
function loadKey(keyPath, label) {
  const keyText = fs.readFileSync(checkKeyLocation(keyPath), 'utf8');
  if (!keyText.includes('-----BEGIN ENCRYPTED PRIVATE KEY-----')) throw new Error(`the ${label} private key must be an encrypted PKCS#8 PEM (made by update-keygen.js); refusing an unencrypted key`);
  return keyText;
}

function unlock(keyText, passphrase, label) {
  const privateKey = crypto.createPrivateKey({ key: keyText, passphrase });
  if (privateKey.asymmetricKeyType !== 'ed25519') throw new Error(`the ${label} key is not an Ed25519 key`);
  return privateKey;
}

async function signRelease(tag, keyPath, macKeyPath, io = {}) {
  const run = io.gh || gh;
  const ask = io.promptHidden || promptHidden;
  const publicKeys = io.publicKeys || embeddedPublicKeys(KEYS_FILE, false);
  const macPublicKeys = io.macPublicKeys || embeddedPublicKeys(KEYS_FILE, true);
  const log = io.log || console.log;

  if (!tag || !keyPath || !macKeyPath) throw new Error(USAGE);
  if (path.resolve(keyPath) === path.resolve(macKeyPath)) throw new Error('--mac-key must be a different key file than the Windows key');
  const releases = JSON.parse(run(['release', 'list', '--repo', REPO, '--limit', '100', '--json', 'tagName,isDraft']));
  const release = releases.find((r) => r.tagName === tag);
  if (!release) throw new Error(`release ${tag} not found`);
  if (!release.isDraft) throw new Error(`release ${tag} is already published; refusing to sign it`);

  // Both key files are checked before the first passphrase is asked.
  const winText = loadKey(keyPath, 'Windows');
  const macText = loadKey(macKeyPath, 'Mac');
  const winKey = unlock(winText, await ask('Windows key passphrase: '), 'Windows');
  const macKey = unlock(macText, await ask('Mac key passphrase: '), 'Mac');
  const manifests = [
    { name: 'latest.yml', privateKey: winKey, keys: publicKeys, keyFile: 'update-keys.ts PRIMARY_UPDATE_PUBLIC_KEY / BACKUP_UPDATE_PUBLIC_KEY' },
    { name: 'latest-mac.yml', privateKey: macKey, keys: macPublicKeys, keyFile: 'update-keys.ts MAC_PRIMARY_UPDATE_PUBLIC_KEY / MAC_BACKUP_UPDATE_PUBLIC_KEY' },
  ];

  const dir = fs.mkdtempSync(path.join(io.tmpDir || os.tmpdir(), 'gamachine-sign-'));
  try {
    run(['release', 'download', tag, '--repo', REPO, '--dir', dir, ...manifests.flatMap((m) => ['--pattern', m.name])]);
    const sigFiles = [];
    for (const { name, privateKey, keys, keyFile } of manifests) {
      const file = path.join(dir, name);
      if (!fs.existsSync(file)) throw new Error(`${name} is missing from the release`);
      const bytes = fs.readFileSync(file);
      const text = bytes.toString('utf8');
      const version = (text.match(/^version:[ \t]*['"]?([^\s'"]+)/m) || [])[1];
      if (`v${version}` !== tag) throw new Error(`${name} says version ${version}, which does not match tag ${tag}; refusing to sign`);
      log(`${name}: version ${version}, sha512 ${[...text.matchAll(/^[ \t]*(?:-[ \t]+)?sha512:[ \t]*(\S+)/gm)].map((m) => m[1].slice(0, 12)).join(' ')}...`);
      const signature = crypto.sign(null, bytes, privateKey).toString('base64');
      if (!verifies(bytes, signature, keys)) {
        throw new Error(keys.length ? `${name}: this key is not one of the ${keyFile} keys; nothing uploaded` : `${name}: ${keyFile} holds no public key yet; nothing uploaded`);
      }
      // A Mac manifest that 4.0.0-4.0.3 Macs would accept is the Squirrel crash this split prevents.
      if (name === 'latest-mac.yml' && verifies(bytes, signature, publicKeys)) {
        throw new Error('latest-mac.yml: the Mac key is also one of the Windows keys, so 4.0.0-4.0.3 Macs would install it through Squirrel and crash; nothing uploaded');
      }
      const sigFile = `${file}.sig`;
      fs.writeFileSync(sigFile, signature);
      sigFiles.push(sigFile);
    }
    run(['release', 'upload', tag, '--repo', REPO, '--clobber', ...sigFiles]);
    manifests.forEach(({ name, keyFile }) => log(`${name}.sig uploaded; verified against ${keyFile}`));
    return true;
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

// Positional <tag> <key> plus --mac-key <path> (or --mac-key=<path>); any other dash-argument,
// long or short, in any position, is refused rather than ignored.
function parseArgs(argv) {
  const positional = [];
  let macKeyPath;
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--mac-key' || a.startsWith('--mac-key=')) {
      if (macKeyPath !== undefined) throw new Error('--mac-key given twice');
      macKeyPath = a === '--mac-key' ? argv[++i] : a.slice('--mac-key='.length);
      if (!macKeyPath || macKeyPath.startsWith('-')) throw new Error(`--mac-key needs a key file path; ${USAGE}`);
    } else if (a.startsWith('-')) {
      throw new Error(`unknown option ${a}`);
    } else {
      positional.push(a);
    }
  }
  const [tag, keyPath, ...extra] = positional;
  if (extra.length) throw new Error(`unexpected argument ${extra.join(' ')}`);
  if (!tag || !keyPath || !macKeyPath) throw new Error(USAGE);
  return { tag, keyPath, macKeyPath };
}

module.exports = { signRelease, verifies, embeddedPublicKeys, parseArgs };

if (require.main === module) {
  Promise.resolve().then(() => {
    const args = parseArgs(process.argv.slice(2));
    return signRelease(args.tag, args.keyPath, args.macKeyPath);
  }).then(
    (ok) => process.exit(ok ? 0 : 1),
    (e) => {
      console.error(`sign-release: ${e.message}`);
      process.exit(1);
    },
  );
}
