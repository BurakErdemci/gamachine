# Releasing and update signing

The app updates itself from GitHub Releases (electron-updater). Windows builds are
not code-signed, so an update installs only when the release's update manifest
(`latest.yml`, `latest-mac.yml`) carries an Ed25519 signature made with OUR key
and the downloaded file matches that signed manifest. Otherwise nothing is
installed and the user is offered the release page. The private key is never in
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
4. Storage: keep each encrypted key file and its passphrase in separate places
   (a password manager entry for the file, another for the passphrase). The
   backup key stays offline (USB stick in a drawer). Never put either in the
   vault, notes, a cloud-synced folder, CI secrets, or the repo.

## Each release

1. Bump the version and push a `v*` tag. `.github/workflows/release.yml` builds
   and creates a DRAFT release with the installers, blockmaps, `latest.yml` and
   `latest-mac.yml`.
2. On the Windows PC, with the GitHub CLI logged in (`gh auth status`):
   `node scripts/sign-release.js v4.0.0 C:\Users\<you>\gamachine-update-primary.pem`
   It refuses a release that is not a draft, downloads both manifests, asks the
   passphrase (hidden), uploads `latest.yml.sig` and `latest-mac.yml.sig`
   (`--clobber`) and prints `OK` for each signature checked against
   `update-keys.ts`. Anything other than two `OK` lines: do not publish.
3. Publish the draft in the GitHub UI. Sign BEFORE publishing: clients that
   see a published release without `.sig` files refuse it.

## Rotating to the backup key

If the primary key is lost or exposed:
1. Sign releases with the backup key (`sign-release.js <tag> <backup-key-path>`).
   Installed apps already accept it because both public keys are embedded.
2. Generate a new backup key, replace the compromised public key in
   `update-keys.ts` with the old backup public key plus the new one, and ship
   that release signed with the backup key.
3. Apps that never received a build containing the backup public key cannot be
   rotated remotely; they keep refusing and show the release-page dialog.

## Differential download

`electron-builder.yml` and the app set no `disableDifferentialDownload`, and the
workflow uploads every `*.blockmap`, so electron-updater's differential download
stays on for the NSIS installer and the mac zip. The final file's sha512 is still
checked against the signed manifest before install.

## Not covered by tests

The real GitHub download, the `gh` upload and an actual NSIS/Squirrel install are
only exercised on a live test release: do one before the first public one.
