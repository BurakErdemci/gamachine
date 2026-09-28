# Third-Party Notices

Gamachine incorporates or redistributes the third-party components
listed below. Each remains under its own license; nothing in this project's
LICENSE alters those terms.

Components marked **[bundled]** ship inside released installers (`.dmg` / `.exe`).
Components marked **[fetched]** are downloaded at build time and are not stored
in this repository.

---

## Source incorporated into this repository

### MCP for Unity — `unity-mcp/`
Copyright (c) 2025 CoplayDev — **MIT License**
Original license retained at `unity-mcp/LICENSE`, which continues to govern that
directory. This project contains a modified fork; modifications are documented in
the project history.

---

## Bundled runtime components

### FFmpeg — **[bundled] [fetched]** — ⚠️ the license differs per platform
The bundled FFmpeg build is **not under the same license on every platform**. The
exact builds are pinned in `scripts/pinned_assets.json`; the table reflects those
pins. Do not collapse these rows into a single claim — each platform ships a
different binary, and pointing a recipient at the wrong license also points them
at the wrong corresponding source.

| Platform | Pinned build | License |
|---|---|---|
| Windows | BtbN `ffmpeg-n8.1.2-…-win64-**lgpl**-8.1.zip` | **LGPL-3.0-or-later** |
| macOS | evermeet.cx `ffmpeg-8.1.2` | **GPL-3.0-or-later** |
| Linux | johnvansickle `ffmpeg-7.0.2-amd64-static` | **GPL-3.0-or-later** |

The Windows binary carries `--enable-version3` **without** `--enable-gpl`, which is
why it is LGPL rather than GPL (verified 2026-08-08 by reading the configuration
string out of the shipped `ffmpeg.exe`). The macOS and Linux rows reflect the
upstream publishers' stated build configuration (`--enable-gpl --enable-version3`,
including libx264/libx265); those two binaries were **not** re-verified from a
packaged artifact on this machine.

- Windows builds: https://github.com/BtbN/FFmpeg-Builds/releases
- macOS builds: https://evermeet.cx/ffmpeg/
- Linux builds: https://johnvansickle.com/ffmpeg/
- FFmpeg project and complete corresponding source: https://ffmpeg.org/download.html
- License texts: https://www.gnu.org/licenses/gpl-3.0.html and
  https://www.gnu.org/licenses/lgpl-3.0.html

FFmpeg is redistributed **unmodified**, as a standalone executable, and is invoked
by this application only as a separate process (no linking against FFmpeg
libraries). This constitutes mere aggregation; it does not place Gamachine's
own code under the GPL or LGPL. Recipients of a build containing FFmpeg are
entitled to the corresponding source of FFmpeg for **their** platform's build,
available at the links above.

⚠️ This reasoning depends on all three conditions holding — unmodified, standalone,
separate process. Linking against the `libav*` libraries would break it and place
this project's own code under the FFmpeg build's license.

### yt-dlp — **[bundled] [fetched]**
**The Unlicense** (public domain dedication) — https://github.com/yt-dlp/yt-dlp

### whisper.cpp + ggml — **[bundled] [built from source]**
Copyright (c) 2023-2026 The ggml authors — **MIT License** — https://github.com/ggml-org/whisper.cpp

Built from commit `d09f61a708f3487afa956ff578e60eae5e7a233c`, unmodified, by
`Backend/vendor/build_whisper.ps1` (Vulkan backend as a separate module). What ships
in `resources/whisper/bin/`: `whisper-server.exe`, `whisper.dll`, `ggml.dll`,
`ggml-base.dll`, `ggml-vulkan.dll` and the `ggml-cpu-*.dll` variants. The upstream
license text is copied next to them as `LICENSE-whisper.cpp.txt`; ggml is part of
the same tree and the same copyright line. It runs as a separate process on a
loopback port; no audio leaves the machine.

### Whisper large-v3-turbo model (ggml q8_0) — **[bundled] [fetched]**
Copyright (c) 2022 OpenAI — **MIT License** — https://github.com/openai/whisper

The weights are OpenAI's Whisper large-v3-turbo, converted and quantised to
`ggml-large-v3-turbo-q8_0.bin` by the whisper.cpp project and published at
https://huggingface.co/ggerganov/whisper.cpp (model card license: MIT). The file is
pinned by repository revision and SHA-256 in `scripts/pinned_assets.json` and ships
in `resources/whisper/models/`.

### Microsoft Visual C++ runtime — **[bundled]** — ⚠️ Microsoft terms, not MIT
`msvcp140.dll`, `vcruntime140.dll`, `vcruntime140_1.dll` and `vcomp140.dll` (OpenMP)
are copied app-locally next to `whisper-server.exe` from the Visual Studio
`VC\Redist\MSVC\<version>\x64\Microsoft.VC*.CRT` and `...OpenMP` folders. They are
"Distributable Code" under the Visual Studio license terms, which permit
redistributing these files unmodified with an application.

### OmniSharp-Roslyn — **[bundled] [fetched]**
Copyright (c) OmniSharp — **MIT License** — https://github.com/OmniSharp/omnisharp-roslyn

### .NET SDK — **[bundled] [fetched]** — ⚠️ Microsoft proprietary terms, not MIT
OmniSharp needs a .NET toolchain, and what ships is the **SDK**, not merely the
runtime — measured 2026-08-08: `third_party/omnisharp/dotnet-win-x64/sdk/10.0.100/`.
Its root `LICENSE.txt` reads **"MICROSOFT SOFTWARE LICENSE TERMS — MICROSOFT .NET
LIBRARY"**. That is *not* the MIT license under which the `dotnet/runtime` **source**
is published, and this entry previously claimed MIT in error.

The license text ships alongside the binary at
`resources/omnisharp/dotnet-win-x64/LICENSE.txt`. Individual SDK components remain
under their own licenses (many of them MIT).

- Applicable terms: the bundled `LICENSE.txt`
- Source repository (MIT — covers the source, not these binaries):
  https://github.com/dotnet/runtime

### Electron — **[bundled]**
Copyright (c) Electron contributors / OpenJS Foundation — **MIT License** — https://github.com/electron/electron

### Chromium / Node.js (via Electron) — **[bundled]**
Chromium: BSD-3-Clause and others. Node.js: MIT.
Full notices are included in the Electron distribution.

---

## Application dependencies

### JavaScript / TypeScript (npm)
Includes Next.js, React, Tailwind CSS, Framer Motion, Monaco Editor, xterm.js,
node-pty, electron-builder, electron-updater, axios, lucide-react and their
transitive dependencies — predominantly **MIT**, with some **Apache-2.0** and
**BSD** components. See `Frontend/frontend/package.json` and the generated
`package-lock.json` for the authoritative list and versions.

### Python (PyPI)
Includes FastAPI, Uvicorn, Pydantic, anthropic, openai, google-genai, ollama and
their transitive dependencies — predominantly **MIT**, **BSD-3-Clause** and
**Apache-2.0**. See `Backend/requirements.txt` for the authoritative list.

### uv — **[fetched]**
Copyright (c) Astral — dual licensed **MIT** or **Apache-2.0** — https://github.com/astral-sh/uv

---

## External tools (not bundled)

The application can drive AI command-line tools that the user installs and
authenticates separately. These are **not** distributed with Gamachine
and remain subject to their own licenses and terms of service:

Claude Code (Anthropic), Codex CLI (OpenAI), Antigravity CLI (Google),
GitHub Copilot CLI, Cursor CLI, OpenCode, Kimi Code CLI (Moonshot AI).

---

## Reporting

If you believe an attribution is missing or incorrect, please open an issue on
the project repository.
