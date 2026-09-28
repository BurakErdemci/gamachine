# Gamachine relay

Cloudflare Worker + Durable Objects that connect the Gamachine desktop app
(the PC) with the phone page, plus the phone page itself. Protocol:
[`docs/remote-control.md`](../docs/remote-control.md). This file pins the
details that document leaves open: the relay control frames and the exact byte
layouts. No npm dependencies; `wrangler` is only used to run and deploy.

```
relay/
  worker/      index.js (routes, static files, CSP), room.js (Durable Object per pairing id),
               limiter.js (Durable Object per client IP), util.js
  public/      phone page: index.html, app.js (UI), net.js (sockets), crypto.js (protocol crypto),
               store.js (IndexedDB), sw.js (web push), manifest + icons
  test/        node:test suites, vectors.json (shared test vectors), nodeimpl.mjs (node:crypto reference)
  tools/       gen-icons.mjs
```

## Commands

```
node --test "test/*.test.mjs"     # all tests; integration.test.mjs starts `wrangler dev --local` on port 8799
RELAY_IT=0 node --test "test/*.test.mjs"   # skip the wrangler-based tests
node test/make-vectors.mjs         # rebuild test/vectors.json (deterministic)
wrangler deploy                    # deploys the worker named gamachine-relay
```

## Endpoints

| Path | What |
|---|---|
| `GET /p` | the phone page (strict CSP, no inline code) |
| `GET /app.js` `/net.js` `/crypto.js` `/store.js` `/style.css` `/sw.js` `/manifest.webmanifest` `/icon-192.png` `/icon-512.png` `/apple-touch-icon.png` | page files; nothing else in `public/` is served |
| `GET /` | redirect to `/p` |
| `WSS /ws/pc/<pair_id>` | the PC. Subprotocols: `gamachine.v1`, `key.<room_key>` |
| `WSS /ws/phone/<pair_id>` | a paired phone. Subprotocols: `gamachine.v1`, `tok.<token>` |
| `WSS /ws/pair/<pair_id>` | a phone that is pairing (no token yet). Subprotocol: `gamachine.v1` |

`pair_id` is 16 random bytes as unpadded base64url (22 characters).

Credentials travel in `Sec-WebSocket-Protocol` because a browser cannot set
any other header on a WebSocket; this also keeps them out of URLs and logs. The
relay answers with `Sec-WebSocket-Protocol: gamachine.v1`. Every refusal
happens before the upgrade, as a plain HTTP status:

| Status | When |
|---|---|
| 400 | `gamachine.v1` not offered |
| 401 | PC without a well-formed key (43-128 base64url chars); phone without a registered token |
| 403 | PC key whose SHA-256 differs from the stored room-key hash |
| 404 | unknown path, bad `pair_id`, or phone/pair socket for a room no PC has registered |
| 426 | not a WebSocket upgrade |
| 429 | pairing limit: 5 per minute per `pair_id`, 20 per hour per client IP |

The first PC that connects to a `pair_id` registers `SHA-256(room_key)` (trust
on first use). The PC should therefore connect before it shows the QR code.
A new PC connection with the right key replaces the old one (close 4000).

## Stored data

Room object: `room_hash`, `tokens` (SHA-256 hashes of phone tokens, max 50),
`last_seen` (ms, set when the PC socket closes), `pair_hits` (timestamps of the
last minute). IP object: `hits` timestamps, deleted by an alarm one hour after
the last attempt. No frame content is ever stored; frames are forwarded from
memory. Hashes are unpadded base64url of SHA-256 over the UTF-8 token string.

## Control frames (relay <-> PC)

All are JSON text frames. Phone and pairing sockets carry the PC's messages
as-is; the relay never parses them.

Relay -> PC:

