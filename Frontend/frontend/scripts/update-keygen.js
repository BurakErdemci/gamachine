#!/usr/bin/env node
/**
 * Generates an Ed25519 update-signing key pair (run once for the primary key,
 * once more for the backup). The private key is written as an encrypted PKCS#8
 * PEM (aes-256-cbc, passphrase); the public PEM goes to stdout for
 * main/helpers/update-keys.ts. See docs/releasing.md.
 *
 *   node scripts/update-keygen.js [<private-key-path>]
 *
 * The private key never belongs in the repo or in a synced folder, so those
 * paths are refused. An existing file is never overwritten.
 */
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const repoRoot = path.resolve(__dirname, '..', '..', '..');
const SYNCED_FOLDER = /^(onedrive.*|dropbox.*|icloud.*|google ?drive.*|googledrive.*|my drive|mobile documents|pcloud.*|nextcloud.*|box|box sync|mega.*|sync|cloudstorage|jarvanvault)$/i;
const MIN_PASSPHRASE = 12;

// macOS and Windows volumes are case-insensitive by default.
const foldCase = (p) => (process.platform === 'darwin' || process.platform === 'win32' ? p.toLowerCase() : p);

function isInside(child, parent) {
  const rel = path.relative(foldCase(parent), foldCase(child));
  return rel === '' || (rel.split(path.sep)[0] !== '..' && !path.isAbsolute(rel));
}

// Resolves symlinks/junctions (and the on-disk case and 8.3 names, via the native realpath) of the
// deepest existing ancestor, so a link or a respelled path into the repo is caught.
function realTarget(target) {
  const abs = path.resolve(target);
  let dir = path.dirname(abs);
  const tail = [path.basename(abs)];
  while (!fs.existsSync(dir)) {
    tail.unshift(path.basename(dir));
    const up = path.dirname(dir);
    if (up === dir) break;
    dir = up;
  }
  return path.join(fs.realpathSync.native(dir), ...tail);
}

// The private key never lives in the repo or in a synced folder, whether it is written or read.
function checkKeyLocation(target, root = repoRoot) {
  const real = realTarget(target);
  if (isInside(real, fs.realpathSync.native(root))) throw new Error(`refusing a key path inside the repository: ${real}`);
  const synced = real.split(path.sep).find((part) => SYNCED_FOLDER.test(part));
  if (synced) throw new Error(`refusing a key path inside a synced folder (${synced}): ${real}`);
  return real;
}

function checkKeyPath(target, root = repoRoot) {
  const real = checkKeyLocation(target, root);
  if (fs.existsSync(real)) throw new Error(`refusing to overwrite an existing file: ${real}`);
  return real;
}

// Reads a line without echo; on a Windows console too (raw mode, no readline).
// Escape sequences (arrows, function keys, paste markers) may arrive split across reads,
// so the parser state outlives a chunk; control characters are never part of the passphrase.
function promptHidden(question, input = process.stdin, output = process.stderr) {
  return new Promise((resolve, reject) => {
    if (!input.isTTY) return reject(new Error('a terminal is required to type the passphrase'));
    output.write(question);
    let value = [];
    let escape = ''; // '' | 'esc' (after ESC) | 'csi' (inside ESC [ ...) | 'ss3' (after ESC O)
    input.setRawMode(true);
    input.resume();
    input.setEncoding('utf8');
    const finish = (fn, arg) => {
      input.removeListener('data', onData);
      input.setRawMode(false);
      input.pause();
      output.write('\n');
      fn(arg);
    };
    const onData = (chunk) => {
      for (const ch of chunk) {
        if (escape === 'esc') { escape = ch === '[' ? 'csi' : ch === 'O' ? 'ss3' : ''; continue; }
        if (escape === 'csi') { if (ch >= '@' && ch <= '~') escape = ''; continue; }
        if (escape === 'ss3') { escape = ''; continue; }
        if (ch === '\u001b') escape = 'esc';
        else if (ch === '\r' || ch === '\n') return finish(resolve, value.join(''));
        else if (ch === '\u0003') return finish(reject, new Error('cancelled'));
        else if (ch === '\b' || ch === '\u007f') value.pop();
        else if (ch >= ' ') value.push(ch);
      }
    };
    input.on('data', onData);
  });
}

function generate(passphrase) {
  return crypto.generateKeyPairSync('ed25519', {
    publicKeyEncoding: { type: 'spki', format: 'pem' },
    privateKeyEncoding: { type: 'pkcs8', format: 'pem', cipher: 'aes-256-cbc', passphrase },
  });
}

async function main(argv = process.argv.slice(2), io = { promptHidden, stdout: process.stdout, stderr: process.stderr }) {
  const target = argv[0] || path.join(os.homedir(), `gamachine-update-key-${new Date().toISOString().slice(0, 10)}.pem`);
  const keyPath = checkKeyPath(target);
  const pass = await io.promptHidden('Passphrase: ');
  if (pass.length < MIN_PASSPHRASE) throw new Error(`passphrase must be at least ${MIN_PASSPHRASE} characters`);
  if (pass !== (await io.promptHidden('Passphrase again: '))) throw new Error('passphrases do not match');
  const { publicKey, privateKey } = generate(pass);
  fs.writeFileSync(keyPath, privateKey, { flag: 'wx', mode: 0o600 });
  io.stderr.write(`Encrypted private key written to ${keyPath}\nPublic key (paste into main/helpers/update-keys.ts):\n`);
  io.stdout.write(publicKey);
  return keyPath;
}

module.exports = { checkKeyLocation, checkKeyPath, promptHidden, generate, main };

if (require.main === module) {
  main().catch((e) => {
    console.error(`update-keygen: ${e.message}`);
    process.exit(1);
  });
}
