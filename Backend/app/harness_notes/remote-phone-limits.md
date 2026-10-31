---
title: What can I and the user do when a chat is driven from the phone (remote control)?
keywords: telefon, uzaktan, uzak kontrol, qr, iphone, onay, remote, phone, relay, pairing, web push, paired device
covers: 
---
Remote control lets the owner manage Gamachine from a paired phone. It is off by default and needs a QR pairing; the agent keeps running on the PC with the same tools and rules.

- From the phone: watch chats, approve or reject cards, answer question cards, stop a turn, write a message into a chat (slash commands work), read and change the approval mode, change a chat's provider and model, change the desktop's effort level (but not turn Ultracode on).
- Not from the phone: other settings, API keys, CLI install, file operations.
- Cards: the first answer wins; a card answered on the phone closes on the desktop.
- Browser: a phone can only read the agent tab's status, halt it and answer cards. It cannot open, navigate or take over a tab. A phone can also halt computer use for a chat.
- A message sent from the phone runs like a typed one, on the chat's model; it is queued if a turn is running.
- Chat text, including code you print, reaches the phone (encrypted end to end).
- The relay carries messages, not files.

Sources: docs/remote-control.md, docs/agent-browser.md.
