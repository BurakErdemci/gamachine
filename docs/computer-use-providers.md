# Computer use and the agent browser for every provider (v4) — design

Status: design, 29 Oct 2026. Owner decisions (vault `Projeler/Gamachine/V4_Roadmap.md`, 29 Oct):
every CLI and API provider gets computer use and the agent browser; API providers use the same
Gamachine tools first, the vendors' own computer tools come later as a quality upgrade. Ollama
cannot call tools and stays out.

## Today

`gamachineComputer` (`Backend/app/computer_use/`) and `gamachineBrowser`
(`Backend/app/browser_use/`) are stdio MCP servers. Only two paths register them:

- Claude SDK: `registration.claude_server_entry(conversation_id)` in `mcp_servers_cfg`
  (`agentic/agent_runner.py`), the tool prefix auto-allowed in `claude_sdk_session._can_use_tool`,
  session rebuilt when the entry changes.
- Codex: `registration.codex_thread_entry(forward_env)` in the thread config, approval mode
  `approve`, session rebuilt on `_computer_changed` / `_browser_changed`.

Everything that matters for safety already lives in the backend and does not depend on the
provider: cards (`/computer-use-request`, `/browser/command`), the halt set, the app grants and
the settings switch. A server only needs `GAMACHINE_CONVERSATION_ID` in its environment; without
it computer-use calls are unowned (no halt) and the browser server refuses every call.

## Rule for every new path

1. Register both servers only while their switch is on (`settings.enabled_now()`), never for a
   read-only side turn.
2. The server process gets the chat id: in the entry's env where the config is per chat, else
   through the CLI's own environment (`conversation_env`) when the CLI forwards its environment
   to MCP children (measured for copilot, opencode, agy).
3. The CLI's own permission prompt must not stop the tools: the backend owns the cards.
4. Flipping a switch takes effect on the next turn at the latest.
5. The agent guide gets the same computer-use / browser lines as the Claude and Codex paths
   (`agent_guide.with_computer_use`, `with_browser_use`).

## Work packages

### P1 — one-shot CLIs: copilot, cursor, kimi, opencode (sonnet)
They rebuild their MCP config every turn from `BaseCLIProvider._product_mcp_servers_stdio`
(`providers/cli_base.py`), so a switch flip is picked up on the next turn without a rebuild.
- Add both entries to that builder (shared helper in the two `registration.py` modules for the
  generic stdio shape; skip when off or read-only).
- copilot: entry env carries the chat id; add `--allow-tool gamachineComputer` and
  `--allow-tool gamachineBrowser` while the entries exist.
- cursor: `.cursor/mcp.json` is shared per workspace, so the id comes from the CLI env
  (`--approve-mcps --force` already allow MCP calls). Env forwarding to MCP children is
  unmeasured: if the server sees no id, the live test shows it (browser refuses with "needs a
  chat").
- kimi: per-turn `--mcp-config-file`; add the `mcp__gamachineComputer__*` /
  `mcp__gamachineBrowser__*` allow rules next to `mcp__unityai__*`.
- opencode: workspace `opencode.json`; id from the CLI env (measured forwarding). Add explicit
  `permission` allow entries for the two servers if opencode's schema supports per-MCP
  permissions; otherwise record what headless opencode does with an unlisted MCP tool.
- Guide lines through `agent_guide.provider_guide`.

### P2 — agy (sonnet)
agy reads one global `~/.gemini/config/mcp_config.json` and runs one long-lived child per chat.
- `_register_mcp` writes or removes both entries, like `unityMCP` today. Their env holds no chat
  id (the file is shared); the id already reaches MCP children through the spawn env.
- The session records the computer/browser state it was spawned with; `stream` restarts the
  child when the desired state differs (same idea as Codex `_computer_changed`).
- The step-gate hook: confirm in a test that `call_mcp_tool` to the two servers is allowed in
  auto, step and balanced, and still denied in side mode.

### P3 — API loop: Anthropic, Gemini, OpenAI-compatible (opus-medium)
No MCP client in the loop today besides Unity's HTTP client. New module (e.g.
`agentic/stdio_toolset.py`) using `mcp.client.stdio.stdio_client`:
- Per API run: open the enabled servers with the same argv and env as the Claude entry (chat id
  included), `list_tools`, expose them as tool definitions next to the built-ins in
  `_all_tool_definitions()` (names prefixed so they cannot clash), route calls back, map an
  `ImageContent` result to the loop's existing `image_base64` path, close every process when the
  run ends (and on cancel).
- Re-check the switches each loop iteration: a switch turned off closes the toolset and removes
  the tools; turned on opens it.
- Tool timeout longer than the 150 s card wait (Codex uses 180 s).
- Not for `_run_simple` (ollama) and not in read-only runs (`_read_only_tool_allowed`).

### P4 — UI and guide text (sonnet)
`cu.set.providers` in `renderer/lib/i18n.tsx` (both languages) says computer use works with every
provider except Ollama; `computer-use-providers-note.test.tsx` follows. Same note on the browser
settings page if it has one.

Order: P1, P2 and P4 in parallel; P3 alone (it touches the API loop in `agent_runner.py`). P2
must not touch `agent_runner.py`.

## Acceptance

Scalar: number of providers where a screenshot tool call from a chat reaches the model and the
card/Stop work, measured live: target 9 of 10 (claude, codex, copilot, cursor, kimi, opencode,
agy, Anthropic API, Gemini API, OpenAI-compatible API); kimi may stay unmeasured (no
subscription), then 8 of 9 measured. Per provider, live:
1. With the switch on, the agent lists and calls a computer tool; the screenshot reaches the
   model.
2. A critical step opens the card; approving runs it once; Stop refuses the next call.
3. Switch off, next turn: the tools are gone.
4. A browser tool call from the same provider carries the chat id (no "needs a chat" refusal).
