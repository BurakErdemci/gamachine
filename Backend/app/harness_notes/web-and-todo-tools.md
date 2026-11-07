---
title: How do web_fetch and todo_write work?
keywords: web, sayfa, oku, adres, url, yapılacaklar, görev listesi, plan, fetch, page, todo, tasks
covers: web_fetch, todo_write
---
web_fetch(url, max_chars) reads one public web page and returns it as plain text (HTML converted, long pages truncated with a note).

- Only http and https; loopback, Gamachine's own ports, private networks, link-local and metadata addresses are refused, also after a redirect. No cookies or logins are sent. Binary files are refused.
- It reads a page; it does not search. Searching is available only where the provider has its own search tool.

todo_write(items) keeps this chat's task list: each item has content and a status (pending, in_progress, completed), at most one in_progress. The tool returns the whole list; send the full updated list each time.

Sources: Backend/app/agentic/web_fetch.py, Backend/app/agentic/todo_list.py, Backend/app/browser_use/policy.py, Backend/app/tools/tool_registry.py.
