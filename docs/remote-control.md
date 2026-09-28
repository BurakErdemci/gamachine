# Remote control (phone) - protocol v1

Status: design, 28 Sep 2026. Build step 1 (relay + phone page, `relay/`) is
built; everything else is not implemented yet. Research and measurements behind
it: owner's vault, `Teknik/Arastirmalar/Gamachine_Uzaktan_Kontrol_2026-09-27`.
This document is binding for both ends; `relay/README.md` adds only the
relay's own control frames, storage and close codes.

## Goal and metric

The owner leaves the PC running and manages Gamachine from an iPhone anywhere
(mobile data, not the home Wi-Fi). The number this work must move: **the share
of approval cards that time out while the owner is away**. Today it is
effectively 100 %; the target is below 10 %. The approval ledger (below)
counts it.

## Owner decisions (28 Sep 2026)

- Off by default. Nothing leaves the machine until the user turns remote
  control on and pairs a phone with a QR code.
- Relay on Cloudflare Workers, running 24/7; the PC does not host it.
- Phone client v1 is a web page added to the home screen (no App Store).
  Measured 28 Sep 2026 on the owner's iPhone: web push arrives over mobile data,
  on the lock screen, with long Turkish text intact.
- Phone scope v1: **watch, approve / reject, answer question cards, stop a
  turn, and write a message into a chat.** Not from the phone: settings,
  approval mode, API keys, CLI install, file operations.
- Notifications are detailed ("Onay bekliyor - Codex (Arena): git commit -m ...",
  "Is bitti - ..."), not a bare "something happened".
- While remote control is on, the PC may be kept awake (checkbox).
- While remote control is on, card timeouts get longer where the provider
  allows it (measure each provider first; agy cuts every MCP call at 180 s).
- The Unity MCP server is never started by this feature (toggle only).
- The "the app never downloads at runtime" rule is about the installed app
  being complete (no "please wait, downloading"). Relay traffic carries
  encrypted messages, not code or files, and does not break it.

## Parts

```
 phone (Safari, home-screen web app)          PC (Gamachine)
   page + service worker from relay             renderer <-> backend (127.0.0.1)
        |  WSS, end-to-end encrypted                 |
        +------------> relay <-----------------------+  one outbound WSS from
                  (Cloudflare Worker +                   Backend/app/remote/
                   one Durable Object per pairing)
 web push: PC -> Apple/Google push service -> phone (relay not involved)
```

1. **Relay** (`relay/` in this repo, MIT, deployed with wrangler - a dev tool,
   never bundled). Serves the phone page and forwards opaque frames between
   the one PC socket and the phone sockets of a pairing. It stores no message
   content, only: when the PC last connected, the phone tokens' hashes (so
   random clients cannot join a room) and recent pairing-attempt times. If the
   PC is not connected it answers `pc_offline {last_seen}` and drops the frame
   (no offline queue in v1). Rules: "Relay connection" below.
2. **Phone page** (`relay/public/`): one screen - chat list with status, a
   chat view with live progress, pending cards, a composer, a Stop button.
   Strict CSP, no third-party scripts. Keys live in IndexedDB as
   non-extractable WebCrypto keys.
3. **Remote bridge** (`Backend/app/remote/`): zero network traffic while off.
   While on: one outbound WSS to the relay, pairing, the crypto session, an
   allow-listed RPC dispatcher, the web push sender, the device list. The
   backend stays bound to 127.0.0.1; the phone never gets `LOCAL_APP_TOKEN` or
   the UI secret, and never reaches any route that is not on the allow list.
4. **Desktop UI**: settings panel (toggle, relay URL with the project default
   and a "my own relay" field, QR, pairing code confirm, paired devices with
   last seen and Remove / Remove all, keep-awake checkbox), a phone badge on
   messages and card answers that came from a phone.

## Relay connection

