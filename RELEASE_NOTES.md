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

## Gamachine v4.0.4

> 🍎 **macOS (Apple Silicon):** `Gamachine-4.0.4-arm64.dmg`
> · 🪟 **Windows:** `Gamachine-Setup-4.0.4.exe`

**Windows:** the installer is not code-signed, so SmartScreen warns on first run.
Click **More info**, then **Run anyway**.
**macOS:** the app is signed with a Developer ID and notarized by Apple, so it opens
without a warning.

**On a Mac with 4.0.0-4.0.3?** Install 4.0.4 by hand from the dmg, once: those versions
cannot verify 4.0.4's update signature and offer the release page instead, because their
own installer crashes on updates (see below).

**Coming from v3?** v3.2.0's update notice only opens the release page, so install
4.0.4 by hand. From 4.0.0 on, updates download inside the app, and only after the release
checks out against our own signed update manifest.

---

A macOS update fix on top of v4.0.3. Everything in
[v4.0.3](https://github.com/BurakErdemci/gamachine/releases/tag/v4.0.3) is included.

### Updates on macOS
- **Mac users on 4.0.0-4.0.3: install 4.0.4 by hand, once.** Download
  `Gamachine-4.0.4-arm64.dmg` and drag Gamachine to Applications. In those versions
  "Restart now" crashes the app instead of installing the update (Squirrel.Mac reads the
  whole 1.37 GB update into memory). Mac updates are signed with a separate key, so
  those versions cannot verify 4.0.4: they show "The new version’s signature could not
  be verified, so the update was not downloaded or installed." with a button to the release page instead of crashing. That dialog is
  expected; install from the dmg as described. 4.0.4 is the first version with the new
  Mac updater, so the first update that goes through it is 4.0.4 to 4.0.5.
- **The Mac app installs updates itself, without Squirrel.Mac.** After the signed
  manifest and the downloaded file's hash check out, the update is unpacked next to the
  installed app and must pass Apple's code signature check, carry Gamachine's developer
  ID and the expected version, and be accepted by Gatekeeper before "Restart now" or
  "Later" is offered. When the app quits, a small helper swaps in the new app and opens
  it ("Restart now"); if any step fails it puts the old app back and opens that instead.
  Choosing "Later" swaps the app when you quit and does not open it. The helper's log is
  `~/Library/Logs/Gamachine/update-swap.log`.
- **When the app cannot replace itself** (opened from the dmg, opened before being moved
  to Applications, or a folder your account cannot write to) nothing is downloaded and
  the release page is offered instead.

### Windows
- No change: updates install through the NSIS installer as before.