| Frame | Meaning |
|---|---|
| `{type:"welcome", phones:[{conn, token_hash}], pairs:[{conn, ip}], tokens:n}` | sent on connect |
| `{type:"phone_open", conn, token_hash}` | a phone connected with that token |
| `{type:"phone_close", conn}` | it left |
| `{type:"pair_open", conn, ip}` | a pairing socket opened (ip for the PC's own per-IP limit) |
| `{type:"pair_close", conn}` | it left |
| `{type:"from", conn, data}` | a text frame from that socket; `data` is the exact string the phone sent |
| `{type:"tokens_ok", count}` | reply to `register_tokens` / `drop_token` |
| `{type:"gone", conn}` | a `to` targeted a socket that no longer exists |
| `{type:"error", error}` | `bad_json`, `unknown_type`, `bad_hashes`, `bad_hash`, `too_many_tokens` |

PC -> relay:

| Frame | Effect |
|---|---|
| `{type:"to", conn, data}` | send the string `data` to that phone or pairing socket. A pairing socket is closed (4002) right after its one reply |
| `{type:"register_tokens", hashes:[...], replace?:bool}` | add token hashes; with `replace:true` the list becomes exactly `hashes` and phones on other tokens are closed (4001). Send this **before** the `pair_ok` that carries the token |
| `{type:"drop_token", hash}` | forget one hash and close its phones (4001) |
| `{type:"reset_room"}` | delete everything stored for this `pair_id` and close all sockets (4006); for "turn remote control off and forget" |

Relay -> phone (not from the PC): `{type:"pc_offline", last_seen}` (on connect
while the PC is away, on every frame sent while it is away, and when it
leaves) and `{type:"pc_online"}` (the PC reconnected; the phone starts a new
session). `{"type":"ping"}` from any socket is answered with `{"type":"pong"}`
by the runtime without waking the object.

Close codes: 4000 replaced PC, 4001 token dropped, 4002 pairing reply
delivered, 4003 pairing socket older than 6 minutes, 4004 bad frame (binary,
over 64 KiB from a phone, second pairing request), 4005 PC offline during
pairing, 4006 room reset. Frames from the PC are limited only by the platform
(1 MiB per WebSocket message), so the bridge must keep single replies below that.

## Byte layouts

All byte strings in JSON are unpadded base64url. Keys are P-256; public keys
are 65-byte uncompressed points; `K_static` and ECDH outputs are the 32-byte
x coordinate. HKDF and HMAC use SHA-256. `||` is plain concatenation; every
variable-length field is last or has a fixed length.

| Value | Definition |
|---|---|
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
- PC -> phone: `{type:"pair_ok", c:1, d}` - the "first encrypted frame" of the
  doc's pairing step 4, sealed with `K_pair`, direction 2, counter 1.
  Plaintext `{device_id, token, vapid_pub}`: `device_id` 16 random bytes
  (22 chars), `token` 32 random bytes (43 chars), `vapid_pub` the PC's VAPID
  public key (65-byte point) that the phone needs for `pushManager.subscribe`.
- PC -> phone: `{type:"pair_reject", reason}`; the page knows `expired`,
  `rate_limited`, `rejected`.
- phone -> PC: `{type:"hello", device_id, eph_phone_pub, t, tag}`; the PC
  rejects `|t - now| > 300` and unknown devices.
- PC -> phone: `{type:"hello_ack", eph_pc_pub, tag}` or
  `{type:"hello_reject", reason}` (`clock`, `unknown_device`).

`test/vectors.json` holds fixed keys and every intermediate value above
(mac input, K_static, SAS, K_pair, pair_ok, hello and hello_ack with their tag
inputs, session keys, five frames). Plaintexts there are compact JSON
(Python: `json.dumps(obj, separators=(",", ":"), ensure_ascii=False)`).
`test/nodeimpl.mjs` is a second implementation with `node:crypto` that also
shows the PC's checks (`pcHandlePairRequest`, `pcHandleHello`).

## What the page expects inside the channel

The doc fixes the request names; these reply shapes are what `app.js` reads.
The bridge may add fields.

- reply: `{id, ok:true, result}` or `{id, ok:false, error, ...}`;
  `answer_card` conflict: `{id, ok:false, error:"already_answered", by, at}`
- `list_chats` -> `{chats:[{chat_id, title, provider, model, status: "running"|"idle"|"awaiting_card", last_activity (ms)}]}`
- `pending_cards` -> `{cards:[{card_id, chat_id, title, detail, choices?}]}`;
  `choices` is a list of strings or `{id, label}`. With choices the page sends
  `answer_card {card_id, decision:"choice", choice:id}`, otherwise
  `decision:"approve"`; "Reddet" always sends `decision:"reject"`.
- `open_chat {chat_id}` -> `{messages:[{role:"user"|"assistant", text, source?}], events:[event...]}`
- `send_message` -> `{status:"accepted"|"desktop_not_ready"}`
- `stop`, `close_chat`, `push_subscribe {subscription}` -> `{}`
- pushes: `{type:"event", chat_id, seq, kind, ...}` with kinds `text {text}`,
  `tool_call {tool, summary}`, `turn_start`, `turn_end {status}`,
  `card_opened`, `card_closed`; `{type:"chat_changed", chat}`;
  `{type:"card_opened", card}`; `{type:"card_closed", card_id}`.

Web push payload (sent by the PC, shown by `sw.js`): JSON
`{title, body, url, tag?}`. `url` must be `/p#chat=<chat_id>` to open that
chat; anything else opens `/p`. `tag` lets a later push replace an earlier one.

## iPhone notes

Web push only works when the page runs from the home screen. Scanning the QR
opens Safari; on an iPhone outside the home-screen app the page first shows
the install guide, keeps the pairing link in the address bar and offers
"copy link" (to paste in the installed app) or "pair here anyway". Whether iOS
carries the `#...` part into the home-screen app, and whether the app shares
storage with Safari, is not verified yet; the paste field covers both cases.
