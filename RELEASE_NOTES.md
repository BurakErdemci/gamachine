<!--
  ⚠️ THIS FILE IS MAINTAINED BY HAND and goes stale easily.
  `.github/workflows/release.yml` publishes it as the BODY of the draft release
  (`body_path: RELEASE_NOTES.md`). On 2 Aug 2026 it still held v2.2.0's text; had
  the release job run, v2.3.0 would have shipped the previous version's notes.
  ▶ BEFORE cutting a release: move the top section of `CHANGELOG.md` here.

  Written in English on purpose: the release page is where a stranger lands at the
  moment they download. (This comment is invisible on GitHub.)
  Images use absolute URLs pinned to the tag: a release body does not resolve
  repository-relative paths.
-->

## Gamachine v4.0.3

> 🍎 **macOS (Apple Silicon):** `Gamachine-4.0.3-arm64.dmg`
> · 🪟 **Windows:** `Gamachine-Setup-4.0.3.exe`

**Windows:** the installer is not code-signed, so SmartScreen warns on first run.
Click **More info**, then **Run anyway**.
**macOS:** the app is signed with a Developer ID and notarized by Apple, so it opens
without a warning.

**Coming from v3?** v3.2.0's update notice only opens the release page, so install
4.0.3 by hand. From 4.0.0 on, updates download inside the app and install with one
click, and only after the release checks out against our own signed update manifest.

---

A safety release on top of v4.0.2. Updating is recommended, above all if you use
Antigravity (agy). Everything in
[v4.0.2](https://github.com/BurakErdemci/gamachine/releases/tag/v4.0.2) is included.

### Approval modes
- **Two modes: Auto and Safe Auto.** Step by Step is gone. A saved Step by Step
  choice opens as Safe Auto, and so does an old phone page or desktop that still
  sends it. Without a UI secret (Docker, a reload worker) the mode is Safe Auto and
  cannot be changed, from the desktop or from the phone.

### Antigravity (agy)
- **Every agy tool call goes through Gamachine's gate.** v4.0.2 knew only agy 1.2.x's
  write tools, so on agy 1.5.x `write_file` and `edit_file` wrote files with no card
  in Safe Auto mode. The hook now sees every built-in call: reads pass, file writes
  get a card, commands go through the `unityai` bridge, and a tool it does not know
  is refused. If the gate's state cannot be read, the call is refused, not let
  through.
- **agy chats run at the same time.** A turn in one chat no longer waits for an agy
  turn in another chat to finish.

### Other CLI agents
- **Cursor runs in a home of its own** (`~/.unity_architect_ai/cursor-home`, with
  your existing chats linked in). Cursor CLI approves every MCP server it loads, so it
  now loads only Gamachine's servers and your project's `.cursor/mcp.json`, not the
  ones in your global `~/.cursor`. A project MCP server that reads files from your
  home folder sees this home instead. Measured on Windows; not yet measured on macOS.
- **OpenCode** denies every tool except reads and Gamachine's own servers. Your own
  deny rules in `opencode.json` are kept, per agent and per mode as well.
- **Kimi Code** denies its writing, command and agent-spawning built-ins, and a turn
  does not start until `config.toml` is read back with those rules in it (validated
  with `kimi doctor config`; the model's behaviour under them was not measured).
- **Claude Code one-shot runs** also deny the `PowerShell` tool, which could write
  files when only `Bash` was denied.
