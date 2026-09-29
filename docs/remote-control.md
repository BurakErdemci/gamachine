# Remote control (phone) - protocol v1

Status, 29 Sep 2026: build steps 1 to 4 are built (relay and phone page, the
backend pieces, the bridge, renderer and Electron); step 5 (measuring card
timeouts per provider and lengthening them) is not. The phone page has no
model or effort picker yet: `list_models`, `set_model` and the `effort` of
`send_message` exist on the bridge for it and for a later native app.
Research and measurements behind it: owner's vault,
`Teknik/Arastirmalar/Gamachine_Uzaktan_Kontrol_2026-09-27`.
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
- Phone scope, widened the same day (28 Sep 2026, owner: "claude remote
  control'de bütün / komutlar çalışıyor"): **slash commands work from the
  phone** (a text starting with `/` is an ordinary message; the phone page can
  list the chat's commands). This replaces the first version's refusal of `/`
  texts (`commands_not_allowed`). A paired phone can already approve commands,
  so it has that trust level.
  **The approval mode can be changed from the phone** (read with `get_config`,
  set with `set_approval_mode`), same reasoning: a phone that can approve
  every card can also say "stop asking". This replaces "not from the phone:
  approval mode". The phone does **not** need the UI secret for it, by design
  (see "Threats and answers").
  **A chat's provider and model can be switched from the phone, and the effort
  of a message chosen** (owner decision, 29 Sep 2026): `get_config` with a
  chat reads them, `list_models` gives the desktop picker's catalog,
  `set_model` changes that one chat's model, `send_message` takes an `effort`.
  The global default model is not touched: it stays the desktop's. Still not
  from the phone: other settings, API keys, CLI install, file operations.
- Notifications are detailed ("Onay bekliyor - Codex (Arena): git commit -m ...",
  "Is bitti - ..."), not a bare "something happened".
- While remote control is on, the PC may be kept awake (checkbox).
- While remote control is on, card timeouts get longer where the provider
  allows it (measure each provider first; agy cuts every MCP call at 180 s).
- The Unity MCP server is never started by this feature (toggle only).
- The "the app never downloads at runtime" rule is about the installed app
  being complete (no "please wait, downloading"). Relay traffic carries
  encrypted messages, never app code and never a file transfer (none exists),
  and does not break it. It does carry chat text: the last 50 messages of an
  opened chat (up to 100,000 characters each) and the live text events of its
  turns, so code an agent printed into a chat reaches the phone, encrypted end
  to end.

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
   chat view with live progress, pending cards, a composer with a `/` command
   picker, a Stop button, and an approval-mode selector on the main screen.
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
   rejects `t` outside +/- 5 minutes and unknown or removed devices. The relay
   admits a phone socket for one token, and the PC also requires the hello's
   `device_id` to be the device that token's hash belongs to (an unknown
   device, a removed one and a mismatch all answer `unknown_device`).
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
| `send_message {chat_id, text, effort?}` | delivered to the renderer, which sends it like a typed message (queued if a turn runs); stored as an ordinary `user` turn; the phone marker (device name) is a client-only field on the renderer's copy and is lost after a reload or a server re-read, since the `messages` table has no source column (see the desktop notes below). Slash commands are ordinary text: the backend does not look at a leading `/`. In the renderer a text that is exactly `/compact` (blanks around it ignored) compacts that chat, as the desktop composer does, and starts no turn; every other `/...` goes out like typed text (`/usage` gets its usage card, the CLI runs the rest). `effort` (optional; absent or `null` = the desktop's own level) must be one of the `effort_levels` `get_config` reports for that chat's current model, else `bad_effort`; it is carried in the `remote_message` frame and the renderer sends that one message with it. Other errors `bad_chat_id`, `unknown_chat`, `bad_text` |
| `list_slash_commands {chat_id}` | `{commands: [str], skills: [str], meta: [{name, description?, argumentHint?, insert?, displayName?}]}`: the catalog the desktop's `/` menu shows (`GET /slash-commands`, one shared function), for the agent family of that chat. Names come without the `/`. Gamachine's own `compact` (run by the renderer) leads `commands` and `meta` for every chat, as in the desktop menu; the CLI's own `compact` is not listed twice. Chats of agents without a catalog (`api-*`, opencode, cursor, kimi, gemini) and agy get only that. Errors `bad_chat_id`, `unknown_chat`, `unavailable` |
| `get_config {chat_id?}` | `{approval_mode: "auto" \| "balanced" \| "step"}`. With a `chat_id` it adds that chat's own model as the backend resolves it (`agentic.chat_model`: stored model, else its latest answer's, else the global default): `provider_type`, `model_name`, `family` (the CLI a subscription model runs on: `claude`, `codex`, `agy`, `opencode`, `cursor`, `copilot`, `kimi`; `null` for API providers and Ollama), and `effort_levels: [str]`, the levels that model accepts from the registry behind the desktop's `/effort-capabilities` (canonical order; `auto` is normally the first; empty when the registry names none). Errors `bad_chat_id`, `unknown_chat` |
| `list_models` | the desktop picker's catalog, `{local: [...], cloud: [...], subscription: [...], cloud_sources: {...}}`, built by the one function behind `GET /available-models` (`build_available_models` in `config_routes.py`, injected into the bridge like `stop_chat`), so it has the same lists, caching and timeouts. The phone never asks for a forced refresh. Long lists are split like any reply. Error `unavailable` |
| `set_model {chat_id, provider_type, model_name}` | stores that chat's model (`chat_model.set_chat_model` with `require_ready`, the check every writer shares) and replies `{provider_type, model_name}`. `model_name` may be a typed id not in the catalog, or `""` for the provider's default. Only that chat changes, never the global default; a turn already running finishes on the model it started with. Errors `bad_chat_id`, `unknown_chat`, `unknown_provider`, `bad_model`, `not_ready {needs}` (`needs`: `apikey`, `install`, `login` or `service`: what the provider lacks now; nothing changed). On success the desktop is told (`chat_model_changed`, below) |
| `set_approval_mode {mode}` | sets the one global mode, exactly as the desktop does (saved, agy gates followed, open cards drained: `auto` approves every open card, `balanced` the MCP cards it can prove routine) and replies `{mode, previous, approved_pending}`. Errors `bad_mode` (not one of the three), `unavailable`, `agy_step_refused {message, params: {pids}}` (a running agy process could not be gated; the mode did not change; `message` is the desktop's Turkish text). On success the desktop is told (`approval_mode_changed`, below) |
| `push_subscribe {subscription}` | stores the web push subscription for this device |

PC -> phone pushes: `event {chat_id, seq, kind, ...}` (turn start/end, text,
tool call, card opened/closed), `chat_changed`, `card_opened`, `card_closed`.

`send_message` goes through the renderer on purpose: the provider arguments,
the message queue, cards and wake rules all live there, and a second path
would drift from them. The backend hands the request to the renderer over the
existing app-wide event channel and reports `accepted` or `desktop_not_ready`.

A card the phone answers first closes on the desktop too. Only for an answer
that won (not `already_answered`, not a refusal), the backend puts
`{type: "card_closed", card_id, conversation_id, by: "phone:<device name>",
decision, outcome, at}` on the same channel (`/wake-stream-all`), so the
desktop copy does not wait for a click that could only get `already_answered`.

A mode the phone changed is announced the same way, after it is applied:
`{type: "approval_mode_changed", mode, previous, approved_pending,
by: "phone:<device name>", at}`. The renderer shows the new mode, clears the
in-chat cards a switch to `auto` approved (the code its own switch uses) and
shows a short note naming the phone. A refused switch publishes nothing.

A model the phone switched is announced the same way, after it is stored:
`{type: "chat_model_changed", conversation_id, provider_type, model_name,
by: "phone:<device name>", at}`. The renderer re-reads the model of that chat
if it is on screen. A chat that is not on screen needs nothing, since the page
reads a chat's model fresh whenever it comes on screen (no per-chat model is
cached in the renderer). A refused switch publishes nothing.

