# Web remote control (v4) — design

Status: design, 29 Oct 2026. Owner decisions: vault `Projeler/Gamachine/V4_Roadmap.md`
(29 Oct "UZAKTAN KONTROL tasarim sorulari" and its correction). Builds on remote control
v1 (`docs/remote-control.md`: relay, pairing, encrypted channel, 14 RPCs).

## What it is, and what it is not

A browser page that looks exactly like the desktop app and remote-controls the paired
desktop Gamachine. It is **not** a browser version of Gamachine. Opened on its own, the page
can do nothing but pair. It keeps no data, settings or keys; every action is a command the
desktop executes, and the page only shows what the desktop sends back. Unpairing, an
expired device (90 days) or a lost connection returns it to the pairing screen.

## Shape

```
browser page (same renderer bundle, remote mode)
   │  window.ipc, axios adapter, fetch  ──▶  RemoteTransport
   │                                          │ encrypted frames over the existing channel
   ▼                                          ▼
relay (static page + opaque frames)  ──▶  desktop backend: remote UI dispatcher
                                              │ allow-listed HTTP routes, in-process
                                              │ allow-listed main-process ops via desktop_channel
                                              ▼
                                          the same handlers the desktop UI uses
```

One renderer codebase. At startup the renderer detects remote mode (served from the relay
origin, no `window.ipc` from preload) and installs three shims before any hook runs:

1. `window.ipc` — a small object whose `invoke(channel, payload)` sends a `ui_ipc` frame for
   the channels on the remote allow-list and rejects every other channel with
   `remote_unavailable` (the UI shows "Bu işlem yalnız bilgisayarda yapılabilir").
2. `axios.defaults.adapter` — sends `ui_http {method, path, query, body}` frames; the reply
   carries status, headers subset and body. The session token header is never set in
   remote mode; the desktop applies the device identity.
3. `fetch` for the backend base URL — same as axios, plus streaming: the desktop sends
   `ui_stream_chunk` frames for streaming endpoints (chat turn stream, wake stream) and the
   shim exposes them as a `ReadableStream`, so `useChat`/`useSideChat` stay unchanged.

## Desktop side: the only place anything happens

`Backend/app/remote/ui_dispatch.py` handles `ui_http`, `ui_stream`, `ui_ipc` from an
authenticated session:

- **HTTP allow-list** of `(method, path pattern)`; each request is executed in-process
  against the FastAPI app (ASGI call) with an internal identity for the device, never with
  the desktop's own session token or UI secret. Anything not listed returns 403.
- **No secrets cross:** routes that read or write API keys, provider credentials, CLI
  installs, approval mode secrets, profile reset, remote-control pairing itself, computer-use
  grants and the browser runtime are never on the list. Model/effort selection stays the
  existing RPCs (lists only, as today).
- **Work parity (owner decision, 29 Oct 2026):** everything the user does at the desk to
  keep the game moving works remotely: chats, cards, file tree, file read and write,
  rename/move/delete inside trusted roots, the terminal (`terminal-spawn/write/resize`
  streamed both ways), scene editor and Unity operations, previews. These IPC channels run
  in Electron main through `desktop_channel` with the same trust checks as local IPC.
  Native dialogs (open-file, open-folder) have no remote equivalent: the remote UI offers a
  picker over the trusted roots instead; authorising a new root stays desktop-only.
  Desktop-only: API keys, provider sign-in, CLI install, remote pairing management,
  profile reset, computer-use app grants. A paired device that is taken can run terminal
  commands; the guards are pairing, the 90-day idle expiry and one-click device removal.
- **Cards** keep their rules: risky cards need the hold on the page; approving a
  computer-use or browser card from remote goes through the existing remote answer path
  (`cards.answer_card(..., device=...)`), which records the device in the ledger.
- **Stop / halt** from remote: a new RPC `halt_computer` sets the same halt set as the
  desktop Stop (added after the agent-browser gateway lane merges; it touches
  `conversation_routes.py`).

Every dispatched request is logged with device id, method and path (no bodies).

## Frame contract (browser ⇄ desktop, inside the existing encrypted channel)

