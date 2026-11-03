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
- `POST /agent-bridge/chats/{id}/messages` - sends; returns `{status, since_seq, epoch, busy_at_send}`.
- `GET /agent-bridge/chats/{id}/events?since=N` - turn events after N.
- `GET /agent-bridge/chats/{id}/messages?limit=N` - recent stored messages.

## Frame contract

A sent message is the phone's `remote_message` frame with `source: "agent"`, `device_id: "agent:<client>"` and `device_name: <client>`. The client name is the MCP client's declared name, else `GAMACHINE_AGENT_NAME`, else `Terminal`; it is stripped of control and bidi characters and cut to 48 characters.

## Never exposed

Answering cards, changing the approval mode, halting computer use and stopping a turn. An agent must not approve its own risky step: those wait for the user in Gamachine.

## Reply detection

Nothing links a sent message to a turn. The send route returns the chat's last event `seq` and the process `epoch`; the reply is the first turn that starts after that `seq` (if the chat was busy at send, the first turn that starts after the running one ends). A wake turn (`origin: "wake"`, a turn Gamachine starts itself after background jobs or mail) is never the reply and is skipped. The MCP server polls the events every second until that turn ends or the wait runs out, then says `still running` and how to continue: `gamachine_wait_reply(chat_id=..., since=..., epoch="...")`. A different `epoch` means Gamachine restarted; the result says the reply may be lost instead of reading another process's turn. The ring keeps 500 events per chat, so a long tool-heavy turn can lose its `turn_start`: when a `turn_end` is left without its start, the reply text is read from the last stored assistant message and the result says so; a turn that is still open keeps waiting.

Known limit: a message the user types into the same chat at the same moment can be taken as the reply turn.
