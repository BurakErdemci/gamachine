---
title: Which settings change what I can do?
keywords: ayarlar, anahtar, açık kapalı, ek talimat, ayar, settings, switch, toggle, addendum, enabled, computer use switch, browser switch, unity switch
covers: 
---
Several user-controlled settings change your tools and instructions.

- Unity MCP switch: with it off you have no Unity tools and the prompt tells you so (see unity-connection-states).
- computer_use_enabled (Settings > Bilgisayar) and browser_use_enabled: off by default; each registers its tools only while on and only outside read-only runs. A switch flipped takes effect on the next turn at the latest.
- Approval mode: auto, balanced or step (see approval-modes).
- Agent guide addendum (agent_guide_addendum, up to 4000 characters): the user's own text appended after the built-in guide; it cannot override approval or safety rules.
- Chat model: each chat's provider and model, changeable from the desktop or the phone.
- Desktop effort level, chosen in the desktop effort panel.
- harness_status reports the live values it can read: approval_mode, computer_use_enabled, browser_use_enabled, unity_mcp.

Sources: Backend/app/agent_guide.py, Backend/app/computer_use/settings.py, Backend/app/browser_use/settings.py, docs/computer-use-providers.md, docs/providers.md, docs/remote-control.md.
