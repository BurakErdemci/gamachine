#!/usr/bin/env bash
# Dictation engine for macOS (Apple silicon) → Backend/vendor/whisper/{bin,models}
#
# The macOS counterpart of build_whisper.ps1: the same whisper.cpp commit and the
# same pinned model, built with Metal instead of Vulkan. Static libraries and the
# Metal shader library embedded in the binary, so bin/ holds one self-contained
# whisper-server that links only system frameworks. electron-builder ships the
# folder as resources/whisper (electron-builder.yml); sign-mac-nested.js signs the
# binary with the rest of the nested Mach-O files.
#
# ONE SOURCE FOR THE PINS: the whisper.cpp commit, repo URL, model key and model
# file name are read from build_whisper.ps1 (fetch-whisper.js reads the commit
# from there too), the model URL and digest from scripts/pinned_assets.json.
#
# Nothing here runs on a user's machine: the installer carries the result.
#
# Needs: cmake, Xcode command line tools (clang), git, curl, python3
# (scripts/pinned_assets.py). Not the Metal compiler: with the embedded library
# the shader source goes into the binary and is compiled on the user's Mac at
# the server's first start (~22 s once, then cached by macOS; measured on an M4).
#
# Usage:
#   bash Backend/vendor/build_whisper.sh [--source-dir <dir>] [--model-file <path>]
#     --model-file (or GAMACHINE_WHISPER_MODEL_FILE): an existing
#       ggml-large-v3-turbo-q8_0.bin; skips the 874 MB download, still verified.
#     --source-dir (or GAMACHINE_WHISPER_SOURCE_DIR): where the source is fetched
#       and built; default $TMPDIR/gm-whisper-<short commit>.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
ps1="$here/build_whisper.ps1"
pinned="$repo_root/scripts/pinned_assets.py"

die() { echo "[whisper] ERROR: $*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || die "this script builds the macOS engine; on Windows use build_whisper.ps1"
[ "$(uname -m)" = "arm64" ] || die "Apple silicon only (Intel Macs are not supported)"

# ps1_value <name>: the single-quoted value of `$<name> = '...'` in build_whisper.ps1.
ps1_value() {
  local v
  v="$(sed -nE "s/^[[:space:]]*\\\$$1[[:space:]]*=[[:space:]]*'([^']+)'.*/\\1/p" "$ps1" | head -n 1)"
  [ -n "$v" ] || die "could not read \$$1 from $ps1"
  printf '%s' "$v"
}

commit="$(ps1_value Commit)"
repo_url="$(ps1_value RepoUrl)"
model_key="$(ps1_value ModelKey)"
model_name="$(ps1_value ModelName)"
[[ "$commit" =~ ^[0-9a-f]{40}$ ]] || die "pinned commit '$commit' is not a full SHA-1"

source_dir="${GAMACHINE_WHISPER_SOURCE_DIR:-${TMPDIR:-/tmp}/gm-whisper-${commit:0:8}}"
model_file="${GAMACHINE_WHISPER_MODEL_FILE:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    --source-dir) source_dir="${2:?--source-dir needs a path}"; shift 2 ;;
    --model-file) model_file="${2:?--model-file needs a path}"; shift 2 ;;
    *) die "unknown argument: $1" ;;
  esac
done

out_dir="$here/whisper"
bin_dir="$out_dir/bin"
models_dir="$out_dir/models"
stamp="$out_dir/.built"

for tool in cmake git curl python3 clang; do
  command -v "$tool" >/dev/null 2>&1 || die "$tool not found (see docs/building.md, \"Dictation engine\")"
done

pinned_value() { python3 "$pinned" "$1" "$2"; }
pinned_ok() { python3 "$pinned" verify "$1" "$2"; }

# ── Source at the pinned commit ─────────────────────────────────────────────
if [ ! -d "$source_dir/.git" ]; then
  mkdir -p "$source_dir"
  git -C "$source_dir" init -q
  git -C "$source_dir" remote add origin "$repo_url"
fi
if [ "$(git -C "$source_dir" rev-parse HEAD 2>/dev/null || true)" != "$commit" ]; then
  git -C "$source_dir" fetch -q --depth 1 origin "$commit"
  git -C "$source_dir" checkout -q --detach FETCH_HEAD