- The PC creates a room key once (32 random bytes, base64url) and keeps it
  while remote control stays paired. The pairing id is derived from it:
  `pair_id = base64url(SHA-256("gamachine-remote-v1 room" || room_key)[0..16])`
  (layout below). The relay recomputes it on every PC connect and refuses a
  key that does not produce the id in the path, so a stranger who learns a
  `pair_id` cannot claim or open its room. Nothing is registered on first use.
  (Changed from "first PC to connect owns the id" after Codex relayaudit,
  28 Sep 2026: a stranger could connect first and lock the owner out.)
- Sockets: PC `/ws/pc/<pair_id>`, paired phone `/ws/phone/<pair_id>`, pairing
  phone `/ws/pair/<pair_id>`. Credentials travel in `Sec-WebSocket-Protocol`,
  the only header a browser can set on a WebSocket, which also keeps them out
  of URLs and logs: `gamachine.v1` always, plus `key.<room_key>` for the PC
  and `tok.<token>` for a paired phone.
- Origin: phone and pairing sockets are accepted only from the relay's own
  page; the PC socket is refused if it carries any `Origin`, so no web page can
  use it (the bridge sends none). Checked before any limit is counted, so
  another site cannot use a visitor's browser to spend their pairing budget.
- The PC connects before it shows the QR, so the room exists when the phone
  scans it. A pairing socket for a room that does not exist gets `no_room`
  and the page says no PC is waiting for this code; this costs no attempt.
- Limits: new rooms 10 per hour per IP (reconnecting to an existing room is
  free). A room whose PC has not been connected for 30 days is deleted; every
  PC connect moves that date. When the PC comes back to a deleted room it
  registers its devices' token hashes again. Frame limits count UTF-8 bytes:
  64 KiB from a phone, 1 MiB from the PC, so the bridge splits anything larger
  (a long chat history) across several replies.

## Pairing

QR content: `https://<relay>/p#<pair_id>.<pc_pub>.<pair_secret>`
- `pair_id`: 128 bits derived from the room key (above), base64url.
  `pc_pub`: the PC's long-term P-256 public key (raw, base64url).
  `pair_secret`: 128-bit random, single use, valid 5 minutes.
- Everything after `#` never reaches the relay (RFC 3986 section 3.5).

Steps:
1. Phone creates its long-term P-256 key pair (non-extractable private key).
2. Phone -> relay -> PC: `pair_request {phone_pub, device_name, mac}` where
   `mac = HMAC-SHA256(pair_secret, phone_pub || device_name)`, one request per
   pairing socket.
3. Both sides compute `K_static = ECDH(own_priv, peer_pub)` and a 4-digit code
   `SAS = HKDF(K_static, salt=pair_secret, info="gamachine-remote-v1 sas")`.
   The PC shows "iPhone wants to pair - code 7314 - Approve / Reject"; the phone
   shows the same code. A photo of the QR alone is not enough.
4. On Approve the PC stores the device (id, name, phone_pub, created, last
   seen), creates a random relay token for it, registers the token's hash with
   the relay, and then sends it to the phone inside the first encrypted frame:
   `pair_ok {c: 1, d}`, sealed with `K_pair` (layout below), direction PC ->
   phone, counter 1. Its plaintext is `{device_id, token, vapid_pub}`; the PC's
   VAPID public key is there because the phone needs it for
   `pushManager.subscribe`. On Reject, expiry or a limit the PC answers
   `pair_reject {reason}` with `rejected`, `expired` or `rate_limited`.
   `pair_secret` is deleted on first use or after 5 minutes.
5. Limits: 5 pairing attempts per minute per pairing id, 20 per hour per IP
   (relay and PC both enforce).

Remove device: the PC deletes the key and tells the relay to drop the token
hash; the relay closes that phone's sockets. Turning remote control off and
forgetting everything: the PC sends `reset_room`, the relay deletes all it
holds for the pairing id and closes every socket.

## Session crypto (per connection)

Static-static ECDH authenticates both ends; a fresh ephemeral ECDH per
connection gives forward secrecy (the Noise KK idea, built from WebCrypto /
`cryptography` primitives both sides already have).

