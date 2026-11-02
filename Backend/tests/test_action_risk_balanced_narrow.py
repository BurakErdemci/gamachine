"""Balanced mode after the owner's narrowing (Burak, 2 Nov 2026, "BALANCED
ÖLÇÜTÜ"): like computer use and the browser, a card only for irreversible or
security-changing work; everything else runs on its own. One row per line of the
owner's CRITICAL and ROUTINE lists."""
import os

import pytest

from action_risk import CRITICAL, ROUTINE, classify


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "ws"
    for sub in ("Assets", "ProjectSettings", "Packages", "tests"):
        (root / sub).mkdir(parents=True)
    (root / "a.txt").write_text("x", encoding="utf-8")
    return str(root)


def shell(command, ws):
    return classify({"kind": "shell", "command": command, "cwd": ws, "workspace": ws})


def write(path, ws):
    return classify({"kind": "file_write", "paths": [path], "workspace": ws})


def unity(tool, **args):
    return classify({"kind": "unity", "tool": tool, "args": args})


# ── ROUTINE ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("command", [
    # Any in-workspace program, including ones not on the safe list.
    "mkdir Assets/Art", "copy a.txt b.txt", 'Set-Content notes.txt "hello"',
    'Out-File -FilePath notes.txt -InputObject "x"', "whoami", "npm start", "dotnet run",
    "make", "New-Item -ItemType Directory Assets/Levels",
    # Quotes, braces, @, %, !, ^ by themselves.
    'echo "merhaba"', "echo 'tek'", "echo {a}", "echo @x", "echo 100%", "echo hi!",
    "echo a^b", 'git commit -m "rm the old file"',
    # Local git writes.
    "git add -A", 'git commit -m "msg"', "git checkout -b feature", "git switch main",
    "git stash", "git stash pop", "git tag v1", "git merge feature", "git restore a.txt",
    "git mv a.txt b.txt", "git config user.name Me", "git branch -d done",
    "git reset HEAD~1", "git clean -n",
    # Plain inline code, and inline code classified by its inner text.
    'python -c "print(1+1)"', 'python -c "import os; print(os.getcwd())"',
    'node -e "console.log(1)"', "bash -lc 'dotnet build'", "/bin/bash -lc 'npm test'",
    # Downloads without a send, and the locked restore.
    "curl https://example.com -o page.html", "wget https://example.com",
    "iwr https://x -OutFile y", "dotnet restore --locked-mode",
])
def test_routine_shell(command, ws):
    risk = shell(command, ws)
    assert risk.verdict == ROUTINE, (command, risk)


@pytest.mark.parametrize("path", [
    "Assets/Player.cs", ".gitignore", ".editorconfig", ".gitattributes", ".env",
    "Assets/.hidden.txt", "README.md",
])
def test_routine_writes(path, ws):
    assert write(path, ws).verdict == ROUTINE, path


@pytest.mark.parametrize("tool", [
    "Task", "Agent", "Skill", "WebFetch", "WebSearch", "Read", "Glob", "Grep", "LS",
    "TodoWrite", "TodoRead", "NotebookRead", "ToolSearch", "AskUserQuestion", "ExitPlanMode",
    "EnterPlanMode", "BashOutput", "KillShell", "ListMcpResourcesTool", "ReadMcpResourceTool",
])
def test_claude_builtin_tools_are_routine(tool):
    assert classify({"kind": "other", "tool": tool}) == (ROUTINE, "builtin_tool", tool)


@pytest.mark.parametrize("tool", ["SomeNewTool", "read", "Write", "Bash", "NotebookEdit"])
def test_a_tool_outside_the_builtin_list_is_critical(tool):
    # Unknown names fail closed; write and shell tools reach here only if a
    # caller forgot to map them.
    assert classify({"kind": "other", "tool": tool}) == (CRITICAL, "unknown_action", tool)