Requests use the v1 request/reply shape (`{id, type, ...}` → `{id, ok, result|error}`,
`relay/public/net.js` `Link.request`, `Backend/app/remote/rpc.py` `Dispatcher.handle`).
Browser → desktop frames are capped at 64 KiB, so larger bodies are uploaded in parts.

| type | params | result / pushes |
|---|---|---|
| `ui_http` | `{method, path, query?, body?, body_b64?, upload_id?}` | `{status, content_type, body?, body_b64?}`; 403 `remote_forbidden` for paths off the allow-list |
| `ui_stream_open` | same as `ui_http` | `{stream_id, status, content_type}`, then pushes `{type:'ui_stream_chunk', stream_id, seq, data_b64}` … `{type:'ui_stream_end', stream_id, error?}` |
| `ui_stream_close` | `{stream_id}` | `{}`; the desktop cancels the underlying request |
| `ui_upload_part` | `{upload_id, seq, data_b64, last}` (≤ 48 KiB decoded per part, ≤ 32 MiB total) | `{received}`; the finished upload is referenced once by `upload_id` |
| `ui_ipc` | `{channel, payload}` | the main-process reply; `remote_unavailable` for channels off the allow-list |
| `ui_ipc_subscribe` | `{channel}` | `{}`; then pushes `{type:'ui_ipc_event', channel, args}` for main → renderer events on that channel (terminal data/exit, browser-live, computer-use-live, hub progress, open-conversation) until the session ends |

HTTP allow-list rules (W1 owns the exact table): everything under the chat, conversation,
workspace, file, scene, Unity, model-list, card and halt routes is allowed. Denied by
default and always: `/api-keys/*`, `/cli-install/*`, `/cli-login/*`, `/profile/reset`,
`/computer-use/grants/*`, `/computer-use/runtime`, `/computer-use/settings` (POST),
`/browser/runtime`, `/browser/settings` (PUT), `/remote/*`, `/approval-mode` (POST; the v1
`set_approval_mode` RPC stays the remote way). Requests run in-process with an internal
device identity. The few allowed routes that require the UI secret (card answer
`/mcp-approval-respond/*`, halt `/computer-use/halt/*`) get it added by the dispatcher from
an explicit, separate list; no other route ever sees it.

## Relay and budget

The renderer export (`Frontend/frontend/renderer/out`) is copied into `relay/public/app/`
at build time and served by prefix (no per-file STATIC map edit). The per-IP request budget
(120/min, `relay/wrangler.toml`) counts static files: the web client loads with one HTML,
a handful of hashed bundles and cached fonts; static paths under `/app/` get their own
higher budget or are exempt, decided in WP-W0 with a measured page-load request count.

## Work packages

- **W0 relay serving (sonnet):** prefix static serving for `/app/`, cache headers for hashed
  files, budget split; build step copying the export. Owns `relay/worker/index.js`,
  `relay/wrangler.toml`, a build script.
- **W1 desktop dispatcher (opus):** `ui_dispatch.py`, allow-lists, in-process ASGI execution,
  streaming, ledger lines, `desktop_channel` ops for the read-only IPC list. Owns
  `Backend/app/remote/*` (new files plus `rpc.py` registration).
- **W2 renderer remote mode (opus):** detection, the three shims, `remote_unavailable` UX,
  pairing screen reuse from `relay/public`, connection-lost banner. Owns new
  `renderer/lib/remote/*` and one bootstrap line in `pages/_app.tsx`.
- **W3 halt RPC + phone CU controls (sonnet):** after the browser gateway merges.

Order: W1 and W2 in parallel against this contract; W0 independent; W3 last.

## Acceptance (live, at the end)

1. A fresh browser at the relay URL shows only pairing; after pairing it looks like the
   desktop app and lists the same chats.
2. Sending a message streams the reply in the browser and on the desktop.
3. Settings → API keys shows placeholders and cannot be changed from the browser.
4. A terminal opened from the browser runs on the desktop and streams both ways; a file
   saved from the browser is changed on the desktop.
5. Removing the device on the desktop drops the browser back to pairing within seconds.
