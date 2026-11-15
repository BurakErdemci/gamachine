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

## Gamachine v4.0.1

> 🍎 **macOS (Apple Silicon):** `Gamachine-4.0.1-arm64.dmg`
> · 🪟 **Windows:** `Gamachine-Setup-4.0.1.exe`

**Windows:** the installer is not code-signed, so SmartScreen warns on first run.
Click **More info**, then **Run anyway**.
**macOS:** the app is signed with a Developer ID and notarized by Apple, so it opens
without a warning.

**Coming from v3?** v3.2.0's update notice only opens the release page, so install
4.0.1 by hand. From 4.0.0 on, updates download inside the app and install with one
click, and only after the release checks out against our own signed update manifest.

---

A maintenance release on top of v4.0.0.

### Providers
- **Antigravity (agy): Gemini 4 models.** The Gemini 4 tiers are picked with the
  effort selector next to the model, and Orchestrator (Carbon) is in the list. A
  model your subscription does not include now says so ("your subscription does not
  support this model") instead of showing agy's raw unknown-model error, in normal
  and side turns. Saved Gemini tier choices move to the new selector by themselves.
- **Kimi Code.** Gamachine now runs the current Kimi Code CLI (2.5.x): the command
  line, the output it reads and the model names (`kimi-code/k3`,
  `kimi-code/kimi-for-coding` and their variants) follow it, and an older saved
  Kimi model moves to its new name. K3 models get Low/High/Max effort. Gamachine's
  tools reach Kimi through Kimi's own `mcp.json`, next to your own servers, which
  are kept; if Gamachine has to replace an entry under one of its names, your file
  is first copied to `mcp.json.gamachine-backup`. Outside Gamachine these entries
  stay silent. A turn that fails after Kimi has started answering now ends with an
  error instead of looking finished, and "not logged in" and "model not
  configured" get clear messages. Not yet tried with a logged-in Kimi account.
- **Cursor.** While Cursor's agent was updating itself, Gamachine could pick the
  half-unpacked update and fall back to a slower start path; it now uses only a
  complete installed version.

### Agent context
- The agent guide is sent once per chat session (and again after the provider
  compacts or loses its context), and the mode and Unity notes only when they
  change, instead of on every message. This shortens every turn for Claude, Codex,
  agy and the CLIs that resume a session.
- When a turn ends with an error, is cancelled or is stopped, the next message
  carries the guide, the notes and, on a first turn, the chat history again, so a
  failed turn can no longer leave the agent without the step-mode rule or the
  earlier conversation.

### Video
- A video dropped on the message box is attached like a picked one.
- At most five videos per message, also for restored drafts and taken-back
  messages.
- Video notices appear in the chat's language, and the guide topic says
  "captions" (the speech is not transcribed).
