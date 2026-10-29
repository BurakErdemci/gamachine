# Agent browser (v4) — design

Status: design, 29 Oct 2026. Owner decisions: vault `Projeler/Gamachine/V4_Roadmap.md`
(13 Oct purpose, 27 Oct freedom, 28 Oct scope and engine).

## What it is

A browser panel inside Gamachine that the agent drives while the user watches. Main use:
finding game assets (animations, packs, sounds) and showing candidates. The agent may
navigate, read, scroll, click and fill forms freely. Submitting, logging in, paying,
deleting, publishing and downloading need the user's approval. Logging into the user's
own accounts is out of scope for v4.

Engine: Electron's own Chromium in a `WebContentsView` on a separate, non-persistent
session (`partition: 'gamachine-agent-browser'`, no `persist:` prefix). No extra download,
no Playwright, no browser extension.

## Non-negotiables

1. The view never loads `preload.js` and gets `nodeIntegration: false`,
   `contextIsolation: true`, `sandbox: true`. It cannot reach `window.ipc`.
2. The session denies every permission request (camera, mic, geolocation, notifications,
   clipboard read, ...) and cancels every `will-download`. Downloads are a v4.1 feature
   behind a card.
3. Navigation allow-list: `http:` and `https:` only. Refused: `file:`, `data:` (top level),
   `javascript:`, `chrome:`, `devtools:`, and any host that resolves to loopback, link-local
   or private ranges (127.0.0.0/8, ::1, 10/8, 172.16/12, 192.168/16, 169.254/16, fc00::/7,
   `localhost`, `*.localhost`). The Gamachine backend and MCP servers live on loopback;
   the page must not reach them. The check runs in `will-navigate`, `will-redirect` and
   `will-frame-navigate`, and in `session.webRequest.onBeforeRequest` for sub-resources.
4. `setWindowOpenHandler` denies pop-ups; the URL is offered to the agent as a link, not
   opened.
5. On-screen page text is data, never instructions. The MCP tool descriptions say so,
   with the same sentence the computer-use tools use.
6. The Stop shortcut and button halt the browser too (same `_computer_halted` set).
7. Off by default; a separate Settings switch `browser_use_enabled`.

## Processes and channels

```
model ──MCP stdio──▶ gamachineBrowser server (thin; backend binary sub-command)
                        │  POST /browser/command (X-Session-Token) {tool, args, conversation_id}
                        ▼
                     backend: browser gateway (policy, describe, cards, one-time grants, halt)
                        │  POST /cmd {op, conversation_id, args}  (Bearer <host token>)
                        ▼
                     Electron main: BrowserHost (127.0.0.1:<random port>)
                        │
                        ▼
                     WebContentsView (agent session)  ──event 'browser-live'──▶ renderer panel
```

The host token is known only to Electron main and the backend process (owner decision,
29 Oct 2026). Main reports `{running, port, token}` with `POST /browser/runtime`
(X-Session-Token + UI secret), exactly like `POST /computer-use/runtime`. No endpoint
returns the port or the token: `GET /browser/settings` answers `{enabled, running}`. Every
browser command, from the MCP server or from anything else holding the local app token,
goes through `POST /browser/command`, so the navigation check, the critical-step
classification, the card and the one-time grant cannot be skipped. Approving a card
needs the UI secret (main) or a paired remote device's answer through the remote answer
path (recorded with the device in the card ledger); one approval runs the step once,
whether answered inside the 2 s wait or later. The host still enforces its own navigation
allow-list and connected-address check. The token is a fresh 32-byte random value per
launch and is compared in constant time. Docker backend mode is not supported in v4 (the
container cannot reach the host's loopback); `running` is false there and the tools say so.

## Command contract (main side)

`POST http://127.0.0.1:<port>/cmd`, header `Authorization: Bearer <token>`, JSON body
`{ "op": string, "conversation_id": int, "args": object }`, 64 KiB body cap, one command
at a time (a queue; a second caller waits). Reply is always HTTP 200 with
`{ ok: boolean, error?: string, url: string, title: string, ... }`.

| op | args | extra reply fields |
|---|---|---|
| `navigate` | `{url}` | — |
| `snapshot` | `{max_chars?}` (default 20 000) | `text` (readable text), `elements`: `[{ref, role, name, href?, value?, kind}]` (links, buttons, inputs, selects; at most 300), `snapshot_id` |
| `click` | `{snapshot_id, ref}` | — |
| `fill` | `{snapshot_id, ref, value}` | — |
| `select` | `{snapshot_id, ref, option}` | — |
| `press` | `{key}` (Enter, Tab, Escape, arrows, PageUp/Down) | — |
| `scroll` | `{direction: 'up'\|'down', amount?}` | — |
| `back` / `forward` / `reload` | `{}` | — |
| `screenshot` | `{}` | `image` (PNG base64, max 1280 px wide) |
| `describe` | `{snapshot_id, ref}` | `element`: `{role, name, tag, type?, href?, download?: bool, submits_form?: bool, form_method?: 'get'\|'post'\|'dialog', form_action?, form_has_password?: bool, is_password?: bool}` |
| `halt` | `{}` | — (stops loading, clears pending input) |

Refs are only valid for the `snapshot_id` they came from; a stale id returns
`{ok:false, error:'stale_snapshot'}`. Element refs are assigned by a script injected
with `webContents.executeJavaScript` in an isolated world (`executeJavaScriptInIsolatedWorld`),
never in the page's main world.

The host shows the panel (`browser-live` event `{active, conversationId, url, title,
loading}`) whenever a command runs, and accepts renderer invokes on channel
`browser-live`: `{op:'status'|'set-bounds'|'visible'|'halt'}`. `set-bounds` positions the
native view over the panel's placeholder; `visible:false` hides it (a native view paints
above the DOM, so menus and cards must not sit under it).