## Web push

- The PC owns a VAPID key pair (generated when remote control is first turned
  on, stored in the app data folder); the public key reaches the phone inside
  `pair_ok`. It sends pushes itself to the push
  service in the subscription (RFC 8291 aes128gcm + RFC 8292 VAPID, with the
  `cryptography` package already bundled); the relay is not involved.
- Content is detailed per the owner's decision: title "<what> - <agent> (<chat>)".
  The body of a card push is `<tool>: <summary>` (the tool and a short
  parameter summary, or the question text); of a finished-turn push, the
  turn's last assistant text (collected back from the end of the turn up to
  about 400 characters, whitespace collapsed); of an error or a woken chat, a
  fixed sentence. Every body is cut to 180 characters (`BODY_MAX` in
  `webpush.py`), so a finished-turn push shows the start of that last text,
  which can be code or file content the agent printed. The payload is end-to-end encrypted to the phone; the push service cannot
  read it. It is still shown on the lock screen - that is the owner's choice.
- Sent for: a card opened, a turn finished or failed, a chat woken by a note.
  Coalesced per chat (one push per chat per 10 s).
- A 404/410 from the push service deletes that subscription.

## Backend pieces this needs (useful without the phone too)

1. **Turn-event ring**: per chat, the last 500 events with a sequence number,
   written by every provider path of `/chat-stream`; readers join at
   `since_seq`. Today only the client that opened the stream sees a turn.
   Bounded by bytes too: a text event holds at most 8 KiB (a larger delta is
   split into consecutive events) and a chat's ring at most 1 MiB; whatever
   falls out reaches a reader as a gap. A negative `since` is refused (422).
