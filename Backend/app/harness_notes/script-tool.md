---
title: What is run_script and when should I use it instead of direct tool calls?
keywords: betik, script, javascript, kod modu, code mode, toplu, paralel, süzme, filtre, sandbox, kum havuzu, run_script, batch, parallel, filter
covers: run_script
---
run_script runs a short JavaScript program in a sandbox; the program calls your Gamachine tools as async functions (`await tools.<name>({...})`) and only its return value, console output and the list of calls it made come back to you.

- Use it for several dependent, repeated or parallel tool calls (Promise.all runs them at once) and to filter, count or pick fields out of big results, so they never fill your context. Use direct calls for one or two calls, for steps where you must judge each result first, and to look at a result whose shape you do not know yet. C# in the Unity Editor is execute_code, not run_script.
- Which tools a script can call depends on the agent: CLI agents with the unityai server (Codex, agy, OpenCode, Cursor, Kimi, Copilot) get its file and shell tools plus the Unity MCP tools; Claude chats get the Unity MCP tools except the C# script writers (write .cs with Write/Edit). Computer-use tools are never callable from a script.
- Every inner call passes the same rules as a direct call: approval mode, the balanced-mode risk check, the Unity file rule and the Unity MCP server's own gate. A refusal that carries the user's comment throws an Error in the script, which may catch it; a plain refusal or Stop ends the script.
- The sandbox (QuickJS compiled to WebAssembly, run by the app's own Electron) has no files, network, processes, timers or require; it acts only through the tools.
- Limits: timeout up to 600 s (default 600), 200 tool calls, 64 MB memory, 300 s per call. The reply is capped at about 6 KB; the full text goes to .gamachine/script-runs/ in the workspace and the reply names the file. An error reply lists the calls that already ran, in order, so nothing is redone blindly.
- Without Electron or a `node` on PATH (for example a Docker backend) run_script answers that it is unavailable.

Sources: Backend/app/script_tool.py, Backend/script_runner/runner.mjs, Backend/app/unity_ai_mcp/tools/script_tools.py, Backend/app/unity_ai_mcp/script_server.py.
