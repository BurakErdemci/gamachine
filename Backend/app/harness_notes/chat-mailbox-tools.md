---
title: How do list_chats and send_chat_message let me leave a note in another chat?
keywords: sohbet, not, mesaj, posta, dal, mention, @, başka sohbet, mailbox, chat, note, message, branch, send
covers: list_chats, send_chat_message
---
list_chats and send_chat_message let one chat's agent leave a note for another chat or branch of the same user.

- list_chats(): lists the user's other chats (number, title, branch or not, running or not). Branches are valid targets too. Allowed in read-only runs.
- send_chat_message(to_chat_id, message): leaves a note, at most 4000 characters. An idle chat wakes with the note; a running chat reads it when its turn ends. Not allowed in read-only runs.
- Use the number the user gave with @<number>. If several chats have similar titles, do not guess; ask the user to pick with @.
- In step mode the user approves the note first; on CLI providers the tool waits up to 150 seconds for that card, then cancels it.
- A note never becomes a user message: it arrives as a system note from another chat's AI, and the user's instructions win.
- Limits: reply chains stop at depth 2; one pair of chats may exchange 5 notes per 10 minutes.
- The sender of a note cannot see your chat; a reply reaches it only through send_chat_message.

Sources: Backend/app/agentic/mailbox.py, Backend/app/tools/tool_registry.py, Backend/app/unity_ai_mcp/tools/mailbox_tools.py.
