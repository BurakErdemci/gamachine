---
title: How can I see the Unity Editor with capture_unity_screenshot or unity_window_capture?
keywords: ekran görüntüsü, unity penceresi, görsel kontrol, screenshot, capture, editor window, dialog, görüntü
covers: capture_unity_screenshot, unity_window_capture
---
Two tools show the Unity Editor as an image.

- capture_unity_screenshot() (API loop): captures the Unity Editor window and returns the image resized to fit 1280 px. On macOS it captures the Unity window when it can find it and the full screen otherwise; on Windows it can capture a minimized or background window; on Linux it takes the whole screen. It needs Pillow; other platforms answer that screenshots are unsupported. Use it only when a visual check is needed, not every step. Allowed in read-only runs.
- unity_window_capture (Unity MCP): captures the whole editor window (Console, Inspector, Game view) and the project's open dialogs from outside Unity, so it still works while Unity is blocked by a dialog. It is only there while the Unity connection is up (see unity-connection-states).
- play_capture (Unity MCP) captures the game camera at a paused play-session frame (see unity-playtest-tools).

Sources: Backend/app/tools/screenshot_tool.py, unity-mcp/manifest.json, Backend/app/agent_guide.py, Backend/app/agentic/agent_runner.py.
