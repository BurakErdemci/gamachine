# Security Policy

> Türkçe rapor göndermek tamamen normaldir — bu dosya İngilizce yazıldı çünkü
> hedef kitlesi dışarıdaki güvenlik araştırmacıları.

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Use one of these instead:

1. **GitHub private vulnerability reporting** — the *Security* tab of this
   repository → *Report a vulnerability*. Preferred, because it keeps the
   discussion attached to the code.
2. **Email** — `erdemciburakemre@gmail.com`, with `SECURITY` in the subject.

Please include: what you did, what happened, what you expected, and the version
you tested (`Help → About`, or the release tag). A minimal reproduction is worth
more than a long description.

This is a one-person, non-commercial project. There is no bounty and no
guaranteed response window. Realistically you should expect a first reply within
a few days.

## What is in scope

This application drives AI coding agents and can read, write and execute things
on the user's machine, so its security properties are mostly about **containment
and consent**. In-scope examples:

- Bypassing the approval gate — getting a file write, a file delete, a terminal
  command or a Unity scene mutation to happen **without** the user's card being
  shown and accepted.
- Escaping the selected workspace: reading or writing outside it, or writing a
  `.cs` file outside `Assets/Scripts/`.
- Leaking secrets: API keys, session tokens or the local MCP token appearing in
  logs, in the chat transcript, in process arguments visible to other processes,
  or in a file readable by another user.
- Getting the renderer to navigate to, or execute, remote content.
- Anything that lets a *prompt* — a chat message, a file's contents, a web page
  the agent fetched — cause an unapproved side effect.

## Remote control (phone)

An optional feature lets a phone watch and steer the desktop app through a
Cloudflare relay. Protocol and byte layouts: `docs/remote-control.md`; relay
code: `relay/`; PC side: `Backend/app/remote/`.

- **Off by default.** Until the user turns it on, the bridge opens no
  connection and no relay traffic exists. The backend stays bound to
  `127.0.0.1`; the phone never receives the local app token or the UI secret
  and reaches only an allow-list of requests.
- **Pairing is by QR code and confirmed on the PC.** The QR carries the relay
  address plus a pairing id, the PC's public key and a single-use 128-bit
  secret in the URL fragment, which the relay never sees. The secret expires
  after 5 minutes. The PC then shows a 4-digit code that the phone shows too,
  and pairing completes only when the user approves it on the desktop, so a
  photo of the QR alone is not enough. Pairing attempts are rate limited (5
  per minute per pairing id, 20 per hour per IP).
- **End-to-end encryption.** Each side holds a long-term P-256 key (the
  phone's private key is a non-extractable WebCrypto key in IndexedDB). Every
  connection does a fresh ephemeral key exchange on top of the long-term keys
  (forward secrecy), then uses AES-256-GCM with one key per direction. Frames
  carry a counter; a receiver drops any counter not greater than the last one
  it accepted, so recorded frames cannot be replayed. The PC's keys are stored
  encrypted with the same key as the API-key vault, so the limitation above
  applies to them too.
- **The relay is untrusted transport.** It sees opaque frames, sizes, timing,
  IP addresses and which pairing id is in use. It stores only when the PC
  last connected, hashes of the phones' tokens and recent pairing-attempt
  times, and it cannot read or forge messages. It does serve the phone page, so
  a malicious or compromised relay operator could serve a malicious page. This
  is accepted for now: the page ships from this repository, and the relay URL
  can be pointed at a relay the user runs.
- **What a paired phone can do:** list and watch chats, answer approval and
  question cards, stop a turn, send messages (a text starting with `/` is an
  ordinary message, so slash commands work) and pick the effort of one message
  (checked against the levels of that chat's model), switch a chat's provider and
  model (exactly as the desktop's picker does, so it also becomes the default a
  new chat opens on; only to a provider that is ready on the PC and a model the
  plan allows: the plan-lock check reads the stored plan cap only, so where that
  cap is unknown or older than 7 days it locks nothing, whereas the desktop's
  picker would probe the CLI first), change the desktop's effort level (the desktop renderer applies
  it, as a click in its effort panel would), read and change the global
  approval mode, and register its own web-push
  subscription (validated on the PC and stored on that device's own row, so it
  changes only that phone's push endpoint, nothing else). It
  cannot change other settings, touch API keys, install
  CLIs, or run file operations. By owner decision, changing the approval mode
  from the phone does **not** need the UI secret that the local
  `POST /approval-mode` route requires; that secret exists to stop the Unity
  MCP server and model-run child processes, which can read the app token, from
  flipping the mode themselves. The phone path is reachable only through the
  paired device's end-to-end session, and every change is announced on the
  desktop with the phone's name. A paired phone can already approve every
  card, so it is trusted about as far as a person at the keyboard.
- **Forgetting a device.** Removing a device on the PC deletes its key and its
  relay token, and the relay closes that phone's sockets. Turning remote
  control off and choosing to forget everything makes the relay delete all it
  holds for the pairing. A room whose PC has been away for 30 days is deleted
  by the relay on its own.
- **Web push** is sent by the PC straight to the phone's push service, not
  through the relay, encrypted to the phone (RFC 8291) and authenticated with
  the PC's own VAPID key. It is detailed on purpose and appears on the lock
  screen: chat title, agent, the tool and a short parameter summary for a
  card; for a finished turn, the turn's closing text (its last text events,
  gathered back to about 400 characters) with the first 180 characters shown.
- **What is sent.** Chat titles, recent messages and live turn events of the
  chats the phone opens, and card summaries. Because that is chat text, code
  the agent printed into a chat travels with it, encrypted. There is no file
  transfer: workspace files are not sent, and API keys, tokens and the UI
  secret never leave the machine.

In scope for reports: anything that lets someone without a paired, approved
phone read or send remote-control traffic, replay or forge frames, pair
without the on-screen approval, keep access after a device was removed, or make
the relay or a web page act as the PC. Out of scope: a phone the user paired
and approved doing what the list above allows, and a relay operator serving a
malicious page (see above).

## What is out of scope

- **Unsigned installers.** Windows SmartScreen and macOS Gatekeeper warnings are
  expected: the builds are not code-signed yet. Known, documented, not a report.
- **Third-party AI CLIs and their credentials.** Claude Code, Codex, Antigravity,
  Copilot, Cursor, OpenCode and Kimi are installed and authenticated by the user;
  report issues in their own trackers. What *is* in scope is how this application
  passes data to them.
- **What the user explicitly approved.** If a card was shown and accepted, the
  resulting action is intended behaviour, however destructive.
- **Auto-approve mode doing what it says.** Choosing it is a decision to skip
  cards.
- Reports produced only by a scanner, with no demonstrated impact.

## Known and accepted limitations

Stated up front so nobody spends time rediscovering them:

- Builds are **not code-signed or notarized** on either platform.
- API keys are encrypted at rest, but the encryption key sits next to the
  database in the user's home directory. This protects against casual reading,
  **not** against anyone who can already read that directory.
- The Windows installer is per-user, so its install directory is writable by the
  user's own processes.
- Cloud-API function calling does not gate file writes; the CLI agent paths do.
  This asymmetry is documented in the README.

If you think one of these is worse than described, that itself is a valid report.
