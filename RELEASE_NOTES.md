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

## Gamachine v4.0.2

> 🍎 **macOS (Apple Silicon):** `Gamachine-4.0.2-arm64.dmg`
> · 🪟 **Windows:** `Gamachine-Setup-4.0.2.exe`

**Windows:** the installer is not code-signed, so SmartScreen warns on first run.
Click **More info**, then **Run anyway**.
**macOS:** the app is signed with a Developer ID and notarized by Apple, so it opens
without a warning.

**Coming from v3?** v3.2.0's update notice only opens the release page, so install
4.0.2 by hand. From 4.0.0 on, updates download inside the app and install with one
click, and only after the release checks out against our own signed update manifest.

---

A model update on top of v4.0.1. Everything in
[v4.0.1](https://github.com/BurakErdemci/gamachine/releases/tag/v4.0.1) is included.

### Providers
- **Codex: GPT-6.2 Astra and GPT-6.2 Sol** are in the model list. They need
  Codex CLI 0.171.0 or newer (`npm install -g @openai/codex`).
- **Antigravity (agy): Gemini 4 Neon and Orchestrator (Neon).** Neon's Low, Medium,
  High and XHigh levels are picked with the effort selector next to the model;
  Orchestrator (Neon) plans with Neon and implements with Hydrogen. They need agy
  1.5.14 or newer (`agy update`) and a paid Google AI plan.