1. Phone -> PC: `hello {device_id, eph_phone_pub, t}` with
   `tag = HMAC(K_static, "hello" || device_id || eph_phone_pub || t)`; the PC
   rejects `t` outside +/- 5 minutes and unknown or removed devices.
2. PC -> phone: `hello_ack {eph_pc_pub}` with its own tag over both
   ephemerals, or `hello_reject {reason}` (`clock`, `unknown_device`).
3. `K_session = HKDF(ECDH(eph, eph) || K_static, info="gamachine-remote-v1 session")`,
   split into two AES-256-GCM keys, one per direction.
4. Every frame: `{c: counter, d: ciphertext}`; the 96-bit nonce is
   direction (32 bits) + counter (64 bits). A receiver drops any counter that
   is not greater than the last one it accepted (replay protection).

### Byte layouts

All byte strings in JSON are unpadded base64url. Keys are P-256; public keys
are 65-byte uncompressed points; `K_static` and ECDH outputs are the 32-byte
x coordinate. HKDF and HMAC use SHA-256. `||` is plain concatenation; every
variable-length field is last or has a fixed length. `relay/test/vectors.json`
has every value below for fixed keys; the bridge must reproduce them.

| Value | Definition |
|---|---|
| `pair_id` | first 16 bytes of `SHA-256("gamachine-remote-v1 room" \|\| room_key)`, `room_key` being the base64url text exactly as sent in `key.<room_key>` (its ASCII bytes, not decoded) |
| QR | `https://<relay>/p#<pair_id>.<pc_pub>.<pair_secret>` (22 + 87 + 22 characters) |
| `mac` | `HMAC(key = pair_secret (16 bytes), phone_pub (65) \|\| UTF-8 device_name)` |
| `K_static` | `ECDH(own_static_priv, peer_static_pub)` |
| SAS | `HKDF(K_static, salt = pair_secret, info = "gamachine-remote-v1 sas", 4 bytes)` read as big-endian uint32, mod 10000, zero-padded to 4 digits |
| `K_pair` | `HKDF(K_static, salt = pair_secret, info = "gamachine-remote-v1 pair", 32 bytes)` |
| hello `tag` | `HMAC(K_static, "hello" \|\| device_id (UTF-8, 22 chars) \|\| eph_phone_pub (65) \|\| t (8-byte big-endian Unix seconds))` |
| hello_ack `tag` | `HMAC(K_static, "hello_ack" \|\| eph_phone_pub (65) \|\| eph_pc_pub (65))` |
| `K_session` | `HKDF(ECDH(eph, eph) \|\| K_static, salt = empty, info = "gamachine-remote-v1 session", 64 bytes)`; bytes 0-31 phone->PC key, 32-63 PC->phone key (AES-256-GCM) |
| nonce | direction as 4-byte big-endian (1 = phone->PC, 2 = PC->phone) \|\| counter as 8-byte big-endian |
| frame | `{c: counter, d: base64url(ciphertext \|\| 16-byte tag)}`, no AAD, plaintext is UTF-8 JSON. Counters start at 1 per direction per connection; the receiver drops any `c` not greater than the last accepted one, and a failed decrypt does not advance it |

Messages outside the encrypted channel (all carry `type`):

- phone -> PC on `/ws/pair`: `{type:"pair_request", phone_pub, device_name, mac}`
- PC -> phone: `{type:"pair_ok", c:1, d}` (pairing step 4). `device_id` is 16
  random bytes (22 chars), `token` 32 random bytes (43 chars), `vapid_pub` the
  65-byte VAPID public key.
- PC -> phone: `{type:"pair_reject", reason}`
- phone -> PC: `{type:"hello", device_id, eph_phone_pub, t, tag}`
- PC -> phone: `{type:"hello_ack", eph_pc_pub, tag}` or `{type:"hello_reject", reason}`

## Messages (inside the encrypted channel)

Requests carry `id`; replies echo it. Everything not listed is refused.

