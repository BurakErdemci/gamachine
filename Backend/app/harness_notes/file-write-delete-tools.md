---
title: How do write_file, save_file and delete_file work and when do they ask for approval?
keywords: yaz, sil, dosya, kaydet, onay, write, save, delete, remove, create, edit, diff, approval card
covers: write_file, save_file, delete_file
---
write_file and delete_file are the API-loop tools; save_file and delete_file are the same actions on CLI providers.

- write_file(file_path, content): writes the whole file and creates missing folders. On the API / Ollama path it writes directly without a card (docs/security.md calls this a deliberate trade-off); it is confined to the workspace.
- save_file(path, content): the CLI-provider name of the same action. It goes through the approval bridge, so a diff card can appear; the CLI's own built-in write tools are disabled so writes use this channel.
- delete_file: asks for approval (a delete card) on the API loop and on CLI providers, subject to the approval mode (see approval-modes).
- Fixed rule in every mode and for every provider: inside a Unity project you cannot write, delete, move or rename a .meta file, and cannot write Unity YAML assets (scene, prefab, material, controller, anim ...) as raw text. Use the Unity tools instead (see unity-file-rule).
- A path outside the workspace is refused.
- Read-only runs refuse both tools with "Yan sohbet salt okunur; bu araç reddedildi."

Sources: Backend/app/tools/file_tools.py, Backend/app/unity_ai_mcp/tools/file_tools.py, Backend/app/agentic/agent_runner.py, docs/security.md.