2. **All pending cards**: one internal call listing every open card across
   chats and providers, and one answer path with first-answer-wins.
3. **Approval ledger** (new table): time, card id, chat, tool, parameter hash,
   approval mode, decision, device (desktop / phone name), outcome (approved,
   rejected, timed out). This is where the metric comes from. Rows are
   written by one background thread, so an answer never waits on SQLite; a
   row that cannot be queued or written is logged and dropped.
4. **Keep awake**: Electron `powerSaveBlocker.start('prevent-app-suspension')`
   while remote control is on and the checkbox is set.

## Threats and answers

| Threat | Answer |
|---|---|
| Someone photographs the QR | single-use secret + 5 min + SAS code confirmed on the PC |
| Relay operator reads traffic | end-to-end encryption; relay sees opaque frames |
| Relay operator serves a malicious page | accepted for v1 (page and relay ship from this repo); users can run their own relay; stated in SECURITY.md |
| Stolen phone | Remove device on the PC; scope excludes other settings, keys, files. It can approve cards, run slash commands, change the approval mode and switch a chat's model or effort (only to a provider that is ready on the PC; API keys stay on the PC and the phone never sees them), all of which it could do to the same effect by approving cards one by one |
| The phone changes the approval mode without the UI secret | Owner decision, 28 Sep 2026. The local `POST /approval-mode` demands a UI secret so that the Unity MCP server and model-run children, which can read the app token, cannot flip themselves into auto. The phone path never touches that route: `set_approval_mode` runs in-process, behind the paired device's end-to-end session keys (only a phone whose hello passed reaches it), calls the same function the route ends in (`apply_approval_mode`) and logs source `phone`. The route itself is unchanged and still refuses without the secret. Every change is announced on the desktop with the phone's name |
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

## Bridge implementation notes (step 3)

Facts about `Backend/app/remote/`; the protocol above stays the contract.

- Modules: `crypto.py` (byte layouts; `tests/test_remote_crypto.py` reproduces
  every value of `relay/test/vectors.json`), `store.py` (keys encrypted with the
  API key vault's Fernet key in `app_settings`, table `remote_devices` with the
  token hash only), `relay_client.py` (the one PC socket), `pairing.py`,
  `session.py`, `rpc.py` + `chats.py` (allow-list and phone shapes),
  `webpush.py`, `desktop_channel.py`, `bridge.py` (ties them together).
- Pairing offers are independent of how the phone learned them:
  `PairingManager.create_offer(source)` is where a second presentation (a typed
  code) plugs in; limits, mac, SAS, approval and expiry are shared, and the
  pending approval always carries the SAS.
- Local routes (app token; `*` also needs the UI secret):
  `GET /remote/status`, `POST /remote/enable*`, `POST /remote/disable`,
  `POST /remote/forget`, `POST /remote/pair/start*`, `GET /remote/pair/pending`,
  `POST /remote/pair/approve*`, `POST /remote/pair/reject`,
  `GET /remote/devices`, `DELETE /remote/devices/{device_id}`,
  `DELETE /remote/devices`, `GET|PUT* /remote/relay-url`,
  `GET|PUT /remote/keep-awake` (`keep_awake_active` = on and checked).
