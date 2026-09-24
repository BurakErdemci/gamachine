# Supported AI providers

Every provider Gamachine can talk to, grouped by where the model runs.

---

## 🤖 Supported AI Providers

Providers fall into 3 categories based on **where the model runs**. Tool usage (MCP / function calling) is a separate layer — covered in the next section.

### 1. Subscription CLI agents (the headline)

The backend invokes the official CLI on your machine as a subprocess, using the tool's own auth (your subscription). Tool usage is via **MCP** — the backend writes the required config before each call.

| CLI Tool | Models (example) | Config file | Tool mechanism |
|---|---|---|---|
| **Claude Code** | claude-sonnet-5, claude-fable-5, claude-opus-5-5, claude-opus-4-8, claude-haiku-4-5 | `~/.claude.json` (user scope) | MCP native (stdio + HTTP) |
| **Codex** | gpt-6-astra/sol/luna, gpt-5.6-sol/terra/luna, gpt-5.5, gpt-5.4 | `~/.codex/config.toml` | MCP native |
| **Antigravity (agy)** | Gemini 3.5 Flash + Claude/GPT-OSS via agy | `~/.gemini/antigravity-cli/` | `run_command` → `unityai` bridge |
| **GitHub Copilot** | copilot-auto + Claude/GPT/Gemini options | session-scoped `--additional-mcp-config` | MCP native (global config untouched) |
| **Cursor** | cursor-auto + Claude/GPT options | session-scoped | MCP native |
| **OpenCode** | free pool + your own key | `opencode.json` | MCP native |
| **Kimi Code** | kimi-k3, kimi-k2.7-code | `<workspace>/.mcp.json` | MCP native |

> ⚠️ **Kimi Code honesty note:** the provider is written and its tests pass, but the development machine has no Kimi subscription, so it has **never been exercised end to end**. If you hit unexpected behaviour on the Kimi path, that is why — an issue would be welcome.

Antigravity uses one persistent `agy --input-format stream-json --output-format stream-json -p=""` process per conversation.
Each turn is one UTF-8 NDJSON stdin line; text, tools, and usage arrive on stdout, with no history injection or prompt in argv.
Turns are serialized globally. A model change respawns the process; saved conversation UUIDs support resume after restart or process failure.
File and terminal operations retain the `unityai` approval bridge, while configured MCP tools are available directly.

### 2. Cloud API (direct API call)

The backend calls the provider's official SDK or the OpenRouter gateway. Tool usage is via **function calling** (the model emits a JSON tool call, the `AgentRunner` dispatches it).

| Provider | Models (example) | Notes |
|---|---|---|
| **Anthropic** | claude-sonnet-5, claude-fable-5, claude-opus-5-5, claude-opus-4-8, claude-haiku-4-5 | Extended Thinking, tool use |
| **Google** | gemini-3.8-flash, gemini-3.7-flash, gemini-3.6-flash, gemini-3.5-flash (+lite), gemini-3.1-pro, gemini-3.1-flash-lite | Thinking stream, vision |
| **OpenAI** | gpt-6-astra/sol/luna (araç çağrısı YOK — Responses API gerekiyor), gpt-5.6-sol/terra/luna, gpt-5.5-pro, gpt-5.5, gpt-5.4 | Function calling, vision |
| **NVIDIA NIM** | GLM 5.2, Qwen3 Coder 480B, Nemotron 3 Ultra/Super, Mistral Large 3, Kimi K2.6… | **Free pool** with a single `nvapi-` key (40 RPM) |
| **z-ai** | glm-5.2 | Open-weight, 1M context |
| **Groq** | llama-3.3-70b-versatile | Low latency (LPU) |
| **DeepSeek** | deepseek-v4-pro, deepseek-v4-flash | Cost-effective |
| **Moonshot / Kimi** | kimi-k3, kimi-k2.7-code, kimi-k2.6 | Long context; thinking is always on for K3 |
| **OpenRouter** | all of the above via `openrouter_id` | One key, all providers (fallback path) |

### 3. Local (Ollama)

`http://localhost:11434` is polled; all installed models are listed dynamically. Zero cost, fully offline. Models that support function calling (Llama 3.3, Qwen2.5, etc.) can use tools.

---

---

[← Back to the README](../README.md)
