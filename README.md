<a id="top"></a>
<div align="center">

<picture>
  <source media="(prefers-color-scheme: light)" srcset="docs/media/readme/banner-en-light.svg"/>
  <img src="docs/media/readme/banner-en.svg" alt="Gamachine v4: AI agents build your Unity game, then play it to check their work." width="100%"/>
</picture>

[![Python](https://img.shields.io/badge/Python-3.13-2C344B?style=for-the-badge&logo=python&logoColor=white&labelColor=141826)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-2C344B?style=for-the-badge&logo=fastapi&logoColor=white&labelColor=141826)](https://fastapi.tiangolo.com)
[![Electron](https://img.shields.io/badge/Electron-34-2C344B?style=for-the-badge&logo=electron&logoColor=white&labelColor=141826)](https://electronjs.org)
[![React](https://img.shields.io/badge/React-18-2C344B?style=for-the-badge&logo=react&logoColor=white&labelColor=141826)](https://react.dev)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.7-2C344B?style=for-the-badge&logo=typescript&logoColor=white&labelColor=141826)](https://typescriptlang.org)
[![Unity MCP](https://img.shields.io/badge/Unity_MCP-Embedded-FF6B3D?style=for-the-badge&logo=unity&logoColor=white&labelColor=141826)](./unity-mcp)
[![License](https://img.shields.io/badge/License-MIT-2CC5B4?style=for-the-badge&labelColor=141826)](LICENSE)

**English** · [Türkçe](./README_TR.md)

</div>

<br/>

<img src="docs/media/readme/stats-en.svg" alt="55 Unity Editor tools · 6 playtest tools · 7 subscription CLIs and 9 API providers · 5 themes" width="100%"/>

<br/>

<img src="docs/media/readme/hero-playtest.webp" alt="Gamachine with Unity connected: in the middle of the chat, the agent's own capture of the game it is playing in the Unity Editor" width="100%"/>

<p align="center"><sub>A real session: the agent started a play session in the Unity Editor and posted what the game camera saw mid-fight.</sub></p>

Gamachine is a desktop app for Windows and macOS that puts AI coding agents (Claude Code,
Codex, Antigravity, Copilot, your own API keys) in one chat, wired to your open Unity Editor.
In v4 the agent does more than write code: it **plays the game in the real Editor**, looks at
what it built, and several chats work on the same project side by side.

<div align="center">

<br/>

<a href="https://github.com/BurakErdemci/gamachine/releases/latest"><img src="docs/media/readme/download-en.svg" alt="Download the latest release" height="76"/></a>

| Platform | File |
|---|---|
| **Windows 10/11** (x64) | `Gamachine-Setup-<version>.exe` |
| **macOS** (Apple Silicon) | `Gamachine-<version>-arm64.dmg` |

<sub>**Windows:** the installer is not code-signed, so SmartScreen warns on first run:
*More info* → *Run anyway*. No Intel Macs, no Linux.
Building from source? See [Installation](#installation).</sub>
<!-- MAC-SIGNING-TBD: add one line here once the macOS signing state of the release is settled (signed and notarized, or right-click → Open). -->

</div>

<br/>

<a id="playtest"></a>
<img src="docs/media/readme/h-plays-en.svg" alt="01 · Playtest: the agent plays your game" width="100%"/>

<img src="docs/media/readme/playtest-fix.webp" alt="The agent works on a reported bug with a frame of the running game in the chat, then reports: two-line fix, clean compile, same sequence replayed in Unity, fix committed" width="100%"/>

Six playtest tools let an agent enter Play Mode in the **real Unity Editor**, step the game
frame by frame while pressing keys and buttons, and get the frame back as an image. Here it
takes a playtester's report on a game in development, changes two lines, confirms a clean
compile, replays the same sequence and commits.

<sub>Also: background runs of up to 30 game-minutes across up to 50 seeds, hooks the game
declares with `[GameHook]`, and `*.playtest.json` scenarios with pass/fail.
Needs Unity's Input System package. A bot playing shows the game runs, not that it is fun.</sub>

<br/>

<a id="live-unity"></a>
<img src="docs/media/readme/h-editor-en.svg" alt="02 · Live Unity: it works inside the open Editor" width="100%"/>

<img src="docs/media/readme/unity-language-switch.webp" alt="The request asks the agent to make the end card follow the language switch and show it in both languages; below it, the end card captured from the running game in English and in Turkish" width="100%"/>

**55 Unity Editor tools** over MCP: scenes, GameObjects, prefabs, materials, scripts, the
console. Asked to make an end card follow the language switch, the agent checked the compile
result and showed the card in both languages, captured from the running game.

<sub>"Compiled clean" now means it compiled: a `compile_status` verdict, not an empty console.
Every agent action is one named Undo step, and raw `.meta` or scene-YAML writes are refused
in every mode, so asset GUIDs stay intact.</sub>

<br/>

<a id="parallel-chats"></a>
<img src="docs/media/readme/h-chats-en.svg" alt="03 · Parallel: several chats, one project" width="100%"/>

<img src="docs/media/readme/parallel-chats.webp" alt="Sidebar with an active task running while three other chats are done; the running chat's answer arrives and it moves into the chat list" width="100%"/>

Chats run in parallel, each with its own stream, Stop button and queue; writes to one Editor
are taken in turn. Branch a chat into a tab, ask a read-only side question, or let one chat
leave a note that wakes another:

<p align="center"><img src="docs/media/readme/chat-note.webp" alt="A chat showing a note that arrived from another chat, and the agent replying to it" width="80%"/></p>

<br/>

<a id="blender"></a>
<img src="docs/media/readme/h-blender-en.svg" alt="04 · 3D work: Blender, too" width="100%"/>

<table>
<tr>
<td width="58%"><img src="docs/media/readme/blender-trophy.webp" alt="The agent posts a work-in-progress look of a low-poly trophy modelled in Blender, then the finished look and the final 256×256 render, with its summary"/></td>
<td width="42%"><img src="docs/media/readme/connectors.webp" alt="Settings, Connectors page: a Blender card with an Install button, and the Terminal agents switch"/></td>
</tr>
<tr>
<td>Blender work done by the agent: a low-poly trophy for the game's end card, shown in the chat as it took shape, then rendered to a transparent PNG.</td>
<td><b>Settings › Connectors</b> installs the Blender add-on (Blender 4.2+): the agent then reads the scene, runs Python in it and looks at the result.</td>
</tr>
</table>

<br/>

<a id="projects"></a>
<img src="docs/media/readme/h-hub-en.svg" alt="05 · Projects: a small Unity Hub on the first screen" width="100%"/>

<p align="center"><img src="docs/media/readme/projects-new-game.webp" alt="Projects screen: the template cards switch between URP Blank and 2D, and the project name is typed into the save slot" width="86%"/></p>

Open a project, launch Unity, or create one with the editor and template you pick. You can
also install a Unity version and its modules from here; it uses Unity Hub 3.21+ and never
logs in to Unity for you.

<br/>

<a id="providers"></a>
<img src="docs/media/readme/h-ai-en.svg" alt="06 · Providers: bring your own AI" width="100%"/>

<table>
<tr>
<td width="50%"><img src="docs/media/readme/providers.webp" alt="Settings, Models and accounts: subscription CLIs with sign-in state, and the API key providers Anthropic, OpenAI, Google, DeepSeek, Kimi, GLM, NVIDIA, Groq and OpenRouter"/></td>
<td width="50%"><img src="docs/media/readme/mission-board.webp" alt="A fresh chat: the mascot says the project is open, a mission board offers four starter tasks, and the slash menu lists Compact and Guide"/></td>
</tr>
<tr>
<td><b>7 subscription CLIs</b> run with your own login (Claude Code, Codex, Antigravity, Copilot, Cursor, OpenCode, Kimi Code), plus <b>9 API providers</b> and local Ollama. Keys stay on your computer.</td>
<td>One work mode for every agent: <b>Safe Auto</b> (the default) lets routine work through and asks before critical, irreversible actions; <b>Step by Step</b> and <b>Auto</b> sit either side.</td>
</tr>
</table>

<sub>Works best with Claude Code, which is what we build and test Gamachine with. Kimi Code
has not been exercised end to end, and Ollama has only partial Unity control.</sub>

<br/>

<a id="themes"></a>
<img src="docs/media/readme/h-themes-en.svg" alt="07 · Appearance: make it yours" width="100%"/>

<img src="docs/media/readme/themes.webp" alt="Settings, Appearance: clicking the theme cards switches the whole app between Arena, Blueprint, Workshop and Plain" width="100%"/>

Five themes (Arena, Plain, Blueprint, Workshop, and Custom with your own colours and fonts),
an English or Turkish interface, a Guide screen and a six-step starter tour.

<br/>

<a id="also-in-v4"></a>
<img src="docs/media/readme/h-also-en.svg" alt="And more: also in v4" width="100%"/>

<table>
<tr>
<td width="50%" valign="top"><img src="docs/media/readme/i-computer.svg" width="44" align="left" alt=""/><b>Computer use</b><br/>Off by default. The agent takes screenshots, clicks and types in desktop apps you allow one by one; a live view shows it, Ctrl+Alt+Esc (Cmd+Shift+Esc) stops it. Tested most with Claude Code and Codex; not with Ollama.</td>
<td width="50%" valign="top"><img src="docs/media/readme/i-browser.svg" width="44" align="left" alt=""/><b>Agent browser</b><br/>Off by default. A built-in browser the agent can search, read, click and fill forms in; submitting, passwords and purchases need your approval. Annotate a page (Ctrl+Shift+E) and send the notes. <a href="docs/agent-browser.md">More</a></td>
</tr>
<tr>
<td width="50%" valign="top"><img src="docs/media/readme/i-phone.svg" width="44" align="left" alt=""/><b>Phone and web remote</b><br/>Off by default. Pair a phone with a QR code and a 4-digit check, then watch chats, approve cards, send messages and stop turns. End-to-end encrypted; the PC stays on. <a href="docs/remote-control.md">More</a></td>
<td width="50%" valign="top"><img src="docs/media/readme/i-terminal.svg" width="44" align="left" alt=""/><b>Terminal agents</b><br/>Claude Code, Codex and other CLI agents can list your chats, open one and message it through a local MCP server. <a href="docs/terminal-agents.md">More</a></td>
</tr>
<tr>
<td width="50%" valign="top"><img src="docs/media/readme/i-scene.svg" width="44" align="left" alt=""/><b>Scene editor</b><br/>An optional Hierarchy and Inspector for the open scene, for you rather than the agent. No 3D view.</td>
<td width="50%" valign="top"><img src="docs/media/readme/i-mic.svg" width="44" align="left" alt=""/><b>Dictation</b><br/>Whisper runs on your computer; nothing is sent until you press Enter, and no audio leaves the machine. The Windows installer is about 1.3 GB because the model ships inside it. <!-- MAC-DICTATION-TBD: state macOS availability here once settled. --></td>
</tr>
<tr>
<td width="50%" valign="top"><img src="docs/media/readme/i-models.svg" width="44" align="left" alt=""/><b>Models</b><br/>Claude Fable 5.2, Opus 5.5, Sonnet 5.5, Haiku 5.5; GPT-6 Luna and Sol, GPT-6.1 Sol and gpt-6.1-terra for Codex; Gemini 4 Argon through agy 1.4.0.</td>
<td width="50%" valign="top"><img src="docs/media/readme/i-image.svg" width="44" align="left" alt=""/><b>Images and @</b><br/>Agents can put an image in the chat with <code>show_image</code>; typing <code>@</code> lists your other chats so the agent can leave one a note.</td>
</tr>
</table>

Full list: [CHANGELOG.md](CHANGELOG.md). Known limits: [release notes](RELEASE_NOTES.md).

<br/>

<br/>

<a id="documentation"></a>
<img src="docs/media/readme/h-docs-en.svg" alt="Documentation" width="100%"/>

<table>
<tr><td><a href="docs/engineering-notes.md"><b>Engineering notes</b></a></td><td>The decisions, the mistakes, and what they cost</td></tr>
<tr><td><a href="docs/architecture.md"><b>Architecture</b></a></td><td>Process layout, agentic loop, tool layer, SSE stream</td></tr>
<tr><td><a href="docs/security.md"><b>Approval &amp; security</b></a></td><td>What is gated, what deliberately isn't, and why</td></tr>
<tr><td><a href="docs/unity-mcp.md"><b>Unity MCP integration</b></a></td><td>The Editor tools, the tool list per URL, playtesting and the input system</td></tr>
<tr><td><a href="docs/providers.md"><b>Supported providers</b></a></td><td>Every CLI agent, cloud API and local model</td></tr>
<tr><td><a href="docs/building.md"><b>Building from source</b></a></td><td>Dev setup and producing a dmg / installer</td></tr>
</table>

<br/>

<a id="installation"></a>
<img src="docs/media/readme/h-install-en.svg" alt="Installation" width="100%"/>

**Using the app:** download it from
[Releases](https://github.com/BurakErdemci/gamachine/releases/latest), then add an API key or
sign in to a CLI agent you already use. The chat stays locked until one is connected, and we
never install a third-party AI tool without telling you. Python, `uv`, OmniSharp, the .NET
SDK and ffmpeg/yt-dlp are bundled.

**From source:** you need Python 3.13+ and Node.js 20+ (plus the Unity Editor, 2021.3 or
newer, for the Unity integration). Full steps, environment variables and packaging are in
[Building from source](docs/building.md).

**The previous version (v3)** stays on the
[`legacy-v3`](https://github.com/BurakErdemci/gamachine/tree/legacy-v3) branch, as it was,
with no new features.

<br/>

<a id="contributing"></a>
<img src="docs/media/readme/h-contrib-en.svg" alt="Contributing" width="100%"/>

Three test suites guard the code:

```bash
# Backend
cd Backend && pytest
#   Windows: venv\Scripts\python.exe -m pytest   (env: PYTHONUTF8=1)

# unity-mcp server
cd unity-mcp/Server && pytest

# Frontend + the TypeScript gate
cd Frontend/frontend && npm test && npx tsc --noEmit
```

CI (`.github/workflows/test.yml`) runs five jobs: backend tests, a black-box contract suite,
unity-mcp server tests, the frontend gate (tsc + vitest) and a PowerShell syntax check. The
release build waits for all of them.

1. Fork the repo
2. Create a feature branch (`git checkout -b feat/amazing-feature`)
3. Run the tests
4. Open a pull request

<br/>

<a id="developer"></a>
<img src="docs/media/readme/h-dev-en.svg" alt="Developer" width="100%"/>

**Burak Emre Erdemci**

An open-source portfolio and research project for developers who want to change how Unity
games are made with AI.

<br/>

<a id="license"></a>
<img src="docs/media/readme/h-license-en.svg" alt="License" width="100%"/>

**[MIT](LICENSE)**: free, with no licence key and nothing to buy.

- ✅ **Use it** for personal, educational, research or commercial work
- ✅ **Study, modify, fork and redistribute it**
- ✅ **Sell the games you make with it**: what you create is yours

You pay your own AI provider. For third-party component licences, see
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) (the bundled FFmpeg is LGPL-3.0 on Windows
and GPL-3.0 on macOS/Linux, and is only ever run as a separate process).

Reporting a vulnerability: [SECURITY.md](SECURITY.md) ·
Contributing: [CONTRIBUTING.md](CONTRIBUTING.md)

<br/>

<a id="trademarks"></a>
<img src="docs/media/readme/h-tm-en.svg" alt="Trademarks" width="100%"/>

This project is **not affiliated with, sponsored by, or endorsed by** Unity
Technologies. "Unity" and "Unity Technologies" are trademarks or registered
trademarks of Unity Technologies or its affiliates in the U.S. and elsewhere.
Likewise "Unreal Engine" is a trademark of Epic Games, "Godot" of the Godot
Foundation, "Claude" of Anthropic, "Codex" and "GPT" of OpenAI, "Gemini" and
"Antigravity" of Google, "GitHub Copilot" of GitHub, "Cursor" of Anysphere, and
"Blender" of the Blender Foundation. These names are used only to **describe**
compatibility.

Gamachine is not endorsed or authorised by Unity; if you have accepted Unity's
[Terms of Service](https://unity.com/legal/terms-of-service), you remain bound by them,
including Section 17.2 on AI-agent access.

<br/>

<p align="center"><a href="#top"><img src="docs/media/readme/footer.svg" alt="Gamachine v4, back to top" width="100%"/></a></p>
