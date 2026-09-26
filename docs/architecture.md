# Architecture

How Gamachine is put together: the process layout, the agentic loop, the tool
layer, and the SSE event stream. For the reasoning behind these choices see
[Engineering notes](engineering-notes.md).

---

## 🏗 System Architecture

```
┌──────────────────────────────────────────────────────┐
│                  Electron Desktop App                  │
│  ┌──────────────────────────────────────────────────┐ │
│  │  main/background.ts — LOCAL_APP_TOKEN = uuid()     │ │
│  │  ├─► Backend subprocess env                        │ │
│  │  ├─► Unity MCP subprocess env                      │ │
│  │  └─► Renderer IPC: 'app-token-get'                 │ │
│  └──────────────────────────────────────────────────┘ │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────┐    │
│  │ Chat Panel  │  │Monaco Editor │  │  Terminal   │    │
│  │ (SSE stream)│  │ (OmniSharp)  │  │ (xterm.js)  │    │
│  └──────┬──────┘  └──────────────┘  └─────────────┘    │
│         │        React 18 + Next.js (TR/EN i18n)        │
└─────────┼──────────────────────────────────────────────┘
          │ HTTP / SSE   +   X-Session-Token header
          ▼
┌──────────────────────────────────────────────────────┐
│            FastAPI Backend (Python 3.13)               │
│  ┌─────────────────────────────────────────────────┐  │
│  │              AgentRunner (Agentic Loop)          │  │
│  │   Request → Think → Tool Call → Result → … → Ans │  │
│  └────────────────────┬────────────────────────────┘  │
│         ┌─────────────┼─────────────┐                  │
│         ▼             ▼             ▼                   │
│  ┌────────────┐ ┌──────────┐ ┌──────────────┐          │
│  │ToolRegistry│ │ProjectRAG│ │MemoryManager │          │
│  │read/write  │ │.cs scan  │ │/compact      │          │
│  │run_command │ │+ keyword │ │memories/*.md │          │
│  │search_proj │ │+ arch map│ └──────────────┘          │
│  └────────────┘ └──────────┘                           │
│  ┌──────────────────────────────────────────────────┐ │
│  │                  AI Providers                     │ │
│  │  Claude │ OpenAI │ Gemini │ Groq │ … │ Ollama     │ │
│  └──────────────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────────────┐ │
│  │       unityai MCP Server (FastMCP)                │ │
│  │  save_file │ read_file │ list_directory │ bash    │ │
│  │            ↑ approval_bridge → approval card       │ │
│  └──────────────────────────────────────────────────┘ │
└─────────────────────────────────┬──────────────────────┘
                                  │ MCP (stdio / HTTP) + run_command bridge
          ┌───────────────────────┼───────────────────┐
          ▼                       ▼                    ▼
   ┌─────────────┐       ┌─────────────────┐   ┌──────────────────┐
   │ Claude Code │       │   Codex CLI     │   │ Antigravity (agy)│
   │ MCP native  │       │   MCP native    │   │ run_command →    │
   │             │       │                 │   │ unityai bridge   │
   └──────┬──────┘       └────────┬────────┘   └─────────┬────────┘
          └───────────────────────┼──────────────────────┘
                                  │ MCP HTTP (127.0.0.1:8080)
                                  ▼
                         ┌─────────────────┐
                         │   Unity MCP     │
                         │ (CoplayDev)     │  ← uvx bundled
                         │ 52 tools, fixed │
                         │ list per URL    │
                         └────────┬────────┘
                                  ▼
                         ┌─────────────────┐
                         │  Unity Editor   │
                         │  (C# Plugin)    │
                         └─────────────────┘
```

### Directory Layout

