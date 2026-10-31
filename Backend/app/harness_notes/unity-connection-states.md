---
title: Is Unity connected? What do the Unity states off, blocked, starting, running, connected and not responding mean for my tools?
keywords: unity bağlı mı, bağlantı, kapalı, yanıt vermiyor, toggle, port, unity mcp, connected, off, not responding, blocked, editor closed
covers: 
---
The Unity tools depend on a Unity MCP switch in the app and on the Editor being reachable.

- Status values from the manager: off (server stopped), starting, running (server up, Editor not connected yet), connected (server up and Unity connected), blocked (another program holds the MCP port, which needs a user action).
- Switch off: you get no Unity tools. The system prompt says so; work with files only and ask the user to turn the switch on if the task needs the editor.
- Switch on but the Editor does not answer (closed, compiling or showing a modal dialog): the prompt says to ask the user to open or unfreeze Unity, or continue file-only and say what is unchecked.
- Connected: the Unity MCP tools are offered. API providers see them only while the tool cache is current; CLI providers see them as mcp__unityMCP__<name>.
- The user turns the switch on in the app with Unity already open; if the Editor is closed it cannot connect.
- Call harness_status and read unity_mcp to see the current value.

Sources: Backend/app/unity_ai_mcp/unity_mcp_manager.py (UnityMCPStatus), Backend/app/agent_guide.py, Backend/app/tools/unity_mcp_tools.py, Backend/app/unity_tool_policy.py, docs/unity-mcp.md.
