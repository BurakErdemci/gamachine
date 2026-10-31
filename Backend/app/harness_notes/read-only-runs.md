---
title: What is a read-only run (side question) and which tools are refused in it?
keywords: salt okunur, yan soru, yan sohbet, yazma yok, read only, side question, side chat, refused, readonly
covers: 
---
A read-only run is a side question asked in a panel beside a main chat. It cannot write anything, whatever the approval mode.

- Offered built-in tools: read_file, search_in_project, find_files, list_directory, capture_unity_screenshot and list_chats.
- Refused with "Yan sohbet salt okunur; bu araç reddedildi.": write_file, delete_file, run_command and send_chat_message.
- Unity tools stay declared, but each call is allowed only when the Unity policy proves it read-only; unknown means refused.
- Computer use and the agent browser are not registered for a read-only turn.
- CLI providers: write tools such as Bash, Write and Edit are kept out of a read-only Claude session.
- The turn text tells you to answer only the final side question from what the conversation shows, with no "let me look" preambles; if you do not know, say so.
- harness_status reports read_only only when the run supplies it; otherwise it is null.

Sources: Backend/app/agentic/agent_runner.py (_READ_ONLY_BUILTIN_TOOLS, _read_only_tool_allowed), Backend/app/agentic/side_prompt.py, docs/computer-use-providers.md.
