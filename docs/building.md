# Building from source

Running the backend and frontend for development, and producing a distributable
dmg or installer. Nothing here is needed to *use* the packaged app.

---

## ⚙️ Installation

### Requirements
- Python 3.13+
- Node.js 20+
- Unity Editor (for Unity MCP, optional)

> None of these are needed for the packaged app — Python, uv, OmniSharp, the .NET SDK, ffmpeg/yt-dlp and the dictation engine are all bundled. These requirements are for **developing from source** only.

### Backend

```bash
cd Backend
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Dev server
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

> You enter API keys from the in-app **Settings** screen (stored encrypted), not via `.env`. If you run the backend standalone (without Electron), `LOCAL_APP_TOKEN` stays empty → token checks are skipped (dev mode).

### Frontend

```bash
cd Frontend/frontend
npm install          # postinstall builds node-pty automatically
npm run dev          # development
```

### Environment variables (optional)

```env
DB_PATH=~/.unity_architect_ai/unity_master_v3.db
HOST=127.0.0.1
PORT=8000                      # Electron picks a random free port; set this for a fixed one
API_KEY_ENCRYPTION_KEY=        # if empty, a file-based key is generated
```

---

## 📦 Packaging (dmg / exe)

A distributable app is produced in four steps (none of the bundled binaries are committed to git; they are fetched before each build):

```bash
# 1) Download the uv toolchain for Unity MCP
#    macOS: downloads both architectures (arm64 + x64)
bash Backend/vendor/fetch_uv.sh
#    Windows: pwsh Backend/vendor/fetch_uv.ps1

# 2) Download OmniSharp + the bundled .NET SDK (code intelligence; bundled on all three platforms)
python3 scripts/fetch_omnisharp.py

# 3) Download the video tools (ffmpeg + yt-dlp — the video→chat feature)
bash Backend/vendor/fetch_video_bins.sh

# 4) Windows: build the dictation engine (whisper.cpp + model) — see below.
#    `npm run build` runs it by itself through its prebuild hook; running it
#    here first only moves the ~2 minute compile out of the packaging step.
#    powershell -File Backend/vendor/build_whisper.ps1

# 5) Compile the backend with PyInstaller, then package the Electron app
cd Backend && ./build_backend.sh        # Windows: build_backend.bat
cd Frontend/frontend && npm install && npm run build
```

Outputs land under `Frontend/frontend/build/`:
- macOS: `Gamachine-<version>-arm64.dmg` (Apple Silicon)
- Windows: NSIS installer (`.exe`)

> ⚠️ **The x64 dmg trap:** electron-builder produces dmgs for both architectures, but the backend binary is only compiled for the host architecture — an x64 dmg built on Apple Silicon **won't work on Intel Macs**. Proper Intel support requires compiling the backend with an x64 Python separately.

> 🍎 **macOS quarantine note:** the dmg is unsigned; if macOS reports it as "damaged" after downloading, clear the quarantine flag with `xattr -cr "/Applications/Gamachine.app"`.

The packaged app does **not** require Python or .NET — the backend is a single frozen binary; the `mcp-server` and `unityai` subcommands are invoked through that same binary. `uvx`, OmniSharp (+.NET), ffmpeg/yt-dlp and the dictation engine are all embedded.

### Dictation engine (whisper.cpp)

Dictation runs `whisper-server` from [whisper.cpp](https://github.com/ggml-org/whisper.cpp)
with the Whisper large-v3-turbo model quantised to q8_0 (owner decision, 28 Sep 2026;
measurements in the vault note `Gamachine_Dikte_2026-09-27`). Nothing is downloaded
at runtime: the installer carries the server, its DLLs and the 874 MB model
(installer ≈ 1.3 GB).

`Backend/vendor/build_whisper.ps1` produces `Backend/vendor/whisper/`, which is
git-ignored and shipped by `electron-builder.yml` as `resources/whisper/`:

| Output | What |
|---|---|
| `bin/whisper-server.exe`, `whisper.dll`, `ggml.dll`, `ggml-base.dll` | the server, built from commit `d09f61a708f3487afa956ff578e60eae5e7a233c` |
| `bin/ggml-vulkan.dll` | the GPU backend as a separately loaded module — without a Vulkan driver the server falls back to the CPU on its own (`no GPU found`) |
| `bin/ggml-cpu-*.dll` | one CPU variant per instruction set; ggml picks the best at startup |
| `bin/msvcp140.dll`, `vcruntime140.dll`, `vcruntime140_1.dll`, `vcomp140.dll` | app-local VC++ runtime (OpenMP included), copied from the Visual Studio redist folder |
| `bin/LICENSE-whisper.cpp.txt` | upstream MIT license text |
| `models/ggml-large-v3-turbo-q8_0.bin` | pinned in `scripts/pinned_assets.json` (Hugging Face revision + SHA-256), verified before it is placed |
| `.built` | stamp: commit, model digest and the SHA-256 of every file above; `scripts/fetch-whisper.js` skips the build when it still matches the tree |

What the script needs on the build machine:

- **Visual Studio 2022 or newer with the C++ workload** — its bundled CMake and Ninja
  are used (found through `vswhere`), plus `vcvars64.bat` for MSVC.
- **Vulkan SDK 1.4.357.0** (for `glslc`, which compiles the shaders baked into
  `ggml-vulkan.dll`; nothing from the SDK ships). It is looked up at `%VULKAN_SDK%`
  or `C:\VulkanSDK\1.4.357.0`. `-InstallVulkanSdk` downloads the pinned installer,
  verifies it and installs it silently — the release workflow does this.
- **git** and **python** (for `scripts/pinned_assets.py`).

The source is fetched into a short temporary path (`%TEMP%\gm-whisper-d09f61a7`,
override with `-SourceDir`): the Vulkan shader generator can exceed the 260-character
path limit in a deep folder. The script refuses a checkout that is not exactly the
pinned commit or that has local modifications. The build flags are the measured ones:

```
cmake -G Ninja -DCMAKE_BUILD_TYPE=Release -DGGML_VULKAN=ON -DGGML_BACKEND_DL=ON \
      -DGGML_CPU_ALL_VARIANTS=ON -DGGML_NATIVE=OFF -DBUILD_SHARED_LIBS=ON -DWHISPER_BUILD_TESTS=OFF
