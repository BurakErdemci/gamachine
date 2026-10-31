---
title: What are harness_search, harness_read and harness_status and how do I use them?
keywords: araç, özellik, sınır, not, arama, durum, harness, tools, features, limits, notes, search, status, help
covers: harness_search, harness_read, harness_status
---
harness_search, harness_read and harness_status let you look up facts about Gamachine itself instead of guessing.

- harness_search(query): finds short notes by Turkish or English words, or by an exact tool name. It returns note ids, titles and a one-line summary.
- harness_read(note_id): returns the full text of one note. An unknown id returns an error that lists the known ids.
- harness_status(): returns live state as JSON: provider, model, language, read_only, approval_mode, computer_use_enabled, browser_use_enabled, unity_mcp (off, blocked, starting, running or connected) and the tool names offered to this run. A field the tool cannot know is null; it never contains keys or tokens.
- All three only read; they change nothing.
- On CLI providers the same three tools come from the unityai MCP server. That process cannot see the run itself: provider, model, language and read_only are null there, and enabled_tools lists only that server's tools. Approval mode, computer and browser switches and the Unity state are read from the backend.
- The system prompt carries one line telling you to call harness_search before answering questions about your own tools, features, settings or limits.

Sources: Backend/app/harness_notes.py, Backend/app/tools/tool_registry.py, Backend/app/unity_ai_mcp/server.py, Backend/app/agent_guide.py.
