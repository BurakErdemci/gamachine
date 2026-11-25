---
title: How do I start sub-agents on other CLI models and get their results?
keywords: alt ajan, ajan başlat, paralel ajan, yardımcı ajan, sub-agent, subagent, agent, delegate, worker, parallel, background helper, agent_start
covers: agent_models, agent_start, agent_message, agent_stop, agent_list, agent_result
---
A CLI main agent (Claude, Codex, agy, OpenCode, Cursor, Copilot, Kimi) can run other connected CLI models as sub-agents: hidden helper chats that work in the background in the same project folder.

- agent_models(): the connected CLI providers, their model ids and the effort levels each model accepts. A provider that is not installed or not logged in is listed as not usable.
- agent_start(model, brief, effort="auto", name=""): starts a sub-agent. Write the brief so it stands alone (goal, files, limits, what to report). The sub-agent is told to end with a self-contained report.
- agent_message(agent_id, text): a running sub-agent gets the text at its next turn boundary (it is never interrupted); a finished, stopped or interrupted one continues with it on its own session.
- agent_stop(agent_id): stops its running turn and drops its queued messages; its open approval cards are refused.
- agent_list(): this chat's sub-agents with model, effort and status (running, done, idle, error, stopped, interrupted).
- agent_result(agent_id): the full last result.
- Results arrive by themselves: when a sub-agent's turn ends, its last message reaches this chat as a system note naming the sub-agent (cut at about 6 KB; agent_result has the rest) and wakes this chat if it is idle. Several results that finish together arrive in one turn. Do not poll agent_list.
- A quota or provider error comes back as the result ("quota exhausted: ..." or "error: ...").
- Depth 1: a sub-agent cannot start sub-agents. No count limit; start only what the job needs.
- Fuse: at most 10 turns of this chat in a row are started by sub-agent results without a user message; later results wait in the chat and go to the AI with the user's next message.
- Before starting two or more sub-agents that write files in the same folder, ask the user first (offer a separate worktree).
- Approval cards stay in the sub-agent's chat and carry this chat's id and the sub-agent's label. Auto mode opens no card; Safe Auto decides by risk, as in any chat.
- Stopping this chat stops its running sub-agents; deleting it deletes them. After an app restart a running sub-agent is "interrupted": nothing reruns it until agent_message.
- Claude chats get these tools from the gamachineAgents server (mcp__gamachineAgents__agent_start); other CLIs from the unityai server.
- API providers and Ollama cannot be main agents or sub-agents.

Sources: Backend/app/agentic/subagents.py, Backend/app/routes/subagent_routes.py, Backend/app/unity_ai_mcp/tools/agent_tools.py, Backend/app/unity_ai_mcp/agent_tools_server.py.
