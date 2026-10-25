# Engineering notes: what I got wrong, and what it cost

A running log of the architectural decisions behind this project — including the
ones that were wrong, what they cost, and what replaced them. Written as it
happened, not cleaned up afterwards.

---

This section documents the real decisions, dead ends and lessons from the project's evolution. The commit history tells you "what changed"; this section answers "why."

---

### The Beginning: the "Analysis Tool" Era

I actually started the project as a Unity code **analysis** tool. In the first version the user pasted a C# script, the system ran static analysis and returned one big JSON report. Many agents (intent classifier, orchestrator, unity expert, critic, game feel…) ran in sequence.

**The problem:** that pipeline took 45–60 seconds. Even when the user said "rename this variable," all the agents fired.

**Lesson:** an analytical architecture is the wrong paradigm for interactive development. 60 seconds is acceptable for a report; for "fix this" it's lethal.

---

### The Big Shift: From Analysis Tool to Agentic IDE

This is the project's most critical architectural decision. In the old model the user asked, a one-shot answer came back, and the chat ended — the AI couldn't see working files, read error logs, or touch the terminal.

**What pushed me to it:** the message "error: NullReferenceException at PlayerController.cs:47, fix it." Since the AI couldn't see line 47, it could only give generic advice. What would a senior do? Open the file, read the line, understand the context, fix it.

```
Old model:  User → [single LLM call] → Answer
New model:  User → [LLM] → tool call → [result] → [LLM] → … → Answer
```

`AgentRunner` and `ToolRegistry` are the products of this shift.

---

### main.py: Refactor Against the God Object

At one point `main.py` reached 2000+ lines — auth, chat, OAuth, analysis, config all in one file. I did a full route refactor:

```
Before:  main.py (2000+ lines)
After:   routes/{auth,conversation,config,analysis,…}_routes.py + main.py (~30-line bootstrap)
```

**Lesson:** "I'll just put it here for now" gets paid back sooner or later. A 2000-line file isn't just code debt, it's cognitive-load debt. (With the same discipline the `ai_providers.py` god object was later split under `providers/`.)

---

### The MCP Decision: Why a CLI Layer?

A critical choice: should the AI tools run directly through the backend, or through CLI tools (Claude Code, Codex, Gemini)?

The problem with going direct: rewriting tool calling for every new model, handling each model's different format, rebuilding security boundaries again and again.

The advantage of the CLI approach: these tools **already have** their own tool ecosystem, security layer and MCP support. Positioning as an MCP server on top of them means inheriting each CLI's power.

```
Old:  Frontend → Backend → [my own tool code] → file system
New:  Frontend → Backend → CLI (MCP client) → MCP server → file system
```

**Cost:** managing 3 different CLI config formats (JSON / TOML / JSON) and their behavioral differences.

---

### Gemini's Hidden Tool Collision

I defined a `write_file` tool over MCP. It didn't show up in the Gemini CLI: `Tool 'mcp_..._write_file' not found`. Instead of analyzing logs I asked the CLI directly:
> "Which MCP tools do you see, and is `write_file` in your built-in list?"

The answer: Gemini had a built-in tool named `write_file` and was **silently suppressing** the same-named MCP tool. Renaming it to `save_file` fixed it.

**Lesson:** when debugging CLI tools, the fastest method isn't logs — it's asking the tool itself.

---

### The Stop Button: Seemingly Simple, Actually Two Layers

On "Stop" I cut the SSE with an AbortController — but the backend CLI process kept running, polling for approval. The fix was in two parts:

```typescript
stopMessage: () => {
  abortControllerRef.current?.abort();               // cut the SSE connection
  fetch(`${API}/chat-stop/${id}`, {
    method: 'POST', headers: { 'X-Session-Token': sessionToken },
  }); // stop this chat's backend turn and reject its pending approval gates
}
```

**Lesson:** cancellation semantics span multiple layers. "The frontend cancelled" ≠ "the operation cancelled." Every async boundary needs its own cancellation mechanism.

---

### I Tore Out Auth: From Web Architecture to Local Desktop

By reflex I started with web patterns: bcrypt, JWT, session DB, OAuth2 (Google+GitHub), rate limiting — ~2000 lines, 4 tables, 7 endpoints.

One day I realized: **this is an Electron app.** The user already has physical access to their device. The multi-user layer was offering fake security — an attacker can already reach `app_data/`, the API keys, the file system.

```typescript
const localAppToken = randomUUID()                 // new each launch
spawn(backend, { env: { ...process.env, LOCAL_APP_TOKEN: localAppToken } })
ipcMain.handle('app-token-get', () => localAppToken)
```

The `users` table now has a single row: `(id=1, username='local')`. The sessions/OAuth tables were dropped.

**Result:** ~2000 lines removed, 7 endpoints reduced to stubs, 4 tables gone, pytest 40% faster.

