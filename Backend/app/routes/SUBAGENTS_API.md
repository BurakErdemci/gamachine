# Sub-agents, fuse, Script tool and queue: the desktop UI contract

Every route takes the app token in `X-Session-Token`, the same token the CLI tools use; nothing else
is needed for the desktop UI. `conversation_id` is always the main (parent) chat. A chat id that is not
the local user's answers 404; a side chat 403 (sub-agent routes) or 400 (queue routes); a sub-agent's own
chat 403 on the sub-agent routes (depth 1). Errors are FastAPI `{"detail": ...}`; `detail` is a string,
or `{"message", "code", ...}` where a code is listed below.

## GET /subagents/list?conversation_id=N

```json
{
  "agents": [
    {
      "id": 3, "parent_conv": 12, "child_conv": 40, "name": "tests", "provider_type": "subscription",
      "model_name": "gpt-6-luna", "effort": "high", "status": "running",
      "created_at": 1764090000.0, "updated_at": 1764090000.0, "last_result": null,
      "progress": {
        "phase": "running_tool",
        "step": {"tool": "commandExecution", "target": "npm test", "seconds": 12},
        "steps_done": 4, "steps_failed": 1,
        "elapsed_s": 185, "last_said": "Running the tests now.", "idle_s": 3,
        "card": null,
        "last_steps": [{"tool": "Read", "target": "src/app.ts", "ok": true, "seconds": 1}]
      }
    }
  ],
  "fuse": {"tripped": false, "count": 2, "limit": 10, "paused": false, "held": 0},
  "text": "#3 \"tests\" gpt-6-luna (effort high): running\n  now: running_tool, ..."
}
```

Row fields other than `progress` are the `subagents` table row as before (times are Unix seconds).

`progress` is `null` unless `status` is `running`. Otherwise:

| field | meaning |
|---|---|
| `phase` | `thinking`, `writing` (streaming text), `running_tool`, `waiting_approval` (an approval or question card of this helper is open), `queued` (input waits, no turn runs yet) |
| `step` | the tool running now, or `null`: `tool` (the provider's tool name), `target` (one line, at most 80 characters: path, first line of a command or script, pattern or url; may be `""`), `seconds` it has run |
| `steps_done` | tool steps finished in this turn, failed ones included |
| `steps_failed` | of those, the ones that failed |
| `elapsed_s` | seconds since this turn started |
| `last_said` | last non-empty line of the text it streamed (at most 160 characters), `""` before any |
| `idle_s` | seconds since its last event (any event: text, tool, status) |
| `card` | `{title, waiting_s}` of its oldest open card while `phase` is `waiting_approval`, else `null`; the card itself is in `/mcp-pending` and the card list with `parent_conversation_id` and `agent_label` |
| `last_steps` | the last 5 finished steps, oldest first: `{tool, target, ok, seconds}` |

`queued`: `steps_done`, `steps_failed`, `elapsed_s` and `idle_s` are 0 and `step` and `card` are
`null`; `last_said` and `last_steps` are the previous turn's.

The record lives in the backend's memory only: after an app restart a helper has none until its next
turn (it is `interrupted` then anyway). Poll this route for the live line; every call reads fresh
values (one database read per helper, no provider call).

`fuse`: `tripped` when `count` (automatic turns of this chat started by results since the user's last
message) reached `limit`; `paused` after the user's Stop in this chat; `held` = results waiting. Show
the Continue button while `held > 0`.

## GET /subagents/fuse?conversation_id=N

The `fuse` object above on its own.

## POST /subagents/continue

Body `{"conversation_id": N}`. The fuse notice's Continue: resets the fuse count, the notice flag and a
Stop's pause, and the held results ride one turn of the chat (origin `subagent`, no user text), as they
would with the user's next message. No running turn is needed.

- 200 `{"status": "started" | "queued", "released": 2, "text": "..."}`; `queued`: a turn of the chat
  is running and the results ride the next one.
- 409 `{"detail": {"message": "...", "code": "nothing_held"}}`: nothing is held (also for a second
  click: results are released once).

## GET /agent-settings/script-tool, PUT /agent-settings/script-tool

GET answers `{"enabled": true}`; PUT takes `{"enabled": false}` and answers the stored value
(400 when `enabled` is not a boolean). Stored as app setting `script_tool_enabled`; unset means on.

Off: Claude sessions do not mount the in-process `gamachineScript` server and get no run_script guide
line; Codex, agy, Cursor, Copilot, OpenCode and Kimi get no guide line and their unityai server does
not register run_script (the CLI's env carries `GAMACHINE_SCRIPT_TOOL=0`). The setting is read when a
session or a CLI turn starts: a session that is already running (a live Claude or Codex session, a kept
agy process) keeps its tools until its next start (a new chat, a model switch, an app restart).

## GET /conversations/{id}/queue

The chat's user messages accepted for a later turn and not yet taken by one, in the order they run:

```json
{"conversation_id": 12,
 "queue": [{"id": 881, "text": "and the tests too", "created_at": "2026-11-25 21:40:03", "order": 1}]}
```

`id` is the message id (the same row `/conversations/{id}/messages` returns); `created_at` is its
timestamp. Only user-origin inputs are listed (not sub-agent results or other background inputs).
These are stored inputs: a message whose window closed while it waited, a sub-agent's messages from its
main agent. A `/chat-stream` request that is still open and waiting at the chat's gate (it got the
`turn_queued` status) is not a stored input and is not listed; closing that request queues its message
here.

## DELETE /conversations/{id}/queue/{input_id}

Remove from queue. The pending mark and the message row are deleted in one transaction, so a withdrawn
message never runs and leaves the chat history.

- 200 `{"status": "withdrawn", "id": 881, "queue": [...]}` (the queue after the removal).
- 409 `{"detail": {"code": "claimed", ...}}`: a turn has already taken it (it runs or ran).
- 409 `{"detail": {"code": "not_user", ...}}`: a pending input that is not a user message.
- 404: no such message in this chat.
