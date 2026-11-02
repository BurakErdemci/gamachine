---
title: What can the agent browser not do or reach?
keywords: tarayıcı sınır, yasak, indirme, indirilenler, projeye kopyala, localhost, dosya, izin, kamera, çıkış, limits, blocked, private address, permissions, extensions, login, remote, download, downloads
covers: browser_downloads, browser_download_to_project
---
The agent browser is deliberately narrow.

- Only http and https pages. file:, data: (top level), javascript:, chrome: and devtools: are refused, and so is any host that resolves to loopback, link-local or private ranges (localhost, 127.x, 10.x, 192.168.x ...). The Gamachine backend lives on loopback and pages must not reach it.
- Every permission request is denied (camera, microphone, geolocation, notifications, clipboard read).
- Downloads work and go to the user's Downloads folder under a new name ("name (1).ext"), never over an existing file. A download you start pauses for one approval: a card with the file name, size and site, unless the click that started it was already approved as a download (or the user turned browser cards off). It shows up as `download` in your next browser reply; browser_downloads lists them (wait_ms waits for one in progress). If the card is still open you get "needs_approval": tell the user, then check browser_downloads; do not click again.
- Never downloaded for you, card or not: executables and scripts (.exe .msi .msix .appx .bat .cmd .com .ps1 .psm1 .vbs .vbe .js .jse .wsf .wsh .scr .pif .cpl .msc .hta .reg .lnk .dll .sys .jar .py .pyw .sh .bash .zsh .dmg .pkg .app .appimage .deb .rpm .apk .command), double extensions such as .pdf.exe included. The user's own downloads have no limit.
- browser_download_to_project copies a completed download into the project: dest relative to the project root, with the same file type, for example Assets/Audio/theme.mp3 (Unity imports what lands under Assets/). Refused: absolute paths, '..', links, an existing file without overwrite (overwrite only under Assets/), Plugins and Editor folders, and code or plugin types (.cs .asmdef .asmref .rsp .dll .so .dylib .bundle .jslib .jspre .mm .m .cpp .c .h .hpp .aar .jar .java .kt .swift).
- Pop-ups are blocked; the agent tab reports popup_blocked.
- Not available: ad blocker, extensions, find in page, zoom, history pages.
- Logins are shared by all tabs and kept on disk, so what you open can use the user's logins. Logging in and submitting forms needs a card that names the host.
- You cannot read the user's own tabs beyond origin and title.
- A phone paired by remote control can only read the agent tab's status, halt it and answer cards; it cannot open or navigate.
- Stop halts the browser and denies browser tools until the user's next message.

Sources: docs/agent-browser.md (Non-negotiables, Downloads, Privacy), Backend/app/browser_use/gateway.py, Backend/app/browser_use/policy.py.