- `send_message` reaches the renderer as a `/wake-stream-all` frame
  `{type:"remote_message", request_id, conversation_id, text, source:"phone",
  device_id, device_name, at, effort?}`; `desktop_not_ready` means no such
  stream is open. `effort` is present only when the phone sent one that passed
  `chats.effort_levels(provider_type, model_name)` for the chat's model at that
  moment.
- The catalog and effort registry are not copied: `list_models` is
  `create_config_router(db).list_models`, the function `GET /available-models`
  awaits after its token check and refresh throttle (`user_id` None means no
  cloud merge, as before); effort levels come from
  `providers.effort_caps.get_effort_caps`. `set_model` runs
  `chat_model.set_chat_model` off the event loop (its readiness probes read the
  key vault and may ask Ollama; none spawns a CLI), maps `ChatModelError` to
  the RPC error of the same code, and publishes `chat_model_changed`
  (`MODEL_CHANGED_TYPE` in `desktop_channel.py`) only after the row is stored.
- `list_slash_commands` maps the chat to a family in one place,
  `chats.slash_family(row)`: the chat's own model (`agentic.chat_model`: the
  stored per-chat model, else the model of its latest answer when that maps
  cleanly, else the global default) goes through `chat_model.cli_family`, which
  is the decision the runner makes from a subscription model id (`claude`,
  `codex`, `agy`; API providers, ollama and the other CLIs = no catalog). The
  `provider`/`model` strings `list_chats` and the chat-changed frames report come
  from the same resolution (`provider` is the family, or `api-<provider>`), so the
  phone sees each chat's own model. The catalog function is injected like
  `stop_chat` (`RemoteBridge(..., list_slash_commands=router.list_slash_commands)`)
  and `chats.phone_catalog` adds the app's `compact` and cuts the catalog down
  to plain strings before it leaves.
  A message the phone sends runs with that chat's own model: the renderer posts
  it to `/chat-stream` with the chat id and the backend resolves it with
  `chat_model.turn_model`, whatever the desktop has selected.
- The mode change is one function, `apply_approval_mode(mode, source)` in
  `conversation_routes.py` (`set_mode`, then the drain), exposed as
  `router.apply_approval_mode` and injected into `RemoteBridge(...,
  apply_approval_mode=...)` like `stop_chat`. `POST /approval-mode` calls it
  after its token, maintenance-header and UI-secret checks (unchanged); the
  bridge calls it with source `phone` and no secret. It runs on the event loop
  because the drain sets asyncio events. `AgyStepGateError` propagates to both
  callers (the route makes it a 409, the bridge an `agy_step_refused` reply).
  A backend process without a UI secret (Docker, a reload worker) reads a saved
  auto or balanced as step and refuses local flips; the phone path does flip it,
  since it never asks for the secret.
- A phone's card answers are ledgered with device `phone:<device name>`.
- Replies over one frame are split: every list in `result` is cut in order,
  each part carries `part` (1-based) and `parts`, all with the request's `id`.
- Extra PC -> phone push: `{type:"gap", chat_id, epoch, last_seq}` when live
  events of an open chat were lost.

## Phone page notes (commands and mode)

Plain look on purpose; a native app will reuse the same requests.

- Composer: a `/` button beside "Gönder" opens a panel (outside the `<form>`,
  so Enter in its search box cannot send the message) with `list_slash_commands`
  for the shown chat (cached 5 minutes per chat), a search box, and one row per
  command or skill: `commands` first, then `skills` the list lacks. A pick
  writes `meta.insert` (Codex skills carry their own text) or `/<name> ` into
  the composer: an empty composer or a half-typed `/word` is replaced, any
  other text stays and the command follows it. Sending is the ordinary
  `send_message`; a sent `/compact` says it runs on the PC.
- Main screen: a selector with the desktop's three modes and Turkish labels
  (Otomatik, Güvenli Otomatik, Adım Adım). It reads `get_config` whenever the
  main screen shows and when the link comes back, is disabled while the PC is
  not connected, and calls `set_approval_mode` on change. Choosing Otomatik asks
  for confirmation first (the desktop warns about it in red). A refused switch
  puts the selector back on the real mode and says why in Turkish
  (`agy_step_refused` names the agy process id). The page does not learn of a
  change made on the desktop until the main screen shows again.
