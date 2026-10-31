---
title: Why was my write to a .meta, scene or prefab file refused?
keywords: meta, sahne, prefab, yaml, reddedildi, yasak, unity dosya kuralı, refused, scene, material, guid, raw text
covers: 
---
Inside a Unity project (a folder with Assets/ and ProjectSettings/) agents may not touch two kinds of files with file tools or the shell.

- .meta files: no write, create, delete, move or rename. A lost or duplicated .meta breaks the asset GUID and every reference to it.
- Unity YAML assets (.unity, .prefab, .asset, .mat, .controller, .anim, .physicMaterial ...): no raw-text write, and no raw delete or move.
- This is a rule, not an approval card. It is checked before the approval mode, so auto, balanced and step all refuse. Outside a Unity project nothing is refused.
- What to use instead: the Unity MCP tools (manage_asset for move, rename and delete; manage_scene; manage_prefabs; manage_material ...). They make Unity write the file and move the .meta with it.
- Shell commands are checked by a heuristic: direct forms are caught, indirect ones (variables, scripts, python -c) can slip through. execute_code and execute_menu_item cannot be checked.
- The Unity MCP server refuses any call that would write a .meta file, before any card.

Sources: docs/security.md section 5, docs/unity-mcp.md, Backend/app/unity_file_guard.py.
