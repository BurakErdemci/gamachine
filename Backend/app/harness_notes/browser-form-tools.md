---
title: When do browser_click, browser_fill, browser_select and browser_press need approval?
keywords: tıkla, doldur, form, şifre, ödeme, onay kartı, indirme, click, fill, select, press, submit, password, download, needs_approval
covers: browser_click, browser_fill, browser_select, browser_press
---
These browser tools act on elements from the latest browser_snapshot (snapshot_id and ref).

- Before a click, fill, select or Enter press, the backend describes the element and classifies it.
- Critical, so an approval card opens: the element or its form's submit button has a risky name (send, delete, purchase, checkout, confirm, submit, publish, transfer and Turkish equivalents); a click submits a form whose method is not GET or that has a password field; a password field is filled (always); a link downloads a file. Approving that click also approves the first download it starts within a few seconds on the same page; any other download pauses for its own card (see browser_downloads).
- Not critical: GET forms such as site search, ordinary links, other fields.
- A critical step does not block. If the card is not answered within about 2 seconds the tool returns "needs_approval: ...". Stop, tell the user what you want to do and why, and wait for their next message. Only the card approves; chat text never does.
- After the user approves, your next identical call proceeds once (the grant covers that element of that snapshot only, and expires after 10 minutes).
- A page change, Stop, switch off or browser restart cancels cards and grants.
- browser_press accepts Enter, Tab, Escape, arrows, PageUp and PageDown.

Sources: docs/agent-browser.md (Policy), Backend/app/browser_use/server.py, Backend/app/computer_use/policy.py (critical_name).
