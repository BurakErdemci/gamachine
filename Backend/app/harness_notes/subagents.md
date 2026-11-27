---
title: How do I start sub-agents on other CLI models and get their results?
keywords: alt ajan, ajan başlat, paralel ajan, yardımcı ajan, şerit, sub-agent, subagent, agent, delegate, worker, parallel, background helper, agent_start, lane, worktree, lane_merge
covers: agent_models, agent_start, agent_message, agent_stop, agent_list, agent_result, lane_merge
---
A CLI main agent (Claude, Codex, agy, OpenCode, Cursor, Copilot, Kimi) can run other connected CLI models as sub-agents: hidden helper chats that work in the background, in the same project folder or in a Unity lane of their own.

- agent_models(): the connected CLI providers, their model ids and the effort levels each model accepts. A provider that is not installed or not logged in is listed as not usable.
- agent_start(model, brief, effort="auto", name=""): starts a sub-agent. Write the brief so it stands alone (goal, files, limits, what to report). The sub-agent is told to end with a self-contained report.
- agent_message(agent_id, text): a running sub-agent gets the text at its next turn boundary (it is never interrupted); a finished, stopped or interrupted one continues with it on its own session.
- agent_stop(agent_id): stops its running turn and drops its queued messages; its open approval cards are refused. The user can also stop a helper from the app or the phone.
- agent_list(): this chat's sub-agents with model, effort and status (running, done, idle, error, stopped, interrupted). Under a running one, one line on what it is doing now: phase (thinking, writing, running_tool, waiting_approval, queued), the current tool and its target with seconds, steps done and failed, elapsed time, its last line ("last said"), and "no activity for N min" after 90 s without an event.
- agent_result(agent_id): the full last result; for a running sub-agent its live progress (phase, current step, last 5 steps, counts, elapsed, last said).
- Results arrive by themselves: when a sub-agent's turn ends, its last message reaches this chat as a system note naming the sub-agent (cut at about 6 KB; agent_result has the rest) and wakes this chat if it is idle. Several results that finish together arrive in one turn. Check agent_list when you decide what to do next or when the user asks how the helpers are doing; never poll it in a loop.
- A quota or provider error comes back as the result ("quota exhausted: ..." or "error: ...").
- Depth 1: a sub-agent cannot start sub-agents. No count limit; start only what the job needs.
- Fuse: at most 10 turns of this chat in a row are started by sub-agent results without a user message; later results wait in the chat and go to the AI with the user's next message, or when the user presses Continue on the fuse notice (it resets the fuse).
- Before starting two or more sub-agents that write files in the same folder, ask the user first (offer Unity lanes).
- agent_start(..., lane="<name>") (lowercase letters, digits, hyphen): the helper works in a Unity lane, a git worktree at <project>/.gamachine/lanes/<name> on branch gamachine/<name> with its own batch-mode Unity editor, so helpers can change scenes and prefabs and run play tests at the same time. A new lane needs a few minutes for its first Unity import; the helper's first turn starts only when the lane's editor answers a command. Each lane needs about 6 GB of free memory; a lane is refused with the reason when memory is short, when the project is not the root of a git repository, or when Unity has no license (sign in to Unity Hub). Give each lane helper its own scenes or prefabs and tell it to commit. The lane's editor closes when its helper stops running; agent_message opens it again (and re-creates the lane worktree from the user's current branch if the lane was already merged).
- lane_merge(lane): after the lane's helper has finished and committed (waiting up to 60 s if only the editor is still closing), merges the lane branch into the user's current branch (scenes and prefabs through Unity's YAML merge) and removes the lane and its branch; then run refresh_unity. Unity's own settings changes in the lane are discarded. Uncommitted helper work, a conflict or local changes in the user's folder stop the merge and change nothing; a conflict lists the files (have the helper merge the user's branch into its lane and resolve there, or ask the user).
- Approval cards stay in the sub-agent's chat and carry this chat's id and the sub-agent's label. Auto mode opens no card; Safe Auto decides by risk, as in any chat.
- Stopping this chat stops its running sub-agents; deleting it deletes them. After an app restart a running sub-agent is "interrupted": nothing reruns it until agent_message.
- Claude chats get these tools from the gamachineAgents server (mcp__gamachineAgents__agent_start); other CLIs from the unityai server.
- API providers and Ollama cannot be main agents or sub-agents.

Sources: Backend/app/agentic/subagents.py, Backend/app/unity_lanes.py, Backend/app/routes/subagent_routes.py, Backend/app/unity_ai_mcp/tools/agent_tools.py, Backend/app/unity_ai_mcp/agent_tools_server.py.
