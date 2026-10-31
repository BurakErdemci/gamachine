---
title: How do I play and test the game with play_session, play_step, play_run, game_hooks, run_playtest and manage_input?
keywords: oyna, test, playtest, bot, kare, girdi, tohum, play, session, step, input, hooks, autopilot, seed, capture
covers: play_session, play_step, play_capture, play_run, game_hooks, run_playtest, manage_input
---
These Unity MCP tools let the agent play the game it built. Core and playtest groups are on by default.

- play_session: start, inspect and stop a deterministic paused play session (start pauses on frame 0).
- play_step: advance N frames with scripted input, with watch, an until condition and a capture of the last frame (game UI included). play_capture captures the paused frame any time.
- play_run: background single or seeded batch plays with timelines and a contact sheet; read its summary before saying the game works. play_run seeds UnityEngine.Random; never seed it from the clock.
- game_hooks: read state hooks and call action hooks the game exposes with [GameHook("name")]; a hook named autopilot returns each frame's input so a bot can play.
- run_playtest: runs *.playtest.json scenario files, each in a fresh play session.
- manage_input: queues keyboard, mouse, gamepad and UI input into Input System virtual devices; no window focus needed. Call describe first: only game code on the new Input System sees these events; legacy UnityEngine.Input does not (only ui_click works there).
- A bot proves the game works, not that it is fun.

Sources: docs/unity-mcp.md, Backend/app/agent_guide.py, unity-mcp/manifest.json.
