---
title: What do the approval modes auto, balanced and step mean, and which actions open an approval card?
keywords: onay modu, otomatik, güvenli otomatik, adım adım, kart, onay, approval mode, auto, balanced, step, card, permission
covers: 
---
The app has one global approval mode, shared by all agents and providers.

- auto: no approval card anywhere. balanced (recommended, the default on a fresh install): a card only for actions the risk classifier cannot prove routine. step: every write asks.
- A saved choice survives restarts. Only the app UI can change the mode; a paired phone can too (see remote-phone-limits). A backend without the UI secret reads any mode as step.
- Always refused, in every mode and without a card: writes to .meta files or Unity YAML assets as raw text (see unity-file-rule).
- Cards in step mode: file diffs from CLI agents (save_file), deletes, terminal commands that are not safe read-only ones, and every Unity MCP call classified as a write. Reads such as read_console, compile_status, find_in_file need none. Some scene queries (manage_scene, find_gameobjects ...) count as writes because they refresh the project first.
- API providers' write_file writes directly without a card.
- execute_code opens a card whenever the mode is not auto.
- Computer use and browser have their own rules (see computer-use-safety, browser-form-tools).
- Wait for a card; never retry a refused action another way.
- harness_status shows approval_mode.

Sources: Backend/app/agentic/approval_mode.py, docs/security.md, Backend/app/agent_guide.py, docs/remote-control.md.
