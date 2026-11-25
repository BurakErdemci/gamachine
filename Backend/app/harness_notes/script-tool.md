---
title: What is run_script and when should I use it instead of direct tool calls?
keywords: betik, script, javascript, kod modu, code mode, toplu, paralel, süzme, filtre, sandbox, kum havuzu, zaman aşımı, run_script, batch, parallel, filter, timeout
covers: run_script
---
run_script runs a short JavaScript program in a sandbox; the program calls your Gamachine tools as async functions (`await tools.<name>({...})`) and only its return value, console output and the list of calls it made come back to you.

- Use it for several dependent, repeated or parallel tool calls (Promise.all runs them at once) and to filter, count or pick fields out of big results, so they never fill your context. Use direct calls for one or two calls, for steps where you must judge each result first, and to look at a result whose shape you do not know yet. C# in the Unity Editor is execute_code, not run_script.
- Callable groups, only those this chat's agent has: CLI agents with the unityai server (Codex, agy, OpenCode, Cursor, Kimi, Copilot) get its file, shell and chat-mail tools; every agent gets the Unity MCP tools (Claude without the C# script writers: write .cs with Write/Edit), the browser panel's read-only tools when the browser is on (navigate, search, snapshot, tabs; click, typing, forms and downloads stay direct calls), the connector tools (e.g. blender_*) when a connector is on, and Claude also list_chats / send_chat_message. Computer-use tools are never callable from a script: each step there needs your eyes on the screen.
- Every inner call passes the same rules as a direct call: approval mode, the balanced-mode risk check, the Unity file rule, the Unity MCP server's gate, the browser and connector gates in the backend, the mail limits; in Claude chats each call first passes the same permission check as a direct call. A refusal that carries the user's comment throws an Error the script may catch; a plain refusal (a declined Blender card included) or Stop ends the script.
- The sandbox (QuickJS compiled to WebAssembly, run by the app's own Electron) has no files, network, processes, timers or require; it acts only through the tools.
- Time: the agent's CLI cancels one long MCP call on its own (measured 25 Nov 2026 from code: Codex 60 s by default, Gamachine sets 660 s for unityai; agy 180 s fixed; Copilot 180 s; Kimi and OpenCode likely 60 s; Cursor unknown; Claude about 27 h). So a script may run up to 600 s in Claude and Codex, 150 s in agy and 50 s in the others; the tool description states this agent's limit. Split longer work into several scripts.
- Other limits: 200 tool calls, 64 MB memory, 300 s per call. The reply is capped at about 6 KB; the full text goes to .gamachine/script-runs/ in the workspace and the reply names the file. An error reply lists the calls that already ran, in order, so nothing is redone blindly.
- Without Electron or a `node` on PATH (for example a Docker backend) run_script answers that it is unavailable.

Sources: Backend/app/script_tool.py, Backend/script_runner/runner.mjs, Backend/app/unity_ai_mcp/tools/script_tools.py, Backend/app/unity_ai_mcp/script_server.py, Backend/app/browser_use/server.py.
