# Approval and security architecture

What the approval gate covers, what it deliberately does not, and how the local
token architecture works. The honesty note about approval scope is the part worth
reading first.

---

## 🛡 Approval & Security Architecture

A multi-layered defense to make an AI's access to the terminal and file system safe:

### 1. File system lock
- All file operations are bounded by `workspace_path`; `Path.resolve()` + prefix checks reject attempts to escape the workspace (both `_resolve` on the backend MCP side and `isAllowedWorkspacePath` on the Electron IPC side).

### 2. Approval gate (shared by all agents)

```
AI wants to change a file
          │
   Any change? (strip() comparison)
     │              │
    No → "no change"
     │
    Yes → gate opens → DiffViewer in the UI
              │
        ┌─────┴─────┐
      Approve      Reject
        │             │
      Written     "rejected" returned
```

The flow above applies to the **CLI agents** (Claude Code, Codex, Copilot, Cursor, OpenCode, Kimi Code) and the **`unityai` bridge** (agy): on those paths a file write goes through `approval_bridge` and not a byte reaches disk unapproved. On the **cloud API / Ollama function-calling path, writes are deliberately out of scope**; deletes and dangerous terminal commands are gated there too.

### ⚠️ Approval scope: what is and isn't confirmed (an honesty note)

The gate does not cover everything. The remaining deliberate trade-offs are **file writes on the cloud API path** and **live Unity scene operations on the Codex/agy paths**:

