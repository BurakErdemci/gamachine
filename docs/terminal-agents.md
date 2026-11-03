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

The send route reads the chat's last stored message id, then returns a `request_id`. `GET /agent-bridge/requests/{id}` finds the request's own message: the first `user` row after that id whose text is exactly the text sent. The reply is the outcome of the turn that began with that row, as the turn itself recorded it: chat-stream records the turn when it stores the user row and again when it ends, with the assistant row it stored or none (`Backend/app/agentic/turn_replies.py`). Row order is never used, so an older turn that stores its reply late, a wake turn, a system row inside our own turn or a message typed after ours cannot take or hide the reply. A Claude turn whose window went away keeps running and records its reply when it stores it. The reply is read from that exact row (up to 200,000 characters, `truncated` says when cut). Statuses: `queued` (own message not stored yet, a turn is running or under 30 s since the send), `running` (stored, the turn has not ended; `waiting_card` names a pending approval), `done`, `ended_without_reply` (the turn ended and stored nothing, e.g. an error or a stop; `turn_status` is `done`, `error` or `stopped`), `not_delivered` (no message appeared within 30 s and no turn runs; informational, the window may still send it when it is free).

The MCP server polls every second until `done` or `ended_without_reply`, or the wait (wall-clock time) runs out; `queued` and `not_delivered` keep it waiting. On timeout it says `still running; call gamachine_wait_reply(request_id="...")`, or, while the message is still not delivered, that Gamachine did not send it yet and the same call. The turn-in-flight flag is the same one the approval policy uses, not the 500-event ring.

Limits: two identical texts sent to the same chat before either is stored are matched in order, so the second request can read the first one's reply; a backend restart forgets every request and every turn record (the tool then says Gamachine restarted and points to `gamachine_read_messages`); at most 500 requests and the newest 2000 turn records are kept; a turn whose record never ended (for example a detached Claude turn that stored nothing) reads `running` until the chat's next turn.
