# Gamachine relay

Cloudflare Worker + Durable Objects that connect the Gamachine desktop app
(the PC) with the phone page, plus the phone page itself. Protocol:
[`docs/remote-control.md`](../docs/remote-control.md), which is binding and
also holds the byte layouts. This file adds what only the relay needs: control
frames, storage and close codes. No npm dependencies; `wrangler` is only used to run and deploy.

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

`pair_id` is derived from the PC's room key (see "Byte layouts"):
`base64url(SHA-256("gamachine-remote-v1 room" || room_key)[0..16])`, 22
characters. The relay recomputes it on every PC connect and refuses a key that
does not produce the `pair_id` in the path, so knowing a `pair_id` gives no way
to open or claim its room. Nothing is registered on first use. The room key is
32 random bytes (43 characters) that the PC creates once and keeps while
remote control stays paired; a new key means a new `pair_id` and a new QR.

`Origin`: `/ws/phone` and `/ws/pair` accept only the relay's own origin (the
page). `/ws/pc` refuses any request that carries an `Origin` header, so no web
page can use it; the bridge must not send one. This is checked before any
limit is counted, so another site cannot spend a visitor's pairing budget.

Credentials travel in `Sec-WebSocket-Protocol` because a browser cannot set
any other header on a WebSocket; this also keeps them out of URLs and logs. The
relay answers with `Sec-WebSocket-Protocol: gamachine.v1`. Refusals happen
before the upgrade, as a plain HTTP status:

| Status | When |
|---|---|
| 400 | `gamachine.v1` not offered |
| 401 | PC without a well-formed key (43-128 base64url chars) |
| 403 | wrong `Origin` (see above); PC key that does not derive the `pair_id` |
| 404 | unknown path, bad `pair_id`, or phone socket for a room that does not exist |
| 426 | not a WebSocket upgrade |
| 429 | pairing: 5 per minute per `pair_id`, 20 per hour per client IP; new rooms: 10 per hour per client IP (reconnecting to an existing room is not counted) |

One exception: a pairing socket for a room that does not exist is accepted,
gets `{type:"no_room"}` and is closed with 4008, because a browser cannot read
the status of a refused upgrade and the page must tell "no PC is waiting for
this code" apart from "too many attempts". It counts against no limit.
Likewise a phone socket whose token is missing or not registered is accepted
and closed with 4009 (nothing is sent and the PC hears nothing of it): with a
plain 401 the page could not tell a removed phone from a network drop and
retried forever. While the connected PC has not yet sent `register_tokens`
with `replace:true` on this connection (a recreated room, or a registration
that failed or is late), an unknown well-formed token is closed with 4010
`tokens_pending` instead: not final, the page just retries. The flag lives in
the PC socket's attachment, so it survives hibernation, and every new PC
connection starts without it. With the PC away the stored list answers 4009,
which may be stale, so the page still takes removal as final only after three
4009 closes in a row with no frame or other close code between them, spread
over at least 30 s (`UNKNOWN_TOKEN_GRACE_MS` in `public/net.js`).

The PC connects before it shows the QR code, so the room exists when the phone
scans it. A new PC connection with the right key replaces the old one (close 4000).

## Stored data

Room object: `last_pc` (ms of the last PC connect; the room exists while it
is set), `tokens` (SHA-256 hashes of phone tokens, max 50), `last_seen` (ms,
set when the PC socket closes), `pair_hits` (timestamps of the last minute).
An alarm deletes all of it 30 days after the PC was last connected; every PC
connect moves that date forward, and a PC that stays connected keeps it alive.
After a deletion (or `reset_room`) the next PC connect creates the room again
and its `welcome` says `tokens: 0`; the bridge then sends its devices' token
hashes again with `register_tokens` and `replace:true`.
IP object: `hits` (pairing attempts) and `rooms` (new rooms) timestamps,
deleted by an alarm one hour after the last one. No frame content is ever
stored; frames are forwarded from memory. Hashes are unpadded base64url of
SHA-256 over the UTF-8 token string.

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
| `{type:"error", error}` | `bad_json`, `unknown_type`, `bad_hashes`, `bad_hash`, `too_many_tokens`, `too_large` (frame over 1 MiB, dropped) |

PC -> relay:

| Frame | Effect |
|---|---|
| `{type:"to", conn, data}` | send the string `data` to that phone or pairing socket. A pairing socket is closed (4002) right after its one reply |
| `{type:"register_tokens", hashes:[...], replace?:bool}` | add token hashes; with `replace:true` the list becomes exactly `hashes` and phones on other tokens are closed (4001). Send this **before** the `pair_ok` that carries the token |
| `{type:"drop_token", hash}` | forget one hash and close its phones (4001) |
| `{type:"reset_room"}` | delete everything stored for this `pair_id` and close all sockets (4006); for "turn remote control off and forget" |

Relay -> phone (not from the PC): `{type:"no_room"}` on a pairing socket (see
above), `{type:"pc_offline", last_seen}` (on connect
while the PC is away, on every frame sent while it is away, and when it
leaves) and `{type:"pc_online"}` (the PC reconnected; the phone starts a new
session). `{"type":"ping"}` from any socket is answered with `{"type":"pong"}`
by the runtime without waking the object.

