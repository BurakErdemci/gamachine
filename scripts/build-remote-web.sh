#!/usr/bin/env bash
# Copies an existing renderer export into relay/public/app/ (builds nothing itself) and writes
# relay/public/app-csp.json: the sha256 of every inline script of each exported page, which the
# relay worker puts in that page's Content-Security-Policy (no 'unsafe-inline').
# Usage: scripts/build-remote-web.sh [--build] [export_dir]   (default Frontend/frontend/app-remote)
# --build runs the REMOTE_WEB=1 renderer export first.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
if [ "${1:-}" = "--build" ]; then
  shift
  (cd "$root/Frontend/frontend" && REMOTE_WEB=1 NODE_ENV=production npx --no-install next build renderer)
fi
src="${1:-$root/Frontend/frontend/app-remote}"
dest="$root/relay/public/app"
csp="$root/relay/public/app-csp.json"

if [ ! -f "$src/index.html" ]; then
  echo "error: $src has no index.html; run the renderer export first" >&2
  exit 1
fi

rm -rf "$dest"
mkdir -p "$dest"
cp -R "$src"/. "$dest"/

# A script runs (and needs a hash) unless it has a src or a type that is not JavaScript, such
# as Next's __NEXT_DATA__ (application/json). A script misread as data gets no hash and is
# blocked, so a mistake here fails closed.
node - "$dest" "$csp" <<'EOF'
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const [dest, out] = process.argv.slice(2);
const JS_TYPE = /^(module|(text|application)\/(x-)?(java|ecma)script|text\/(jscript|livescript))$/;
const attr = (attrs, name) => {
  const m = attrs.match(new RegExp(`(?:^|\\s)${name}\\s*=\\s*(?:"([^"]*)"|'([^']*)'|([^\\s>]+))`, 'i'));
  return m ? (m[1] ?? m[2] ?? m[3]) : null;
};
const pages = {};
const walk = (dir) => {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(full);
    else if (entry.name.endsWith('.html')) {
      const html = fs.readFileSync(full, 'utf8');
      const hashes = [];
      for (const m of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script\s*>/gi)) {
        const type = (attr(m[1], 'type') ?? '').trim().toLowerCase();
        if (/(?:^|\s)src\s*=/i.test(m[1]) || (type !== '' && !JS_TYPE.test(type))) continue;
        hashes.push('sha256-' + crypto.createHash('sha256').update(m[2], 'utf8').digest('base64'));
      }
      pages[path.relative(dest, full).split(path.sep).join('/')] = [...new Set(hashes)];
    }
  }
};
walk(dest);
fs.writeFileSync(out, JSON.stringify(pages, null, 1) + '\n');
const total = Object.values(pages).reduce((n, h) => n + h.length, 0);
console.log(`inline scripts hashed: ${total} across ${Object.keys(pages).length} pages -> ${out}`);
EOF

echo "copied $(find "$dest" -type f | wc -l | tr -d ' ') files, $(du -sh "$dest" | cut -f1) to $dest"
