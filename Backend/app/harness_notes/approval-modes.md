---
title: What do the approval modes auto and balanced (Safe Auto) mean, and which actions open an approval card?
keywords: onay modu, otomatik, güvenli otomatik, güvenli oto, kart, onay, approval mode, auto, balanced, safe auto, card, permission
covers: 
---
The app has one global approval mode, shared by all agents and providers.

- There are two modes. auto: no approval card anywhere. balanced (Safe Auto; recommended, the default on a fresh install): a card only for actions the risk classifier cannot prove routine. An old client that still asks for the removed "step" mode gets balanced.
- A saved choice survives restarts. Only the app UI can change the mode; a paired phone can too (see remote-phone-limits). A backend without the UI secret runs as balanced, whatever was saved.
- Always refused, in every mode and without a card: writes to .meta files or Unity YAML assets as raw text (see unity-file-rule).
- Cards in balanced mode: deletes and moves; terminal commands on the danger list (installs, network sends, irreversible git, permission changes, tool config paths, leaving the workspace); Unity MCP actions that delete, remove, move or rename something, run code, change packages, open dialogs or Save As; and any action the classifier does not know. Reads such as read_console, compile_status, find_in_file and notes between chats need none.
- File writes (API write_file, CLI agents' save_file and built-in writers): no card in auto mode. In balanced mode write_file and the other writers get a card for a protected path: a path outside the workspace, the workspace root, any top-level entry starting with a dot, anything under .git, .claude, .cursor, .opencode, .agents, .gemini, .codex, .vscode or ProjectSettings, a file named .mcp.json or opencode.json, manifest.json or packages-lock.json directly inside a Packages folder, or a Windows 8.3 short name (like PROJEC~1) anywhere in the path.
- execute_code opens a card whenever the mode is not auto.
- Computer use and browser have their own rules (see computer-use-safety, browser-form-tools).
- Wait for a card; never retry a refused action another way.
- harness_status shows approval_mode.

Sources: Backend/app/agentic/approval_mode.py, Backend/app/agentic/agent_runner.py (_balanced_approval_prompt), Backend/app/action_risk.py (protected_reason), docs/security.md, Backend/app/agent_guide.py, docs/remote-control.md.