- `page.test.mjs` checks the helpers in `net.js`, that every id `app.js` looks
  up exists in `index.html`, and that the desktop's mode labels in
  `Frontend/frontend/renderer/lib/i18n.tsx` are the ones the page uses.

## Model and effort from the phone (bridge only)

The phone page has no picker for these yet (the owner will see the page later;
the native app reuses the same requests). The order a client follows:
`get_config {chat_id}` for the chat's current model and `effort_levels`;
`list_models` for what can be chosen; `set_model` to switch (a `not_ready`
refusal names what the provider lacks); `send_message {effort}` for the level
of one message. Effort is per message, not stored: the next message without one
uses the desktop's level. After a model switch, read `get_config` again,
because the levels belong to the model.

## Desktop implementation notes (step 4)

Facts about the Electron and renderer side; plain look, visual design later.

- Every `/remote/*` call goes through the main process: invoke channel
  `remote-control` with `(action, arg)`, a fixed table in
  `Frontend/frontend/main/helpers/remote-control.ts` (`status`, `enable`,
  `disable`, `forget`, `pair-start`, `pair-pending`, `pair-approve`,
  `pair-reject`, `devices`, `remove-device`, `remove-all-devices`,
  `relay-url`, `set-relay-url`, `set-keep-awake`). The UI secret is added
  there, only for enable, pair/start, pair/approve and PUT relay-url; page JS
  never holds it. Answers are `{ok:true, data}` or `{ok:false, code}`.
- Keep awake: `powerSaveBlocker.start('prevent-app-suspension')` follows the
  backend's `keep_awake_active`, read from every answer that carries it and
  from a status poll every 30 s (app start with remote control already on,
  other windows). Three failed polls in a row release it; quitting stops it.
- Phone messages: each `remote_message` frame is claimed once per
  `request_id` (in-window set, plus a localStorage record written under a Web
  Lock so a second window cannot claim it too), then sent through the typed
  message path with the conversation id as `targetOverride`: queued while that
  chat runs, a user turn otherwise; the composer is never touched. A frame
  that arrives before the page has chosen its send options waits for them.
  A frame's `effort` (one of `auto off minimal low medium high xhigh max`;
  `parseRemoteMessage` returns null for any other string, so a broken frame is
  dropped, never sent with a guessed level) replaces the page's thinking level
  for that one message, including while it waits in the chat's queue.
  A text that is exactly `/compact` is not sent: it compacts the addressed chat
  (`compactConversation(chatId)`; the desktop button's busy state is only set
  when that chat is on screen) and needs no send options. The phone gets no
  answer for it beyond `accepted`; the desktop shows the usual compact toasts.
- The phone marker (`📱 <device name>` over the user bubble) is renderer-only:
  `/chat-stream` has no field for a message source and the `messages` table no
  column for it, so a phone turn is kept as a client-only copy and the marker
  is lost when the app restarts or the chat is re-read from the server.
- A desktop answer that gets `already_answered {by, at, decision}` closes the
  card with a note ("Telefondan (<name>) onaylandi / reddedildi"), not the
  "outcome unknown" warning.
- An `approval_mode_changed` frame makes every window show the new mode
  (`adoptGenerationMode` in `useChat`, the same code the window's own switch
  ends in: the mode state, and after `auto` the in-chat command cards of every
  chat cleared, since the backend already approved them) and show a note
  "Telefondan (<name>) çalışma modu değiştirildi: <mode>", with the count of
  approved cards when there were any. Nothing is written back: the backend has
  applied the mode. A mode outside `auto | balanced | step`, or a `by` that is
  not `phone:...`, is ignored.
- A `chat_model_changed` frame (`parseChatModelChanged`: chat id a positive
  integer, non-empty `provider_type`, string `model_name`, `by` `phone:...`;
  anything else is ignored) makes `useChat` call the page's callback when its
  chat is the one on screen; `home.tsx` answers it with
  `ai.showChatModel(user.id, chatId)`, the read the page makes when a chat
  comes on screen, so the selector, effort caps, slash catalog and provider
  gate follow. Nothing is written back and no note is shown.
- A `card_closed` frame closes the card without a click: a turn's command or
  question card (the next queued card of that chat takes its place), the
  Unity bridge / note card on screen, or a tray entry, with the same note as
  above. Every window that holds the card closes it; an unknown card id or a
  malformed frame is ignored. A click already in flight for that card when it
  closes leaves the card that replaced it alone.