| Request (phone -> PC) | Reply / effect |
|---|---|
| `list_chats` | chats with title, provider/model, status (running, idle, awaiting card), last activity |
| `open_chat {chat_id, since_seq?}` | last N messages + the turn-event ring from `since_seq`; then live `event` pushes for that chat |
| `close_chat {chat_id}` | stop live pushes |
| `pending_cards` | every open card in every chat (MCP gates, command gates, Claude/Codex in-stream cards, question cards) |
| `answer_card {card_id, decision, choice?}` | first answer wins; a later answer gets `already_answered {by, at}`; ledger row with the device |
| `stop {chat_id}` | same effect as the desktop Stop |
| `send_message {chat_id, text}` | delivered to the renderer, which sends it like a typed message (queued if a turn runs); stored with source `phone` |
| `push_subscribe {subscription}` | stores the web push subscription for this device |

PC -> phone pushes: `event {chat_id, seq, kind, ...}` (turn start/end, text,
tool call, card opened/closed), `chat_changed`, `card_opened`, `card_closed`.

`send_message` goes through the renderer on purpose: the provider arguments,
the message queue, cards and wake rules all live there, and a second path
would drift from them. The backend hands the request to the renderer over the
existing app-wide event channel and reports `accepted` or `desktop_not_ready`.

## Web push

- The PC owns a VAPID key pair (generated when remote control is first turned
  on, stored in the app data folder); the public key reaches the phone inside
  `pair_ok`. It sends pushes itself to the push
  service in the subscription (RFC 8291 aes128gcm + RFC 8292 VAPID, with the
  `cryptography` package already bundled); the relay is not involved.
- Content is detailed per the owner's decision: title "<what> - <agent> (<chat>)",
  body with the tool and a short parameter summary, capped at ~180 characters.
  The payload is end-to-end encrypted to the phone; the push service cannot
  read it. It is still shown on the lock screen - that is the owner's choice.
- Sent for: a card opened, a turn finished or failed, a chat woken by a note.
  Coalesced per chat (one push per chat per 10 s).
- A 404/410 from the push service deletes that subscription.

## Backend pieces this needs (useful without the phone too)

1. **Turn-event ring**: per chat, the last 500 events with a sequence number,
   written by every provider path of `/chat-stream`; readers join at
   `since_seq`. Today only the client that opened the stream sees a turn.
2. **All pending cards**: one internal call listing every open card across
   chats and providers, and one answer path with first-answer-wins.
3. **Approval ledger** (new table): time, card id, chat, tool, parameter hash,
   approval mode, decision, device (desktop / phone name), outcome (approved,
   rejected, timed out). This is where the metric comes from.
4. **Keep awake**: Electron `powerSaveBlocker.start('prevent-app-suspension')`
   while remote control is on and the checkbox is set.

## Threats and answers

| Threat | Answer |
|---|---|
| Someone photographs the QR | single-use secret + 5 min + SAS code confirmed on the PC |
| Relay operator reads traffic | end-to-end encryption; relay sees opaque frames |
| Relay operator serves a malicious page | accepted for v1 (page and relay ship from this repo); users can run their own relay; stated in SECURITY.md |
| Stolen phone | Remove device on the PC; scope excludes settings, modes, keys, files |
| Replay of an approval | per-direction counters; card ids are single use |
| Phone approves while desktop also answers | first answer wins, both sides see who answered |
| Relay flooding | `pair_id` derived from the PC's room key, token hashes for phones, rate limits (pairing per id and per IP, new rooms per IP), rooms deleted after 30 days without the PC, byte caps on frames |
| Another website drives the visitor's browser at the relay | phone sockets only from the relay's own page, PC socket from no browser; checked before any limit counts |
| Remote control silently on | off by default; a visible indicator while on; zero traffic while off |

## Build order

1. Relay + phone page (independent of the app).
2. Backend: turn-event ring, all-pending-cards, ledger (no network).
3. Backend: remote bridge (pairing, crypto, RPC, web push, devices).
4. Renderer + Electron: settings panel, QR, pairing confirm, device list,
   phone badge, `send_message` hand-off, keep-awake.
5. Measure card timeouts per provider, then lengthen what can be lengthened.

Each step is audited by Codex before the next one builds on it.