```

To skip the 874 MB download when you already have the file, pass
`-ModelFile <path>` (or set `GAMACHINE_WHISPER_MODEL_FILE`); it is verified against
the same pinned digest.

**The prebuild hook fails a Windows build without the engine** — an installer
without it would break the product rule. To package without dictation on purpose
(no toolchain on this machine), set `GAMACHINE_ALLOW_NO_DICTATION=1`; the app then
answers the mic button with "speech recognition files are missing".

At runtime the backend (`Backend/app/providers/stt_whisper.py`) starts one server
on the first dictation, on a free loopback port and behind a random request path
(the server has an unauthenticated `/load` endpoint and answers any origin), with
`-bs 5 -bo 5 -nlp -t <physical cores>`. On Windows the child is tied to the
backend with a kill-on-close job object, so it cannot outlive a crashed backend.
For development without packaging, `GAMACHINE_WHISPER_DIR` points the backend at
any folder with the same `bin/` + `models/` layout.

#### macOS (not implemented)

The Windows build does not apply as is. What a macOS build would need:

- Build the same commit with **Metal** instead of Vulkan:
  `-DGGML_METAL=ON -DGGML_METAL_EMBED_LIBRARY=ON` (the shader library embedded in the
  binary, so nothing has to sit next to it), `-DBUILD_SHARED_LIBS=OFF` (one
  self-contained `whisper-server`), `-DGGML_NATIVE=OFF`; for both architectures
  either `-DCMAKE_OSX_ARCHITECTURES="arm64;x86_64"` or two builds. Needs Xcode's
  command line tools; no Vulkan SDK, no VC++ runtime.
- A `build_whisper.sh` producing the same `bin/` + `models/` layout; the resolver in
  `stt_whisper.py` already looks for `whisper-server` without `.exe`.
- Signing: the dmg is unsigned today, but a hardened-runtime build would have to sign
  the extra executable, and the microphone description in `electron-builder.yml`
  (`NSMicrophoneUsageDescription`) is already in place.
- Measure it before shipping: the GPU/CPU detection reads the server's startup log
  (`using … backend` / `no GPU found`) and was only measured on Windows + Vulkan.

Until then `scripts/fetch-whisper.js` warns and the dmg ships without dictation.

---

## Running the backend in Docker (development only)

For working on the Python side without installing Python 3.13 and building a
venv. The Electron app still runs on the host; only the backend moves into a
container.

```bash
# One token, shared by both sides — see below for why this is the whole trick.
export LOCAL_APP_TOKEN=$(python -c "import uuid; print(uuid.uuid4())")
# Required, with no default, and it MUST be absolute: the project the agent
# will work on. A relative path resolves against the Compose project directory
# for Compose and against Frontend/frontend for Electron — the same string then
# names two different folders, so the app refuses it rather than guess.
export GAMACHINE_WORKSPACE=/absolute/path/to/your/unity/project
# Linux only: run as your own uid so the container can write the bind mount.
export GAMACHINE_UID=$(id -u)

docker compose up --build            # first build installs every wheel: minutes

