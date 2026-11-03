# Terminal agents

A terminal agent (Claude Code, Codex, opencode) can register Gamachine as a stdio MCP server and then list chats, list models, open a chat, set a chat's model, send a message and read the reply. The message appears in the open Gamachine window like a phone message, marked as coming from the terminal.

Register `backend agent-mcp-server` (the exact command is `server_command` in `GET /agent-bridge/settings`). The server is named `gamachine`.

## The toggle

One setting, `agent_bridge_enabled`, off by default ("external agents may access"). While it is off every `/agent-bridge` route except `/settings` answers `403 agent_bridge_disabled`. There is no per-CLI approval. Every route needs the app token (`X-Session-Token`); the MCP server reads it from `~/.unity_architect_ai/local-app-token` and the backend URL from `~/.unity_architect_ai/backend-url` (written at startup, removed at shutdown).

## Routes

- `GET/PUT /agent-bridge/settings` - `{enabled, server_command}`; `PUT {enabled: bool}`.
- `GET /agent-bridge/chats` - id, title, provider, provider_type, model_name, status, busy, last_activity.
- `GET /agent-bridge/models` - the phone's `list_models`.
- `POST /agent-bridge/chats` - new chat in the project open in Gamachine; optional title and model. Publishes `conversation_created` so the window reloads its list.
- `PUT /agent-bridge/chats/{id}/model` - the phone's `set_model`.
- `POST /agent-bridge/chats/{id}/messages` - sends; returns `{status, request_id}` (`status: desktop_not_ready` has no id).
- `GET /agent-bridge/requests/{request_id}` - state of one send: `{request_id, chat_id, status, reply, model, waiting_card, truncated}`; `404 unknown_request` after a restart or 24 h.
- `GET /agent-bridge/chats/{id}/messages?limit=N` - recent stored messages.

## Frame contract

A sent message is the phone's `remote_message` frame with `source: "agent"`, `device_id: "agent:<client>"` and `device_name: <client>`. The client name is the MCP client's declared name, else `GAMACHINE_AGENT_NAME`, else `Terminal`; it is stripped of control and bidi characters and cut to 48 characters.

## Never exposed

Answering cards, changing the approval mode, halting computer use and stopping a turn. An agent must not approve its own risky step: those wait for the user in Gamachine.

## Reply detection

The send route reads the chat's last stored message id, then returns a `request_id`. `GET /agent-bridge/requests/{id}` looks at the stored messages after that id: the request's own message is the first `user` row whose text is exactly the text sent; the reply is the first `assistant` row after it and before the next `user` or `system` row (so a wake turn or another message never counts). The reply is read from the row itself (up to 200,000 characters, `truncated` says when cut). Statuses: `queued` (own message not stored yet, a turn is running or under 30 s since the send), `running` (stored, turn in flight; `waiting_card` names a pending approval), `done`, `ended_without_reply` (the turn ended and stored nothing, e.g. an error or a stop), `not_delivered` (no message appeared within 30 s and no turn runs: the window did not take it).

The MCP server polls every second until `done`, `ended_without_reply` or `not_delivered`, or the wait runs out; then it says `still running; call gamachine_wait_reply(request_id="...")`. The turn-in-flight flag is the same one the approval policy uses, not the 500-event ring.

Limits: two identical texts sent to the same chat before either is stored are matched in order, so the second request can read the first one's reply; a backend restart forgets every request (the tool then says Gamachine restarted and points to `gamachine_read_messages`); at most 500 requests are kept.
