---
title: How do computer_click, computer_type_text, computer_press_key, computer_hotkey, computer_scroll, computer_drag and the other input tools work?
keywords: tıkla, yaz, tuş, kısayol, kaydır, sürükle, menü, değer, click, double click, right click, type text, press key, hotkey, scroll, drag, menu, set value, delivery mode, background
covers: computer_click, computer_double_click, computer_right_click, computer_scroll, computer_drag, computer_type_text, computer_set_value, computer_press_key, computer_hotkey, computer_invoke_menu
---
These computer-use tools send input to an app window. All require pid; most take window_id and either an element_token or x and y.

- computer_click, computer_double_click, computer_right_click, computer_drag, computer_scroll (direction required), computer_type_text(text), computer_press_key(key), computer_hotkey(keys), computer_set_value(value, element_token).
- computer_invoke_menu(path, window_id): menu items are often disabled while the app is in the background; prefer the keyboard shortcut with computer_hotkey. On Windows this tool is not available.
- Input is delivered to background apps by default. If a hotkey or click is refused in the background (background_unavailable), send it again with delivery_mode foreground and window_id.
- In Open and Save panels set the file name with computer_set_value on the name field; typed text often does not reach them.
- Unknown or extra arguments are refused ("Forbidden argument"); booleans in numeric fields are refused.
- Several known steps should go in one computer_run_steps (see computer-use-batch-tools).
- Do not send synthetic input to the desktop any other way: the system prompt allows it only through these tools.

Sources: Backend/app/computer_use/policy.py, Backend/app/computer_use/server.py, Backend/app/computer_use/platform/macos.py, Backend/app/computer_use/platform/windows.py, Backend/app/agent_guide.py.
