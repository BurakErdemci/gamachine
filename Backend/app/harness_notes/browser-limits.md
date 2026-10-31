---
title: What can the agent browser not do or reach?
keywords: tarayıcı sınır, yasak, indirme, localhost, dosya, izin, kamera, çıkış, limits, blocked, private address, permissions, extensions, login, remote
covers: 
---
The agent browser is deliberately narrow.

- Only http and https pages. file:, data: (top level), javascript:, chrome: and devtools: are refused, and so is any host that resolves to loopback, link-local or private ranges (localhost, 127.x, 10.x, 192.168.x ...). The Gamachine backend lives on loopback and pages must not reach it.
- Every permission request is denied (camera, microphone, geolocation, notifications, clipboard read). Every download is cancelled; downloading is not an agent action yet.
- Pop-ups are blocked; the agent tab reports popup_blocked.
- Not available: ad blocker, extensions, find in page, zoom, history pages.
- Logins are shared by all tabs and kept on disk, so what you open can use the user's logins. Logging in and submitting forms needs a card that names the host.
- You cannot read the user's own tabs beyond origin and title.
- A phone paired by remote control can only read the agent tab's status, halt it and answer cards; it cannot open or navigate.
- Stop halts the browser and denies browser tools until the user's next message.

Sources: docs/agent-browser.md (Non-negotiables, Privacy), Backend/app/browser_use/gateway.py.