| Operation | Tool | Approval? |
|---|---|:---:|
| Create / edit a file — **CLI agents** | `save_file` (MCP) / `unityai save-file` | ✅ **Diff card appears** |
| Create / edit a file — **cloud API & Ollama** | `write_file` (function calling) | ❌ **No approval, writes directly** |
| Delete a file | `delete_file` / `unityai` / function calling | ✅ **Delete card appears** |
| Terminal command | `bash` / `run_command` | ✅ (except safe commands) |
| unityMCP call that **reads** the scene | `manage_scene action=get_hierarchy`, `read_console`… | ➖ No card (read) |
| unityMCP call that **mutates** the scene — **Claude path** | `manage_gameobject`, `manage_input`… | ✅ **Card appears** (v2.3.0) |
| unityMCP call that **mutates** the scene — **Codex / agy** | same tools | ❌ **No approval, runs directly** |
| Write/delete/move a `.meta` file, or write a Unity YAML asset as raw text, inside a Unity project | any file tool or shell, every provider | 🚫 **Refused in every mode** — a fixed rule, not a card ([§5](#5-fixed-unity-file-rule-every-approval-mode)) |
| Same, through a shell command | `Bash`, `run_command`… | ⚠️ Heuristic: direct forms refused, indirect ones (variables, scripts) not caught |
| Same, through `execute_code` / `execute_menu_item` | Unity MCP | ❌ **Not checked** — their effect is not visible in the arguments |

**Why is `write_file` unapproved on the cloud API path?** Writing code into the workspace is what this product is for. Asking on every write trains reflex-approval, which does not strengthen the gate — it destroys it, and then the delete card that actually matters gets approved by the same reflex. Writes are instead **confined to the workspace** by `_validate_path` (`Path.resolve()` + prefix check). Deletes are rare and irreversible, so they always show a card.

> **In practice:** with a cloud API model, **"create PlayerController.cs"** writes without asking (git can undo it). The same request through Claude Code / Codex / agy shows a diff card. If you want to see every change before it lands, **pick one of the CLI agents.**

**Why does unityMCP differ per provider?** Originally it was ungated everywhere: scene operations are undoable (Ctrl+Z) and a card on every GameObject move made the workflow unusable. v2.3.0 removed that trade-off on the Claude path — but opens a card only for calls that **mutate state**.

The read/write split is not a guess, it is a ledger: `unity-mcp/Server/src/services/registry/tool_actions.json` classifies every action of every tool, and `Backend/app/unity_tool_policy.py` reads it from the source rather than copying it — a copied list previously granted an exemption to an action that did not exist, and that line never matched anything. **If the ledger cannot be read, the policy fails closed:** no exemptions, every call shows a card.

Codex and agy do not have this gate: unityMCP is still handed to them with `default_tools_approval_mode = "approve"` (Codex) and `trust: true` (agy).

### 3. Terminal security
- Safe (read-only) commands run directly; any command outside the whitelist shows an approval card
- Attempts to write files via the terminal (`python3 -c "open().write()"`, `printf > path`, `echo > path`) are caught and routed to the DiffViewer
- CLI built-in write tools (`Write`/`Edit`, agy's `write_to_file`, etc.) are disabled via `disallowedTools`/`disabledTools` → the model is forced onto the approved channel (`save_file` / `unityai`)

### 4. Local token architecture (ephemeral)

Because this is a desktop app, the OAuth/JWT/session-DB layers were **removed**. In their place, an application-lifetime token:

```
Electron starts → generates a token with randomUUID()
   ├─► Backend subprocess env (LOCAL_APP_TOKEN)
   ├─► Unity MCP subprocess env
   └─► Exposed to the renderer via IPC ('app-token-get')

Every HTTP request carries an X-Session-Token header
   → auth_utils._check_token() compares against the env var
       mismatch → 401 · match → user_id=1 (single local user)
```

- **API key encryption**: keys are encrypted with Fernet; the key lives deterministically at `~/.unity_architect_ai/api_key_fernet.key` (file-based because an unsigned packaged binary can't reliably read the Keychain). The `api_keys` table holds only encrypted data.
  > **Why the old name in that path?** `~/.unity_architect_ai/` and the keyring service name are **legacy paths, deliberately kept for backward compatibility**. They are the address of your existing encryption key and database — renaming them during the move to Gamachine would have made every already-installed user's saved keys undecryptable. The rename stops at the user's data directory on purpose.

### 5. Fixed Unity file rule (every approval mode)

Inside a Unity project (a folder holding both `Assets/` and `ProjectSettings/`, for
files under either of them) an agent may not:

- write, create, delete, move or rename a **`.meta` file** — Unity owns them, and a
  lost or duplicated `.meta` breaks the asset's GUID and every reference to it;
- write a **Unity YAML asset** (`.unity`, `.prefab`, `.asset`, `.mat`, `.controller`,
  `.anim`, `.physicMaterial`, ...) as raw text, or delete/move one as a raw file,
  which orphans its `.meta`.

This is a rule, not an approval card: it is checked before the approval mode, so it
holds in **step and auto mode alike** and no card is shown for a call that would be
refused anyway. The refusal goes back to the model with what to use instead: the
Unity MCP tools (`manage_asset` move/rename/delete, `manage_scene`, `manage_prefabs`,
`manage_material`, ...), which make Unity write the file and move the `.meta` with
it. Outside a Unity project nothing is refused.

| Path | What is checked |
|---|---|
| Claude Code | `Write`, `Edit`, `MultiEdit`, `NotebookEdit`; `Bash` and `PowerShell` commands |
| Codex | file changes and commands in its approval requests |
| agy | `write_to_file`, `replace_file_content`, `multi_replace_file_content`, `sed_file` and `run_command` (the agy hook), plus the `unityai` bridge |
| unityai MCP (CLI agents) | `save_file`, `delete_file`, `bash` |
| Cloud API / Ollama | `write_file`, `delete_file`, `run_command` |
| Unity MCP server | every tool call (including `batch_execute` sub-calls) and `/api/command` — the `.meta` half only, because these tools are the route that makes Unity write YAML assets |

The backend copy is `Backend/app/unity_file_guard.py`; the server keeps its own
(`unity-mcp/Server/src/services/protection_rules.py`) because it cannot import the
backend. NTFS 8.3 short names (`LONGAS~1.PRE`, `XPNG~1.MET`) are judged by the long
name of the file they open, not by their own extension.

Known gaps, stated rather than hidden:

- **Shell commands are checked by a heuristic.** It catches direct deletes/moves,
  redirects, write cmdlets, in-place `sed`/`perl` and copies onto a protected file.
  A variable, a script file or `python -c` can still reach one.
- **`execute_code` and `execute_menu_item` cannot be checked.** What arbitrary C# or
  a menu item touches is not visible in their arguments.
- **A Codex refusal carries no reason text.** Codex's decline has no reason field, so
  the model only sees that the call was declined; the chat shows the reason.

---

---

[← Back to the README](../README.md)
