<div align="center">

# Gamachine

**An agentic development studio that unifies every coding agent and live Unity control in a single desktop app**

[![Python](https://img.shields.io/badge/Python-3.13-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Electron](https://img.shields.io/badge/Electron-34-47848F?style=for-the-badge&logo=electron&logoColor=white)](https://electronjs.org)
[![React](https://img.shields.io/badge/React-18-61DAFB?style=for-the-badge&logo=react&logoColor=black)](https://react.dev)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.7-3178C6?style=for-the-badge&logo=typescript&logoColor=white)](https://typescriptlang.org)
[![Unity MCP](https://img.shields.io/badge/Unity_MCP-Embedded-7B2FBE?style=for-the-badge&logo=unity&logoColor=white)](./unity-mcp)
[![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)](LICENSE)

*Claude Code, Codex, Antigravity (agy), cloud APIs and live Unity Editor control — all in the same chat window, behind the same approval system, aware of the same project.*

[Türkçe README](./README_TR.md)

<br/>

![Gamachine Demo](docs/media/demo.gif)

*Develop with natural language in the code editor, then control the Unity Editor live — all from a single window.*

<sub>Curious *why* it is built this way? I kept a log of the architectural decisions —
including the wrong ones and what they cost →
**[Engineering notes](docs/engineering-notes.md)**</sub>

<br/>

### Download

**[⬇ Download the latest release](https://github.com/BurakErdemci/gamachine/releases/latest)**

| Platform | File |
|---|---|
| **Windows 10/11** (x64) | `Gamachine-Setup-<version>.exe` |
| **macOS** (Apple Silicon) | `Gamachine-<version>-arm64.dmg` |

Then bring your own AI: add an API key, or point it at a CLI agent you already
use. The chat stays locked until one is connected — **we do not install a
third-party AI tool on your machine without telling you.**

<sub>The builds are not code-signed yet, so the OS will warn you on first run:
Windows SmartScreen → *More info* → *Run anyway*; macOS → right-click → *Open*,
or `xattr -cr "/Applications/Gamachine.app"`. Intel Macs are not
supported. Prefer building from source? See [Installation](#-installation).</sub>

</div>

---

## 🎯 Under One Roof: What Does This Project Unify?

Today a Unity developer juggles multiple windows for different jobs: one CLI to generate code, a separate plugin to control the Editor, yet another app to chat. Each has its own approval logic, its own configuration, its own disconnected view of the project.

**Gamachine brings them all into a single application** — and puts them behind *one approval gate*. You pick a model from a dropdown; whether it's the Claude Code CLI, Codex, Antigravity (agy), GitHub Copilot CLI, or a direct cloud API — they all:

- run **in the same chat window**,
- see **the same project** as their workspace,
- show **the same file/terminal approval cards** (scope below: deletes and dangerous commands are gated on every path; code writes are gated on the CLI paths and deliberately ungated on the cloud API path),
- and, when enabled, drive **the same live Unity Editor** over MCP.

### Agent × Capability Matrix

| | Chat & Analysis | Write/Edit files (approved) | Terminal (approved) | Live Unity Editor control | Auth source |
|---|:---:|:---:|:---:|:---:|---|
| **Claude Code** (CLI) | ✅ | ✅ MCP | ✅ MCP | ✅ unityMCP — **gated** | your Anthropic subscription |
| **Codex** (CLI) | ✅ | ✅ MCP | ✅ MCP | ✅ unityMCP | your OpenAI subscription |
| **Antigravity / agy** (CLI) | ✅ | ✅ `unityai` bridge | ✅ bridge | ✅ unityMCP (HTTP) | your Google subscription |
| **GitHub Copilot** (CLI) | ✅ | ✅ MCP | ✅ MCP | ✅ unityMCP | your Copilot subscription |
| **Cursor** (CLI) | ✅ | ✅ MCP | ✅ MCP | ✅ unityMCP | your Cursor subscription |
| **OpenCode** (CLI) | ✅ | ✅ MCP | ✅ MCP | ✅ unityMCP | free / your own key |
| **Kimi Code** (CLI) | ✅ | ✅ MCP | ✅ MCP | ✅ unityMCP | your Moonshot subscription |
| **Cloud API** (Claude/GPT/Gemini/…) | ✅ | ✅ function calling | ✅ function calling | ✅ function calling | your API key |
| **Ollama** (local) | ✅ | ✅ (compatible models) | ✅ | ⚠️ partial | free, offline |

What this matrix delivers is simple but rare: **the experience is identical regardless of the source.** Switching from Codex to Claude Code is one dropdown; the diff viewer, terminal approval and Unity integration you're used to stay exactly the same.

> **Important distinction:** Approval cards appear for **file deletes and dangerous terminal commands** on every path; for **file writes** on the CLI agent paths, but **not on the cloud API / Ollama function-calling path**. For **live Unity scene operations** it depends on the provider: on the Claude path, unityMCP calls that *mutate* the scene now open a card; on Codex and agy they do not. Full table and rationale: [Approval scope](docs/security.md#️-approval-scope-what-is-and-isnt-confirmed-an-honesty-note).

---

## 🚀 Why Gamachine?

AI tools in the Unity ecosystem usually land at one of two extremes: they either just write code, or they just chat. Neither can do what a real development partner does on its own — understand the project, find the bug, fix it, test it in the terminal, and see it inside the Unity Editor.

| Traditional AI Assistants | Gamachine |
|---|---|
| Locked to a single provider | 7 CLI agents (Claude Code, Codex, agy, Copilot, Cursor, OpenCode, Kimi Code), 8+ cloud APIs (incl. the free NVIDIA NIM pool), Ollama — one menu |
| Writes code, doesn't see the project | Scans every `.cs` file in the workspace, extracts an architecture map |
| Can't touch the file system | Read/write/delete files — every dangerous op behind an approval card |
| Unaware of the Unity Editor | Adds GameObjects, binds components, reads the console via MCP |
| Can't run terminal | Secure terminal layer; dangerous commands require approval |
| Every chat starts from zero | Persistent memory + project analysis keep the context |
| Walks away when a background job finishes | The conversation wakes itself up and reports what completed |
| Setup hassle | `uv`, OmniSharp + .NET SDK, ffmpeg/yt-dlp, offline speech models — all **bundled into the app**, zero extra install |

---

## ✨ Features

**Many agents, one experience** — 7 CLI agents and 8+ cloud APIs plus local Ollama,
switchable per message. When a CLI is selected the backend writes that tool's MCP
config at call time, so nothing has to be set up by hand. Antigravity runs as one
long-lived session per conversation rather than a fresh process per turn.

**The model list is your account's, not ours** — each cloud provider is asked, with
your key, which models it will actually serve you, and a public catalogue supplies the
label, context size and price beside each name. A provider you have no key for still
shows up, marked unconfirmed, because "this model exists" and "your account has it"
are two different claims.

**Autonomous agentic loop** — task → think → call a tool → evaluate → repeat, stopped
by a progress guard (five identical tool/argument/result repeats) with a hard fuse at 300 steps. Every step streams live over SSE, and Stop cancels at both layers:
it cuts the stream *and* rejects the pending approval gates on the backend.

**One approval gate for all of them** — file writes open a side-by-side diff, deletes
show a content preview, terminal commands show the command. CLI agents and cloud APIs
go through the same UI. Exactly what is and isn't gated is written down in
[Approval scope](docs/security.md#️-approval-scope-what-is-and-isnt-confirmed-an-honesty-note).

**Live Unity Editor control, zero install** — 46 Editor tools over MCP: scenes,
GameObjects, prefabs, materials, physics, build settings. It can also
[play the game](docs/unity-mcp.md#-the-ai-can-now-play-the-game-manage_input): enter
play mode, send input, screenshot the result and judge what it built. The `uv`
toolchain ships inside the app.

**Project awareness** — every `.cs` file in the workspace is scanned and chunked;
"Learn Project" extracts classes, inheritance and key methods into an architecture
map. `/compact` summarises long conversations before they hit the token limit, and a
usage/context panel shows where you stand — with an honest "stale" or "estimate"
badge when the live number is not reachable.

**The conversation resumes itself** — a subagent or background command that finishes
after you stopped watching used to leave the chat idle until you typed. Now the
conversation wakes up, picks the turn back up and tells you what finished. It waits
while an approval card is on screen and stops after three wakes in a row.

**3D models and images open in place** — click an `.fbx`, `.glb`, `.gltf`, `.obj`,
`.stl`, `.ply` or `.dae` in the file tree and it opens in a preview instead of the
text editor: orbit the camera, scrub the timeline, play animations at 0.25x–2x. An
animation-only file gets a mannequin built from its own bones so there is something
to watch. Textures and sprites open in the same slot, with an actual-size mode that
keeps pixel art sharp.

**Dictation that never leaves the machine** — a microphone button in the chat box
writes what you say into the box while you are still speaking; you read it, fix it,
press Enter. Turkish and English recognition models ship inside the installer, so it
works offline and no audio is uploaded anywhere.

**C# intelligence, zero install** — an OmniSharp LSP sidecar gives real Roslyn
analysis in the Monaco editor, with the .NET SDK it needs bundled on all three
platforms. (The SDK rather than the runtime is a measured requirement, not a
preference — see [Architecture](docs/architecture.md).)

**Real effort control** — the effort selector actually takes effect on every
provider; the UI only offers the levels each model supports.

**Video → chat** — drop in a video link (YouTube included) or a file and bundled
ffmpeg + yt-dlp extract frames and a transcript into the analysis pipeline.

**A real IDE around it** — Monaco editor, an xterm.js terminal on a real PTY, diff
viewer, live thinking block, and a bilingual TR/EN interface.

---

## 📚 Documentation

| | |
|---|---|
| [Engineering notes](docs/engineering-notes.md) | The decisions, the mistakes, and what they cost |
| [Architecture](docs/architecture.md) | Process layout, agentic loop, tool layer, SSE stream |
| [Approval & security](docs/security.md) | What is gated, what deliberately isn't, and why |
| [Unity MCP integration](docs/unity-mcp.md) | The 46 Editor tools and the input system |
| [Supported providers](docs/providers.md) | Every CLI agent, cloud API and local model |
| [Building from source](docs/building.md) | Dev setup and producing a dmg / installer |

---

## ⚙️ Installation

**Using the app:** download it from
[Releases](https://github.com/BurakErdemci/gamachine/releases/latest) and add an API
key or point it at a CLI agent you already use. Python, `uv`, OmniSharp, the .NET SDK
and ffmpeg/yt-dlp are all bundled — nothing else to install.

**From source:** you need Python 3.13+ and Node.js 20+ (plus the Unity Editor if you
want the Unity MCP integration). Full steps, environment variables and packaging are
in [Building from source](docs/building.md).

---

## 💡 Usage

1. **Pick a workspace** — choose your Unity project folder; the backend scans the `.cs` files.
2. **Pick a model** — choose a provider/model in Settings. For a cloud API, enter your key (stored encrypted); for a CLI, just having it installed is enough.
3. **Talk** —

```
"Find the performance issues in PlayerController.cs"
"Create a ScriptableObject-based ItemData script for Inventory"
"NullReferenceException PlayerController.cs:47 — what's the cause, fix it"
"Add a Player capsule to the scene and attach a Rigidbody"   # when Unity MCP is on
/compact                                                      # summarize a long chat
```

4. **Approve** — when the AI requests a file/terminal operation the stream pauses and a diff/command card opens; approve or reject.
5. **Look and talk** — click a `.fbx` or a texture in the file tree to preview it in place; press the microphone to dictate instead of typing.

---

## 🤝 Contributing

The quality gate is three separate test suites — **~3,100 tests total**:

```bash
# Backend (~1,118 tests)
cd Backend && pytest
#   Windows: venv\Scripts\python.exe -m pytest   (env: PYTHONUTF8=1)

# unity-mcp server (~1,596 tests)
cd unity-mcp/Server && pytest

# Frontend (~396 tests) + the TypeScript gate
cd Frontend/frontend && npm test && npx tsc --noEmit
```

CI (`.github/workflows/test.yml`) runs four jobs: **Backend tests**, **unity-mcp Server tests**, **Frontend gate (tsc + vitest)** and a **PowerShell syntax check**. No release ships unless all four are green.

1. Fork the repo
2. Create a feature branch (`git checkout -b feat/amazing-feature`)
3. Run the tests
4. Open a pull request

---

## 👤 Developer

**Burak Emre Erdemci**

An open-source portfolio and research project for developers who want to fundamentally transform Unity development with AI.

---

## 📄 License

**[MIT](LICENSE)** — open source, with no strings attached:

- ✅ **Use it** — personal, educational, research, or commercial settings
- ✅ **Study, modify, fork and redistribute it**
- ✅ **Sell the games you make with it** — whatever you create is entirely yours

For third-party component licenses, see [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)
(The bundled FFmpeg's licence **differs per platform** — LGPL-3.0 on Windows,
GPL-3.0 on macOS/Linux — and in every case it is invoked only as a separate process.)

Reporting a vulnerability: [SECURITY.md](SECURITY.md) ·
Contributing: [CONTRIBUTING.md](CONTRIBUTING.md)

---

## Trademarks

This project is **not affiliated with, sponsored by, or endorsed by** Unity
Technologies. "Unity" and "Unity Technologies" are trademarks or registered
trademarks of Unity Technologies or its affiliates in the U.S. and elsewhere.
Likewise "Unreal Engine" is a trademark of Epic Games, "Godot" of the Godot
Foundation, "Claude" of Anthropic, "Codex" and "GPT" of OpenAI, "Gemini" and
"Antigravity" of Google, "GitHub Copilot" of GitHub, and "Cursor" of Anysphere.
These names are used only to **describe** compatibility.
