# Releasing and update signing

The app updates itself from GitHub Releases (electron-updater). Windows builds are
not code-signed, so an update installs only when the release's update manifest
(`latest.yml`, `latest-mac.yml`) carries an Ed25519 signature made with OUR key.
Windows and macOS use separate key sets: `latest.yml` is signed with the Windows
keys, `latest-mac.yml` with the Mac keys (see "Why the Mac keys are separate").
The app checks the signature first and downloads the update only if the files
electron-updater is about to fetch match that signed manifest; the downloaded
file's sha512 is checked again before the install is offered. Otherwise nothing
is downloaded or installed and the user is offered the release page (once per
version). The private key is never in
CI, the repo, or a synced folder; the owner signs the draft release locally.

## One-time setup

1. Generate the primary key on the Windows PC (from `Frontend/frontend`):
   `node scripts/update-keygen.js C:\Users\<you>\gamachine-update-primary.pem`
   It asks for a passphrase twice (hidden, at least 12 characters), writes the
   encrypted private key (PKCS#8, aes-256-cbc) and prints the public PEM. It
   refuses a path inside the repo or inside OneDrive/Dropbox/iCloud/Google Drive,
   and never overwrites a file.
2. Generate the backup key the same way, preferably on another machine or an
   offline one, and copy only its public PEM out.
3. Paste both public PEMs into `Frontend/frontend/main/helpers/update-keys.ts`
   (`PRIMARY_UPDATE_PUBLIC_KEY`, `BACKUP_UPDATE_PUBLIC_KEY`) and ship a release
   that contains them. While both are empty the app refuses every update.
   The Mac keys are a second pair, generated the same way with their own files
   (e.g. `gamachine-update-mac-primary.pem`, `gamachine-update-mac-backup.pem`).
   Embed their public PEMs in `MAC_PRIMARY_UPDATE_PUBLIC_KEY` and
   `MAC_BACKUP_UPDATE_PUBLIC_KEY` in the same file. While both are empty macOS
   refuses every update ("imza anahtarı tanımlı değil") and shows the release-page dialog.
4. Storage: keep each encrypted key file and its passphrase in separate places
   (a password manager entry for the file, another for the passphrase). The
   backup key stays offline (USB stick in a drawer). Never put either in the
   vault, notes, a cloud-synced folder, CI secrets, or the repo.

## Each release

1. Bump the version and push a `v*` tag. `.github/workflows/release.yml` builds
   and creates a DRAFT release with the installers, blockmaps, `latest.yml` and
   `latest-mac.yml`.
   The macOS build waits behind the `release-signing` environment: approve it
   under Actions -> the run -> "Review deployments". Every attempt asks again,
   including a re-run. If macOS signing fails with "The timestamp service is not
   available" (Apple's timestamp server, transient), re-run only the failed jobs
   (`gh run rerun <run-id> --failed`) and approve again; the draft is created
   only after both builds pass.
2. On the Windows PC, with the GitHub CLI logged in (`gh auth status`), from
   `Frontend/frontend` (the script lives in `Frontend/frontend/scripts/`):
   `node scripts/sign-release.js v4.0.5 C:\Users\<you>\gamachine-update-primary.pem --mac-key C:\Users\<you>\gamachine-update-mac-primary.pem`
   It refuses a release that is not a draft, downloads both manifests, asks the
   passphrase of each key file once (hidden), prints each manifest's version and
   sha512 prefixes, and refuses a manifest whose version differs from the tag.
   `latest.yml` is signed with the first key and verified against
   `PRIMARY/BACKUP_UPDATE_PUBLIC_KEY`; `latest-mac.yml` is signed with the `--mac-key`
   key and verified against `MAC_PRIMARY/BACKUP_UPDATE_PUBLIC_KEY`. Both are
   verified BEFORE `latest.yml.sig` and `latest-mac.yml.sig` are uploaded
   (`--clobber`); a key that is not embedded for its manifest uploads nothing, and so
   does a Mac key that is also a Windows key. Check that the printed version and
   hashes are the release you built. `--mac-key` is required, a missing manifest
   fails the run, and any other option (long or short) is refused before any `gh` call.
   It also refuses an unencrypted key and a key path inside the repo or a synced folder.
3. Publish the draft in the GitHub UI. Sign BEFORE publishing: clients that
   see a published release without `.sig` files refuse it.

## Why the Mac keys are separate

4.0.0-4.0.3 install macOS updates through Squirrel.Mac, which reads the whole
1.37 GB zip into memory and crashes the app on "Restart now" (measured 25 Nov 2026,
4.0.0 -> 4.0.3, 3 of 3; electron/electron#55089). Those builds verify `latest-mac.yml`
with the Windows keys, so a Mac manifest signed with the separate Mac keys fails their
check: they show "the signature of the new version could not be verified, download it
manually from the release page" and never reach Squirrel. That dialog is by design, not
a bug. A Mac on 4.0.0-4.0.3 installs 4.0.4 (or later) once by hand from the dmg; from
4.0.4 on the app uses the Mac keys and its own bundle swap below.

Release procedure for 4.0.4 and later (nothing special per version):

1. Generate the Mac primary and backup keys once with `scripts/update-keygen.js`
   (one-time setup above) and embed their public PEMs in `MAC_PRIMARY_UPDATE_PUBLIC_KEY`
   and `MAC_BACKUP_UPDATE_PUBLIC_KEY`; the build that ships them is the first one that
   accepts a Mac manifest.
2. Build and draft the release as usual ("Each release" step 1).
3. Sign both manifests: `node scripts/sign-release.js <tag> <windows-key> --mac-key <mac-key>`.
4. Publish. Say in the release notes that Macs on 4.0.0-4.0.3 install once from the dmg.

## What the app does with an update

Nothing downloads or installs on its own. After the signed manifest checked out the app
downloads the update, hashes the file itself and only then offers "Şimdi yeniden başlat" /
"Sonra". Install-on-quit is armed only when "Sonra" is chosen for a file whose hash matched;
a file that does not match is deleted and nothing can install it later.

### macOS (4.0.4+): our own bundle swap, no Squirrel

`main/helpers/mac-install.ts`. electron-updater only checks and downloads; the app never
calls `quitAndInstall` or electron's `autoUpdater`, and replaces MacUpdater's
`updateDownloaded` so the download is not handed to Squirrel's local proxy either.

1. Before downloading, the app refuses (release-page dialog, once per version) when it
   cannot replace itself: bundle path unknown, running translocated (`/AppTranslocation/`),
   from a mounted image (`/Volumes/...`), or its folder is not writable (a non-admin user
   with the app in `/Applications`).
2. After the sha512 match the zip is unpacked with `ditto -x -k` into a hidden
   `.<App>-update-XXXXXX` folder next to the installed bundle (same volume, so the swap is
   a rename). The unpacked app must pass `codesign --verify --deep --strict`, carry
   TeamIdentifier `MG4KS3D497`, the verified version as `CFBundleShortVersionString`, the
   installed app's bundle id, and `spctl --assess --type execute`. Any failure removes the
   folder and shows the release-page dialog; only then is "Restart now / Later" offered.
3. On quit (`will-quit`, for both choices) a detached `/bin/bash` helper (`swap.sh`, written
   into the staging folder) waits up to 60 s for the app's pid to exit, renames the old
   bundle to `.<App>.app.backup-<pid>` in the same folder (and stops, changing nothing, if
   that path already exists), moves the new one in, removes `com.apple.quarantine`, and
   deletes the backup once the swap succeeded (with `relaunch=1` only after the new app was
   opened). If both the install rename and the restoring rename fail, the old app stays at
   the backup path and the normal path is empty (logged; not recoverable by the helper). It takes a `relaunch` argument:
   "Restart now" passes `1` (open the new app; on failure restore the old bundle and open
   it); "Later" (a normal quit) passes `0` (the swap happens but no app is opened, also not
   the old one after a failed swap). It logs to `~/Library/Logs/Gamachine/update-swap.log`
   (the app logs that path).
4. If the app quits while a staged update is not armed (the ready dialog still open, or
   "Restart now" still stopping the backend), the staging folder is removed synchronously
   at `will-quit` so no multi-GB hidden folder is left next to the app.
5. If MacUpdater cannot be overridden (a different electron-updater layout), the app logs an
   error and switches updating off for that run rather than let a download reach Squirrel.

## Rotating to the backup key

If the primary key is lost or exposed:
1. Sign releases with the backup key (`sign-release.js <tag> <backup-key-path> --mac-key <mac-key-path>`,
   using the Mac backup key for `--mac-key` when the Mac primary is the lost one).
   Installed apps already accept it because both public keys are embedded.
2. Generate a new backup key, replace the compromised public key in
   `update-keys.ts` with the old backup public key plus the new one, and ship
   that release signed with the backup key.
3. Apps that never received a build containing the backup public key cannot be
   rotated remotely; they keep refusing and show the release-page dialog.

## Differential download

`electron-builder.yml` and the app set no `disableDifferentialDownload`, and the
workflow uploads every `*.blockmap`, so electron-updater's differential download
stays on for the NSIS installer and the mac zip. electron-updater diffs only when its own
cache already holds the previous `update.zip` / installer, which it keeps only after an
update it downloaded itself; so the first update after a dmg or manual install is a full
download. electron-updater verifies the result against the sha512 the app already matched
to the signed manifest, and the app hashes the final file once more before offering the install.

## Not covered by tests

The real GitHub download, the `gh` upload and an actual NSIS install are only exercised
on a live test release: do one before the first public one. The macOS swap helper was run
for real on a copy of the installed 4.0.3 app with the signed 4.0.3 zip (success and
rollback, 25 Nov 2026); an app-initiated update end to end is first measurable 4.0.4 -> 4.0.5,
and non-admin users and the in-app `open` of the new bundle were not measured.
