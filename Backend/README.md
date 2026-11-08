# Backend

## Isolated dev backend

A second backend can run beside a live Gamachine without touching its Unity MCP
server on port 8080. With `GAMACHINE_NO_UNITY_MCP=1` this backend never starts,
probes, supervises or stops the Unity MCP server, and skips the 8080 port cleanup
at startup and shutdown.

Set these in the shell that starts it (PowerShell syntax; `<tmp>` is a fresh temp dir):

```powershell
$env:USERPROFILE = "<tmp>\home"
$env:HOME = "<tmp>\home"
$env:APPDATA = "<tmp>\home\AppData\Roaming"
$env:LOCALAPPDATA = "<tmp>\home\AppData\Local"
$env:DB_PATH = "<tmp>\gamachine.db"
$env:PORT = "18123"              # a free port in 18000-18999
$env:HOST = "127.0.0.1"
$env:LOCAL_APP_TOKEN = "<any test token>"
$env:GAMACHINE_NO_UNITY_MCP = "1"
$env:CLAUDE_CONFIG_DIR = "<tmp>\home\.claude"
$env:KIMI_CODE_HOME = "<tmp>\home\.kimi"
venv\Scripts\python.exe app\main.py
```

Warning: chat, usage and agent routes may still start provider CLIs. Do not call
them from an isolated backend unless that is intended.

The contract suite in `contract/` (repo root) starts exactly this kind of backend.
It runs on a temp root, with fake provider CLIs on a restricted PATH and a fake
OpenAI-compatible model server behind `OPENAI_BASE_URL`; see `contract/README.md`.
Under the switch, three earlier gaps are closed. The C# project sync and the scene
editor no longer contact 8080. Saving a workspace no longer rewrites the project's
`UserSettings/Gamachine/autoconnect.json`. The Unity toggle no longer answers a
bare 500; it answers 409 with "Unity MCP is disabled for this backend
(GAMACHINE_NO_UNITY_MCP)." (`Backend/tests/test_no_unity_mcp_gaps.py`).
