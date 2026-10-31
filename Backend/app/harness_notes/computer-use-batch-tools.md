---
title: How do computer_run_steps, computer_confirm_risky_step and the clipboard tools work?
keywords: toplu adım, pano, riskli adım, onay, kopyala, yapıştır, run steps, clipboard, confirm risky step, batch, expect, wait_for, copy
covers: computer_run_steps, computer_confirm_risky_step, computer_clipboard_read, computer_clipboard_write
---
These computer-use tools batch steps, confirm risky actions and use the clipboard.

- computer_run_steps(pid, steps): one batch of 1 to 64 steps; each is {"tool": NAME, "args": {...}} or {NAME: {...}} where NAME is click, double_click, right_click, set_value, type_text, press_key, hotkey, scroll or drag (no computer_ prefix). Steps inherit pid and window_id from the batch and must not name a different one. delay_ms between steps is 0 to 2000. A step may add wait_for, expect (one check or up to 4) and timeout_ms. Use it for multi-step foreground work.
- computer_confirm_risky_step(pid, summary): call it before deleting, purchasing, sending or publishing, overwriting, or downloading with the user's account; it opens a card. It performs no driver action itself.
- computer_clipboard_read and computer_clipboard_write(text) use the clipboard; clipboard_write is allowed without a card.
- An action whose target name looks like send, delete, purchase, publish and similar is classified risky and needs a card.

Sources: Backend/app/computer_use/policy.py, Backend/app/computer_use/server.py, Backend/app/computer_use/gate.py, Backend/app/computer_use/platform/macos.py.
