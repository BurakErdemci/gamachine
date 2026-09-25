# MCP for Unity - 开发者指南

| [English](README-DEV.md) | [简体中文](README-DEV-zh.md) |
|---------------------------|------------------------------|

## 贡献代码

**从 `beta` 分支创建 PR**。`main` 分支仅用于稳定版本发布。

在提出重大新功能之前，请先联系讨论——可能已有人在开发，或者该功能曾被讨论过。请通过 issue 或 discussion 进行协调。

## 本地开发环境设置

### 1. 将 Unity 指向本地 Server

开发 Python server 时，最快的迭代方式：

1. 打开 Unity，进入 **Window > MCP for Unity**
2. 打开 **Settings > Advanced Settings**
3. 将 **Server Source Override** 设置为本地 `Server/` 目录路径
4. 启用 **Dev Mode (Force fresh server install)** - 这会在 uvx 命令中添加 `--refresh`，确保每次启动 server 时都使用最新代码

### 2. 切换包源

使用 `mcp_source.py` 快速切换 Unity 项目的 MCP 包源：

```bash
python mcp_source.py
```

选项：
1. **Upstream main** - 稳定版本 (CoplayDev/unity-mcp)
2. **Upstream beta** - 开发分支 (CoplayDev/unity-mcp#beta)
3. **Remote branch** - 你的 fork 当前分支
4. **Local workspace** - 指向本地 MCPForUnity 文件夹的 file: URL

切换后，在 Unity 中打开 Package Manager 并 Refresh 以重新解析依赖。

## 工具选择与 Meta-Tool

MCP for Unity 将工具组织为**分组**（Core、Playtest、Docs、VFX & Shaders、Animation、UI Toolkit、Scripting Extensions、Testing、ProBuilder、Profiling）。Core 和 Playtest 默认启用；Playtest 包含确定性试玩工具（`game_hooks`、`play_session`、`play_step`、`play_capture`、`run_playtest`）。你可以选择性地启用或禁用工具，以控制哪些能力暴露给 AI 客户端——减少上下文窗口占用，让 AI 专注于相关工具。

在 HTTP 模式下，客户端看到的工具列表由其连接的 URL 决定，在一次连接内不会改变（所有 URL 都需要 `X-API-Key` 请求头；未知的 profile 返回 404）：

| URL | 工具 |
|-----|------|
| `/mcp` | Unity Editor 中启用的分组（默认 core + playtest）加上服务器 meta-tools；Editor 未连接时共 36 个工具 |
| `/mcp/gamachine` | 仅 core + playtest，不含 meta-tools（31 个工具）；即 Gamachine 后端导出的集合 |
| `/mcp/full` | 所有分组，不受 Editor 开关影响（51 个工具） |

下文的 Editor 开关只影响 `/mcp` 和 `/mcp/gamachine`。

### 使用编辑器中的 Tools 标签页

打开 **Window > MCP for Unity**，切换到 **Tools** 标签页。每个工具分组显示为可折叠面板，包含：

- **单个工具开关** — 点击单个工具的开关来启用或禁用。
- **分组复选框** — 每个分组折叠面板的标题旁内嵌一个复选框，可一次性启用或禁用该分组内所有工具，且不会触发折叠面板的展开或收起。
- **Enable All / Disable All** — 全局按钮，切换所有工具的启用状态。
- **Rescan** — 重新从程序集发现工具（在添加新的 `[McpForUnityTool]` 类后使用）。
- **Reconfigure Clients** — 一键重新注册工具到服务器并重新配置所有检测到的 MCP 客户端，无需返回 Clients 标签页即可应用更改。

### 更改如何传播

工具可见性的变更根据传输模式有所不同：

**HTTP 模式**（推荐）：

1. 切换工具会调用 `ReregisterToolsAsync()`，通过 WebSocket 将更新后的启用工具列表发送到 Python 服务器。
2. 服务器替换其启用分组集合（`Server/src/transport/tool_profiles.py`）；Unity 注册了某分组中至少一个工具时，该分组即视为启用。
3. 服务器向保持通知流打开的已连接客户端发送 `tools/list_changed` MCP 通知。
4. 处理该通知的客户端会重新获取列表；其他客户端（包括 Claude Code）在重新连接前保留连接时获得的列表。

**Stdio 模式**：

1. 开关状态在本地保存，但无法推送到服务器（没有 WebSocket 连接）。
2. 服务器启动时所有分组均启用。更改开关后，让 AI 执行 `manage_tools`，`action` 设为 `'sync'`——这会从 Unity 拉取当前工具状态并同步服务器可见性。
3. 也可以重启服务器来应用更改。

### `manage_tools` Meta-Tool

服务器暴露一个内置的 `manage_tools` 工具（不受分组限制；在 `/mcp` 和 `/mcp/full` 上列出，`/mcp/gamachine` 上没有），AI 可以直接调用：

| Action | 描述 |
|--------|------|
| `list_groups` | 列出所有工具分组及其工具、每个分组在本次调用所用 URL 上是否可用，以及（HTTP）当前 profile 和其他 profile URL |
| `activate` | 不做任何更改：返回 `success=false`，并指向各 profile URL（HTTP）或 Editor 工具设置加 `sync`（stdio） |
| `deactivate` | 同 `activate` |
| `sync` | 从 Unity 拉取当前工具状态并同步服务器可见性（stdio 模式必需） |
| `reset` | 同 `activate`：没有按会话保存的状态可重置 |

### 何时需要重新配置

切换工具启用/禁用后，MCP 客户端需要获知这些变更：

- **HTTP 模式**：变更立即作用于 `/mcp` 和 `/mcp/gamachine`，工具调用按当前分组状态检查。处理 `tools/list_changed` 的客户端会刷新列表，其他客户端需要重新连接。如果客户端未更新，请在 Tools 标签页点击 **Reconfigure Clients**，或前往 Clients 标签页点击 Configure。若不想改动开关而需要全部工具，请让客户端连接 `/mcp/full`。
- **Stdio 模式**：服务器进程需要被告知变更。可以让 AI 调用 `manage_tools(action='sync')`，或重启 MCP 会话。点击 **Reconfigure Clients** 以使用更新后的配置重新注册所有客户端。

## 运行测试

所有新功能都应包含测试覆盖。

### Python 测试 (502 个测试)

位于 `Server/tests/`：

```bash
cd Server
uv run pytest tests/ -v
```

### Unity C# 测试 (605 个测试)

位于 `TestProjects/UnityMCPTests/Assets/Tests/`。

**使用 CLI**（需要 Unity 运行且 MCP bridge 已连接）：

```bash
cd Server

# 运行 EditMode 测试（默认）
uv run python -m cli.main editor tests

# 运行 PlayMode 测试
uv run python -m cli.main editor tests --mode PlayMode

# 异步运行并轮询结果（适用于长时间测试）
uv run python -m cli.main editor tests --async
uv run python -m cli.main editor poll-test <job_id> --wait 60

# 仅显示失败的测试
uv run python -m cli.main editor tests --failed-only
```

**直接使用 MCP 工具**（从任意 MCP 客户端）：

```
run_tests(mode="EditMode")
run_tests(mode="PlayMode", init_timeout=120000)  # PlayMode 由于域重载可能需要更长的初始化时间
get_test_job(job_id="<id>", wait_timeout=60)
```

### 代码覆盖率

```bash
cd Server
uv run pytest tests/ --cov --cov-report=html
open htmlcov/index.html
```