cd Frontend/frontend && npm run dev:docker
```

Compose refuses to start if either `LOCAL_APP_TOKEN` or `GAMACHINE_WORKSPACE` is
unset, and the Electron side throws with the same instruction. Neither will hand
you a container that looks healthy and then rejects everything.

Backend source is bind-mounted read-only with autoreload on, so editing a file
on the host restarts the server in the container. Changing `requirements.txt`
still needs `docker compose up --build`.

### The one thing that makes this work

Electron mints a random `LOCAL_APP_TOKEN` per launch and passes it to the
backend **process it spawns**. In Docker mode it spawns nothing, so the
container has to be told the same secret from outside — hence the exported
variable that both Compose and the Electron process read. This is the reason
Docker mode never worked before 31 Aug 2026: Compose set
`REQUIRE_LOCAL_APP_TOKEN=1` and never supplied a token, so every request was
rejected.

The environment variable is honoured **only** when `USE_DOCKER_BACKEND=true`, so
the normal path keeps a fresh random token per launch.

At startup the app now calls an authenticated `/health/auth` as well as the
public `/health`. Reaching `/health` only proves *a* backend is listening; a
container left running by `restart: unless-stopped` outlives the shell that
exported its token and answers it happily, then rejects every real call.

### Changing `GAMACHINE_WORKSPACE` — `down` first, always

```bash
docker compose down                  # ← not optional
export GAMACHINE_WORKSPACE=/absolute/path/to/the/other/project
docker compose up
```

A bind source is fixed when the container is **created**, and `restart:
unless-stopped` keeps the old container alive across the shell that started it.
So `docker compose up` on its own reuses the existing container with the *old*
mount. Exporting the new variable changes what the Electron app believes is
mounted and nothing about what actually is.

Before 31 Aug 2026 that combination was silent and expensive (FINDING D4-03):
you exported a new workspace, relaunched, selected project B, the app mapped it
onto `/workspace` successfully — and the live backend read and wrote project A.
The editor showed one project while every agent, command and backend file tool
worked in another, both halves reporting success.

The app now refuses to start in that state. At startup it compares a fingerprint
of the host tree it is configured for against one the backend computes over its
own `/workspace` (authenticated `GET /health/workspace`), and the error names
`docker compose down` as the fix.

Be clear about what that check is worth. A **mismatch** is strong: the two
directories do not have the same layout, so they are not the same tree. A
**match** is much weaker — it compares entry names and kinds two levels deep, so
two copies of one project, or two untouched projects from the same template,
look identical to it, and file contents are never read at all. It reliably
catches "you forgot to recreate the service"; it is not a proof of identity.

It is read-only on purpose: a marker file would be conclusive on every platform,
but a Unity project reacts to new files — the Editor's asset importer picks them
up. Comparing inode numbers would also be conclusive on a Linux bind mount, but
Docker Desktop synthesises them, so it would reject the *correct* tree on macOS
and Windows. None of this runs when `USE_DOCKER_BACKEND` is unset: the check
lives on the Docker startup path only, so the ordinary path makes no extra
request and pays no extra startup cost.

### Where the token actually lives — read this before treating it as ephemeral

Per-launch randomness is not the same as "never persisted", and the code has
always persisted it:

- `Backend/app/main.py` writes it to a `0600` file on **every** path — the
  container's `/root`-equivalent home in Docker mode, your own home
  (`~/.unity_architect_ai/local-app-token`) on the normal path. It has to: the
  MCP server is started by the CLIs and does not inherit our environment.
  The file survives until a later backend start overwrites or removes it.
- In Docker mode the value is additionally part of the service definition, so
  anything that can inspect the running service can read it, and
  `docker compose config` prints it in the clear.

None of that is new to Docker mode except the last point, but an earlier version
of this document claimed the token was "never written to disk", which was simply
false. Treat it as a local secret with a real lifetime, not a value that dies
with the process.

### What does not work in the container, and why

| | Works | Why |
|---|---|---|
| Cloud API providers (Gemini, Anthropic, OpenAI, DeepSeek…) | ✅ | plain HTTP, nothing local needed |
| File tools | ✅ | the app translates the selected folder to `/workspace` before the backend sees it; the backend can reach that tree and nothing else |
| Picking a folder **outside** `GAMACHINE_WORKSPACE` | ❌ | Docker mounts exactly one tree, so the backend has no name for anything else. The app says so and does not save it — it will not let the editor work in one project while the agents work in another |
| Editing backend code | ✅ | source is mounted read-only with autoreload |
| Subscription CLIs (Claude Code, Codex, Cursor, Copilot, Kimi, agy) | ❌ | installed on your machine and signed in as you; neither the binaries nor the sessions exist in the image |
| Unity MCP | ⚠️ | the Editor runs on the host, so the container reaches it through `host.docker.internal` (`UNITY_MCP_URL` overrides it) |
| OmniSharp / C# analysis | ❌ | not installed in the image |

So: use Docker mode to work on backend code with an API model. It is not a way
to run the product.

`Backend/tests/test_docker_contract.py` guards this contract. Where Compose can
answer, it asks Compose — the resolved model, not the file text — because the
first version of that file asserted on raw text and an audit showed every
assertion could stay green while the property it named was false.

---

---

[← Back to the README](../README.md)
