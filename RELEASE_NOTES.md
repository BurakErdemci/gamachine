<!--
  ⚠️ THIS FILE IS MAINTAINED BY HAND and goes stale easily.
  `.github/workflows/release.yml` publishes it as the BODY of the draft release
  (`body_path: RELEASE_NOTES.md`). On 2 Aug 2026 it still held v2.2.0's text; had
  the release job run, v2.3.0 would have shipped the previous version's notes.
  ▶ BEFORE cutting a release: move the top section of `CHANGELOG.md` here.

  Written in English on purpose: the release page is where a stranger lands at the
  moment they download. (This comment is invisible on GitHub.)
  Images use absolute URLs pinned to the tag: a release body does not resolve
  repository-relative paths.
-->

## Gamachine v4.1.0

> 🍎 **macOS (Apple Silicon):** `Gamachine-4.1.0-arm64.dmg`
> · 🪟 **Windows:** `Gamachine-Setup-4.1.0.exe`

**Windows:** the installer is not code-signed, so SmartScreen warns on first run.
Click **More info**, then **Run anyway**.
**macOS:** the app is signed with a Developer ID and notarized by Apple, so it opens
without a warning.

**On a Mac with 4.0.0-4.0.3?** Install 4.1.0 by hand from the dmg, once: those versions
cannot verify the new Mac update signature. From 4.0.4 on, the app updates itself.

**Coming from v3?** v3.2.0's update notice only opens the release page, so install
4.1.0 by hand.

---

Your main agent can now run a team: it starts helper agents on other connected CLI models, runs scripts that do many tool calls in one step, and in Unity it can give each helper a project copy and editor of its own.

### Sub-agents
- **A CLI main agent can start helper chats on other connected CLI models.** Claude, Codex, agy, OpenCode, Cursor, Copilot and Kimi can all be the main agent, and any connected one of them can be a helper. The agent picks a model and effort level, writes a self-contained brief and the helper works in the background in the same project folder. There is no fixed limit on how many helpers run at once.
- **Steer and stop.** The agent can send a running helper a message (it arrives at the helper's next turn boundary, never mid-step) or continue a finished one. You can also stop a helper yourself from the app or the phone; stopping refuses its open approval cards. Stopping the main chat stops its helpers, and deleting it deletes them.
- **Results arrive by themselves.** When a helper finishes, its report reaches the main chat and wakes it if it is idle; results that arrive together can share one turn, and `agent_result` gives the full report of a long one.
- **Watch what helpers are doing.** The chat shows each helper's model, status, the step it is on, steps done and failed, and its last line. A running helper that goes quiet for 90 seconds is flagged.
- **Approval cards say who is asking.** A helper's cards carry the main chat and the helper's name. The approval mode works as in any chat: Auto opens no card, Safe Auto decides by risk.
- **A fuse against runaway loops.** After 10 turns in a row started by helper results without a message from you, the rest wait. They go to the AI with your next message, or when you press Continue on the notice once the chat and its helpers are idle.
- **Helpers stay out of the way.** They do not appear in the phone's chat list. A helper cannot start helpers of its own, and a read-only turn cannot start or steer one. If the app restarts, a running helper shows as interrupted until the agent messages it.

### Unity lanes
- **Parallel Unity work without edit collisions.** Ask the agent to run helpers in lanes and each helper gets its own git worktree of your project, branch and a batch-mode Unity editor, so several helpers can change scenes and prefabs and run play tests at the same time. A new lane needs a few minutes for its first Unity import; the helper starts when its editor answers.
- **Merge with Unity's own YAML merge.** When a helper has finished and committed, `lane_merge` merges its branch into your current branch (scenes and prefabs go through Unity's YAML merge tool that ships with the editor) and removes the lane and branch. Unity's own settings and cache changes in the lane are discarded. A conflict, other uncommitted helper work or your local changes in files the merge touches stop it before anything is merged; a conflict lists the files so the helper or you can resolve it.
- **Your Unity setup is left alone.** Lane editors never stop your own Unity MCP server, and on Windows Gamachine puts back the last-project values Unity Hub had before a lane editor rewrote them.
- **What a lane needs.** The Unity project folder must be the root of a git repository with at least one commit, the project's Unity version must be installed with an active license (sign in through Unity Hub), and there must be 6 GB of free memory per starting lane plus 2 GB to spare (8 GB for the first one). When something is missing, the helper reports why its lane could not start. Lanes and `lane_merge` are for CLI main agents.

### Scripts that do the legwork
- **`run_script`: one program instead of a long string of tool calls.** The agent can write a short JavaScript program that calls Gamachine's tools, runs them in parallel with `Promise.all`, filters and counts big results, and returns the answer with a short summary of its calls. Big results no longer fill the chat's context.
- **Sandboxed, and every call is gated.** The program runs in a sandbox with no files, network or processes of its own. Every tool call inside it passes the same approval mode, risk check, Unity file rule and gates as a direct call. The chat shows the code and a summary of the calls it made.
- **On by default; you can turn it off** in the agent settings. Time limits depend on the agent's CLI (up to 600 s in Claude and Codex, 150 s in agy, 50 s in the others), and the tool description states the limit.

### Chat turns and Unity batches
- **One turn at a time per chat.** A message you send while a turn is running waits in line and runs in order (a Claude chat can also take it into the running turn). The chat shows your waiting messages and you can withdraw one before it starts. Helper results go through the same line, so they never collide with your messages.
- **`batch_execute` now works in Claude chats.** Several scene, component, prefab and asset steps go in one call. A batch that carries a C# script write or delete is refused with a note to use Write/Edit for `.cs` files.

### Scene writing mode
- **Choose how the agent builds your scenes, per Unity project:** Code (the agent may build layout from code at runtime), Mixed (systems in scripts, level layout in scenes and prefabs, tuning as Inspector fields) or Team (like a Unity developer in a team: everything the player sees lives in a scene or prefab, with code spawning only truly dynamic objects, data in ScriptableObjects, one prefab or additive scene per feature so teammates and parallel helpers do not edit the same file).
- **Mixed is the default.** The mode shows as a chip in the chat title and on the empty chat, and it shapes the agent's project guide. It is saved with the project, not the chat, and lanes follow their main project's mode.

### For agents
- New tools: `agent_models`, `agent_start`, `agent_message`, `agent_stop`, `agent_list`, `agent_result`, `lane_merge` (CLI main agents), `run_script`; `batch_execute` is allowed on the Claude chat path. `harness_search` answers questions about them.

Everything in [v4.0.4](https://github.com/BurakErdemci/gamachine/releases/tag/v4.0.4) is included.
