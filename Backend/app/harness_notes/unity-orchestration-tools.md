---
title: What do batch_execute, execute_code, execute_menu_item, execute_custom_tool and unity_dialog do?
keywords: toplu, c# çalıştır, menü, diyalog, özel araç, batch, execute code, menu item, custom tool, dialog, press
covers: batch_execute, execute_code, execute_menu_item, execute_custom_tool, unity_dialog
---
These Unity MCP tools run several steps, arbitrary code or menu items, or answer editor dialogs.

- batch_execute: up to 25 commands in one call, one undo step; later commands can reference earlier results ("$[0].data.instanceID").
- execute_code (scripting_ext group): runs a C# snippet in the Editor with implicit usings (write UnityEngine.Object in full). When the approval mode is not auto every call opens an approval card; in auto mode none. Prefer manage_* tools and batch_execute first.
- execute_menu_item: runs an Editor menu item.
- Neither execute_code nor execute_menu_item can be checked against the fixed Unity file rule, because what they touch is not visible in their arguments.
- execute_custom_tool runs custom Editor tools registered by the project.
- unity_dialog: list or press buttons of native Unity dialogs. While a dialog is open nothing runs. Read it with list, answer with press: Save when the changes are yours, Cancel when unsure, never Don't Save or Discard unless the user asked. Pressing can save or discard user work.

Sources: docs/unity-mcp.md, docs/security.md, Backend/app/agent_guide.py, unity-mcp/manifest.json.