Close codes: 4000 replaced PC, 4001 token dropped, 4002 pairing reply
delivered, 4003 pairing socket older than 6 minutes, 4004 bad frame (binary,
over 64 KiB from a phone, second pairing request), 4005 PC offline during
pairing, 4006 room reset, 4007 room deleted after 30 days without the PC
(phones keep retrying), 4008 no such room (pairing socket), 4009 unknown or
missing phone token (the page forgets its pairing after three in a row over
at least 30 s), 4010 phone token not known yet because the connected PC has
not replaced its token list on this connection (phones keep retrying; it
restarts the 4009 count).

Frame limits count UTF-8 bytes, not characters: 64 KiB for a phone or pairing
frame (the socket is closed with 4004), 1 MiB for a PC frame (answered with
`error: too_large` and dropped; the PC stays connected). The bridge must split
anything larger, such as a long chat history, across several replies.

## Byte layouts

Binding definitions, including the messages outside the encrypted channel:
[`docs/remote-control.md`](../docs/remote-control.md), "Byte layouts".
`test/vectors.json` holds fixed keys and every intermediate value there
(`pair_id` with its hash input, mac input, K_static, SAS, K_pair, pair_ok,
hello and hello_ack with their tag inputs, session keys, five frames).
Plaintexts there are compact JSON
(Python: `json.dumps(obj, separators=(",", ":"), ensure_ascii=False)`).
`test/nodeimpl.mjs` is a second implementation with `node:crypto` that also
shows the PC's checks (`pcHandlePairRequest`, `pcHandleHello`).

## What the page expects inside the channel

The doc fixes the request names; these reply shapes are what `app.js` reads
(Backend/app/remote/rpc.py and chats.py send them). The bridge may add fields.
The DOM-free logic is in `net.js` and tested in `test/page.test.mjs`.

- reply: `{id, ok:true, result}` or `{id, ok:false, error, ...}`;
  `answer_card` conflict: `{id, ok:false, error:"already_answered", by, at}`
  where `by` is `desktop`, `phone:<name>` or `system` (timeout, Stop). A reply
  over ~700 KB comes as several frames with the same `id`, `part` (1-based) and
  `parts`; every list in `result` is cut in order. The page joins them before
  the request resolves (at most 64 parts / 32 MiB, and it fails when no part
  arrives for 20 s). Replies nobody waits for are dropped.
- `list_chats` -> `{chats:[{chat_id (string), title, provider, model, status: "running"|"idle"|"awaiting_card", last_activity (ms), hidden}]}`
- `pending_cards` -> `{cards:[{card_id, chat_id, kind, title, detail, choices?}]}`.
  Question cards (`kind:"question"`) with `choices` (`{id, label}`, id = the
  option label) send `answer_card {card_id, decision:"choice", choice:id}`;
  a question without `choices` (several questions or multi-select) cannot be
  answered from the phone and shows "Bu soruyu bilgisayardan cevaplayın".
  Other cards send `decision:"approve"`; "Reddet" always sends `decision:"reject"`.
- `open_chat {chat_id}` -> `{chat_id, messages:[{role, text, at, truncated?}], events:[event...], epoch, last_seq}`;
  a list item too big for one frame arrives as `{truncated:true}`.
  An open reply for a chat the page no longer shows is not rendered, and the
  page sends `close_chat` for it again (the bridge may have registered its
  listener after the first `close_chat`); an `event` for a chat not shown does
  the same, at most once per 10 s per chat.
- `send_message {chat_id, text}` (at most 20 000 characters and one 64 KiB frame) -> `{status:"accepted"|"desktop_not_ready"}`
- `list_slash_commands {chat_id}` -> `{commands:[name], skills:[name], meta:[{name, description?, argumentHint?, insert?, displayName?}]}`
  (names without the `/`; Gamachine's own `compact` always leads, a chat whose
  agent has no catalog gets only that).
  A text starting with `/` is sent with `send_message` like any other.
- `get_config` -> `{approval_mode:"auto"|"balanced"|"step"}` (more keys may join);
  `set_approval_mode {mode}` -> `{mode, previous, approved_pending}`, or
  `{ok:false, error:"bad_mode"|"unavailable"}`, or
  `{ok:false, error:"agy_step_refused", message, params:{pids}}` when the mode
  did not change because a running agy process could not be gated.
- `stop` -> `{status:"ok"|"no_session"|"error"}`; `close_chat`, `push_subscribe {subscription}` -> `{}`
- pushes: `{type:"event", chat_id, seq, kind, ...}` with kinds `text {text}`,
  `tool_call {tool, summary}`, `turn_start`, `turn_end {status: "done"|"error"|"stopped"}`,
  `card_opened {card_kind, tool}`, `card_closed`; `{type:"chat_changed", chat?}`
  (without `chat` the list is reloaded; a hidden idle chat leaves the list);
  `{type:"card_opened", card}`; `{type:"card_closed", card_id}`;
  `{type:"gap", chat_id, epoch, last_seq}` when live events were lost: the page
  opens the shown chat again.

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
