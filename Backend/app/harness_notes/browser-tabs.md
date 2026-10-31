---
title: How do browser_tabs and browser_use_tab work, and which tab can the agent reach?
keywords: sekme, sekmeler, tarayıcı sekmesi, açık sayfa, tab, tabs, use tab, switch tab, agent tab, user control, kullanıcı kullanıyor
covers: browser_tabs, browser_use_tab
---
A chat owns several browser tabs, but exactly one is the agent tab, and it is the only page the browser tools can act on.

- browser_tabs: lists this chat's own tabs (id, title, url, active, agent, userTouched). Only the agent tab shows its full url; other tabs show just their site and title. Another chat's tabs never appear.
- browser_use_tab(tab_id): makes one of this chat's tabs the agent tab; the user's view does not change. Take a new browser_snapshot afterwards. Call browser_tabs first when the user refers to a page that may already be open.
- Refused: a tab the user used since their last message (user_control), an unknown or other-chat id (no_tab), a tab busy with a running command (busy).
- When the user clicks or types in the agent tab, the browser answers user_control: do not call browser tools again this turn, tell the user you will continue after their next message. The user's next message releases it.
- A switch counts as a page change: grants and open cards for the old page are dropped.
- Per chat at most 8 tabs; creating more fails with too_many_tabs.

Sources: docs/agent-browser.md, Backend/app/browser_use/server.py.