**Lesson:** architecture must fit the application's context. Copying web patterns into a local app only produces technical debt.

---

### The agy Saga: I Learned the Limits of Embedding a CLI (xD)

The project's biggest dead end. It limped along behind a **warning banner** for a while — until I found the right channel (the `run_command` bridge) and **actually solved it.**

#### Scene 1: "We have 27 days"
I learned Google would retire the Gemini CLI in 27 days; Antigravity (`agy`) was the replacement. The hot-swap looked simple: call `--print`, feed the prompt via stdin, read the output. It looked like a 3-hour job. **It took three days.**

#### Scene 2: "Where are the tool calls?"
agy returned text, but on file-write requests the MCP tools were **never called**. The agy log had a key line: `checkpoint model generated tool calls`. I asked agy and Codex: **`--print` mode blocks tool dispatch by design**, they said.

#### Scene 3: "We'll use `-i` mode!"
They suggested interactive (`-i`) mode instead of `--print`. First attempt:
```
bubbletea: could not open TTY: open /dev/tty: device not configured
```
bubbletea (agy's TUI) refused the PIPE — a real PTY was required.

#### Scene 4: The PTY and Terminal Hijack Disaster
`pty.openpty()`, `termios`, `start_new_session=True` — all by the book. I ran it: agy's TUI (sign-in, spinners, permission prompts) leaked **into the user's terminal**, and the backend could read nothing from stdout. Worse, even `--dangerously-skip-permissions` didn't bypass the native shell prompts in `-i` mode.

#### Scene 5: Research — We're Not Alone
On GitHub, `antigravity-cli` Issue #187 was exactly our problem, with no response from Google. The Gemini API docs said "Antigravity Agent: function_calling and mcp are not yet supported." All three paths were closed.

#### Scene 6: Acceptance and a Banner
I went back to `--print`. agy sometimes wrote files / ran commands — bypassing our approval bridge. The only honest solution: a dismissable yellow banner telling the user.

#### Scene 7: The Fix — "I Was Knocking on the Wrong Door"
The banner held for a few days. Then I asked again: *how does agy use unityMCP?* Because unityMCP **worked** — agy could add GameObjects to the scene. An isolated test gave the answer. agy explained it in its own words:

> "My `read_console` tool is lazily-loaded over an HTTP MCP server; so I wrote a Python script into the workspace and connected to `127.0.0.1:8080/mcp` with `streamable_http_client` to call the tool from there."

**The lightbulb moment.** agy doesn't natively load MCP under `--print` — but it's smart enough: seeing an HTTP MCP URL, it writes **its own bridge script** with `run_command` and connects. So under `--print` the **only real channel agy sees is `run_command`.** I'd been knocking on the wrong door the whole time.

**The solution — the `unityai` CLI bridge:**
1. I wrote `unityai_cli.py` + a `unityai` wrapper. This CLI **shares the same `approval_bridge`** as the MCP tools — a `unityai save-file …` call opens the approval card just like `mcp__unityai__save_file`. agy calls it via `run_command`.
2. I disabled agy's **real** built-in write tools (`write_to_file`, `replace_file_content`, `multi_replace_file_content`) via `disabledTools`. (I learned the correct names by having agy dump its own tool list — the earlier guessed names never matched.) With writes disabled, agy's only path is `run_command → unityai` → **an approval card appears.**
3. I removed the yellow banner — it was lying now. agy goes through the approval gate just like Claude Code/Codex.

**A small remaining trade-off (in the spirit of honesty):** we can't disable `run_command` (we call `unityai` through it). So agy could theoretically bypass approval via raw shell; we only deter that with prompting. Claude Code & Codex load MCP natively, so for them the ban is **absolute**; for agy it isn't. An acceptable trade-off so agy can keep using unityMCP freely for scene control.

#### Lessons
1. **Embedding a CLI is categorically different from API integration** — a CLI is designed as an interactive user tool; driving it programmatically means forcing the wrong layer.
2. **When you consult an AI agent, verify** — agy said "it falls back on PIPE," which was wrong; Codex said the same. Agents don't remember their libraries' behavior, they guess.
3. **"It doesn't work" is an answer** — clear information (even if temporary) beats a fragile hack.
4. **Check GitHub Issues early** — Issue #187 was there on day one; I'd have saved 2 days.
5. **Document the dead ends** — that's what this section is for.

---

### Practical Rules (When Contributing)

1. **Write CLI configs at global scope** — headless mode usually doesn't read project-level config.
2. **Use a `strip()` comparison in the approval gate** — a trailing-newline diff opens an unnecessary card.
3. **Compare MCP tool names against the CLI's built-in list** — name collisions are silent.
4. **Split routes early** — any route file over 500 lines should be split; this was the most expensive debt.
5. **Scan upstream Issues before spawning a CLI as a subprocess.**
6. **Verify an AI agent's claims about its own libraries with an isolated test.**

---

---

[← Back to the README](../README.md)