## Policy (backend side)

`browser_navigate`, `browser_snapshot`, `browser_scroll`, `browser_back`,
`browser_forward`, `browser_reload`, `browser_screenshot`: allowed without a card when
the switch is on and the chat is not halted.

`browser_click`, `browser_fill`, `browser_select`, `browser_press(Enter)`: the server
first calls `describe` and classifies:

- **critical** when any of: `computer_use.policy.critical_name` matches the element's
  name or its form's submit button name; the click submits a form whose method is not
  GET or that contains a password field (`type=submit`, a button inside a form, or Enter
  in a form input; GET forms such as site search are navigation and stay allowed, because
  asset search is the main use); the field is a password field
  (filling it always needs approval); the element is a link whose target is a download
  (`download` attribute or a known archive/installer extension).
- otherwise allowed.

A critical step **does not block**. The server opens an approval card
(`/mcp-approval-request`, tool `browser_<op>`, `risky: true`, `risk_reason` naming the
action, the element and the page host) with a 2-second wait. If the card is not answered
in that time the tool returns at once:

> `needs_approval: <reason>. An approval card is open in the chat. Tell the user what
> you want to do and why, then wait for their next message before trying again.`

When the user approves the card (on the desktop or from a paired remote device) after
the gateway's wait has ended, the backend stores a one-time grant keyed by
`(conversation_id, op, url, element fingerprint, value hash)`. For click, fill and select
the fingerprint (role + name + form action, normalised) also carries the call's snapshot
id and ref, so the grant covers that one element of that snapshot only; Enter keeps the
element fingerprint. The agent's next identical call finds the grant, consumes it and
proceeds without a new card. An approval answered inside the wait runs the step in that
same call and leaves no grant; a Stop pressed after the approval still stops the step.
A rejected card, any successful navigation (navigate, back, forward, reload), a halt or
10 minutes without use drop the grant. Only `_browser_card` may create `browser-` gates;
`/mcp-approval-request` refuses those ids and any id already in use (409). The chat text
never approves anything; only the card does.

## Work packages

Each package owns its files; the shared files below have exactly one owner.

**WP-A — Electron host (opus-medium).** New `Frontend/frontend/main/helpers/browser-host.ts`
(view, session hardening, allow-list, snapshot/refs, input, screenshot, bounds, halt) and
`browser-server.ts` (loopback server, token, queue, runtime report and reconcile like
`computer-use.ts`). Shared, owned here: `main/background.ts` (construct, register
`browser-live` with `handleSecure`, shutdown), `main/helpers/ipc-whitelist.ts`
(`browser-live`), `main/helpers/create-window.ts` only if the main window's
`will-attach-webview` rule needs no change (expected: unchanged). Tests with faked
`WebContentsView` and session.

**WP-B — backend MCP server, policy, cards, provider registration (sonnet-medium).**
New `Backend/app/browser_use/{__init__,registration,settings,policy,server}.py` mirroring
`computer_use/` (SERVER_NAME `gamachineBrowser`, tool prefix, `server_argv` sub-command
`browser-mcp-server` in `Backend/main.py`). Shared, owned here:
`routes/conversation_routes.py` (`/browser/runtime` POST, `/browser/settings` GET/PUT,
`browser_` branch next to the `computer_` one at :2467 with halt check, journal,
activity, grants), `agent_runner.py` (Claude entry next to :2325 and the rebuild trigger),
`claude_sdk_session.py` (auto-allow by prefix next to :1108), `codex_session.py` (next to
:826), `agentic/agent_guide.py` (one guidance block). Cursor, OpenCode and the API loop
are out of scope for v4.

**WP-C — renderer panel, card, setting (sonnet-medium).** New
`renderer/components/home/BrowserPanel.tsx` (placeholder measured with
`ResizeObserver`, address bar, loading state, Stop, idle state; hides the native view
when a menu, modal or card overlaps) and `BrowserApproval.tsx` on `ApprovalCard`. Shared,
owned here: `renderer/lib/workspacePanel.ts` (`'tarayici'` tab), `Workspace.tsx`
(`TAB_KEY`, `PANE_ANCHOR`), `pages/home.tsx` (pane + reveal on `browser-live` active),
`lib/i18n.tsx` (tr + en), `hooks/home/useMCPApproval.ts` and `McpApprovalCards.tsx`
(`browser_` prefix → `BrowserApproval`), `lib/computerStep.ts` (step rendering), the
Settings page switch, the workspace CSS. Depends on WP-A's `browser-live` contract only;
develop against a mock.

Order: A and B in parallel against this contract; C in parallel against a mocked
`window.ipc`. Integration order B → A → C. Each package gets the usual verify round.

## Acceptance (measured at the end, live)

1. With the switch off, no `gamachineBrowser` tools reach the model.
2. "Find a free CC0 walk-cycle animation" ends with candidates shown, no card.
3. A form submit, a password fill and a "Buy" click each open a card and the agent says it
   is waiting; approving the card lets the identical retry through; rejecting it does not.
4. `http://127.0.0.1:<backend port>/` and `file:///` are refused.
5. Stop halts an in-flight navigation and denies the next browser tool until the user's
   next message.