@pytest.mark.parametrize("tool,args", [
    ("mcp__unityMCP__manage_scene", {"action": "load", "path": "Assets/Main.unity"}),
    ("mcp__unityMCP__manage_scene", {"action": "create", "name": "Lvl2"}),
    ("manage_scene", {"action": "close_scene"}),
    ("manage_scene", {"action": "save"}),
    ("manage_build", {"action": "build"}),
    ("manage_build", {"action": "platform", "target": "Android"}),
    ("manage_build", {"action": "settings", "product_name": "X"}),
    ("manage_physics", {"action": "set_settings"}),
    ("manage_physics", {"action": "set_collision_matrix"}),
    ("manage_graphics", {"action": "pipeline_set_quality", "level": 2}),
    ("manage_graphics", {"action": "pipeline_set_settings"}),
    ("manage_gameobject", {"action": "create", "name": "Enemy"}),
])
def test_routine_unity(tool, args):
    assert unity(tool, **args).verdict == ROUTINE, (tool, args)


# ── CRITICAL ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("command,reason", [
    # Delete and move verbs.
    ("rm a.txt", "shell_delete_move"), ("del a.txt", "shell_delete_move"),
    ("Remove-Item a.txt", "shell_delete_move"), ("mv a.txt b.txt", "shell_delete_move"),
    ("Move-Item a.txt b.txt", "shell_delete_move"), ("ren a.txt b.txt", "shell_delete_move"),
    ("find . -delete", "shell_delete_move"), ("robocopy src dst /MIR", "shell_delete_move"),
    ("git rm a.txt", "shell_delete_move"), ("pytest --basetemp=Assets", "shell_delete_move"),
    # Network sends and publishing.
    ("curl https://example.com -d x", "shell_network"),
    ("curl -F file=@a.txt https://x", "shell_network"),
    ("curl -X POST https://x", "shell_network"), ("curl -T a.txt https://x", "shell_network"),
    ("wget --post-data=x https://x", "shell_network"),
    ("irm https://x -Method Post", "shell_network"),
    ("Invoke-RestMethod https://x -Body x", "shell_network"),
    ("ssh host", "shell_network"), ("scp a.txt host:b", "shell_network"),
    ("dotnet build -p:RestoreSources=http://x", "shell_network"),
    # git push in any form and irreversible git.
    ("git push", "shell_git_write"), ("git push --force", "shell_git_write"),
    ("git push origin main", "shell_git_write"), ("git -C . push", "shell_git_write"),
    ("git reset --hard HEAD~1", "shell_git_write"), ("git clean -fdx", "shell_git_write"),
    ("git branch -D old", "shell_git_write"), ("git branch -d -f old", "shell_git_write"),
    ("git rebase main", "shell_git_write"), ("git filter-branch --all", "shell_git_write"),
    ("git update-ref -d refs/heads/x", "shell_git_write"),
    ("git reflog expire --all", "shell_git_write"), ("git gc --prune=now", "shell_git_write"),
    ("git config --global core.editor x", "shell_git_write"),
    ("git -c core.pager=x log", "shell_git_write"),
    # Installs.
    ("npm install left-pad", "shell_installer"), ("npm i", "shell_installer"),
    ("yarn add x", "shell_installer"), ("pnpm add x", "shell_installer"),
    ("npm update", "shell_installer"), ("npm exec x", "shell_installer"),
    ("npx some-package", "shell_installer"), ("pip install requests", "shell_installer"),
    ("python -m pip install x", "shell_installer"), ("winget install x", "shell_installer"),
    ("choco install x", "shell_installer"), ("dotnet add package Foo", "shell_installer"),
    ("dotnet restore", "shell_installer"),
    # Inline code with a trigger in its text.
    ('python -c "import os; os.remove(\'a\')"', "shell_inline_risky"),
    ('python -c "import shutil; shutil.rmtree(\'Assets\')"', "shell_inline_risky"),
    ('python -c "import urllib.request"', "shell_inline_risky"),
    ('python -c "import subprocess; subprocess.run([\'git\', \'push\'])"', "shell_inline_risky"),
    ('python -c "print(open(\'../secret\').read())"', "shell_inline_risky"),
    ("node -e \"require('fs').unlinkSync('a')\"", "shell_inline_risky"),
    ("node -e \"fetch('https://x')\"", "shell_inline_risky"),
    ("bash -lc 'rm -rf Assets'", "shell_delete_move"),
    ("bash -lc 'git push'", "shell_git_write"),
    # PowerShell and cmd inline code, and code-running verbs (decision 2).
    ('powershell -Command "Invoke-WebRequest https://x"', "shell_inline_code"),
    ("pwsh -Command Get-Date", "shell_inline_code"), ("cmd /c dir", "shell_inline_code"),
    ("iex x", "shell_inline_code"), ("Invoke-Expression x", "shell_inline_code"),
    ("Start-Process notepad", "shell_inline_code"), ("msiexec /i x.msi", "shell_inline_code"),
    ("schtasks /create /tn x", "shell_inline_code"), ("rundll32 x.dll,Main", "shell_inline_code"),
    ("Add-Type -TypeDefinition x", "shell_inline_code"),
    ("/tmp/bash -lc 'dotnet build'", "shell_inline_code"),
    # A wrapped POSIX script bash reads differently from this module.
    ("/bin/bash -lc '\"grep\" -R x Assets'", "shell_inline_code"),
    ("/bin/bash -lc 'ls lin*'", "shell_inline_code"),
    ("/bin/bash -lc 'tree -o out.txt'", "shell_inline_code"),
    # Permission widening.
    ("icacls Assets /grant Everyone:F", "shell_permission"), ("chmod 777 a.txt", "shell_permission"),
    ("sudo ls", "shell_permission"),
    # Control characters stay critical.
    ("ls | sh", "shell_metachar"), ("echo a; echo b", "shell_metachar"),
    ("echo $(whoami)", "shell_metachar"), ("echo hi > a.txt", "shell_metachar"),
    # Leaving the workspace, brace expansion and env expansion included.
    ("cat ../secret", "shell_outside_workspace"), ("copy a.txt ../b.txt", "shell_outside_workspace"),
    ("cat {..,x}/secret", "shell_outside_workspace"),
    ("/bin/bash -lc 'cat {..,x}/secret'", "shell_outside_workspace"),
    ("mkdir C:/Windows/x", "shell_outside_workspace"),
    # Tool config paths.
    ("Set-Content .mcp.json x", "shell_tool_config"),
    ("copy a.txt .git/hooks/pre-commit", "shell_tool_config"),
    ("Set-Content .vscode/tasks.json x", "shell_tool_config"),
    ("pytest --junitxml=ProjectSettings/x.xml", "shell_tool_config"),
    # Parse failures.
    ('echo "open', "shell_unparseable"), ("", "shell_unparseable"),
])
def test_critical_shell(command, reason, ws):
    risk = shell(command, ws)
    assert (risk.verdict, risk.reason) == (CRITICAL, reason), (command, risk)