```
gamachine/
├── Backend/
│   ├── app/
│   │   ├── agentic/             # AgentRunner (agentic loop), approval gates
│   │   ├── providers/           # Provider layer (split)
│   │   │   ├── manager.py        #   provider selection (single entry point)
│   │   │   ├── cli_base.py       #   shared CLI logic + MCP config writing
│   │   │   ├── claude_provider.py / codex_provider.py / agy_provider.py
│   │   │   └── api_providers.py  #   Anthropic/Gemini/OpenAI/Ollama SDKs
│   │   ├── unity_ai_mcp/        # Our MCP server (FastMCP)
│   │   │   ├── tools/            #   save_file, delete_file, read_file, list_dir, bash
│   │   │   ├── approval_bridge.py#   MCP/CLI ↔ backend approval bridge
│   │   │   ├── unity_mcp_manager.py # Unity MCP subprocess + bundled uvx
│   │   │   └── server.py
│   │   ├── unityai_cli.py       # run_command bridge for agy (same approval gate)
│   │   ├── tools/               # Function-calling ToolRegistry (cloud API path)
│   │   ├── rag/                 # ProjectRAG (scan+keyword), MemoryManager
│   │   ├── routes/              # FastAPI routers (auth/chat/config/…)
│   │   ├── omnisharp/           # OmniSharp LSP sidecar (manager + client, with bundled .NET)
│   │   ├── auth_utils.py        # LOCAL_APP_TOKEN validation
│   │   └── database.py          # SQLite — single local user (id=1), Fernet
│   ├── vendor/                  # fetch_uv (uv toolchain) + fetch_video_bins (ffmpeg/yt-dlp)
│   ├── backend.spec             # PyInstaller — frozen 'backend' binary
│   └── tests/
├── Frontend/frontend/
│   ├── renderer/                # home.tsx (IDE), components/, hooks/, lib/i18n.tsx
│   └── main/background.ts       # Electron main process, LOCAL_APP_TOKEN + updater
├── scripts/fetch_omnisharp.py   # downloads OmniSharp + .NET SDK (third_party/, not in git)
├── unity-mcp/                   # CoplayDev/unity-mcp fork (Server + MCPForUnity plugin)
└── docker-compose.yml
```

---

## 🔧 Tool Ecosystem (MCP + Function Calling)

Tool usage is a layer **independent** of the provider. When a model wants to read/write a file or run a command, there are these mechanisms:

| Mechanism | Who | How |
|---|---|---|
| **Function Calling** | Cloud API + Ollama | Model emits a JSON tool call → `AgentRunner` catches it → runs it via `ToolRegistry` |
| **MCP** | Claude Code, Codex | The CLI is an MCP client → connects to MCP servers via the config the backend writes → calls tools over stdio/HTTP |
| **run_command bridge** | agy | agy calls the `unityai` CLI via `run_command` → the CLI uses the same approval gate as MCP |

### MCP servers the backend runs

1. **unityai MCP** (`Backend/app/unity_ai_mcp/server.py`) — `save_file`, `delete_file`, `read_file`, `list_directory`, `bash`/`run_terminal_command`/`execute_shell_command`. Write/delete/command operations are routed to the approval panel via `approval_bridge`.
2. **Unity MCP** (CoplayDev/unity-mcp) — 52 tools for the Unity Editor (scene, GameObject, prefab, input, playtest…), over HTTP at `127.0.0.1:8080`. The URL fixes the profile: `/mcp` (groups currently enabled in the Editor plus meta-tools, so its list follows the Editor's toggles; 37 with no Editor), `/mcp/gamachine` (core + playtest, 32), `/mcp/full` (every group, 52); all need `X-API-Key`. See [unity-mcp.md](unity-mcp.md).

### When are MCP configs written?

| Event | Result |
|---|---|
| Cloud API / Ollama selected | No config written (function calling is used) |
| CLI model selected | Nothing happens yet |
| **A message is sent with a CLI** | The **unityai MCP** entry is written to that CLI's config file |
| **Unity MCP toggle turned on** | The Unity MCP subprocess starts; the next CLI call adds a `unityMCP` entry to the config |
| Toggle turned off | The server stops, `unityMCP` is removed from subsequent configs |

---

## 🧩 Agentic System Details

### Toolbox (function-calling ToolRegistry)

```python
read_file(file_path)                 # Read file (max 500 lines, summarized)
write_file(file_path, content)       # Write file (approval)
delete_file(file_path)               # Delete file (approval)
list_directory(dir_path)             # List a directory
search_in_project(query, exts)       # In-project search
find_files(pattern)                  # File-name pattern
run_command(command)                 # Terminal (approval if dangerous)
save_to_memory(content) / recall_memory()   # Persistent memory
capture_unity_screenshot()           # Editor screenshot (visual verification)
```

### SSE Event Stream

```
POST /chat-stream → SSE opens
event: thinking      → the AI's reasoning text
event: tool_call     → the called tool + arguments
event: tool_result   → tool output summary
event: response      → final answer (+ code blocks)
event: context_usage → context fill percentage
event: done
```

---

---

[← Back to the README](../README.md)
