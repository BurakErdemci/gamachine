---
title: How do API providers and CLI providers differ in tools and tool names?
keywords: sağlayıcı, model, api, cli, abonelik, ollama, araç adı, provider, claude code, codex, gemini, openai, tool names, save_file, write_file, gm_browser, gm_computer, mcp
covers: 
---
Providers fall into three groups: subscription CLI agents (Claude Code, Codex, Antigravity/agy, Copilot, Cursor, OpenCode, Kimi Code), cloud APIs (Anthropic, Google, OpenAI, NVIDIA NIM, z-ai, Groq, DeepSeek, Moonshot, OpenRouter) and local Ollama. Each chat keeps its own provider and model.

- API loop: the model calls built-in function tools. File: read_file, write_file, delete_file, list_directory, search_in_project, find_files; shell: run_command; plus capture_unity_screenshot, list_chats, send_chat_message and harness_*.
- CLI providers that mount the unityai server get its tools over MCP: read_file, list_directory, save_file (instead of write_file), delete_file, and bash / run_terminal_command / execute_shell_command (instead of run_command), list_chats, send_chat_message, harness_*. Their built-in write tools are disabled.
- Claude chats mount no unityai server: harness_* come from the gamachineHarness server (also in read-only sessions) and list_chats / send_chat_message from gamachineMail. agy side turns get no harness_* tools.
- Unity MCP tools: API providers see them by their own names; CLI providers see mcp__unityMCP__<name>.
- Computer use and browser tools: the API loop sees gm_computer__<tool> and gm_browser__<tool> (via stdio servers started per run); CLI providers use the gamachineComputer and gamachineBrowser MCP servers.
- Writes: CLI providers show a diff card in step mode; API write_file does not, and in balanced mode asks only for a protected path (see approval-modes).
- Ollama cannot call tools for computer use and browser; other tool support depends on the model.

Sources: docs/providers.md, docs/architecture.md, docs/computer-use-providers.md, Backend/app/agentic/stdio_toolset.py, Backend/app/unity_ai_mcp/server.py, Backend/app/unity_ai_mcp/harness_server.py, Backend/app/agentic/agent_runner.py (_run_claude_session), docs/security.md.