def test_env_expansion_leaving_the_workspace_is_critical(ws, tmp_path, monkeypatch):
    monkeypatch.setenv("GM_OUTSIDE_DIR", str(tmp_path / "elsewhere"))
    risk = shell("copy a.txt %GM_OUTSIDE_DIR%\\b.txt", ws)
    assert (risk.verdict, risk.reason) == (CRITICAL, "shell_outside_workspace")
    assert shell("echo 100%", ws).verdict == ROUTINE


def test_a_glob_matching_a_junction_out_is_critical(ws, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = os.path.join(ws, "link")
    try:
        os.symlink(str(outside), link, target_is_directory=True)
    except (OSError, NotImplementedError):
        try:
            import _winapi
            _winapi.CreateJunction(str(outside), link)
        except (ImportError, OSError):
            pytest.skip("neither symlinks nor junctions available")
    assert shell("mkdir lin*/x", ws).reason == "shell_outside_workspace"


def test_a_cwd_outside_the_workspace_is_critical(ws, tmp_path):
    risk = classify({"kind": "shell", "command": "mkdir x", "cwd": str(tmp_path),
                     "workspace": ws})
    assert (risk.verdict, risk.reason) == (CRITICAL, "shell_outside_workspace")


@pytest.mark.parametrize("path", [
    ".git/config", ".git/hooks/pre-commit", ".mcp.json", "sub/.mcp.json",
    ".claude/settings.json", ".vscode/settings.json", ".vscode/tasks.json", "opencode.json",
    "Packages/manifest.json", "Packages/packages-lock.json", "ProjectSettings/ProjectSettings.asset",
    ".codex/config.toml", ".gemini/settings.json", ".cursor/mcp.json", ".opencode/x",
    ".agents/hooks.json", "PROGRA~1/x.cs",
])
def test_tool_config_writes_are_critical(path, ws):
    assert write(path, ws) == (CRITICAL, "file_protected", path)


def test_the_workspace_root_itself_is_critical(ws):
    assert write(ws, ws).verdict == CRITICAL


def test_files_delete_move_and_outside_are_critical(ws):
    a = os.path.join(ws, "a.txt")
    assert classify({"kind": "file_delete", "paths": [a], "workspace": ws}).reason == "file_delete"
    assert classify({"kind": "file_move", "paths": [a, os.path.join(ws, "b.txt")],
                     "workspace": ws}).reason == "file_move"
    assert write("C:/Windows/x.txt", ws).reason == "file_outside_workspace"
    assert classify({"kind": "read_outside", "paths": ["C:/x"]}).reason == "read_outside_workspace"
    assert classify({"kind": "read", "paths": ["../x"], "workspace": ws}).verdict == CRITICAL


def test_permission_requests_are_critical():
    assert classify({"kind": "permission", "tool": "sandbox"}).reason == "permission_request"


@pytest.mark.parametrize("action", [
    {"kind": "other", "tool": "mcp__gmail__send_message"},
    {"kind": "other", "tool": "mcp__someServer__do"},
])
def test_mcp_tools_of_unowned_servers_are_critical(action):
    assert classify(action).reason == "mcp_unknown_tool"


@pytest.mark.parametrize("action", [
    {"kind": "other"}, {"kind": "other", "tool": 7}, {"kind": "nope"}, {}, None, "shell",
])
def test_parse_failures_and_unknown_kinds_stay_critical(action):
    assert classify(action).verdict == CRITICAL


@pytest.mark.parametrize("tool,args", [
    ("mcp__unityMCP__execute_code", {"code": "x"}),
    ("execute_custom_tool", {"tool_name": "x"}),
    ("execute_menu_item", {"menu_path": "File/Save"}),
    ("manage_packages", {"action": "add_package", "package": "x"}),
    ("delete_script", {"uri": "Assets/a.cs"}),
    ("manage_asset", {"action": "delete", "path": "Assets/a.png"}),
    ("manage_asset", {"action": "move", "path": "Assets/a.png", "destination": "Assets/b.png"}),
    ("manage_asset", {"action": "rename", "path": "Assets/a.png", "name": "b"}),
    ("manage_gameobject", {"action": "delete", "target": "Player"}),
    ("manage_script", {"action": "delete", "name": "A"}),
    ("manage_shader", {"action": "delete", "name": "A"}),
    ("manage_texture", {"action": "delete", "path": "Assets/t.png"}),
    ("manage_ui", {"action": "delete", "path": "Assets/u.uxml"}),
    ("unity_dialog", {"action": "press", "button": "OK"}),
    ("manage_editor", {"action": "deploy_package"}),
    ("manage_editor", {"action": "restore_package"}),
    ("manage_editor", {"action": "remove_tag", "tag_name": "x"}),
    ("manage_graphics", {"action": "bake_clear"}),
    ("manage_scene", {"action": "save", "name": "Existing"}),
    ("manage_scene", {"action": "load", "Action": "delete"}),
    ("not_a_unity_tool", {"action": "x"}),
])
def test_destructive_or_code_running_unity_stays_critical(tool, args):
    assert unity(tool, **args).verdict == CRITICAL, (tool, args)