fi
head="$(git -C "$source_dir" rev-parse HEAD)"
[ "$head" = "$commit" ] || die "whisper.cpp source is at $head, expected $commit"
# A local edit would be compiled into what ships; the build dir is untracked.
dirty="$(git -C "$source_dir" status --porcelain --untracked-files=no)"
[ -z "$dirty" ] || die "whisper.cpp source at $source_dir has local modifications:
$dirty"

# ── Build ───────────────────────────────────────────────────────────────────
# Metal with the shader library embedded (nothing has to sit next to the binary),
# static libraries (one self-contained whisper-server), no -mcpu=native so the
# binary runs on every Apple silicon generation, not only the build machine's.
# Deployment target 13.3, not Electron's 11.0: at this commit ggml calls
# cblas_sgemm from the new Accelerate interface (macOS 13.3+) and a Metal 12.0+
# API without availability guards (compiler warnings, measured 12 Nov 2026), so
# an 11.0 binary would start on macOS 11-13.2 and crash on the first dictation.
# With 13.3 the loader refuses it there and the backend reports a failed start.
# Without any target the binary would only run on the build machine's macOS.
build_dir="$source_dir/build-metal"
jobs="$(sysctl -n hw.ncpu 2>/dev/null || echo 4)"
echo "[whisper] configuring and building whisper-server (Metal, arm64)"
cmake -S "$source_dir" -B "$build_dir" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_OSX_ARCHITECTURES=arm64 \
  -DCMAKE_OSX_DEPLOYMENT_TARGET=13.3 \
  -DGGML_METAL=ON -DGGML_METAL_EMBED_LIBRARY=ON \
  -DBUILD_SHARED_LIBS=OFF -DGGML_NATIVE=OFF \
  -DWHISPER_BUILD_TESTS=OFF
cmake --build "$build_dir" --config Release --target whisper-server -j "$jobs"

built="$build_dir/bin/whisper-server"
[ -x "$built" ] || die "build finished but $built is missing"

# ── Stage bin/ ──────────────────────────────────────────────────────────────
rm -rf "$bin_dir"
mkdir -p "$bin_dir"
cp "$built" "$bin_dir/whisper-server"
cp "$source_dir/LICENSE" "$bin_dir/LICENSE-whisper.cpp.txt"

# Self-contained means only system libraries: anything under @rpath or outside
# /usr/lib and /System would be missing inside the app bundle.
foreign="$(otool -L "$bin_dir/whisper-server" | tail -n +2 | awk '{print $1}' | grep -vE '^(/usr/lib/|/System/Library/)' || true)"
[ -z "$foreign" ] || die "whisper-server links non-system libraries:
$foreign"
# A missing import or a binary for the wrong OS fails here, not on the user's
# first dictation.
"$bin_dir/whisper-server" --help >/dev/null 2>&1 || die "whisper-server --help failed"

# ── Model ───────────────────────────────────────────────────────────────────
mkdir -p "$models_dir"
model_dest="$models_dir/$model_name"
if [ -f "$model_dest" ] && pinned_ok "$model_key" "$model_dest"; then
  echo "[whisper] model already in place and verified"
elif [ -n "$model_file" ]; then
  pinned_ok "$model_key" "$model_file" || die "$model_file does not match the pinned model digest"
  cp "$model_file" "$model_dest"
else
  url="$(pinned_value url "$model_key")"
  part="$model_dest.part"
  rm -f "$part"
  echo "[whisper] downloading $url"
  curl -L --fail --retry 3 -o "$part" "$url"
  # A mismatch is never retried: retrying only hands a tampered host more tries.
  if ! pinned_ok "$model_key" "$part"; then
    rm -f "$part"
    die "$model_key does not match its pinned digest; nothing was installed"
  fi
  mv -f "$part" "$model_dest"
fi

# ── Stamp: what was built, and the bytes that were installed ─────────────────
# Same format as build_whisper.ps1; fetch-whisper.js re-hashes every listed file.
{
  echo "commit=$commit model=$(pinned_value digest "$model_key")"
  (cd "$out_dir" && find . -type f ! -name '.built*' ! -name '*.part' | sed 's|^\./||' | LC_ALL=C sort) |
    while IFS= read -r rel; do
      echo "$rel sha256:$(shasum -a 256 "$out_dir/$rel" | awk '{print $1}')"
    done
} > "$stamp.tmp"
mv -f "$stamp.tmp" "$stamp"
echo "[whisper] done: $out_dir"
